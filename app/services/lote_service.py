"""
Service de "rodar lote agora" — orquestra todo o pipeline de postagem
automática:

1. Seleciona combinações produto × grupo (selecao_service)
2. Pra cada uma, escolhe template do nicho (templates_service)
3. Renderiza template com produto
4. Enfileira tarefa via dispatcher (que decide WS ou Celery)

Resultado: dict com estatísticas + IDs das tarefas criadas.

Não roda automaticamente — só quando admin clica "rodar lote" ou
quando Celery beat agendar (Fase 4d).
"""
from __future__ import annotations

from app.core.config import settings
from app.core.logging import get_logger
from app.models import Usuario
from app.services import (
    afiliado_service,
    dispatcher,
    linkbuilder,
    redirect_service,
    selecao_service,
    templates_service,
)
from sqlalchemy.ext.asyncio import AsyncSession

log = get_logger(__name__)


async def rodar_lote(
    db: AsyncSession,
    *,
    org_id: int,
    max_produtos: int = 10,
    canal_tipo: str | None = None,
    criado_por_usuario_id: int | None = None,
    usuario: Usuario | None = None,
) -> dict:
    """
    Executa um lote de postagens. Retorna estatísticas.

    Returns:
        {
            "produtos_avaliados": N,
            "tarefas_criadas":    M,
            "sem_grupo":          X,    # produtos sem grupo compatível
            "sem_template":       Y,    # produtos cujo nicho não tem template
            "ja_postado":         Z,    # produtos já postados recentemente nesse grupo
            "tarefas_ids":        [...] # ids das tarefas criadas
            "detalhes":           [...] # mensagens de log/erro pra UI
        }
    """
    detalhes: list[str] = []

    # 1. Seleciona
    combinacoes, stats = await selecao_service.montar_combinacoes(
        db,
        org_id=org_id,
        max_produtos=max_produtos,
        canal_tipo=canal_tipo,
        usuario=usuario,
    )

    if not combinacoes:
        if stats.get("avaliados", 0) == 0:
            detalhes.append("Nenhum produto elegível (sem produtos com nicho associado, ou todos bloqueados).")
        else:
            detalhes.append(
                f"Avaliados {stats['avaliados']} produtos, mas "
                f"{stats.get('sem_grupo_compativel', 0)} sem grupo compatível e "
                f"{stats.get('ja_postado_recentemente', 0)} já postados recentemente."
            )
        return {
            "produtos_avaliados": stats.get("avaliados", 0),
            "tarefas_criadas":    0,
            "sem_grupo":          stats.get("sem_grupo_compativel", 0),
            "sem_template":       0,
            "ja_postado":         stats.get("ja_postado_recentemente", 0),
            "tarefas_ids":        [],
            "detalhes":           detalhes,
        }

    # 2-4. Renderiza + enfileira
    # Late binding da tag de afiliado: produto.url_afiliado pode estar
    # "congelado" com tag desatualizada (de quando importou). Aqui
    # recalculamos a URL POR POSTAGEM, aplicando a tag do disparador via
    # cascata. Cada user posta com a tag dele (ou fallback do admin).
    tarefas_ids: list[int] = []
    sem_template = 0

    # Cache de tag por plataforma — evita N queries pra mesma plataforma
    # quando o lote pega vários produtos do mesmo marketplace.
    cache_tag: dict[str, str | None] = {}

    async def _url_pro_produto(p) -> str:
        """Resolve a URL final a ser postada pra um produto.

        Pipeline com PRIORIDADE (Fase 15):
        1. **meli.la oficial** se já cacheado em `p.url_afiliado` — usa
           direto. ML credita comissão de verdade. NÃO passa pelo nosso
           encurtador (`meli.la` já é shortlink dele).
        2. Senão, fallback: monta URL longa via linkbuilder (com `?matt_word=`
           que NÃO é reconhecido como afiliado válido — mas posta mesmo
           assim) + encurta no nosso `/r/{slug}` pra ficar bonito.

        Quando a Fase 15 estiver completa (agente gerou meli.la pros 50
        produtos), o caminho 1 vai dominar. O caminho 2 só serve enquanto
        o linkbuilder do agente ainda não rodou pra esse produto.
        """
        plat = (p.plataforma or "").lower()

        # Caminho 1 (Fase 15): meli.la oficial gerado pelo agente
        if plat == "ml" and p.url_afiliado and "meli.la/" in p.url_afiliado:
            return p.url_afiliado

        # Caminho 2 (fallback): tag via cascata + linkbuilder genérico + nosso /r/
        if plat not in cache_tag:
            cache_tag[plat] = await afiliado_service.tag_com_cascata(
                db,
                plataforma=plat,
                usuario_id=criado_por_usuario_id,
                org_id=org_id,
            )
        url_longa = linkbuilder.gerar_url_afiliado(
            plataforma=plat,
            url_canonica=p.url_canonica,
            tag=cache_tag[plat],
        ) or p.url_canonica or ""
        if not url_longa:
            return ""
        base = (settings.public_base_url or "").rstrip("/")
        if not base:
            return url_longa
        red = await redirect_service.criar_ou_atualizar_pro_produto(
            db, produto_id=p.id, url_destino=url_longa,
        )
        return f"{base}/r/{red.slug}"

    for comb in combinacoes:
        template = await templates_service.selecionar_template(
            db,
            org_id=org_id,
            nicho_ids=comb.nichos_do_produto,
        )

        url_override = await _url_pro_produto(comb.produto)

        if template is None:
            # Usa fallback hardcoded — não bloqueia o lote
            sem_template += 1
            texto = templates_service.renderizar(
                templates_service.TEMPLATE_FALLBACK, comb.produto,
                url_override=url_override,
            )
            detalhes.append(
                f"⚠ Produto '{comb.produto.nome[:40]}' usou template fallback "
                f"(nicho sem template cadastrado)"
            )
        else:
            texto = templates_service.renderizar(
                template, comb.produto, url_override=url_override,
            )
            await templates_service.registrar_uso(db, template_id=template.id)

        # Enfileira
        try:
            tarefa = await dispatcher.enfileirar_postagem(
                db,
                org_id=org_id,
                grupo_id=comb.grupo.id,
                texto=texto,
                imagem_url=comb.produto.foto_url,
                produto_id=comb.produto.id,
                criado_por_usuario_id=criado_por_usuario_id,
            )
            tarefas_ids.append(tarefa.id)
        except dispatcher.DispatcherError as e:
            detalhes.append(
                f"✗ Falha enfileirando produto {comb.produto.id}: {e}"
            )

    log.info(
        "lote.concluido",
        org_id=org_id,
        tarefas_criadas=len(tarefas_ids),
        sem_template=sem_template,
    )

    return {
        "produtos_avaliados": stats.get("avaliados", 0),
        "tarefas_criadas":    len(tarefas_ids),
        "sem_grupo":          stats.get("sem_grupo_compativel", 0),
        "sem_template":       sem_template,
        "ja_postado":         stats.get("ja_postado_recentemente", 0),
        "tarefas_ids":        tarefas_ids,
        "detalhes":           detalhes,
    }

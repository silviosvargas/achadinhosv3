"""
Service de buscas Mercado Livre — ingest e roteamento.

Fluxo:
1. Admin (ou Celery beat) chama `enfileirar_execucao(busca_id)` que cria uma
   Tarefa(BUSCAR_MERCADO_LIVRE) e tenta entregar via WS pro agente. Atualiza
   `proxima_exec_em` da busca.
2. Agente roda Selenium, extrai produtos, e chama
   `POST /api/v1/produtos/ingest` com o lote.
3. Endpoint chama `ingerir_produtos(...)` aqui, que:
   - Resolve tag de afiliado e dono baseado em quem disparou a busca.
   - Faz upsert por (org, [dono], plataforma, item_id).
   - Aplica mapping `categoria_ml → nicho_id` automaticamente.
   - Marca tarefa como concluida.

Regras de tag / visibilidade (ADR-008):
- Busca disparada por admin/usuario_comum → produtos públicos (dono=NULL),
  tag do admin da org.
- Busca disparada por afiliado → produtos privados (dono=afiliado),
  tag dele próprio (`Usuario.afiliado_ml`).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import (
    BuscaML,
    NichoCategoriaML,
    Produto,
    ProdutoNicho,
    StatusTarefa,
    Tarefa,
    TipoTarefa,
    Usuario,
)
from app.services import linkbuilder
from app.services.agente_registry import registry

log = get_logger(__name__)


class BuscaServiceError(Exception):
    """Erro de negócio em operação de busca."""


# ============================================================
# Detecção termo vs URL
# ============================================================

def detectar_tipo_entrada(entrada: str) -> str:
    """'url' se começa com http(s)://, senão 'termo'."""
    entrada_low = entrada.strip().lower()
    if entrada_low.startswith(("http://", "https://")):
        return "url"
    return "termo"


# ============================================================
# Enfileirar execução de busca
# ============================================================

async def enfileirar_execucao(
    db: AsyncSession,
    *,
    busca_id: int,
    org_id: int,
    criado_por_usuario_id: int | None = None,
) -> Tarefa:
    """
    Cria Tarefa(BUSCAR_MERCADO_LIVRE) e entrega via WS se agente online.
    Senão fica pendente. Atualiza estado da busca (próxima exec, contagem).
    """
    busca = await db.get(BuscaML, busca_id)
    if busca is None or busca.org_id != org_id:
        raise BuscaServiceError("Busca não encontrada nesta organização")
    if not busca.ativo:
        raise BuscaServiceError("Busca está inativa")

    # Quem dispara: o usuário que clicou (criado_por_usuario_id) OU
    # o dono original da busca (busca.criado_por_usuario_id) se for agendada.
    disparado_por = criado_por_usuario_id or busca.criado_por_usuario_id

    tipo_entrada = detectar_tipo_entrada(busca.entrada)

    tarefa = Tarefa(
        org_id=org_id,
        tipo=TipoTarefa.BUSCAR_MERCADO_LIVRE,
        status=StatusTarefa.PENDENTE,
        agente_id=busca.agente_id,
        payload={
            "busca_id":      busca.id,
            "tipo_entrada":  tipo_entrada,
            "entrada":       busca.entrada,
            "max_paginas":   busca.max_paginas,
            "max_produtos":  busca.max_produtos,
            "disparado_por": disparado_por,
        },
        criado_por_usuario_id=criado_por_usuario_id,
    )
    db.add(tarefa)
    await db.commit()
    await db.refresh(tarefa)

    # Atualiza estado da busca
    agora = datetime.now(tz=timezone.utc)
    busca.ultima_exec_em   = agora
    busca.ultima_tarefa_id = tarefa.id
    busca.execucoes       += 1
    if busca.intervalo_minutos:
        busca.proxima_exec_em = agora + timedelta(minutes=busca.intervalo_minutos)
    await db.commit()

    # Tenta entregar via WS se agente específico online
    if busca.agente_id and registry.esta_online(busca.agente_id):
        await _entregar_para_agente(db, tarefa, agente_id=busca.agente_id)
    else:
        # Sem agente específico: pega qualquer agente da org online
        # (Fase futura: round-robin entre agentes). Por enquanto: deixa pendente
        # e o agente puxa via reentregar_pendentes quando reconectar.
        log.info("busca.aguarda_agente", busca_id=busca.id, tarefa_id=tarefa.id)

    return tarefa


async def _entregar_para_agente(
    db: AsyncSession, tarefa: Tarefa, *, agente_id: int,
) -> None:
    """Envia comando `iniciar_busca_ml` via WS."""
    payload = {
        "tipo":         "iniciar_busca_ml",
        "tarefa_id":    tarefa.id,
        **tarefa.payload,
    }
    enviado = await registry.enviar_para(agente_id, payload)
    if enviado:
        tarefa.status      = StatusTarefa.PROCESSANDO
        tarefa.iniciado_em = datetime.now(tz=timezone.utc)
        tarefa.tentativas += 1
        await db.commit()
        log.info("busca.entregue", tarefa_id=tarefa.id, agente_id=agente_id)


# ============================================================
# Ingest — recebe produtos do agente e popula catálogo
# ============================================================

async def ingerir_produtos(
    db: AsyncSession,
    *,
    org_id: int,
    agente_id: int,
    produtos_recebidos: list[dict[str, Any]],
    busca_id: int | None = None,
    tarefa_id: int | None = None,
) -> dict[str, Any]:
    """
    Recebe lote de produtos extraídos pelo agente. Faz upsert respeitando:
    - tag de afiliado de quem disparou a busca
    - visibilidade pública vs privada
    - mapping categoria_ml → nicho_id (auto-classificação)

    Retorna estatísticas pra resposta ao agente.
    """
    stats = {
        "recebidos":   len(produtos_recebidos),
        "criados":     0,
        "atualizados": 0,
        "ignorados":   0,
        "com_nicho":   0,
        "detalhes":    [],
    }

    if not produtos_recebidos:
        return stats

    # 1. Descobre quem disparou pra resolver tag + dono
    disparador, dono_id = await _resolver_disparador(
        db, org_id=org_id, tarefa_id=tarefa_id, busca_id=busca_id,
    )
    tag_ml = (disparador.afiliado_ml if disparador else None)

    # Fallback: se disparador não tem tag, usa a do admin da org
    if not tag_ml and (disparador is None or not disparador.eh_admin):
        tag_ml = await _tag_do_admin(db, org_id=org_id)

    # 2. Carrega mapping categoria → nicho da org (1 query)
    mapping_rows = (await db.execute(
        select(NichoCategoriaML.categoria_ml, NichoCategoriaML.nicho_id)
        .where(NichoCategoriaML.org_id == org_id)
    )).all()
    mapping: dict[str, int] = {c.lower(): n for c, n in mapping_rows}

    # 3. Upsert em loop
    for item in produtos_recebidos:
        try:
            criou, com_nicho = await _upsert_produto(
                db,
                org_id=org_id,
                dono_id=dono_id,
                tag_ml=tag_ml,
                item=item,
                mapping_categoria=mapping,
            )
            if criou:
                stats["criados"] += 1
            else:
                stats["atualizados"] += 1
            if com_nicho:
                stats["com_nicho"] += 1
        except Exception as e:
            stats["ignorados"] += 1
            stats["detalhes"].append(
                f"item_id={item.get('item_id', '?')}: {type(e).__name__}: {str(e)[:120]}"
            )

    await db.commit()

    # 4. Marca tarefa como concluída
    if tarefa_id:
        tarefa = await db.get(Tarefa, tarefa_id)
        if tarefa and tarefa.org_id == org_id:
            tarefa.status = StatusTarefa.CONCLUIDA
            tarefa.concluido_em = datetime.now(tz=timezone.utc)
            tarefa.resultado = {
                "recebidos":   stats["recebidos"],
                "criados":     stats["criados"],
                "atualizados": stats["atualizados"],
                "ignorados":   stats["ignorados"],
                "com_nicho":   stats["com_nicho"],
            }
            await db.commit()

    log.info(
        "busca.ingest.concluido",
        org_id=org_id, agente_id=agente_id, busca_id=busca_id,
        tarefa_id=tarefa_id, **{k: v for k, v in stats.items() if k != "detalhes"},
    )
    return stats


async def _resolver_disparador(
    db: AsyncSession, *, org_id: int, tarefa_id: int | None, busca_id: int | None,
) -> tuple[Usuario | None, int | None]:
    """
    Devolve (usuario_disparador, dono_id_pra_produtos).

    - Se afiliado disparou: dono_id = afiliado.id (produto privado).
    - Senão (admin/usuario comum/sem disparador): dono_id = None (produto público).
    """
    user: Usuario | None = None

    if tarefa_id:
        tarefa = await db.get(Tarefa, tarefa_id)
        if tarefa and tarefa.criado_por_usuario_id:
            user = await db.get(Usuario, tarefa.criado_por_usuario_id)
    if user is None and busca_id:
        busca = await db.get(BuscaML, busca_id)
        if busca and busca.criado_por_usuario_id:
            user = await db.get(Usuario, busca.criado_por_usuario_id)

    if user is None:
        return None, None

    dono_id = user.id if user.eh_afiliado else None
    return user, dono_id


async def _tag_do_admin(db: AsyncSession, *, org_id: int) -> str | None:
    """Pega `afiliado_ml` do primeiro admin ativo da org."""
    result = await db.execute(
        select(Usuario.afiliado_ml)
        .where(
            Usuario.org_id == org_id,
            Usuario.ativo.is_(True),
            Usuario.papel.in_(("admin", "super")),
        )
        .order_by(Usuario.id)
        .limit(1)
    )
    row = result.first()
    return row[0] if row else None


async def _upsert_produto(
    db: AsyncSession,
    *,
    org_id: int,
    dono_id: int | None,
    tag_ml: str | None,
    item: dict[str, Any],
    mapping_categoria: dict[str, int],
) -> tuple[bool, bool]:
    """
    Insere ou atualiza um produto. Retorna (criou_novo, recebeu_nicho).
    """
    plataforma = (item.get("plataforma") or "ml").lower()
    item_id    = str(item.get("item_id") or "").strip()
    if not item_id:
        raise BuscaServiceError("item_id ausente")

    # Busca existente respeitando dono (público vs privado)
    cond_dono = (
        (Produto.usuario_dono_id.is_(None))
        if dono_id is None
        else (Produto.usuario_dono_id == dono_id)
    )
    existente = await db.scalar(
        select(Produto).where(
            Produto.org_id == org_id,
            cond_dono,
            Produto.plataforma == plataforma,
            Produto.item_id == item_id,
        )
    )

    url_canonica = item.get("url_canonica")
    url_afiliado = linkbuilder.gerar_url_afiliado(
        plataforma=plataforma, url_canonica=url_canonica, tag=tag_ml,
    )

    if existente is None:
        produto = Produto(
            org_id=org_id,
            usuario_dono_id=dono_id,
            plataforma=plataforma,
            item_id=item_id,
            nome=item.get("nome", "")[:500],
            categoria=item.get("categoria"),
            preco=float(item.get("preco") or 0),
            preco_orig=item.get("preco_orig"),
            desconto=item.get("desconto"),
            frete_gratis=bool(item.get("frete_gratis")),
            url_canonica=url_canonica,
            url_afiliado=url_afiliado,
            foto_url=item.get("foto_url"),
            fonte="busca_ml",
            descoberto_em=datetime.now(tz=timezone.utc),
        )
        db.add(produto)
        await db.flush()
        criou = True
    else:
        produto = existente
        produto.nome = item.get("nome", produto.nome)[:500]
        if item.get("categoria"):
            produto.categoria = item["categoria"]
        produto.preco = float(item.get("preco") or produto.preco)
        if item.get("preco_orig") is not None:
            produto.preco_orig = item["preco_orig"]
        if item.get("desconto") is not None:
            produto.desconto = item["desconto"]
        produto.frete_gratis = bool(item.get("frete_gratis", produto.frete_gratis))
        if url_canonica:
            produto.url_canonica = url_canonica
            produto.url_afiliado = url_afiliado
        if item.get("foto_url"):
            produto.foto_url = item["foto_url"]
        criou = False

    # Auto-classificação por categoria
    recebeu_nicho = False
    categoria = (produto.categoria or "").lower().strip()
    if categoria and not await _tem_algum_nicho(db, produto_id=produto.id):
        nicho_id = mapping_categoria.get(categoria)
        # Fallback: match por prefixo (categoria do ML é hierárquica
        # "Eletrônicos > Áudio > Fones" — tenta níveis intermediários)
        if nicho_id is None:
            for cat_chave, nid in mapping_categoria.items():
                if categoria.startswith(cat_chave) or cat_chave in categoria:
                    nicho_id = nid
                    break
        if nicho_id is not None:
            db.add(ProdutoNicho(produto_id=produto.id, nicho_id=nicho_id))
            recebeu_nicho = True

    return criou, recebeu_nicho


async def _tem_algum_nicho(db: AsyncSession, *, produto_id: int) -> bool:
    row = await db.scalar(
        select(ProdutoNicho.id).where(ProdutoNicho.produto_id == produto_id).limit(1)
    )
    return row is not None

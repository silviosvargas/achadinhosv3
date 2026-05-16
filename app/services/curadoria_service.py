"""
Curadoria via nota no produto (Fase 18 — reformulada).

A nota é calculada NO INGEST (`busca_service._upsert_produto`) usando
`scoring.calcular_nota` e gravada na coluna `produtos.nota`. Esse service
**não calcula nada** — só filtra/ordena.

Não tem snapshot diário, não tem Celery beat. TOP é live, sempre os
produtos com nota mais alta no DB.

Cascata de fallback (Fase 11 catálogo compartilhado):
1. Org do user
2. Org admin (`settings.admin_org_id`) — pra plano free
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.models import Postagem, Produto, ProdutoNicho
from app.services.scoring import calcular_nota

log = get_logger(__name__)


# Janela: produto postado nessa org nos últimos N dias NÃO entra no TOP
JANELA_DEDUP_DIAS = 7

# Nota mínima pra aparecer no TOP. Configurável por chamada.
NOTA_MINIMA_DEFAULT = 30.0


# --------------------------------------------------------------------
# Leitura — usada por endpoints API/web
# --------------------------------------------------------------------

async def listar_top(
    db: AsyncSession,
    *,
    org_id: int,
    limite: int = 50,
    nota_minima: float = NOTA_MINIMA_DEFAULT,
    janela_dedup_dias: int = JANELA_DEDUP_DIAS,
    incluir_postados_recentemente: bool = False,
) -> list[Produto]:
    """
    Retorna produtos ordenados por nota DESC.

    Filtros:
    - org_id (catálogo da org)
    - público (usuario_dono_id IS NULL) — privados de afiliado ficam fora
    - não bloqueado, preco > 0, foto_url NOT NULL
    - tem pelo menos 1 nicho associado (precisa pra postagem)
    - nota >= nota_minima
    - NÃO postado nos últimos N dias (default 7) — opcional

    Args:
        org_id: org do user
        limite: máx produtos (default 50)
        nota_minima: mínimo de nota (default 30 — descarta produtos sem comissão
            real + sem desconto + sem sinal de venda)
        janela_dedup_dias: ignora produtos postados nessa janela
        incluir_postados_recentemente: se True, ignora dedup (admin manual)
    """
    desde_dedup = datetime.now(tz=timezone.utc) - timedelta(days=janela_dedup_dias)

    ja_postado = exists().where(and_(
        Postagem.produto_id == Produto.id,
        Postagem.org_id == org_id,
        Postagem.postado_em >= desde_dedup,
    ))
    tem_nicho = exists().where(ProdutoNicho.produto_id == Produto.id)

    base = (
        select(Produto)
        .where(
            Produto.org_id == org_id,
            Produto.usuario_dono_id.is_(None),
            Produto.bloqueado.is_(False),
            Produto.preco > 0,
            Produto.foto_url.is_not(None),
            Produto.foto_url != "",
            Produto.nota >= nota_minima,
            tem_nicho,
        )
        .order_by(Produto.nota.desc(), Produto.atualizado_em.desc())
        .limit(limite)
    )
    if not incluir_postados_recentemente:
        base = base.where(~ja_postado)

    rows = (await db.execute(base)).scalars().all()
    return list(rows)


async def listar_top_com_fallback(
    db: AsyncSession,
    *,
    org_id: int,
    limite: int = 50,
    nota_minima: float = NOTA_MINIMA_DEFAULT,
) -> tuple[list[Produto], str]:
    """
    Mesma `listar_top`, mas com cascata de fallback Fase 11.

    Retorna `(produtos, fonte)` onde fonte ∈ {"propria", "admin_org"}.
    """
    produtos = await listar_top(
        db, org_id=org_id, limite=limite, nota_minima=nota_minima,
    )
    if produtos:
        return produtos, "propria"

    if org_id != settings.admin_org_id:
        produtos = await listar_top(
            db, org_id=settings.admin_org_id,
            limite=limite, nota_minima=nota_minima,
        )
        if produtos:
            return produtos, "admin_org"

    return [], "vazio"


# --------------------------------------------------------------------
# Tools admin: recalcular notas + revalidar comissões
# --------------------------------------------------------------------

async def recalcular_notas_da_org(
    db: AsyncSession, *, org_id: int,
) -> dict[str, int]:
    """
    Re-aplica `calcular_nota` em TODOS produtos da org (públicos e privados).

    Útil quando:
    - A fórmula de nota muda (PESO_COMISSAO ajustado, novo bônus)
    - Backfill após deploy da Fase 18 (produtos antigos têm nota=0)

    Não toca em outros campos — só `nota` e `comissao_validada`.
    """
    produtos = list((await db.execute(
        select(Produto).where(Produto.org_id == org_id)
    )).scalars().all())

    atualizados = 0
    for p in produtos:
        info = calcular_nota({
            "plataforma":     p.plataforma,
            "preco":          p.preco,
            "preco_orig":     p.preco_orig,
            "desconto":       p.desconto,
            "comissao":       p.comissao,
            "total_vendidos": p.total_vendidos,
            "is_bestseller":  p.is_bestseller,
            "is_em_alta":     p.is_em_alta,
        })
        if (p.nota != info["nota"]
                or p.comissao_validada != info["comissao_validada"]):
            p.nota              = info["nota"]
            p.comissao_validada = info["comissao_validada"]
            atualizados += 1

    await db.flush()
    log.info("curadoria.recalcular_notas",
             org_id=org_id, total=len(produtos), atualizados=atualizados)
    return {
        "total":       len(produtos),
        "atualizados": atualizados,
    }


async def revalidar_comissoes_da_org(
    db: AsyncSession, *, org_id: int,
) -> dict[str, int]:
    """LEGADO: passa todas comissões pela validação de range + recalcula nota.

    NÃO consulta o agente — só re-executa `calcular_nota` com valores
    já no DB. Para captura REAL via barra ML, use `disparar_revalidacao_comissoes_via_agente`.
    """
    return await recalcular_notas_da_org(db, org_id=org_id)


async def disparar_revalidacao_comissoes_via_agente(
    db: AsyncSession,
    *,
    org_id: int,
    limite: int = 50,
) -> dict:
    """Fase 18.3 (v3.4.2) — dispara tarefa pro agente abrir cada produto
    DO TOP atual via seu LINK DE AFILIADO (meli.la), capturando a comissão
    real da barra preta de afiliados ML no destino do redirect.

    Estratégia:
    1. Pega os produtos do TOP atual da org (ordenados por nota DESC)
    2. Filtra ML com `url_afiliado LIKE '%meli.la/%'` (sem link real não dá)
    3. Filtra os que ainda não têm `comissao_fonte=ml_barra_afiliados`
       (não revalida o que já foi)
    4. Cria 1 tarefa `REVALIDAR_COMISSAO_ML` com payload `items=[{produto_id, url_afiliado}]`
    5. Entrega via dispatcher
    6. Hook em `marcar_concluida` aplica resultado via
       `afiliado_ml_writer.aplicar_mapping_comissoes_por_id`

    Por que `meli.la` em vez da URL canônica:
    O ML registra como clique afiliado real → barra mostra a comissão
    correta do programa. URL canônica direta pode mostrar comissão
    genérica ou nenhuma.

    Por que filtrar pelo TOP:
    Faz sentido revalidar prioridade nos produtos que vão ser POSTADOS —
    se não está no TOP, não vai pra grupo, não precisa ter comissão precisa.

    Custo: ~2s por produto. TOP de 50 = ~1.5min.

    Returns:
        {"ok": bool, "tarefa_id": N, "items_enfileirados": M, "mensagem": "..."}
    """
    from app.models import Agente, StatusTarefa, Tarefa, TipoTarefa
    from app.services import dispatcher
    from app.services.agente_registry import registry

    # 1. Pega produtos do TOP atual (sem filtro de nota — pega todos
    #    pra revalidar o máximo possível)
    produtos_top = await listar_top(
        db, org_id=org_id, limite=limite, nota_minima=0,
        incluir_postados_recentemente=True,
    )
    # Fallback admin_org se org do user não tem TOP (catálogo compartilhado)
    if not produtos_top:
        from app.core.config import settings as _s
        if org_id != _s.admin_org_id:
            produtos_top = await listar_top(
                db, org_id=_s.admin_org_id, limite=limite, nota_minima=0,
                incluir_postados_recentemente=True,
            )

    # 2-3. Filtra ML com meli.la + sem revalidação ainda
    items: list[dict] = []
    for p in produtos_top:
        if p.plataforma != "ml":
            continue
        if not p.url_afiliado or "meli.la/" not in p.url_afiliado:
            continue
        if p.comissao_fonte == "ml_barra_afiliados":
            continue  # já revalidado, pula
        items.append({"produto_id": p.id, "url_afiliado": p.url_afiliado})

    if not items:
        return {
            "ok":                True,
            "tarefa_id":         None,
            "items_enfileirados": 0,
            "mensagem":          "Nenhum produto do TOP pra revalidar (ou todos já tem ✅ ML barra, ou não tem meli.la)",
        }

    # 4. Pega 1º agente online da org
    agentes = list((await db.execute(
        select(Agente).where(
            Agente.org_id == org_id, Agente.ativo.is_(True),
        )
    )).scalars().all())
    agente = next((a for a in agentes if registry.esta_online(a.id)), None)
    if agente is None:
        return {
            "ok":    False,
            "erro":  "Nenhum agente online — abra o AchadinhosAgent no PC primeiro",
        }

    tarefa = Tarefa(
        org_id=org_id,
        tipo=TipoTarefa.REVALIDAR_COMISSAO_ML,
        status=StatusTarefa.PENDENTE,
        agente_id=agente.id,
        payload={"items": items},
    )
    db.add(tarefa)
    await db.commit()
    await db.refresh(tarefa)

    await dispatcher._tentar_entrega(db, tarefa)

    log.info("curadoria.revalidar_via_agente.disparado",
             org_id=org_id, tarefa_id=tarefa.id, items=len(items))
    return {
        "ok":                True,
        "tarefa_id":         tarefa.id,
        "items_enfileirados": len(items),
        "mensagem":          (
            f"Tarefa #{tarefa.id} enfileirada com {len(items)} produtos do TOP. "
            f"Agente leva ~{len(items) * 2}s. Recarregue a página em ~"
            f"{max(1, len(items) // 30)}min."
        ),
    }

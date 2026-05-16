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
    """
    Passa todas comissões da org pela validação de range (`app/core/comissoes.py`).

    Marca `comissao_validada` + re-aplica `nota`. Útil pra detectar comissões
    suspeitas (ex: ML retornou 0% por sessão expirada) — produto sai do TOP
    automaticamente quando perde score_comissao.
    """
    # Reusa recalcular_notas — a validação acontece dentro do calcular_nota
    return await recalcular_notas_da_org(db, org_id=org_id)

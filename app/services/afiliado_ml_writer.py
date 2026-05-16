"""
Aplicação do mapping {url_canonica: meli.la} no banco (Fase 15).

Chamado pelo `dispatcher.marcar_concluida` quando uma tarefa do tipo
`GERAR_LINK` é reportada como concluída pelo agente. Atualiza:

1. `produtos.url_afiliado` — substitui pelo `meli.la/XXX` oficial pro
   produto correspondente.
2. `redirects.url_destino` — se já existe shortlink interno pro produto,
   redireciona pro `meli.la` (que internamente redireciona pra URL com
   tag de afiliado real).

Match em 2 níveis: primeiro tenta exato; se falhar, extrai o MLB ID da
URL e usa LIKE. URL pode ter fragments/query do scraping ML que confundem
match exato — MLB ID é estável.

Sem side-effect quando produto não é encontrado (URL pode ter sido
limpa antes do callback chegar).
"""
from __future__ import annotations

import re

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import Produto, Redirect

log = get_logger(__name__)


_RE_MLB = re.compile(r"MLB-?\d{8,15}")


def _extrair_mlb(url: str) -> str | None:
    """MLB ID normalizado (sem hífen) da URL canônica ML."""
    if not url:
        return None
    m = _RE_MLB.search(url)
    return m.group(0).replace("-", "") if m else None


async def aplicar_mapping(
    db: AsyncSession,
    *,
    org_id: int,
    mapping: dict[str, str],
) -> dict[str, int]:
    """Aplica `{url_canonica: meli.la}` aos produtos da org.

    Returns:
        Estatísticas {produtos_atualizados, redirects_atualizados, ignorados,
        sem_match}.
    """
    if not mapping:
        return {"produtos_atualizados": 0, "redirects_atualizados": 0,
                "ignorados": 0, "sem_match": 0}

    stats = {"produtos_atualizados": 0, "redirects_atualizados": 0,
             "ignorados": 0, "sem_match": 0}

    for url_canonica, meli_la in mapping.items():
        if not meli_la or not url_canonica:
            stats["ignorados"] += 1
            continue
        if "meli.la/" not in meli_la:
            log.warning("afiliado_ml.url_inesperada",
                        url_canonica=url_canonica[:80], recebido=meli_la[:80])
            stats["ignorados"] += 1
            continue

        # Match em 2 estratégias:
        # 1) Exata por url_canonica (cobre o caso normal)
        # 2) Por MLB ID (LIKE) — cobre fragment/query/encoding divergente
        produto_id = (await db.execute(
            select(Produto.id).where(
                Produto.org_id == org_id,
                Produto.plataforma == "ml",
                Produto.url_canonica == url_canonica,
            ).limit(1)
        )).scalar_one_or_none()

        if produto_id is None:
            mlb_id = _extrair_mlb(url_canonica)
            if mlb_id:
                produto_id = (await db.execute(
                    select(Produto.id).where(
                        Produto.org_id == org_id,
                        Produto.plataforma == "ml",
                        Produto.url_canonica.like(f"%{mlb_id}%"),
                    ).limit(1)
                )).scalar_one_or_none()
                if produto_id is not None:
                    log.info("afiliado_ml.match_via_mlb",
                             mlb=mlb_id, produto_id=produto_id)

        if produto_id is None:
            log.warning("afiliado_ml.sem_match",
                        url_canonica=url_canonica[:120], meli_la=meli_la)
            stats["sem_match"] += 1
            continue

        # 1. produtos.url_afiliado
        result_prod = await db.execute(
            update(Produto)
            .where(Produto.id == produto_id)
            .values(url_afiliado=meli_la)
        )
        if (result_prod.rowcount or 0) > 0:
            stats["produtos_atualizados"] += result_prod.rowcount

        # 2. redirects.url_destino — 1 row por produto
        result_red = await db.execute(
            update(Redirect)
            .where(Redirect.produto_id == produto_id)
            .values(url_destino=meli_la)
        )
        if (result_red.rowcount or 0) > 0:
            stats["redirects_atualizados"] += result_red.rowcount

    await db.commit()
    log.info("afiliado_ml.mapping_aplicado", org_id=org_id, **stats)
    return stats

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
from urllib.parse import urlparse, urlunparse

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import Produto, Redirect

log = get_logger(__name__)


# Aceita variantes do ID do ML:
#   MLB1234567890      → produto comum (path /p/MLB...)
#   MLBU3387021403     → catálogo unificado (path /up/MLBU...) — letra opcional
#   MLB-1234567890     → formato antigo com hífen
_RE_MLB = re.compile(r"MLB[A-Z]?-?\d{8,15}")


def _extrair_mlb(url: str) -> str | None:
    """MLB ID normalizado (sem hífen) da URL canônica ML."""
    if not url:
        return None
    m = _RE_MLB.search(url)
    return m.group(0).replace("-", "") if m else None


def _normalizar_url(url: str) -> str:
    """Tira fragment + query — usado pra match flexível."""
    if not url:
        return url
    parts = urlparse(url)
    return urlunparse(parts._replace(query="", fragment=""))


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

        # Match em 3 estratégias (cascata):
        #   1. Exata por url_canonica (caso normal — URL idêntica no DB)
        #   2. Por URL normalizada (sem fragment/query) via LIKE prefix
        #   3. Por MLB ID extraído da URL (LIKE %MLBxxx%) — cobre MLBU,
        #      hífens, encoding divergente. ÚLTIMO recurso porque LIKE
        #      pode pegar produtos com IDs parcialmente coincidentes.
        produto_id = (await db.execute(
            select(Produto.id).where(
                Produto.org_id == org_id,
                Produto.plataforma == "ml",
                Produto.url_canonica == url_canonica,
            ).limit(1)
        )).scalar_one_or_none()

        if produto_id is None:
            url_limpa = _normalizar_url(url_canonica)
            if url_limpa and url_limpa != url_canonica:
                produto_id = (await db.execute(
                    select(Produto.id).where(
                        Produto.org_id == org_id,
                        Produto.plataforma == "ml",
                        Produto.url_canonica.like(f"{url_limpa}%"),
                    ).limit(1)
                )).scalar_one_or_none()
                if produto_id is not None:
                    log.info("afiliado_ml.match_via_url_limpa",
                             url=url_limpa[:120], produto_id=produto_id)

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
                        url_canonica=url_canonica[:200], meli_la=meli_la)
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

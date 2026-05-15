"""
Cliente REST pra enviar produtos extraídos pra cloud.

Endpoint: POST {servidor_api}/api/v1/produtos/ingest
Auth: Bearer <token do agente>

Contrato definido em achadinhos-v3/docs/protocolo_agente.md (seção REST).
Payload no formato IngestLoteRequest (ver achadinhos-v3/app/schemas/produto.py).
"""
from __future__ import annotations

from typing import Any

import httpx
import structlog

from agent.config import Config

log = structlog.get_logger(__name__)


class IngestError(Exception):
    """Erro retornado pelo servidor ao receber lote."""


async def enviar_lote(
    cfg: Config,
    *,
    produtos: list[dict[str, Any]],
    busca_id: int | None = None,
    tarefa_id: int | None = None,
    timeout_seg: float = 60.0,
) -> dict[str, Any]:
    """
    POST do lote. Servidor faz upsert + auto-classificação e retorna stats.

    Retorna o JSON de resposta (ResultadoIngest):
        { recebidos, criados, atualizados, ignorados, com_nicho, detalhes }
    """
    url = f"{cfg.servidor_api}/api/v1/produtos/ingest"
    headers = {
        "Authorization": f"Bearer {cfg.token}",
        "Content-Type":  "application/json",
    }
    payload = {
        "busca_id":  busca_id,
        "tarefa_id": tarefa_id,
        "produtos":  produtos,
    }

    log.info("ingest.enviando",
             url=url, total=len(produtos),
             busca_id=busca_id, tarefa_id=tarefa_id)

    async with httpx.AsyncClient(timeout=timeout_seg) as client:
        try:
            r = await client.post(url, headers=headers, json=payload)
        except httpx.HTTPError as e:
            raise IngestError(f"falha de rede: {type(e).__name__}: {e}") from None

    if r.status_code >= 400:
        detalhe = r.text[:300]
        raise IngestError(f"HTTP {r.status_code}: {detalhe}")

    resultado = r.json()
    log.info("ingest.concluido",
             **{k: v for k, v in resultado.items() if k != "detalhes"})
    return resultado

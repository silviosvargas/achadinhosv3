"""Task Celery hourly — processa solicitações personalizadas pendentes
da fila admin (Fase C — 17/05/2026).

Roda no `worker --beat` (Railway): no minuto 0 de cada hora, pega todas
as solicitações com `status=pendente` e processa cada uma chamando
`solicitacao_service.processar_solicitacao` (cria Tarefa pro agente
do admin central, marca status=processando).

Se nenhum agente admin estiver online, pula essa solicitação e tenta
de novo na próxima hora.
"""
from __future__ import annotations

import asyncio

import structlog

from app.db import get_db_async
from app.workers.celery_app import celery_app

log = structlog.get_logger(__name__)


@celery_app.task(name="processar_solicitacoes_pendentes")
def processar_solicitacoes_pendentes() -> dict:
    """Wrapper síncrono pro Celery — usa `asyncio.run` pra chamar a
    lógica async do service.

    Returns: dict com sumário {pendentes, enfileiradas, sem_agente, falhas}
    """
    return asyncio.run(_executar())


async def _executar() -> dict:
    """Lógica async — itera pendentes e processa cada uma."""
    from app.services import solicitacao_service

    sumario = {
        "pendentes":    0,
        "enfileiradas": 0,
        "sem_agente":   0,
        "falhas":       0,
    }

    async for db in get_db_async():
        try:
            pendentes = await solicitacao_service.listar_pendentes(db, limite=50)
            sumario["pendentes"] = len(pendentes)

            for s in pendentes:
                r = await solicitacao_service.processar_solicitacao(
                    db, solicitacao_id=s.id, admin=None,
                )
                if r["ok"]:
                    sumario["enfileiradas"] += 1
                elif "agente" in (r.get("erro") or "").lower():
                    sumario["sem_agente"] += 1
                else:
                    sumario["falhas"] += 1
        except Exception as e:
            log.exception("solicitacoes_tasks.crash", erro=str(e)[:200])
        break  # async generator dá só 1 sessão

    log.info("solicitacoes_tasks.hourly_concluido", **sumario)
    return sumario

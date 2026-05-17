"""
Buffer + worker async pra persistir logs INFO+ no Postgres + publicar
no Redis pub/sub pra streaming SSE.

Pipeline:
    log.info("evento", k=v) ─ via structlog processor (logging.py)
                            │
                            ▼
                    buffer.append(entry)  ◀── thread-safe (lock asyncio)
                            │
                            ▼  (a cada FLUSH_INTERVAL_S)
              worker_loop() ──► INSERT batch no Postgres
                            │
                            └──► r.publish("logs:org:{N}", json) pra cada entry

Por que buffer + worker em vez de INSERT direto no processor:
- structlog processor é SÍNCRONO (não pode `await`)
- INSERT por log mata performance (rede + commit por chamada)
- Batch a cada 2s tolera burst de 500 logs/segundo sem suar

Por que NÃO usar Celery: queue Redis externa pra logs do próprio servidor
é overkill. Buffer in-memory + flush local é o suficiente pro escopo.

Limite: se restart do FastAPI antes do flush, perde até 2s de logs.
Tradeoff aceito pra simplicidade.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import insert

from app.core.config import settings
from app.core.redis import get_redis
from app.db.session import AsyncSessionLocal
from app.models.log_entry import LogEntry

# stdlib logger pra log do próprio buffer (sem criar loop infinito com structlog)
_stdlog = logging.getLogger(__name__)

# Buffer in-memory. Cap em 5000 evita explodir RAM se DB cair por horas.
# Quando estoura, descarta as MAIS ANTIGAS (deque com maxlen faz auto-drop).
_BUFFER_MAX = 5000
_FLUSH_INTERVAL_S = 2.0
_FLUSH_BATCH_MAX = 500       # quantos logs por INSERT (proteção contra bind explosivo)

_buffer: deque[dict[str, Any]] = deque(maxlen=_BUFFER_MAX)
_lock = asyncio.Lock()
_worker_task: asyncio.Task | None = None
_publicar_redis = True       # off em testes via flag


def adicionar_log(entry: dict[str, Any]) -> None:
    """Adiciona log no buffer. SÍNCRONO — pode ser chamado de qualquer lugar.

    `entry` deve ter as keys: ts (datetime), nivel (str), evento (str | None),
    mensagem (str | None), contexto (dict), tarefa_id (int | None),
    org_id (int | None), agente_id (int | None), source (str).
    """
    # deque.append + maxlen é atômico no CPython (GIL). Sem precisar de lock
    # aqui — o lock async é só pro drain do worker.
    _buffer.append(entry)


async def _drain_buffer() -> list[dict[str, Any]]:
    """Pega todo o buffer atual + esvazia. Bloqueia leitores enquanto copia."""
    async with _lock:
        if not _buffer:
            return []
        # Cap em batch máx — se acumulou 10k durante outage, processa em pedaços
        n = min(len(_buffer), _FLUSH_BATCH_MAX)
        batch = [_buffer.popleft() for _ in range(n)]
        return batch


async def _flush_batch(batch: list[dict[str, Any]]) -> None:
    """INSERT batch + publish no Redis. Erros não derrubam o worker."""
    if not batch:
        return

    # 1. INSERT no Postgres (1 query)
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(insert(LogEntry), batch)
            await session.commit()
    except Exception as e:
        _stdlog.exception("log_buffer.insert_falhou erro=%s tamanho_batch=%d",
                          e, len(batch))
        # Não re-adiciona ao buffer — risco de loop infinito se DB está down

    # 2. Publish no Redis (best-effort, não bloqueia se Redis cair)
    if not _publicar_redis:
        return
    try:
        r = get_redis()
        for entry in batch:
            org_id = entry.get("org_id")
            # Canal "all" sempre + canal por org pra clients filtrarem
            payload = json.dumps(_serializar_pra_sse(entry), default=str)
            await r.publish("logs:all", payload)
            if org_id is not None:
                await r.publish(f"logs:org:{org_id}", payload)
    except Exception as e:
        _stdlog.warning("log_buffer.publish_falhou erro=%s", e)


def _serializar_pra_sse(entry: dict[str, Any]) -> dict[str, Any]:
    """Converte entry pro formato que o frontend espera no SSE."""
    return {
        "ts":         entry["ts"].isoformat() if isinstance(entry.get("ts"), datetime) else entry.get("ts"),
        "nivel":      entry.get("nivel"),
        "evento":     entry.get("evento"),
        "mensagem":   entry.get("mensagem"),
        "contexto":   entry.get("contexto") or {},
        "tarefa_id":  entry.get("tarefa_id"),
        "org_id":     entry.get("org_id"),
        "agente_id":  entry.get("agente_id"),
        "source":     entry.get("source") or "server",
    }


async def _worker_loop() -> None:
    """Loop infinito: drena buffer e flushea a cada FLUSH_INTERVAL_S."""
    _stdlog.info("log_buffer.worker_iniciado intervalo=%.1fs", _FLUSH_INTERVAL_S)
    while True:
        try:
            await asyncio.sleep(_FLUSH_INTERVAL_S)
            batch = await _drain_buffer()
            if batch:
                await _flush_batch(batch)
        except asyncio.CancelledError:
            # Shutdown ordeiro: faz um último drain antes de sair
            _stdlog.info("log_buffer.worker_cancelado fazendo_drain_final")
            try:
                final = await _drain_buffer()
                if final:
                    await _flush_batch(final)
            except Exception as e:
                _stdlog.warning("log_buffer.drain_final_falhou erro=%s", e)
            raise
        except Exception as e:
            # Nunca mata o loop — só loga e continua
            _stdlog.exception("log_buffer.worker_iter_erro erro=%s", e)


async def iniciar_worker() -> asyncio.Task:
    """Cria a task background do worker. Chamado no lifespan FastAPI."""
    global _worker_task
    if _worker_task is not None and not _worker_task.done():
        _stdlog.warning("log_buffer.worker_ja_rodando")
        return _worker_task
    _worker_task = asyncio.create_task(_worker_loop(), name="log_buffer_worker")
    return _worker_task


async def parar_worker() -> None:
    """Cancela o worker — chamado no shutdown."""
    global _worker_task
    if _worker_task is None:
        return
    _worker_task.cancel()
    try:
        await _worker_task
    except asyncio.CancelledError:
        pass
    _worker_task = None


# ── Cleanup TTL ──────────────────────────────────────────────

async def cleanup_logs_antigos(dias_retencao: int = 30) -> int:
    """Apaga logs mais velhos que `dias_retencao`. Retorna nº de linhas apagadas.

    Idempotente — chame quantas vezes quiser. Pra rodar diariamente,
    chamar do lifespan + asyncio.sleep ou de um Celery beat task.
    """
    from datetime import timedelta
    from sqlalchemy import delete

    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=dias_retencao)
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            delete(LogEntry).where(LogEntry.ts < cutoff)
        )
        await session.commit()
        return result.rowcount or 0


# Permite ligar/desligar publish em testes
def desligar_publish_redis() -> None:
    global _publicar_redis
    _publicar_redis = False


def ligar_publish_redis() -> None:
    global _publicar_redis
    _publicar_redis = True

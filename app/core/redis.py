"""
Cliente Redis async singleton.

Setup compartilhado pra qualquer parte da app que precise de Redis
(pub/sub, cache, locks). Atualmente usado pelo sistema de logs em tempo
real (`app/core/log_buffer.py` + endpoint SSE).

USO:
    from app.core.redis import get_redis
    r = get_redis()
    await r.publish("canal", "mensagem")

A conexão é lazy — só abre quando alguém pede pela primeira vez.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.config import settings

if TYPE_CHECKING:
    from redis.asyncio import Redis


_cliente: "Redis | None" = None


def get_redis() -> "Redis":
    """Retorna o singleton Redis async. Lazy: cria na 1ª chamada."""
    global _cliente
    if _cliente is None:
        # Import local pra não pagar custo de import quando redis não é usado
        import redis.asyncio as redis_async
        _cliente = redis_async.from_url(
            settings.redis_url,
            decode_responses=True,  # bytes → str automático (logs são texto)
        )
    return _cliente


async def fechar_redis() -> None:
    """Fecha a conexão (chamado no shutdown do FastAPI)."""
    global _cliente
    if _cliente is not None:
        await _cliente.aclose()
        _cliente = None

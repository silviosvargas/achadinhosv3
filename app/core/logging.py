"""
Logging estruturado com structlog.

Em vez de `logger.info("Postou produto X em grupo Y")` com texto livre,
usamos `logger.info("postagem.concluida", produto_id=123, grupo="Ofertas")`.

Vantagem: logs viram JSON em produção, fáceis de buscar e agregar
(Datadog, Loki, ELK, ou simplesmente `grep '"produto_id":123'`).

Bonus: custom processor `_persistir_no_db_processor` copia INFO+ pro
buffer in-memory (`log_buffer.py`) que persiste no Postgres em batch +
publica no Redis pub/sub pra streaming SSE em /admin/logs.
"""
import logging
import sys
from datetime import datetime, timezone

import structlog

from app.core.config import settings


# Níveis stdlib pra comparação no processor (DEBUG=10, INFO=20, ...)
_NIVEL_MINIMO_PERSIST = logging.INFO


def _persistir_no_db_processor(logger, method_name, event_dict):
    """Processor structlog que copia log INFO+ pro buffer assíncrono de persistência.

    Não bloqueia: só faz `deque.append`, que é O(1) atômico. O worker async
    em `app/core/log_buffer.py` faz batch INSERT a cada 2s.

    Roda DEPOIS do `add_log_level` e ANTES do renderer (que pode mutar o
    event_dict). Retorna o event_dict intocado pra preservar pipeline.
    """
    try:
        nivel_str = (event_dict.get("level") or method_name or "info").upper()
        nivel_int = getattr(logging, nivel_str, logging.INFO)
        if nivel_int < _NIVEL_MINIMO_PERSIST:
            return event_dict

        # Import local pra evitar circular import (logging.py é carregado
        # cedo, antes do modelo/SQLAlchemy).
        from app.core.log_buffer import adicionar_log

        # Contexto = todo o event_dict menos keys reservadas/redundantes
        evento = event_dict.get("event")
        ctx = {
            k: v for k, v in event_dict.items()
            if k not in {"event", "level", "timestamp", "logger", "_record"}
        }

        # Vínculos opcionais — pega do contexto e remove pra não duplicar
        tarefa_id = ctx.pop("tarefa_id", None) or ctx.pop("task_id", None)
        org_id    = ctx.pop("org_id", None)
        agente_id = ctx.pop("agente_id", None)

        # Mensagem renderizada simples (sem ANSI). Para display rápido sem
        # parsing do contexto.
        mensagem = str(evento) if evento else ""

        adicionar_log({
            "ts":         datetime.now(tz=timezone.utc),
            "nivel":      nivel_str,
            "evento":     str(evento)[:120] if evento else None,
            "mensagem":   mensagem[:500] if mensagem else None,
            "contexto":   _serializar_contexto(ctx),
            "tarefa_id":  _safe_int(tarefa_id),
            "org_id":     _safe_int(org_id),
            "agente_id":  _safe_int(agente_id),
            "source":     "server",
        })
    except Exception:
        # NUNCA derrube um log normal por falha no persistor. Se DB/Redis
        # estiverem fora do ar, logs vão pro stdout normalmente e somem.
        pass
    return event_dict


def _safe_int(v) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _serializar_contexto(ctx: dict) -> dict:
    """Garante que o contexto é JSON-serializável. Objetos complexos viram str."""
    out = {}
    for k, v in ctx.items():
        if isinstance(v, (str, int, float, bool, type(None))):
            out[k] = v
        elif isinstance(v, (list, tuple)):
            out[k] = [str(x)[:200] for x in v[:20]]
        elif isinstance(v, dict):
            out[k] = {str(kk): str(vv)[:200] for kk, vv in list(v.items())[:20]}
        else:
            out[k] = str(v)[:300]
    return out


def configurar_logging() -> None:
    """Configura structlog + logging stdlib. Chame uma vez no startup."""
    nivel = getattr(logging, settings.app_log_level.upper(), logging.INFO)

    # Em dev: saída colorida e legível
    # Em prod: JSON puro pra agregadores
    if settings.is_production:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            _persistir_no_db_processor,   # ← NOVO: copia INFO+ pro buffer
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(nivel),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Manda libs (uvicorn, sqlalchemy) também via structlog pra padronizar
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=nivel,
    )


def get_logger(nome: str | None = None):
    """Atalho — `logger = get_logger(__name__)`."""
    return structlog.get_logger(nome)

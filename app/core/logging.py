"""
Logging estruturado com structlog.

Em vez de `logger.info("Postou produto X em grupo Y")` com texto livre,
usamos `logger.info("postagem.concluida", produto_id=123, grupo="Ofertas")`.

Vantagem: logs viram JSON em produção, fáceis de buscar e agregar
(Datadog, Loki, ELK, ou simplesmente `grep '"produto_id":123'`).
"""
import logging
import sys

import structlog

from app.core.config import settings


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

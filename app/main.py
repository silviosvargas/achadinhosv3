"""
Achadinhos V3 — Entrypoint FastAPI.

Como rodar (dev):
    docker compose up -d
    # acesse http://localhost:8000/docs

Em produção (sem Docker, raro):
    uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import router as v1_router
from app.core.config import settings
from app.core.logging import configurar_logging, get_logger
from app.web.routes import router as web_router

configurar_logging()
log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown da aplicação."""
    log.info("app.startup", env=settings.app_env, debug=settings.app_debug)

    # Worker que persiste logs INFO+ em batch no Postgres + publica no Redis
    # pra streaming SSE em /admin/logs. Lazy import pra não puxar SQLAlchemy
    # antes do configurar_logging (que roda no import-time deste módulo).
    from app.core.log_buffer import iniciar_worker, parar_worker
    await iniciar_worker()

    try:
        yield
    finally:
        log.info("app.shutdown")
        # Drena buffer + para worker ordeiramente
        try:
            await parar_worker()
        except Exception as e:
            log.exception("app.shutdown.parar_worker_falhou", erro=str(e))
        # Fecha conexão Redis se foi aberta
        try:
            from app.core.redis import fechar_redis
            await fechar_redis()
        except Exception:
            pass


def criar_app() -> FastAPI:
    app = FastAPI(
        title=f"{settings.app_name} API",
        version="3.0.0",
        description="Plataforma SaaS de automação de marketing de afiliados.",
        lifespan=lifespan,
        # Em prod, esconda /docs (ou proteja por auth)
        docs_url="/docs" if settings.app_debug else None,
        redoc_url="/redoc" if settings.app_debug else None,
    )

    # CORS — em dev libera tudo. Em prod, aperta a allowlist.
    if not settings.is_production:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # API JSON sob /api/v1/*
    app.include_router(v1_router)

    # Páginas HTML (login, dashboard, etc) na raiz
    app.include_router(web_router)

    # Arquivos estáticos (CSS, JS, ícones)
    static_dir = Path(__file__).parent / "web" / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    return app


app = criar_app()

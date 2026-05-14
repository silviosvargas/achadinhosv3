"""
Endpoint de saúde — usado por:
- Healthcheck do Docker
- Monitoramento (Uptimerobot, Statuspage)
- Smoke test do CI/CD
"""
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db import get_db_async

router = APIRouter(tags=["sistema"])


@router.get("/health")
async def health() -> dict:
    """Liveness — 200 se a app está respondendo."""
    return {"status": "ok", "app": settings.app_name, "env": settings.app_env}


@router.get("/health/ready")
async def readiness(db: AsyncSession = Depends(get_db_async)) -> dict:
    """
    Readiness — 200 só se conseguir falar com Postgres.
    Usado pra saber se a app pode receber tráfego.
    """
    try:
        result = await db.execute(text("SELECT 1"))
        result.scalar_one()
        db_ok = True
    except Exception:
        db_ok = False

    status_geral = "ok" if db_ok else "degradado"
    return {
        "status":   status_geral,
        "checks":   {"postgres": db_ok},
    }

"""
Sessões SQLAlchemy.

- Async: pra endpoints FastAPI (alta concorrência, I/O bound)
- Síncrona: pra Alembic, Celery workers e scripts CLI (mais simples,
  mexe com banco geralmente em background)
"""
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, contextmanager

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings


# ── Engine / Session ASYNC (usado nos endpoints HTTP) ─────────────
async_engine = create_async_engine(
    settings.postgres_url_async,
    echo=False,                  # True pra ver SQL no log (debug)
    pool_pre_ping=True,           # detecta conexões mortas
    pool_size=10,
    max_overflow=20,
)

AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,       # objetos continuam usáveis depois do commit
    autoflush=False,
)


async def get_db_async() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency injection do FastAPI.

    Uso:
        @router.get("/algo")
        async def listar(db: AsyncSession = Depends(get_db_async)):
            ...
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


@asynccontextmanager
async def sessao_async() -> AsyncGenerator[AsyncSession, None]:
    """Context manager pra usar fora de endpoints FastAPI."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# ── Engine / Session SÍNCRONA (Alembic, Celery, scripts) ──────────
sync_engine = create_engine(
    settings.postgres_url,
    echo=False,
    pool_pre_ping=True,
)

SyncSessionLocal = sessionmaker(
    bind=sync_engine,
    expire_on_commit=False,
    autoflush=False,
)


@contextmanager
def sessao_sync():
    """Context manager pra scripts e workers."""
    session: Session = SyncSessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

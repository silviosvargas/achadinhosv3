"""
Configuração comum dos testes.

Estratégia: testes contra Postgres real (igual ao de prod), em DB próprio
'achadinhos_test'. Docker compose cria automaticamente se não existir.
"""
import asyncio
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.base import Base
from app.main import app


# ── Loop event compartilhado ──────────────────────────
@pytest.fixture(scope="session")
def event_loop():
    """Reusa o mesmo loop pra toda a sessão de testes."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ── Engine de teste (DB separado) ─────────────────────
TEST_DB_URL = settings.postgres_url_async.replace(
    f"/{settings.postgres_db}", f"/{settings.postgres_db}_test"
)


@pytest_asyncio.fixture(scope="session")
async def engine_teste():
    """Cria engine no DB de teste e aplica schema. Limpa no fim."""
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db(engine_teste) -> AsyncGenerator[AsyncSession, None]:
    """Sessão de teste — rollback automático no fim de cada teste."""
    SessionT = async_sessionmaker(engine_teste, expire_on_commit=False)
    async with SessionT() as session:
        yield session
        await session.rollback()


# ── Cliente HTTP pra testar a API ─────────────────────
@pytest_asyncio.fixture
async def cliente() -> AsyncGenerator[AsyncClient, None]:
    """Cliente HTTPX com transport ASGI — chamadas in-process à API."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

"""Smoke test: API responde."""
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_responde(cliente: AsyncClient):
    """GET /api/v1/health deve retornar 200 sem auth."""
    r = await cliente.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "app" in body


@pytest.mark.asyncio
async def test_raiz_responde(cliente: AsyncClient):
    """GET / deve retornar info básica."""
    r = await cliente.get("/")
    assert r.status_code == 200
    body = r.json()
    assert body["versao"] == "3.0.0"

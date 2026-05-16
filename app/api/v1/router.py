"""
Router v1 — agrega todos endpoints sob /api/v1/*.

Quando criar um endpoint novo, só importa o router dele aqui.
"""
from fastapi import APIRouter

from app.api.v1.endpoints import (
    agentes,
    auth,
    buscas,
    canais,
    curadoria,
    grupos,
    health,
    lote,
    mappings_nichos,
    produtos,
    tarefas,
    templates,
    usuarios,
    ws_agente,
)

router = APIRouter(prefix="/api/v1")

# /api/v1/health, /api/v1/health/ready
router.include_router(health.router)

# /api/v1/auth/login, /api/v1/auth/refresh, /api/v1/auth/me
router.include_router(auth.router)

# /api/v1/usuarios/...
router.include_router(usuarios.router)

# /api/v1/agentes/...
router.include_router(agentes.router)

# /api/v1/canais/...
router.include_router(canais.router)

# /api/v1/grupos/...
router.include_router(grupos.router)

# /api/v1/produtos/... (+ /produtos/import-csv)
router.include_router(produtos.router)

# /api/v1/templates/...
router.include_router(templates.router)

# /api/v1/buscas/...
router.include_router(buscas.router)

# /api/v1/mappings-nichos/...
router.include_router(mappings_nichos.router)

# /api/v1/lote/rodar
router.include_router(lote.router)

# /api/v1/curadoria/top + recalcular-notas + revalidar-comissoes (Fase 18)
router.include_router(curadoria.router)

# /api/v1/tarefas/...
router.include_router(tarefas.router)

# /api/v1/ws/agente (WebSocket)
router.include_router(ws_agente.router)

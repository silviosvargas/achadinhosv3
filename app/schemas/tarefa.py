"""Schemas Pydantic — Tarefas (fila de comandos)."""
from datetime import datetime

from pydantic import BaseModel, Field


# ── Entrada ──────────────────────────────────────────

class EnfileirarPostagemRequest(BaseModel):
    """Enfileira uma postagem manual num grupo específico.

    Se for canal whatsapp_agente, vai pro agente do canal (PC local).
    Se for canal telegram_bot, vai pro worker Celery (cloud).
    """
    grupo_id:    int
    texto:       str = Field(min_length=1, max_length=4096)
    imagem_url:  str | None = Field(default=None, max_length=2000)
    produto_id:  int | None = None


# ── Saída ────────────────────────────────────────────

class TarefaPublica(BaseModel):
    id:                int
    org_id:            int
    tipo:              str
    status:            str
    agente_id:         int | None
    payload:           dict
    resultado:         dict | None
    erro:              str | None
    iniciado_em:       datetime | None
    concluido_em:      datetime | None
    tentativas:        int
    max_tentativas:    int
    criado_por_usuario_id: int | None
    criado_em:         datetime

    model_config = {"from_attributes": True}


class FiltroTarefas(BaseModel):
    """Filtros pra listagem (querystring)."""
    status:    str | None = None
    tipo:      str | None = None
    agente_id: int | None = None
    pagina:    int = Field(default=1, ge=1)
    por_pagina: int = Field(default=50, ge=1, le=200)

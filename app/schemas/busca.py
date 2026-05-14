"""Schemas Pydantic — BuscaML e mapping categoria→nicho."""
from datetime import datetime

from pydantic import BaseModel, Field


# ============================================================
# BuscaML — CRUD
# ============================================================

class CriarBuscaRequest(BaseModel):
    nome:    str = Field(min_length=1, max_length=150)
    entrada: str = Field(min_length=1, max_length=2000,
                         description="Termo livre ou URL completa do ML")
    agente_id:    int | None = Field(default=None,
                                     description="None = qualquer agente online da org")
    max_paginas:  int = Field(default=3, ge=1, le=20)
    max_produtos: int = Field(default=50, ge=1, le=500)
    intervalo_minutos: int | None = Field(
        default=None, ge=15,
        description="None = só manual; valor = intervalo entre execuções (min 15)",
    )
    ativo: bool = True


class AtualizarBuscaRequest(BaseModel):
    nome:    str | None = Field(default=None, min_length=1, max_length=150)
    entrada: str | None = Field(default=None, min_length=1, max_length=2000)
    agente_id:    int | None = None
    max_paginas:  int | None = Field(default=None, ge=1, le=20)
    max_produtos: int | None = Field(default=None, ge=1, le=500)
    intervalo_minutos: int | None = Field(default=None, ge=15)
    ativo: bool | None = None


class BuscaPublica(BaseModel):
    id:     int
    org_id: int
    criado_por_usuario_id: int | None
    agente_id:    int | None
    nome:    str
    entrada: str
    max_paginas:  int
    max_produtos: int
    intervalo_minutos: int | None
    ativo: bool
    ultima_exec_em:   datetime | None
    proxima_exec_em:  datetime | None
    ultima_tarefa_id: int | None
    execucoes: int
    criado_em:     datetime
    atualizado_em: datetime

    model_config = {"from_attributes": True}


# ============================================================
# Mapping categoria_ml → nicho
# ============================================================

class CriarMappingCategoriaRequest(BaseModel):
    categoria_ml: str = Field(min_length=1, max_length=200)
    nicho_id:     int


class MappingCategoriaPublico(BaseModel):
    id:           int
    org_id:       int
    categoria_ml: str
    nicho_id:     int
    criado_em:    datetime

    model_config = {"from_attributes": True}

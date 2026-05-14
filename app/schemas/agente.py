"""Schemas Pydantic — Agente (PC local do afiliado)."""
from datetime import datetime

from pydantic import BaseModel, Field


# ── Entrada ──────────────────────────────────────────

class CriarAgenteRequest(BaseModel):
    """Admin cria um novo agente pra um usuário."""
    usuario_id: int
    nome:       str = Field(min_length=1, max_length=100,
                            description="Identificação amigável: 'PC do João'")


class AtualizarAgenteRequest(BaseModel):
    """Atualizações permitidas (parciais)."""
    nome:  str | None = Field(default=None, min_length=1, max_length=100)
    ativo: bool | None = None


# ── Saída ────────────────────────────────────────────

class AgentePublico(BaseModel):
    """Visão pública do agente (sem token)."""
    id:           int
    org_id:       int
    usuario_id:   int
    nome:         str
    versao_app:   str | None
    sistema_op:   str | None
    ativo:        bool
    online:       bool
    ultimo_ping:  datetime | None
    ultimo_ip:    str | None
    metricas_atuais: dict
    criado_em:    datetime

    model_config = {"from_attributes": True}


class AgenteCriadoResponse(BaseModel):
    """Resposta de criação — INCLUI o token (só aparece UMA vez).

    Admin precisa copiar e salvar — não é recuperável depois.
    Esse token vai pro arquivo de config do agente local.
    """
    agente: AgentePublico
    token:  str = Field(description="JWT do agente. SALVE — não aparece de novo.")


class AutoRegistroRequest(BaseModel):
    """
    Auto-registro (Fase 6): usuário logado (via JWT de acesso) registra
    um agente PARA ELE MESMO. Usado pelo instalador do agente — em vez de
    admin copiar token, o app do agente loga com email/senha e chama isso.
    """
    nome: str = Field(
        default="PC", min_length=1, max_length=100,
        description="Nome amigável (ex: 'PC casa', 'Notebook trabalho'). "
                    "Hostname é uma boa default.",
    )
    sistema_op: str | None = Field(
        default=None, max_length=50,
        description="SO detectado pelo app (ex: 'Windows 11 22H2'). Opcional.",
    )


class AutoRegistroResponse(BaseModel):
    """Resposta do auto-registro — token + URL do WS pronta pra usar."""
    agente:    AgentePublico
    token:     str = Field(description="JWT do agente. App grava em arquivo local.")
    ws_url:    str = Field(description="URL completa do WS pra conectar (com /api/v1/ws/agente).")
    api_url:   str = Field(description="URL base da API REST (sem /api/v1).")

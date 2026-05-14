"""Schemas Pydantic — Canal de postagem e Grupo de destino."""
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class TipoCanal(StrEnum):
    """Tipos de canal suportados."""
    WHATSAPP_AGENTE = "whatsapp_agente"   # via agente local
    TELEGRAM_BOT    = "telegram_bot"      # via Bot API na nuvem


# ── Canal ────────────────────────────────────────────

class CriarCanalRequest(BaseModel):
    tipo:       TipoCanal
    nome:       str = Field(min_length=1, max_length=100)
    usuario_id: int | None = Field(default=None,
                                   description="Dono do canal. None = canal da org.")
    config:     dict = Field(
        default_factory=dict,
        description=(
            "WhatsApp: {agente_id}. "
            "Telegram: {bot_token, bot_username}."
        ),
    )


class AtualizarCanalRequest(BaseModel):
    nome:   str | None = Field(default=None, min_length=1, max_length=100)
    ativo:  bool | None = None
    config: dict | None = None


class CanalPublico(BaseModel):
    id:                 int
    org_id:             int
    usuario_id:         int | None
    tipo:               str
    nome:               str
    ativo:              bool
    config:             dict
    ultima_postagem_em: datetime | None
    ultima_falha_em:    datetime | None
    ultima_falha_msg:   str | None
    criado_em:          datetime

    model_config = {"from_attributes": True}


# ── Grupo ────────────────────────────────────────────

class CriarGrupoRequest(BaseModel):
    canal_id:        int
    nome:            str = Field(min_length=1, max_length=200,
                                 description="Nome amigável (ex: 'Achadinhos - Ofertas 01')")
    identificador:   str = Field(min_length=1, max_length=200,
                                 description="WhatsApp: nome exato no app. Telegram: chat_id.")
    proprietario_id: int | None = None
    nichos_ids:      list[int] = Field(
        default_factory=list,
        description="IDs dos nichos que esse grupo aceita receber.",
    )


class AtualizarGrupoRequest(BaseModel):
    nome:           str | None = Field(default=None, min_length=1, max_length=200)
    identificador:  str | None = Field(default=None, min_length=1, max_length=200)
    ativo:          bool | None = None
    nichos_ids:     list[int] | None = None


class GrupoPublico(BaseModel):
    id:               int
    org_id:           int
    canal_id:         int
    proprietario_id:  int | None
    nome:             str
    identificador:    str
    ativo:            bool
    precisa_atencao_admin: bool
    nichos_ids:       list[int]
    criado_em:        datetime

    model_config = {"from_attributes": True}

"""Schemas Pydantic — usuários."""
from datetime import datetime

from pydantic import BaseModel, Field

# Regex de email simples — não 100% RFC, mas pega 99% dos casos.
# Evita dependência extra (`email-validator`) só pra um campo opcional.
EMAIL_REGEX = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"


class CriarUsuarioRequest(BaseModel):
    """Admin cria um afiliado/usuário na sua org."""
    login:         str = Field(min_length=3, max_length=80, pattern=r"^[a-zA-Z0-9_.-]+$")
    senha:         str = Field(min_length=6, max_length=255)
    papel:         str = Field(default="afiliado", pattern=r"^(afiliado|usuario|admin)$")
    nome_exibicao: str | None = Field(default=None, max_length=150)
    email:         str | None = Field(default=None, max_length=200, pattern=EMAIL_REGEX)


class AtualizarUsuarioRequest(BaseModel):
    nome_exibicao: str | None = Field(default=None, max_length=150)
    email:         str | None = Field(default=None, max_length=200, pattern=EMAIL_REGEX)
    papel:         str | None = Field(default=None, pattern=r"^(afiliado|usuario|admin)$")
    ativo:         bool | None = None


class TrocarSenhaRequest(BaseModel):
    senha_atual: str = Field(min_length=1)
    senha_nova:  str = Field(min_length=6, max_length=255)


class UsuarioPublico(BaseModel):
    """Visão pública (sem senha_hash)."""
    id:                  int
    org_id:              int
    login:               str
    nome_exibicao:       str | None
    email:               str | None
    papel:               str
    ativo:               bool
    onboarding_completo: bool
    criado_em:           datetime
    ultimo_login:        datetime | None

    model_config = {"from_attributes": True}

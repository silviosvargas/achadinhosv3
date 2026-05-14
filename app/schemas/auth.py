"""Schemas Pydantic pra autenticação."""
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    """Body do POST /api/v1/auth/login."""
    login:    str = Field(min_length=1, max_length=80)
    senha:    str = Field(min_length=1, max_length=255)
    org_slug: str | None = Field(default=None, max_length=50,
                                 description="Slug da org. Opcional se login é único globalmente.")


class TokenResponse(BaseModel):
    """Resposta de login bem-sucedido."""
    access_token:  str
    refresh_token: str
    token_type:    str = "bearer"
    expires_in:    int
    usuario:       "UsuarioPublico"


class RefreshRequest(BaseModel):
    refresh_token: str


class SignupRequest(BaseModel):
    """
    Cadastro público de novo cliente (Fase 5).
    Cria Organização (plano free) + usuário admin + faz autologin.
    """
    org_nome: str = Field(min_length=2, max_length=150,
                          description="Nome da empresa/projeto. Slug gerado automaticamente.")
    login:    str = Field(min_length=3, max_length=80,
                          pattern=r"^[a-zA-Z0-9_.-]+$",
                          description="Login do admin (sem espaços; letras/números/_.-)")
    senha:    str = Field(min_length=6, max_length=255)
    email:    str | None = Field(default=None, max_length=200,
                                 description="Email do admin (opcional)")
    nome_exibicao: str | None = Field(default=None, max_length=150)


class SignupResponse(BaseModel):
    """Resposta do signup — JWT + dados pra UI."""
    access_token:  str
    refresh_token: str
    token_type:    str = "bearer"
    expires_in:    int
    org_slug:      str
    usuario:       "UsuarioPublico"


class UsuarioPublico(BaseModel):
    """Dados do usuário expostos pra UI. NUNCA contém senha_hash."""
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


# Permite forward reference
TokenResponse.model_rebuild()
SignupResponse.model_rebuild()

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
    """Visão pública (sem senha_hash nem credenciais cifradas)."""
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
    # Credenciais (Fase 4b.1) — só o usuário (plain) e flag de senha
    usuario_ml:          str | None = None
    tem_senha_ml:        bool = False

    model_config = {"from_attributes": True}


# ============================================================
# Credenciais de plataformas (Fase 4b.1)
# ============================================================

class CredenciaisMLRequest(BaseModel):
    """Admin/afiliado define usuário+senha do ML.
    Passar `senha_ml=None` mantém a anterior; `senha_ml=""` apaga.
    """
    usuario_ml: str | None = Field(default=None, max_length=150)
    senha_ml:   str | None = Field(default=None, max_length=255,
        description="None = não mexe; '' = apaga; string = nova senha (será cifrada)")


class CredenciaisAgenteResponse(BaseModel):
    """Resposta do GET /agentes/me/credenciais — payload PLAIN pro agente.

    Servidor decifra na hora e envia via TLS (em prod). Em dev pelo localhost
    sem TLS — aceitável por ser dev.
    """
    ml: dict[str, str | None] = Field(
        default_factory=lambda: {"usuario": None, "senha": None}
    )

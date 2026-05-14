"""
Segurança: hash de senha (bcrypt) e tokens JWT.

JWT no lugar de sessão Flask por 3 motivos:
1. Stateless — não precisa armazenar sessão no servidor
2. O agente local (Windows) usa o mesmo token pra autenticar via WebSocket
3. Quando o app mobile vier, mesmo mecanismo
"""
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt

from app.core.config import settings

# ── Hashing de senha ────────────────────────────────
# Uso direto da lib bcrypt. passlib foi descontinuado e quebra com bcrypt 4+.
# bcrypt limita senha a 72 bytes — truncamos defensivamente. UTF-8 pode usar
# múltiplos bytes por char (acento conta 2), por isso truncamos em bytes.
_BCRYPT_MAX_BYTES = 72


def hash_senha(senha_em_texto: str) -> str:
    """Gera hash bcrypt da senha (custo padrão = 12 rounds)."""
    senha_bytes = senha_em_texto.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(senha_bytes, salt).decode("utf-8")


def verificar_senha(senha_em_texto: str, hash_armazenado: str) -> bool:
    """Compara senha em texto com hash armazenado. Constant-time."""
    try:
        senha_bytes = senha_em_texto.encode("utf-8")[:_BCRYPT_MAX_BYTES]
        return bcrypt.checkpw(senha_bytes, hash_armazenado.encode("utf-8"))
    except (ValueError, TypeError):
        # Hash corrompido ou formato desconhecido — falha sem detalhes
        return False


# ── JWT ─────────────────────────────────────────────
# Tipos de token
TOKEN_ACESSO  = "access"
TOKEN_REFRESH = "refresh"
TOKEN_AGENTE  = "agente"   # tokens de longa duração pro agente local

# Subjects do JWT
# - sub:  identificador do usuário (string, "uid:<id>")
# - org:  organização ativa
# - papel: 'admin' | 'afiliado' | 'usuario' | 'agente'
# - tipo: tipo de token (acima)


def criar_access_token(*, usuario_id: int, org_id: int, papel: str) -> str:
    """Token de acesso curto (60 min default). Usado no header Authorization."""
    expira = datetime.now(tz=timezone.utc) + timedelta(
        minutes=settings.jwt_access_token_expire_minutes
    )
    return _criar_token({
        "sub":   f"uid:{usuario_id}",
        "uid":   usuario_id,
        "org":   org_id,
        "papel": papel,
        "tipo":  TOKEN_ACESSO,
        "exp":   expira,
    })


def criar_refresh_token(*, usuario_id: int, org_id: int) -> str:
    """Token de refresh (30 dias). Trocado por novo access token sem login."""
    expira = datetime.now(tz=timezone.utc) + timedelta(
        days=settings.jwt_refresh_token_expire_days
    )
    return _criar_token({
        "sub":  f"uid:{usuario_id}",
        "uid":  usuario_id,
        "org":  org_id,
        "tipo": TOKEN_REFRESH,
        "exp":  expira,
    })


def criar_token_agente(*, usuario_id: int, org_id: int, agente_id: int) -> str:
    """
    Token de longa duração (1 ano) pro agente local.
    O agente armazena esse token no PC do afiliado e usa pra conectar via WebSocket.
    Em caso de comprometimento, admin revoga via dashboard.
    """
    expira = datetime.now(tz=timezone.utc) + timedelta(days=365)
    return _criar_token({
        "sub":     f"uid:{usuario_id}",
        "uid":     usuario_id,
        "org":     org_id,
        "agente":  agente_id,
        "papel":   "agente",
        "tipo":    TOKEN_AGENTE,
        "exp":     expira,
    })


def decodificar_token(token: str) -> dict[str, Any]:
    """
    Decodifica e valida assinatura + expiração.
    Levanta jwt.PyJWTError se inválido — caller deve tratar.
    """
    return jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[settings.jwt_algorithm],
    )


def _criar_token(payload: dict[str, Any]) -> str:
    """Helper interno — adiciona 'iat' e assina."""
    payload = {**payload, "iat": datetime.now(tz=timezone.utc)}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


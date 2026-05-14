"""
Cifragem simétrica de credenciais sensíveis (Fase 4b.1).

Usado pra cifrar senhas de plataformas (ML, Shopee, etc) antes de gravar
no banco. A chave fica em `settings.credenciais_secret_key` (env var
`CREDENCIAIS_SECRET_KEY`).

Algoritmo: Fernet (AES-128-CBC + HMAC-SHA256) da biblioteca `cryptography`.
- Símétrico: mesma chave cifra/decifra.
- Autenticado: detecta adulteração do ciphertext.
- Chave base64 url-safe de 32 bytes.

Atenção:
- A chave do .env é o ponto fraco. Se vazar, vaza tudo. Em produção real,
  trocar por secret manager (AWS KMS / GCP Secret / Vault).
- Não usar pra senhas DE USUÁRIO (essas são hashed com bcrypt — só comparação).
  Cifragem reversível é só pra credenciais que o agente PRECISA decifrar pra
  usar (login em plataforma externa).
"""
from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


class CredencialError(Exception):
    """Erro de cifragem/decifragem (chave errada, dado corrompido, etc)."""


def _gerar_chave_fernet() -> bytes:
    """
    Deriva chave Fernet (32 bytes base64) a partir do segredo do .env.

    Aceita qualquer string no settings (não exige formato Fernet) e
    deriva via SHA-256 — facilita configuração inicial.
    """
    raw = (settings.credenciais_secret_key or "").encode("utf-8")
    if not raw:
        raise CredencialError(
            "settings.credenciais_secret_key vazio — "
            "defina CREDENCIAIS_SECRET_KEY no .env"
        )
    # SHA-256 → 32 bytes → base64 url-safe = formato Fernet
    sha = hashlib.sha256(raw).digest()
    return base64.urlsafe_b64encode(sha)


def cifrar(plain: str | None) -> str | None:
    """Devolve ciphertext base64 url-safe. None → None."""
    if plain is None or plain == "":
        return None
    f = Fernet(_gerar_chave_fernet())
    token = f.encrypt(plain.encode("utf-8"))
    return token.decode("ascii")


def decifrar(ciphertext: str | None) -> str | None:
    """
    Devolve plain. None se ciphertext for None/vazio.
    Lança CredencialError se chave estiver errada ou dado corrompido.
    """
    if ciphertext is None or ciphertext == "":
        return None
    try:
        f = Fernet(_gerar_chave_fernet())
        plain = f.decrypt(ciphertext.encode("ascii"))
        return plain.decode("utf-8")
    except InvalidToken as e:
        raise CredencialError(
            "credencial não pôde ser decifrada (chave trocada ou dado corrompido)"
        ) from e

"""Testa hashing de senha e ciclo de vida do JWT."""
import time

import jwt
import pytest

from app.core.security import (
    TOKEN_ACESSO,
    TOKEN_REFRESH,
    criar_access_token,
    criar_refresh_token,
    decodificar_token,
    hash_senha,
    verificar_senha,
)


def test_hash_e_verifica_senha():
    """Hash deve ser diferente da senha; verify ok pra senha certa."""
    h = hash_senha("super-secret-123")
    assert h != "super-secret-123"
    assert verificar_senha("super-secret-123", h) is True
    assert verificar_senha("errada", h) is False


def test_hash_diferente_a_cada_vez():
    """bcrypt usa salt — mesmo input gera hashes diferentes."""
    h1 = hash_senha("abc")
    h2 = hash_senha("abc")
    assert h1 != h2
    # Mas ambos verificam OK
    assert verificar_senha("abc", h1)
    assert verificar_senha("abc", h2)


def test_access_token_decodifica():
    token = criar_access_token(usuario_id=42, org_id=1, papel="afiliado")
    payload = decodificar_token(token)
    assert payload["uid"] == 42
    assert payload["org"] == 1
    assert payload["papel"] == "afiliado"
    assert payload["tipo"] == TOKEN_ACESSO


def test_refresh_token_decodifica():
    token = criar_refresh_token(usuario_id=1, org_id=1)
    payload = decodificar_token(token)
    assert payload["tipo"] == TOKEN_REFRESH


def test_token_alterado_falha():
    """Mexer no token quebra a assinatura."""
    token = criar_access_token(usuario_id=1, org_id=1, papel="admin")
    falsificado = token[:-3] + "xyz"
    with pytest.raises(jwt.InvalidSignatureError):
        decodificar_token(falsificado)

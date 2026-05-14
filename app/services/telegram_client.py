"""
Cliente do Telegram Bot API.

Wrapper pequeno em volta do `httpx` pra chamar os endpoints relevantes:
- getMe          → validar bot_token
- sendMessage    → postar texto
- sendPhoto      → postar imagem + caption

Usa httpx síncrono porque é chamado de tasks Celery (sync por natureza).
Para uso async (validação no momento da criação do canal) tem versão async.

Documentação oficial: https://core.telegram.org/bots/api
"""
from __future__ import annotations

from typing import Any

import httpx
import structlog

log = structlog.get_logger(__name__)


# Limite oficial do Telegram pra mensagens de texto
TELEGRAM_TEXTO_MAX = 4096
# Limite pra caption de foto (sendPhoto)
TELEGRAM_CAPTION_MAX = 1024
# Timeout da requisição em segundos
TIMEOUT_SEG = 30


class TelegramError(Exception):
    """Erro vindo da Bot API. `codigo` reflete o tipo pra retry inteligente."""

    def __init__(
        self,
        mensagem: str,
        *,
        codigo: str = "telegram_erro",
        status_http: int | None = None,
        descricao: str | None = None,
    ) -> None:
        super().__init__(mensagem)
        self.codigo = codigo
        self.status_http = status_http
        self.descricao = descricao


def _url(token: str, metodo: str) -> str:
    return f"https://api.telegram.org/bot{token}/{metodo}"


def _interpretar_erro(resp: httpx.Response) -> TelegramError:
    """Transforma resposta de erro do Telegram em exceção tipada."""
    try:
        data = resp.json()
        descricao = data.get("description", resp.text[:200])
    except Exception:
        descricao = resp.text[:200]

    # Códigos típicos
    if resp.status_code == 401:
        codigo = "token_invalido"
    elif resp.status_code == 400:
        codigo = "request_invalido"
    elif resp.status_code == 403:
        # Bot bloqueado, removido do grupo, sem permissão
        codigo = "bot_sem_permissao"
    elif resp.status_code == 404:
        codigo = "chat_nao_encontrado"
    elif resp.status_code == 429:
        codigo = "rate_limit"
    elif resp.status_code >= 500:
        codigo = "telegram_indisponivel"
    else:
        codigo = "telegram_erro"

    return TelegramError(
        f"Telegram retornou {resp.status_code}: {descricao}",
        codigo=codigo,
        status_http=resp.status_code,
        descricao=descricao,
    )


# ============================================================
# Versão SÍNCRONA (usada pelas tasks Celery)
# ============================================================

def get_me(token: str) -> dict[str, Any]:
    """Valida o token. Retorna info do bot ou levanta TelegramError."""
    with httpx.Client(timeout=TIMEOUT_SEG) as client:
        resp = client.get(_url(token, "getMe"))
    if resp.status_code != 200:
        raise _interpretar_erro(resp)
    data = resp.json()
    if not data.get("ok"):
        raise TelegramError(
            f"getMe ok=false: {data.get('description')}",
            codigo="token_invalido",
            descricao=data.get("description"),
        )
    return data["result"]


def send_message(
    token: str,
    chat_id: str,
    texto: str,
    *,
    parse_mode: str = "HTML",
    desabilitar_preview: bool = False,
) -> dict[str, Any]:
    """Posta texto. Telegram aceita HTML básico (b, i, u, a, code, pre)."""
    if len(texto) > TELEGRAM_TEXTO_MAX:
        raise TelegramError(
            f"Texto excede limite de {TELEGRAM_TEXTO_MAX} caracteres",
            codigo="texto_muito_longo",
        )

    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": texto,
        "parse_mode": parse_mode,
    }
    if desabilitar_preview:
        payload["link_preview_options"] = {"is_disabled": True}

    with httpx.Client(timeout=TIMEOUT_SEG) as client:
        resp = client.post(_url(token, "sendMessage"), json=payload)

    if resp.status_code != 200:
        raise _interpretar_erro(resp)
    return resp.json()["result"]


def send_photo(
    token: str,
    chat_id: str,
    imagem_url: str,
    *,
    caption: str | None = None,
    parse_mode: str = "HTML",
) -> dict[str, Any]:
    """Posta foto via URL. Telegram baixa a imagem do servidor remoto."""
    if caption and len(caption) > TELEGRAM_CAPTION_MAX:
        raise TelegramError(
            f"Caption excede limite de {TELEGRAM_CAPTION_MAX} caracteres "
            f"(mande como sendMessage separado se for maior)",
            codigo="caption_muito_longa",
        )

    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "photo": imagem_url,
        "parse_mode": parse_mode,
    }
    if caption:
        payload["caption"] = caption

    with httpx.Client(timeout=TIMEOUT_SEG) as client:
        resp = client.post(_url(token, "sendPhoto"), json=payload)

    if resp.status_code != 200:
        raise _interpretar_erro(resp)
    return resp.json()["result"]


# ============================================================
# Versão ASYNC (usada em validação no endpoint web — chamada antes
# do Celery worker pegar a task)
# ============================================================

async def get_me_async(token: str) -> dict[str, Any]:
    """Versão async do getMe."""
    async with httpx.AsyncClient(timeout=TIMEOUT_SEG) as client:
        resp = await client.get(_url(token, "getMe"))
    if resp.status_code != 200:
        raise _interpretar_erro(resp)
    data = resp.json()
    if not data.get("ok"):
        raise TelegramError(
            f"getMe ok=false: {data.get('description')}",
            codigo="token_invalido",
            descricao=data.get("description"),
        )
    return data["result"]

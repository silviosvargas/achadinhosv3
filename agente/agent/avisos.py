"""
Avisos do agente pro dashboard via WS.

Módulo global thread-safe — quando código sync (rodando em
`asyncio.to_thread`) precisa avisar o user via dashboard
(captcha Shopee, login expirado, etc), chama `publicar(...)`
e essa função reschedula o envio no event loop principal.

Fluxo:
  1. `main.py` chama `configurar(ws_client, loop)` após criar o WSClient.
  2. Qualquer thread chama `avisos.publicar(tipo, mensagem, ...)`.
  3. Mensagem `{"tipo": "aviso_user", ...}` viaja via WS pro servidor.
  4. Servidor armazena em `agente_avisos.avisos` (5min TTL).
  5. Dashboard faz polling em `/api/v1/agentes/avisos` e mostra toast.

Sem WS configurado (modo offline) → log em DEBUG e ignora.
"""
from __future__ import annotations

import asyncio
from typing import Any

import structlog

log = structlog.get_logger(__name__)


_ws_client: Any = None
_loop: asyncio.AbstractEventLoop | None = None


def configurar(ws_client: Any, loop: asyncio.AbstractEventLoop) -> None:
    """Linka módulo ao WSClient + event loop principal."""
    global _ws_client, _loop
    _ws_client = ws_client
    _loop = loop
    log.info("avisos.configurado")


def publicar(
    tipo: str,
    mensagem: str,
    *,
    detalhe: str | None = None,
    marketplace: str | None = None,
    ttl_seg: int = 300,
) -> None:
    """
    Publica aviso pro dashboard via WS (thread-safe).

    Args:
        tipo: "captcha" | "login_expirado" | "qr_pendente" | "info" | ...
        mensagem: texto user-facing (até 500 chars).
        detalhe: extra (URL, contagem de tentativa, etc).
        marketplace: "shopee" / "ml" / ... — permite múltiplos avisos
                     simultâneos do mesmo agente.
        ttl_seg: tempo até expirar no servidor (10..1800s, default 300=5min).
    """
    if _ws_client is None or _loop is None:
        log.debug("avisos.sem_ws_client", tipo=tipo, mensagem=mensagem[:80])
        return

    payload = {
        "tipo":        "aviso_user",
        "aviso_tipo":  tipo,
        "mensagem":    mensagem[:500],
        "detalhe":     detalhe,
        "marketplace": marketplace,
        "ttl_seg":     ttl_seg,
    }

    try:
        # `asyncio.run_coroutine_threadsafe` é seguro de chamar de threads
        # diferentes do event loop. Se o WS estiver desconectado, o `enviar`
        # vai falhar silenciosamente — não bloqueia o thread chamador.
        asyncio.run_coroutine_threadsafe(_ws_client.enviar(payload), _loop)
    except Exception as e:
        log.warning("avisos.envio_falhou", erro=str(e)[:120], tipo=tipo)


def limpar(*, marketplace: str | None = None) -> None:
    """Sinal pro servidor remover o aviso (user resolveu)."""
    publicar("limpar", "", marketplace=marketplace, ttl_seg=10)

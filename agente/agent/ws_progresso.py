"""
Módulo global pro agente reportar progresso de tarefas via WS (Fase 20).

Por que global em vez de passar por parâmetro: o WS é singleton no agente,
e as funções de scraping rodam em threads separadas (asyncio.to_thread).
Configurar uma vez no startup e usar dentro de qualquer função sync.

Servidor recebe via `_h_busca_progresso` (ws_agente.py) e persiste em
`tarefas.progresso_pct/mensagem/atualizado_em`. UI faz polling.
"""
from __future__ import annotations

import asyncio
from typing import Any

import structlog

log = structlog.get_logger(__name__)


# Configurado em main.py após o cliente WS estar conectado
_cliente_ws: Any | None = None
_loop: asyncio.AbstractEventLoop | None = None


def configurar(cliente: Any, loop: asyncio.AbstractEventLoop) -> None:
    """Chamado uma vez no startup do agente após conectar WS."""
    global _cliente_ws, _loop
    _cliente_ws = cliente
    _loop = loop
    log.info("ws_progresso.configurado")


def reportar(
    tarefa_id: int | str | None,
    pct: float,
    mensagem: str = "",
) -> None:
    """Envia mensagem `busca_progresso` ao servidor.

    Thread-safe: pode ser chamado de qualquer thread (Selenium roda em
    `asyncio.to_thread`). Internamente usa `asyncio.run_coroutine_threadsafe`
    pra agendar o `enviar()` no event loop principal.

    Não levanta exceção — falha silenciosamente (apenas log debug).
    """
    if _cliente_ws is None or _loop is None:
        log.debug("ws_progresso.nao_configurado")
        return
    if tarefa_id is None:
        return
    try:
        pct_f = max(0.0, min(100.0, float(pct)))
    except (TypeError, ValueError):
        return
    payload = {
        "tipo":      "busca_progresso",
        "tarefa_id": int(tarefa_id),
        "pct":       pct_f,
        "mensagem":  str(mensagem or "")[:200],
    }
    try:
        asyncio.run_coroutine_threadsafe(_cliente_ws.enviar(payload), _loop)
    except Exception as e:
        log.debug("ws_progresso.envio_falhou", erro=str(e)[:100])

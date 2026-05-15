"""
Cliente WebSocket do agente.

Conecta no servidor, mantém viva a conexão, processa comandos.
Usa reconexão com backoff exponencial (tenacity).
"""
from __future__ import annotations

import asyncio
import json
import platform
from typing import Any, Callable, Awaitable

import websockets
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_never,
    wait_exponential,
)

import structlog

from agent.config import Config

log = structlog.get_logger(__name__)

# Versão do agente — incrementar a cada release
VERSAO_AGENTE = "3.0.0-alpha"


# Tipo: handler de mensagem recebida do servidor
HandlerComando = Callable[[dict[str, Any]], Awaitable[dict[str, Any] | None]]


class WSClient:
    """Cliente WebSocket persistente.

    Uso:
        client = WSClient(cfg)
        client.on_comando("postar_whatsapp", postador.postar)
        await client.run_forever()
    """

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._handlers: dict[str, HandlerComando] = {}
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._parar = asyncio.Event()

    def on_comando(self, tipo: str, handler: HandlerComando) -> None:
        """Registra handler pra um tipo de comando vindo do servidor."""
        self._handlers[tipo] = handler

    async def run_forever(self) -> None:
        """Loop infinito: conecta, processa, reconecta em caso de queda."""
        url = f"{self.cfg.servidor_ws}?token={self.cfg.token}"

        async for tentativa in AsyncRetrying(
            stop=stop_never,
            wait=wait_exponential(multiplier=1, min=2, max=60),
            retry=retry_if_exception_type(Exception),
            reraise=False,
        ):
            with tentativa:
                if self._parar.is_set():
                    return
                log.info("ws.conectando", url=self.cfg.servidor_ws)
                async with websockets.connect(
                    url,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=5,
                ) as ws:
                    self._ws = ws
                    log.info("ws.conectado")
                    await self._enviar_metricas_iniciais()
                    await self._loop_recebimento()

    async def parar(self) -> None:
        self._parar.set()
        if self._ws is not None:
            await self._ws.close()

    # ── Envio ────────────────────────────────────────

    async def enviar(self, payload: dict[str, Any]) -> None:
        """Envia mensagem JSON pro servidor."""
        if self._ws is None:
            log.warning("ws.envio_sem_conexao", payload_tipo=payload.get("tipo"))
            return
        await self._ws.send(json.dumps(payload))

    async def _enviar_metricas_iniciais(self) -> None:
        await self.enviar({
            "tipo":          "metricas",
            "versao_app":    VERSAO_AGENTE,
            "sistema_op":    f"{platform.system()} {platform.release()}",
            "chrome_aberto": False,  # postador atualiza depois
            "whatsapp_ok":   False,
        })

    # ── Recebimento ──────────────────────────────────

    async def _loop_recebimento(self) -> None:
        """Lê mensagens do servidor e despacha pros handlers."""
        assert self._ws is not None
        async for raw in self._ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                log.warning("ws.json_invalido", raw=raw[:100])
                continue

            tipo = msg.get("tipo")
            if not tipo:
                continue

            # Ping do servidor → responde com pong (não passa por handler)
            if tipo == "ping":
                await self.enviar({"tipo": "pong", "ts": msg.get("ts")})
                continue

            # Desconectar imediato
            if tipo == "desconectar":
                log.info("ws.desconectar_solicitado", motivo=msg.get("motivo"))
                await self._ws.close()
                return

            handler = self._handlers.get(tipo)
            if handler is None:
                log.warning("ws.tipo_sem_handler", tipo=tipo)
                continue

            # Executa handler em background pra não bloquear o loop
            asyncio.create_task(self._executar_handler(tipo, msg, handler))

    async def _executar_handler(
        self, tipo: str, msg: dict[str, Any], handler: HandlerComando,
    ) -> None:
        tarefa_id = msg.get("tarefa_id")
        try:
            resultado = await handler(msg)
            if tarefa_id and resultado is not None:
                if resultado.get("ok"):
                    await self.enviar({
                        "tipo":      "tarefa_concluida",
                        "tarefa_id": tarefa_id,
                        "resultado": resultado,
                    })
                else:
                    await self.enviar({
                        "tipo":      "tarefa_falhou",
                        "tarefa_id": tarefa_id,
                        "erro":      resultado.get("erro", "erro_desconhecido"),
                        "tentar_de_novo": resultado.get("tentar_de_novo", False),
                    })
        except Exception as e:
            log.exception("handler.crashou", tipo=tipo, erro=str(e))
            if tarefa_id:
                await self.enviar({
                    "tipo":      "tarefa_falhou",
                    "tarefa_id": tarefa_id,
                    "erro":      f"crash: {type(e).__name__}: {e}",
                    "tentar_de_novo": True,
                })

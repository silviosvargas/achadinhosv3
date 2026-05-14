"""
Registry de agentes conectados via WebSocket.

Mantém um mapa em memória {agente_id: WebSocket} pra que o dispatcher
saiba se um agente está online e como entregar mensagens pra ele.

⚠️ É local ao processo. Quando escalar pra múltiplas réplicas da API,
trocar por Redis pub/sub (cada réplica faz subscribe num canal e quem
tiver a conexão WS local processa).

Por enquanto (1 réplica), é suficiente.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from app.core.logging import get_logger

if TYPE_CHECKING:
    from fastapi import WebSocket

log = get_logger(__name__)


class AgenteRegistry:
    """Mantém conexões WS ativas, indexadas por agente_id."""

    def __init__(self) -> None:
        self._conexoes: dict[int, WebSocket] = {}
        self._lock = asyncio.Lock()

    async def conectar(self, agente_id: int, ws: WebSocket) -> None:
        """Registra um agente como online. Substitui conexão antiga se houver."""
        async with self._lock:
            antiga = self._conexoes.get(agente_id)
            if antiga is not None:
                # Conexão duplicada — fecha a antiga (afiliado abriu o agente em 2 PCs?)
                log.warning("agente.conexao_duplicada", agente_id=agente_id)
                try:
                    await antiga.close(code=4001, reason="Nova conexão estabelecida")
                except Exception:
                    pass
            self._conexoes[agente_id] = ws
            log.info("agente.conectado", agente_id=agente_id, total_online=len(self._conexoes))

    async def desconectar(self, agente_id: int) -> None:
        """Remove agente do registro."""
        async with self._lock:
            if agente_id in self._conexoes:
                del self._conexoes[agente_id]
                log.info("agente.desconectado",
                         agente_id=agente_id, total_online=len(self._conexoes))

    def esta_online(self, agente_id: int) -> bool:
        return agente_id in self._conexoes

    def get_ws(self, agente_id: int) -> WebSocket | None:
        return self._conexoes.get(agente_id)

    async def enviar_para(self, agente_id: int, payload: dict) -> bool:
        """
        Envia mensagem JSON pra um agente específico.
        Retorna True se conseguiu, False se agente offline ou erro.
        """
        ws = self._conexoes.get(agente_id)
        if ws is None:
            return False
        try:
            await ws.send_json(payload)
            return True
        except Exception as e:
            log.exception("agente.envio_falhou", agente_id=agente_id, erro=str(e))
            # Conexão quebrada — desregistra
            await self.desconectar(agente_id)
            return False

    @property
    def total_online(self) -> int:
        return len(self._conexoes)


# Singleton — uma instância pra todo o processo
registry = AgenteRegistry()

"""
WebSocket /ws/agente — canal bidirecional cloud ↔ agente local.

Protocolo descrito em docs/protocolo_agente.md.

Mensagens recebidas do agente:
    pong                 → resposta a ping (saúde)
    tarefa_concluida     → marca tarefa como concluída
    tarefa_falhou        → marca tarefa como falhou (com retry opcional)
    busca_progresso      → relato parcial de execução de busca ML (log only)
    qr_pendente          → notifica admin que QR do WhatsApp expirou
    metricas             → atualiza métricas do agente

Nota: produtos extraídos por busca NÃO vêm via WS — agente faz POST em
/api/v1/produtos/ingest (REST). Mantém WS leve pra postagens em tempo real.

Mensagens enviadas pro agente:
    ping                 → heartbeat (a cada 30s)
    postar_whatsapp      → comando de postagem
    iniciar_busca_ml     → comando de execução de busca no Mercado Livre
    desconectar          → admin pediu pra fechar
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import jwt
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status

from app.core.logging import get_logger
from app.core.security import TOKEN_AGENTE, decodificar_token
from app.db import sessao_async
from app.models import Agente
from app.services import agente_service, dispatcher
from app.services.agente_registry import registry

log = get_logger(__name__)

router = APIRouter(tags=["websocket"])

# Heartbeat
PING_INTERVAL_SEG = 30
PING_TIMEOUT_SEG  = 90  # 3 pings perdidos = considera offline


@router.websocket("/ws/agente")
async def ws_agente(
    websocket: WebSocket,
    token: str = Query(..., description="JWT do agente (token de longa duração)"),
) -> None:
    """
    Endpoint WS principal do agente.

    Fluxo:
    1. Valida token → extrai agente_id
    2. Confere agente existe e ativo no banco
    3. Aceita conexão, registra no registry
    4. Reentrega tarefas pendentes
    5. Loop: recebe mensagens, dispara handlers
    6. Em paralelo: envia pings periódicos
    7. Desconexão: limpa registry, marca offline
    """
    # ── 1. Valida token ────────────────────────────
    try:
        payload = decodificar_token(token)
    except jwt.ExpiredSignatureError:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Token expirado")
        return
    except jwt.PyJWTError:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Token inválido")
        return

    if payload.get("tipo") != TOKEN_AGENTE:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Tipo de token incorreto")
        return

    agente_id = payload.get("agente")
    if not agente_id:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Token sem agente_id")
        return

    # ── 2. Valida agente no banco ──────────────────
    async with sessao_async() as db:
        agente = await db.get(Agente, agente_id)
        if agente is None or not agente.ativo:
            await websocket.close(
                code=status.WS_1008_POLICY_VIOLATION,
                reason="Agente não encontrado ou desativado",
            )
            return

    # ── 3. Aceita e registra ───────────────────────
    await websocket.accept()
    client_ip = websocket.client.host if websocket.client else None

    await registry.conectar(agente_id, websocket)
    async with sessao_async() as db:
        await agente_service.marcar_online(db, agente_id=agente_id, ip=client_ip)

    # ── 4. Reentrega pendentes ─────────────────────
    async with sessao_async() as db:
        await dispatcher.reentregar_pendentes(db, agente_id=agente_id)

    # ── 5+6. Loop principal + heartbeat em paralelo ──
    heartbeat_task = asyncio.create_task(_heartbeat_loop(websocket))

    try:
        while True:
            mensagem: dict[str, Any] = await websocket.receive_json()
            await _processar_mensagem(agente_id=agente_id, mensagem=mensagem)

    except WebSocketDisconnect:
        log.info("ws.agente_desconectou", agente_id=agente_id)
    except Exception as e:
        log.exception("ws.erro_inesperado", agente_id=agente_id, erro=str(e))
    finally:
        # ── 7. Cleanup ─────────────────────────────
        heartbeat_task.cancel()
        await registry.desconectar(agente_id)
        async with sessao_async() as db:
            await agente_service.marcar_offline(db, agente_id=agente_id)


# ============================================================
# Handlers de mensagem
# ============================================================

async def _processar_mensagem(*, agente_id: int, mensagem: dict[str, Any]) -> None:
    """Despacha pra handler apropriado baseado em mensagem.tipo."""
    tipo = mensagem.get("tipo")
    if not tipo:
        log.warning("ws.mensagem_sem_tipo", agente_id=agente_id)
        return

    handler = _HANDLERS.get(tipo)
    if handler is None:
        log.warning("ws.tipo_desconhecido", agente_id=agente_id, tipo=tipo)
        return

    try:
        await handler(agente_id=agente_id, mensagem=mensagem)
    except Exception as e:
        log.exception("ws.handler_falhou",
                      agente_id=agente_id, tipo=tipo, erro=str(e))


async def _h_pong(*, agente_id: int, mensagem: dict[str, Any]) -> None:
    """Pong recebido — atualiza ultimo_ping silenciosamente."""
    async with sessao_async() as db:
        await agente_service.marcar_online(db, agente_id=agente_id)


async def _h_tarefa_concluida(*, agente_id: int, mensagem: dict[str, Any]) -> None:
    tarefa_id = mensagem.get("tarefa_id")
    if not tarefa_id:
        return
    async with sessao_async() as db:
        await dispatcher.marcar_concluida(
            db, tarefa_id=tarefa_id, resultado=mensagem.get("resultado"),
        )


async def _h_tarefa_falhou(*, agente_id: int, mensagem: dict[str, Any]) -> None:
    tarefa_id = mensagem.get("tarefa_id")
    if not tarefa_id:
        return
    async with sessao_async() as db:
        await dispatcher.marcar_falhou(
            db,
            tarefa_id=tarefa_id,
            erro=mensagem.get("erro", "erro_sem_detalhes"),
            tentar_de_novo=bool(mensagem.get("tentar_de_novo")),
        )


async def _h_metricas(*, agente_id: int, mensagem: dict[str, Any]) -> None:
    metricas = {k: v for k, v in mensagem.items() if k != "tipo"}
    async with sessao_async() as db:
        await agente_service.atualizar_metricas(
            db, agente_id=agente_id, metricas=metricas,
        )


async def _h_qr_pendente(*, agente_id: int, mensagem: dict[str, Any]) -> None:
    """WhatsApp deslogou no agente — registra que precisa atenção do admin."""
    log.warning("agente.qr_pendente", agente_id=agente_id)
    # TODO Fase 3: emitir notificação na dashboard via SSE pra admin
    # Por enquanto, só log.


async def _h_busca_progresso(*, agente_id: int, mensagem: dict[str, Any]) -> None:
    """
    Relato parcial de busca ML. Útil pra UI mostrar 'varrendo página 3/5'.
    Por enquanto só log — UI de progresso fica pra fase futura.

    Payload esperado: { tarefa_id, busca_id, pagina_atual, total_paginas,
                        produtos_encontrados_ate_agora }
    """
    log.info(
        "busca.progresso",
        agente_id=agente_id,
        tarefa_id=mensagem.get("tarefa_id"),
        pagina=mensagem.get("pagina_atual"),
        total=mensagem.get("total_paginas"),
        encontrados=mensagem.get("produtos_encontrados_ate_agora"),
    )


_HANDLERS = {
    "pong":             _h_pong,
    "tarefa_concluida": _h_tarefa_concluida,
    "tarefa_falhou":    _h_tarefa_falhou,
    "metricas":         _h_metricas,
    "qr_pendente":      _h_qr_pendente,
    "busca_progresso":  _h_busca_progresso,
}


# ============================================================
# Heartbeat
# ============================================================

async def _heartbeat_loop(ws: WebSocket) -> None:
    """Envia ping a cada 30s. Se conexão cair, exception aborta o loop."""
    try:
        while True:
            await asyncio.sleep(PING_INTERVAL_SEG)
            await ws.send_json({
                "tipo": "ping",
                "ts":   datetime.now(tz=timezone.utc).isoformat(),
            })
    except asyncio.CancelledError:
        # cleanup do finally do endpoint principal cancela essa task
        raise
    except Exception:
        # conexão morreu — silencioso, o loop principal já vai notar
        pass

"""
Dispatcher de tarefas.

Responsabilidades:
- Criar tarefas no banco (status=pendente)
- Rotear de acordo com o tipo de canal:
  * whatsapp_agente → tenta entregar via WebSocket pro agente local
  * telegram_bot    → enfileira no Celery worker (cloud)
- Receber callbacks de conclusão/falha
- Reentregar pendentes quando agente reconectar (WhatsApp)

Tipos de tarefa:
- postar_whatsapp: vai pro agente local específico (canal.config.agente_id)
- postar_telegram: vai pro Celery worker (executa em qualquer momento)
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import (
    Agente,
    Canal,
    Grupo,
    StatusTarefa,
    Tarefa,
    TipoTarefa,
)
from app.services.agente_registry import registry

log = get_logger(__name__)


class DispatcherError(Exception):
    """Erro de despacho."""


# ============================================================
# Criar tarefa
# ============================================================

async def enfileirar_postagem(
    db: AsyncSession,
    *,
    org_id: int,
    grupo_id: int,
    texto: str,
    imagem_url: str | None = None,
    produto_id: int | None = None,
    criado_por_usuario_id: int | None = None,
) -> Tarefa:
    """
    Cria uma tarefa de postagem.

    Roteamento:
    - Canal whatsapp_agente → tipo postar_whatsapp + agente_id do canal
    - Canal telegram_bot    → tipo postar_telegram + agente_id=None (cloud)
    """
    # 1. Carrega grupo + canal pra determinar pra onde vai
    grupo = await db.scalar(
        select(Grupo).where(Grupo.id == grupo_id, Grupo.org_id == org_id)
    )
    if grupo is None:
        raise DispatcherError("Grupo não encontrado nesta organização")
    if not grupo.ativo:
        raise DispatcherError("Grupo está inativo")

    canal = await db.get(Canal, grupo.canal_id)
    if canal is None:
        raise DispatcherError("Canal do grupo foi removido")
    if not canal.ativo:
        raise DispatcherError("Canal está inativo")

    # 2. Decide tipo + agente_id baseado no canal
    tipo, agente_id = _resolver_destino(canal)

    # 3. Cria a tarefa
    tarefa = Tarefa(
        org_id=org_id,
        tipo=tipo,
        status=StatusTarefa.PENDENTE,
        agente_id=agente_id,
        payload={
            "grupo_id":       grupo.id,
            "grupo_nome":     grupo.nome,
            "identificador":  grupo.identificador,
            "canal_tipo":     canal.tipo,
            "texto":          texto,
            "imagem_url":     imagem_url,
            "produto_id":     produto_id,
        },
        criado_por_usuario_id=criado_por_usuario_id,
    )
    db.add(tarefa)
    await db.commit()
    await db.refresh(tarefa)

    # 4. Roteamento — depende do tipo de canal
    if canal.tipo == "telegram_bot":
        # Cloud: enfileira no Celery worker
        # Importação tardia pra evitar ciclo (worker importa models que importam dispatcher)
        from app.workers.celery_app import celery_app
        celery_app.send_task("postar_telegram", args=[tarefa.id])
        log.info("tarefa.enfileirada.celery", tarefa_id=tarefa.id, canal_tipo=canal.tipo)

    elif agente_id and registry.esta_online(agente_id):
        # WhatsApp: agente está online — entrega via WS agora
        await _tentar_entrega(db, tarefa)

    else:
        # WhatsApp: agente offline — fica pendente até reconectar
        log.info("tarefa.aguarda_agente",
                 tarefa_id=tarefa.id, agente_id=agente_id)

    return tarefa


def _resolver_destino(canal: Canal) -> tuple[str, int | None]:
    """A partir do canal, decide tipo e quem executa."""
    if canal.tipo == "whatsapp_agente":
        agente_id = (canal.config or {}).get("agente_id")
        if not agente_id:
            raise DispatcherError(
                "Canal whatsapp_agente sem agente_id em config"
            )
        return TipoTarefa.POSTAR_WHATSAPP, int(agente_id)

    if canal.tipo == "telegram_bot":
        return TipoTarefa.POSTAR_TELEGRAM, None

    raise DispatcherError(f"Tipo de canal não suportado: {canal.tipo}")


# ============================================================
# Entregar tarefa pro agente
# ============================================================

_TIPO_TAREFA_PARA_COMANDO_WS = {
    TipoTarefa.POSTAR_WHATSAPP:      "postar_whatsapp",
    TipoTarefa.BUSCAR_MERCADO_LIVRE: "iniciar_busca_ml",
}


async def _tentar_entrega(db: AsyncSession, tarefa: Tarefa) -> None:
    """
    Tenta entregar a tarefa via WebSocket pro agente online.
    Se falhar (agente desconectou no meio), volta pra pendente.

    Mapeia o `TipoTarefa` pro comando WS correspondente (postar_whatsapp,
    iniciar_busca_ml, etc).
    """
    if tarefa.agente_id is None:
        return  # tarefa cloud (Telegram), não vai por WS

    comando = _TIPO_TAREFA_PARA_COMANDO_WS.get(tarefa.tipo)
    if comando is None:
        log.warning("tarefa.sem_comando_ws", tarefa_id=tarefa.id, tipo=tarefa.tipo)
        return

    payload = {
        "tipo":      comando,
        "tarefa_id": tarefa.id,
        **tarefa.payload,
    }

    enviado = await registry.enviar_para(tarefa.agente_id, payload)
    if enviado:
        tarefa.status = StatusTarefa.PROCESSANDO
        tarefa.iniciado_em = datetime.now(tz=timezone.utc)
        tarefa.tentativas += 1
        await db.commit()
        log.info("tarefa.entregue", tarefa_id=tarefa.id,
                 agente_id=tarefa.agente_id, comando=comando)
    else:
        log.info("tarefa.aguarda_agente",
                 tarefa_id=tarefa.id, agente_id=tarefa.agente_id)


# ============================================================
# Backfill — quando agente reconecta, manda tudo que estava pendente
# ============================================================

async def reentregar_pendentes(db: AsyncSession, *, agente_id: int) -> int:
    """
    Manda pro agente todas as tarefas pendentes ou processando dele.
    Chamado logo após o agente conectar via WS.

    Retorna número de tarefas reentregues.
    """
    result = await db.execute(
        select(Tarefa)
        .where(
            Tarefa.agente_id == agente_id,
            Tarefa.status.in_([StatusTarefa.PENDENTE, StatusTarefa.PROCESSANDO]),
        )
        .order_by(Tarefa.criado_em)
    )
    pendentes = list(result.scalars().all())

    for tarefa in pendentes:
        await _tentar_entrega(db, tarefa)

    log.info("tarefas.reentregues", agente_id=agente_id, total=len(pendentes))
    return len(pendentes)


# ============================================================
# Callbacks do agente (recebidos via WS)
# ============================================================

async def marcar_concluida(
    db: AsyncSession, *, tarefa_id: int, resultado: dict[str, Any] | None = None,
) -> None:
    """Agente reportou sucesso."""
    tarefa = await db.get(Tarefa, tarefa_id)
    if tarefa is None:
        log.warning("tarefa.callback_orfa", tarefa_id=tarefa_id)
        return
    tarefa.status = StatusTarefa.CONCLUIDA
    tarefa.resultado = resultado or {}
    tarefa.concluido_em = datetime.now(tz=timezone.utc)
    await db.commit()
    log.info("tarefa.concluida", tarefa_id=tarefa_id)


async def marcar_falhou(
    db: AsyncSession,
    *,
    tarefa_id: int,
    erro: str,
    tentar_de_novo: bool = False,
) -> None:
    """Agente reportou erro. Se tentar_de_novo, volta pra pendente."""
    tarefa = await db.get(Tarefa, tarefa_id)
    if tarefa is None:
        return

    if tentar_de_novo and tarefa.tentativas < tarefa.max_tentativas:
        tarefa.status = StatusTarefa.PENDENTE
        tarefa.erro = erro
        log.info("tarefa.retry", tarefa_id=tarefa_id, tentativas=tarefa.tentativas)
    else:
        tarefa.status = StatusTarefa.FALHOU
        tarefa.erro = erro
        tarefa.concluido_em = datetime.now(tz=timezone.utc)
        log.warning("tarefa.falhou_definitivo", tarefa_id=tarefa_id, erro=erro)

    await db.commit()

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
    TipoTarefa.POSTAR_WHATSAPP:       "postar_whatsapp",
    TipoTarefa.BUSCAR_MERCADO_LIVRE:  "iniciar_busca_ml",
    TipoTarefa.GERAR_LINK:            "gerar_links_afiliado_ml",
    TipoTarefa.REVALIDAR_COMISSAO_ML: "revalidar_comissao_ml",
}


async def _invalidar_agente_zumbi(agente_id: int, motivo: str) -> None:
    """Fecha WS + remove do registry quando o registro do agente foi
    apagado/desativado no DB enquanto a conexão WS continuava aberta.

    Cenário (Fase 3.32+): admin apaga um user via UI, CASCADE remove o
    agente dele, mas o WS dele continua no registry tentando trabalhar.
    Tarefas via WS são "aceitas" pelo agente mas POST /ingest depois
    falha com 401 ("Agente não encontrado ou desativado") porque o
    REST revalida no DB.

    Esta função limpa o estado zumbi:
    1. Fecha o WS com código 1008 + reason específico
    2. Remove do registry pra dispatcher não tentar entregar mais nada
    3. Agente recebe o close, JS de /agentes/baixar já detecta token órfão
       (commit 9e7a5f5) e oferece "Conectar meu agente" pra re-parear.
    """
    ws = registry.get_ws(agente_id)
    if ws is None:
        return
    log.warning("dispatcher.agente_zumbi_invalidado",
                agente_id=agente_id, motivo=motivo)
    try:
        await ws.close(
            code=1008,  # WS_1008_POLICY_VIOLATION
            reason="Agente não encontrado ou desativado",
        )
    except Exception as e:
        log.debug("dispatcher.close_ws_falhou", agente_id=agente_id, erro=str(e))
    await registry.desconectar(agente_id)


async def _tentar_entrega(db: AsyncSession, tarefa: Tarefa) -> None:
    """
    Tenta entregar a tarefa via WebSocket pro agente online.
    Se falhar (agente desconectou no meio), volta pra pendente.

    Mapeia o `TipoTarefa` pro comando WS correspondente (postar_whatsapp,
    iniciar_busca_ml, etc).
    """
    if tarefa.agente_id is None:
        return  # tarefa cloud (Telegram), não vai por WS

    # Revalidação antes de despachar: o registro do agente pode ter sido
    # apagado/desativado DEPOIS do handshake WS (ex: admin apagou o user
    # dono do agente via /usuarios → CASCADE em agentes.usuario_id).
    # Sem este check, o agente recebe a tarefa via WS, executa tudo, mas
    # o POST /ingest depois levaria 401 → busca completa perdida.
    agente_db = await db.get(Agente, tarefa.agente_id)
    if agente_db is None or not agente_db.ativo:
        motivo = "agente_apagado" if agente_db is None else "agente_desativado"
        log.warning(
            "tarefa.agente_invalido_no_despacho",
            tarefa_id=tarefa.id, agente_id=tarefa.agente_id, motivo=motivo,
        )
        await _invalidar_agente_zumbi(tarefa.agente_id, motivo)
        # Tarefa fica PENDENTE — quando user re-parear, novo agente_id
        # vai pegar (ou expira no max_tentativas). Não incrementa
        # tentativas porque a falha não é do agente.
        return

    comando = _TIPO_TAREFA_PARA_COMANDO_WS.get(tarefa.tipo)
    if comando is None:
        log.warning("tarefa.sem_comando_ws", tarefa_id=tarefa.id, tipo=tarefa.tipo)
        return

    # Defesa contra payload legado/bugado que tenha chave "tipo" ou "tarefa_id":
    # spread vem PRIMEIRO; comando WS e tarefa_id sobrescrevem por último. Sem
    # isso, tarefa antiga (pré-hotfix v3.0.3) cai em `ws.tipo_sem_handler` quando
    # `reentregar_pendentes` re-despacha. Loga warning pra rastrear casos legados.
    if isinstance(tarefa.payload, dict) and (
        "tipo" in tarefa.payload or "tarefa_id" in tarefa.payload
    ):
        log.warning("tarefa.payload_chave_conflitante",
                    tarefa_id=tarefa.id,
                    chaves=[k for k in ("tipo", "tarefa_id") if k in tarefa.payload])
    payload = {
        **(tarefa.payload or {}),
        "tipo":      comando,
        "tarefa_id": tarefa.id,
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
    Manda pro agente todas as tarefas PENDENTE dele.

    Chamado logo após o agente conectar via WS.

    NÃO re-entrega PROCESSANDO: se uma tarefa estava sendo executada quando
    o WS caiu, o agente provavelmente terminou (ou está terminando) e o
    callback vai chegar quando o WS subir de novo. Re-entregar nesse caso
    causa execução duplicada — observado em prod com `GERAR_LINK` abrindo
    múltiplas instâncias do Chrome ML simultâneas e crashando com
    `SessionNotCreatedException: cannot connect to chrome at 127.0.0.1:XXXX`.

    Tarefas legadas presas em PROCESSANDO precisam ser remediadas via
    /tarefas (UI admin) ou query SQL direta.

    Retorna número de tarefas reentregues.
    """
    result = await db.execute(
        select(Tarefa)
        .where(
            Tarefa.agente_id == agente_id,
            Tarefa.status == StatusTarefa.PENDENTE,
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

def _calcular_duracao_seg(tarefa: Tarefa) -> int | None:
    """Fase 20.2: int((concluido_em - iniciado_em).total_seconds()) ou None."""
    if tarefa.iniciado_em is None or tarefa.concluido_em is None:
        return None
    delta = tarefa.concluido_em - tarefa.iniciado_em
    return max(0, int(delta.total_seconds()))


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
    tarefa.duracao_seg = _calcular_duracao_seg(tarefa)
    # Marca progresso 100% pra UI mostrar "concluído" antes de sumir
    tarefa.progresso_pct = 100.0
    if tarefa.duracao_seg is not None:
        mins, secs = divmod(tarefa.duracao_seg, 60)
        tarefa.progresso_mensagem = f"✓ Concluído em {mins}min {secs}s"
    await db.commit()
    log.info("tarefa.concluida", tarefa_id=tarefa_id, duracao_seg=tarefa.duracao_seg)

    # Hook por tipo: tarefas com side-effect pós-conclusão.
    # GERAR_LINK (Fase 15): aplica o mapping retornado pelo linkbuilder
    # do agente, atualizando `produtos.url_afiliado` com os meli.la oficiais.
    if tarefa.tipo == TipoTarefa.GERAR_LINK:
        mapping = (resultado or {}).get("mapping") or {}
        if mapping:
            from app.services import afiliado_ml_writer
            await afiliado_ml_writer.aplicar_mapping(
                db, org_id=tarefa.org_id, mapping=mapping,
            )
    # REVALIDAR_COMISSAO_ML (Fase 18.3, v3.4.2): agente abriu cada link de
    # afiliado, capturou comissão real da barra de afiliados ML, retornou
    # mapping indexado por produto_id. Aplica nos produtos do TOP.
    elif tarefa.tipo == TipoTarefa.REVALIDAR_COMISSAO_ML:
        mapping_por_id = (resultado or {}).get("mapping_por_id") or {}
        if mapping_por_id:
            from app.services import afiliado_ml_writer
            await afiliado_ml_writer.aplicar_mapping_comissoes_por_id(
                db, org_id=tarefa.org_id, mapping_por_id=mapping_por_id,
            )

    # Fase C (17/05/2026): solicitações personalizadas — marca status
    # da fila quando a tarefa termina. Agente do admin processou pedido
    # do cliente; agora atualiza a SolicitacaoPersonalizada correspondente.
    solicitacao_id = (tarefa.payload or {}).get("solicitacao_id")
    if solicitacao_id:
        from app.services import solicitacao_service
        criados = int((resultado or {}).get("criados") or 0)
        await solicitacao_service.marcar_concluida_via_hook(
            db,
            solicitacao_id=int(solicitacao_id),
            produtos_criados=criados,
            ok=True,
        )


async def cancelar(
    db: AsyncSession, *, tarefa_id: int,
) -> dict:
    """Fase 20.1 — admin pediu pra cancelar tarefa em andamento.

    Estratégia:
    1. Envia comando WS `cancelar_tarefa` pro agente sinalizar flag
       (cancelamento cooperativo — loops longos param graciosamente)
    2. Marca tarefa como CANCELADA no DB imediatamente (não espera o
       agente confirmar — o agente vai reportar progresso/concluida normal
       quando o loop dele realmente parar)

    Returns:
        {"ok": bool, "comando_enviado": bool, "mensagem": str}
    """
    tarefa = await db.get(Tarefa, tarefa_id)
    if tarefa is None:
        return {"ok": False, "comando_enviado": False, "mensagem": "Tarefa não existe"}

    if tarefa.status not in (StatusTarefa.PROCESSANDO, StatusTarefa.PENDENTE):
        return {
            "ok": False, "comando_enviado": False,
            "mensagem": f"Tarefa já terminou (status={tarefa.status})",
        }

    # Envia comando WS pro agente (se houver agente_id e online)
    comando_enviado = False
    if tarefa.agente_id is not None:
        comando_enviado = await registry.enviar_para(
            tarefa.agente_id,
            {"tipo": "cancelar_tarefa", "tarefa_id": tarefa.id},
        )

    # Marca CANCELADA no DB independente do agente confirmar
    tarefa.status = StatusTarefa.CANCELADA
    tarefa.erro = "Cancelada pelo usuário"
    tarefa.concluido_em = datetime.now(tz=timezone.utc)
    tarefa.duracao_seg = _calcular_duracao_seg(tarefa)   # Fase 20.2
    await db.commit()

    log.info("tarefa.cancelada",
             tarefa_id=tarefa_id,
             agente_id=tarefa.agente_id,
             comando_enviado=comando_enviado)

    return {
        "ok": True,
        "comando_enviado": comando_enviado,
        "mensagem": (
            "Tarefa cancelada. " +
            ("Agente notificado — vai parar na próxima checagem (até ~1min)."
             if comando_enviado
             else "Agente offline — só marcou cancelada no DB.")
        ),
    }


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
        tarefa.duracao_seg = _calcular_duracao_seg(tarefa)   # Fase 20.2
        log.warning("tarefa.falhou_definitivo", tarefa_id=tarefa_id, erro=erro)

        # Fase C: marca solicitação como falhou (se for tarefa de solicitação)
        solicitacao_id = (tarefa.payload or {}).get("solicitacao_id")
        if solicitacao_id:
            from app.services import solicitacao_service
            await solicitacao_service.marcar_concluida_via_hook(
                db,
                solicitacao_id=int(solicitacao_id),
                produtos_criados=0,
                ok=False,
                mensagem_erro=erro,
            )

    await db.commit()

"""
Task agendadora — roda pelo Celery beat a cada minuto.

Varre `buscas_ml` ativas com `proxima_exec_em <= now` e enfileira uma
Tarefa(BUSCAR_MERCADO_LIVRE) pra cada uma. Atualiza `proxima_exec_em`
baseado em `intervalo_minutos`.

Usa sessão SÍNCRONA — Celery não é async-friendly. Em vez de chamar
o async `busca_service.enfileirar_execucao`, faz a lógica equivalente em
modo síncrono. O agente puxa via `reentregar_pendentes` quando reconectar.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import select

from app.db import sessao_sync
from app.models import BuscaML, StatusTarefa, Tarefa, TipoTarefa
from app.services.busca_service import detectar_tipo_entrada
from app.workers.celery_app import celery_app

log = structlog.get_logger(__name__)


@celery_app.task(name="agendar_buscas_devidas")
def agendar_buscas_devidas() -> dict:
    """
    Pega buscas ativas cujo `proxima_exec_em <= now` e cria tarefas.
    Retorna sumário: { processadas, enfileiradas }.
    """
    agora = datetime.now(tz=timezone.utc)
    processadas = 0
    enfileiradas = 0

    with sessao_sync() as db:
        buscas = list(db.execute(
            select(BuscaML)
            .where(
                BuscaML.ativo.is_(True),
                BuscaML.intervalo_minutos.isnot(None),
                BuscaML.proxima_exec_em.isnot(None),
                BuscaML.proxima_exec_em <= agora,
            )
        ).scalars().all())

        for busca in buscas:
            processadas += 1
            try:
                tipo_entrada = detectar_tipo_entrada(busca.entrada)
                # Fase 16: parseia marketplaces (JSON string no DB) pra lista
                try:
                    marketplaces_list = json.loads(busca.marketplaces or '["ml"]')
                    if not isinstance(marketplaces_list, list):
                        marketplaces_list = ["ml"]
                except (json.JSONDecodeError, TypeError):
                    marketplaces_list = ["ml"]
                tarefa = Tarefa(
                    org_id=busca.org_id,
                    tipo=TipoTarefa.BUSCAR_MERCADO_LIVRE,
                    status=StatusTarefa.PENDENTE,
                    agente_id=busca.agente_id,
                    payload={
                        "busca_id":      busca.id,
                        "tipo_entrada":  tipo_entrada,
                        "entrada":       busca.entrada,
                        "max_paginas":   busca.max_paginas,
                        "max_produtos":  busca.max_produtos,
                        "disparado_por": busca.criado_por_usuario_id,
                        # "tipo_busca" (não "tipo") pra não colidir com a chave
                        # de comando WS no spread do dispatcher (lição v3.0.3).
                        "tipo_busca":   getattr(busca, "tipo", "termo_livre"),
                        "marketplaces": marketplaces_list,
                    },
                    criado_por_usuario_id=busca.criado_por_usuario_id,
                )
                db.add(tarefa)
                db.flush()

                busca.ultima_exec_em   = agora
                busca.ultima_tarefa_id = tarefa.id
                busca.execucoes       += 1
                busca.proxima_exec_em  = agora + timedelta(minutes=busca.intervalo_minutos)

                enfileiradas += 1
                log.info("scheduler.busca_enfileirada",
                         busca_id=busca.id, tarefa_id=tarefa.id)
            except Exception as e:
                log.exception("scheduler.erro",
                              busca_id=busca.id, erro=str(e))

        db.commit()

    return {"processadas": processadas, "enfileiradas": enfileiradas}

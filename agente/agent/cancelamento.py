"""
Cancelamento cooperativo de tarefas (Fase 20.1).

Python não permite "matar" thread em execução. A solução clean é flag
global que loops longos consultam entre iterações pra parar voluntariamente.

Fluxo:
1. User clica "✕ Cancelar" na UI dashboard
2. Servidor envia comando WS `cancelar_tarefa` com `tarefa_id`
3. Handler no main.py chama `cancelamento.marcar(tarefa_id)`
4. Loops longos (ex: `busca_padrao_ml._varrer_padrao_completo_sync`)
   chamam `cancelamento.foi_cancelada(tarefa_id)` entre iterações e
   saem cedo quando True

Granularidade depende do loop — busca padrão checa entre categorias
(~1min cada), então cancelamento pode levar até 1min pra ter efeito.
"""
from __future__ import annotations

import threading
from typing import Set

import structlog

log = structlog.get_logger(__name__)

# Flag global: IDs de tarefas que o user pediu pra cancelar.
# Limpa-se sozinho quando a tarefa termina (verificado em `consumir`).
_canceladas: Set[int] = set()
_lock = threading.Lock()


def marcar(tarefa_id: int | str | None) -> None:
    """Marca tarefa pra cancelamento. Chamado pelo handler WS."""
    if tarefa_id is None:
        return
    try:
        tid = int(tarefa_id)
    except (TypeError, ValueError):
        return
    with _lock:
        _canceladas.add(tid)
    log.info("cancelamento.marcada", tarefa_id=tid)


def foi_cancelada(tarefa_id: int | str | None) -> bool:
    """Chamado pelos loops longos entre iterações. Não consome a flag."""
    if tarefa_id is None:
        return False
    try:
        tid = int(tarefa_id)
    except (TypeError, ValueError):
        return False
    with _lock:
        return tid in _canceladas


def consumir(tarefa_id: int | str | None) -> bool:
    """Checa E remove a flag. Chamado quando a tarefa termina pra limpar."""
    if tarefa_id is None:
        return False
    try:
        tid = int(tarefa_id)
    except (TypeError, ValueError):
        return False
    with _lock:
        if tid in _canceladas:
            _canceladas.discard(tid)
            return True
    return False

"""
Monitor de saúde da postagem WhatsApp — anti-banimento.

Portado de V2/src/postar/saude_postagem.py.

Detecta sinais de problema:
- Sequência de falhas (banimento provável)
- Aplica pausas adaptativas pra reduzir risco

Estado vive em memória — se reiniciar o agente, reseta. Aceitável.
"""
from __future__ import annotations

import time
from collections import deque

import structlog

log = structlog.get_logger(__name__)


# Estado global (singleton no processo)
_falhas_recentes: deque[dict] = deque(maxlen=20)
_pausa_extra: float = 0.0


# Limites
MAX_FALHAS_PRA_ABORTAR  = 5     # 5 falhas em janela curta = pode estar banido
JANELA_FALHAS_SEGUNDOS  = 300   # 5 minutos
LIMIAR_PAUSA_DOBRADA    = 2     # 2 falhas seguidas → pausa 2x
LIMIAR_PAUSA_TRIPLA     = 4     # 4+ → pausa 3x


def registrar_sucesso() -> None:
    """Marca sucesso — limpa o multiplicador de pausa."""
    global _pausa_extra
    _falhas_recentes.append({"tipo": "sucesso", "ts": time.time()})
    _pausa_extra = 0.0


def registrar_falha(motivo: str = "desconhecido") -> bool:
    """Marca falha. Retorna True se o monitor acha que deve PARAR."""
    global _pausa_extra
    agora = time.time()
    _falhas_recentes.append({"tipo": "falha", "motivo": motivo, "ts": agora})

    # Falhas na janela
    falhas_janela = sum(
        1 for ev in _falhas_recentes
        if ev["tipo"] == "falha" and (agora - ev["ts"]) < JANELA_FALHAS_SEGUNDOS
    )

    # Falhas seguidas
    falhas_seguidas = 0
    for ev in reversed(_falhas_recentes):
        if ev["tipo"] == "falha":
            falhas_seguidas += 1
        else:
            break

    # Pausa adaptativa
    if falhas_seguidas >= LIMIAR_PAUSA_TRIPLA:
        _pausa_extra = 2.0
        log.warning("saude.pausa_triplicada", falhas_seguidas=falhas_seguidas)
    elif falhas_seguidas >= LIMIAR_PAUSA_DOBRADA:
        _pausa_extra = 1.0
        log.warning("saude.pausa_dobrada", falhas_seguidas=falhas_seguidas)

    # Aborto?
    if falhas_janela >= MAX_FALHAS_PRA_ABORTAR:
        log.error(
            "saude.deve_abortar",
            falhas_janela=falhas_janela,
            janela_seg=JANELA_FALHAS_SEGUNDOS,
            motivo=motivo,
        )
        return True
    return False


def fator_pausa() -> float:
    """Multiplicador da pausa entre postagens (1.0 normal, 2.0 ou 3.0 com problema)."""
    return 1.0 + _pausa_extra


def resetar() -> None:
    """Limpa estado — útil em testes ou reinício manual."""
    global _pausa_extra
    _falhas_recentes.clear()
    _pausa_extra = 0.0

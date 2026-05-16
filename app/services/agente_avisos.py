"""
Store em memória de avisos ativos publicados pelos agentes pro dashboard.

Quando o agente local detecta algo que precisa de intervenção humana
(captcha Shopee, login ML expirado, WhatsApp deslogado, etc), ele publica
um aviso via WS — `{tipo: "aviso_user", aviso_tipo, mensagem, ttl_seg}`.

O dashboard faz polling em `GET /api/v1/agentes/avisos` e mostra um toast
amarelo persistente até o user resolver (agente publica `aviso_tipo="limpar"`
ou TTL expira).

Não persiste em DB porque:
- Avisos são transientes (TTL curto, expiram).
- Restart do servidor zera tudo (perfeito — agente vai re-publicar
  na próxima detecção).
- Sem fan-out entre processos por enquanto (1 instância Railway).
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from time import time


@dataclass
class Aviso:
    """Aviso ativo de um agente pro dashboard da org."""
    agente_id:    int
    org_id:       int
    tipo:         str            # "captcha" | "login_expirado" | "qr_pendente" | ...
    mensagem:     str
    detalhe:      str | None    = None
    marketplace:  str | None    = None    # "shopee" / "ml" / ... quando aplicável
    criado_em:    float         = field(default_factory=time)
    expira_em:    float         = 0.0     # epoch — se 0, nunca expira


class _StoreAvisos:
    """Threadsafe store em memória — 1 aviso ativo por (agente_id, chave)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # Chave composta: (agente_id, marketplace_ou_None) → permite captcha
        # da Shopee coexistir com qr_pendente do WhatsApp pro mesmo agente.
        self._avisos: dict[tuple[int, str | None], Aviso] = {}

    def publicar(self, aviso: Aviso) -> None:
        with self._lock:
            self._avisos[(aviso.agente_id, aviso.marketplace)] = aviso

    def remover(self, *, agente_id: int, marketplace: str | None = None) -> bool:
        with self._lock:
            return self._avisos.pop((agente_id, marketplace), None) is not None

    def remover_todos_do_agente(self, agente_id: int) -> int:
        with self._lock:
            chaves = [k for k in self._avisos if k[0] == agente_id]
            for k in chaves:
                del self._avisos[k]
            return len(chaves)

    def por_org(self, org_id: int) -> list[Aviso]:
        agora = time()
        with self._lock:
            # Limpa expirados ao listar
            expirados = [
                k for k, av in self._avisos.items()
                if av.expira_em and av.expira_em < agora
            ]
            for k in expirados:
                del self._avisos[k]
            return [av for av in self._avisos.values() if av.org_id == org_id]


# Singleton de processo
avisos = _StoreAvisos()

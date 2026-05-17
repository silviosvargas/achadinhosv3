"""
Logs persistentes do servidor — pra investigação de bugs sem precisar
de SSH no Railway. Agrupados por `tarefa_id` quando disponível (igual
ao "Logs de jobs antigos" do Railway/CI).

NÃO substitui logging.info()/structlog do servidor — é alimentado por um
processor custom em `app/core/log_buffer.py` que captura tudo INFO+ e
faz batch INSERT a cada ~2s pra não bloquear request.

TTL: 30 dias (cleanup via Celery beat futuro). Mantemos só o suficiente
pra diagnosticar bugs recentes.
"""
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

if TYPE_CHECKING:
    pass


class LogEntry(Base):
    """1 linha de log persistente. Sem TimestampMixin (já tem `ts` próprio)."""
    __tablename__ = "log_entries"
    __table_args__ = (
        # Pra listar logs de uma tarefa específica em ordem cronológica.
        Index("ix_log_entries_tarefa_ts", "tarefa_id", "ts"),
        # Pra listar logs recentes filtrados por org (admin central vê tudo).
        Index("ix_log_entries_org_ts", "org_id", "ts"),
        # Pra cleanup TTL: DELETE WHERE ts < now() - 30 days
        Index("ix_log_entries_ts", "ts"),
    )

    id:  Mapped[int]      = mapped_column(Integer, primary_key=True)
    ts:  Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Nível: INFO | WARNING | ERROR | CRITICAL (DEBUG não é persistido)
    nivel: Mapped[str] = mapped_column(String(10), nullable=False)

    # Nome do evento (structlog `event` arg). Ex: "busca.ingest.concluido"
    evento:   Mapped[str | None] = mapped_column(String(120), default=None)
    # Mensagem renderizada (caso queiramos display rápido sem parsing do contexto)
    mensagem: Mapped[str | None] = mapped_column(Text, default=None)
    # Contexto adicional (kwargs do structlog): dict serializável
    contexto: Mapped[dict] = mapped_column(JSON, default=dict)

    # Origem: "server" (este MVP) | "agente" (fase futura via WS)
    source: Mapped[str] = mapped_column(String(20), default="server")

    # Vínculo opcional com tarefa — permite agrupar "logs do job N"
    tarefa_id: Mapped[int | None] = mapped_column(
        ForeignKey("tarefas.id", ondelete="SET NULL"),
        default=None, index=True,
    )
    # Org pra filtragem (admin central vê tudo; demais só a própria)
    org_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizacoes.id", ondelete="SET NULL"),
        default=None, index=True,
    )
    # Agente (quando log veio de processamento ligado a um agente específico)
    agente_id: Mapped[int | None] = mapped_column(
        ForeignKey("agentes.id", ondelete="SET NULL"),
        default=None,
    )

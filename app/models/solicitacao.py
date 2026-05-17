"""Solicitação de produto personalizado (Fase C — 17/05/2026).

Quando um cliente comum (qualquer org não-admin-central) cadastra um
produto novo em `/produtos/personalizados/novo`, em vez de disparar
busca no agente dele (Fase 17 original), agora cria uma `Solicitacao`
nessa fila — admin processa via UI ou rotina Celery hourly usando o
agente do admin central.

Filosofia: cliente comum NÃO tem agente próprio pra Selenium ML/Shopee
(Regra 2 do user). Só admin executa Selenium. Cliente só pede; admin
processa.
"""
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class StatusSolicitacao(StrEnum):
    PENDENTE    = "pendente"     # aguardando admin processar
    PROCESSANDO = "processando"  # tarefa criada, esperando agente terminar
    CONCLUIDA   = "concluida"    # produtos no catálogo
    FALHOU      = "falhou"       # erro durante processamento
    REJEITADA   = "rejeitada"    # admin recusou manualmente


class TipoSolicitacao(StrEnum):
    PALAVRA_CHAVE = "palavra_chave"  # termo livre no ML
    URL           = "url"             # link direto de produto
    SOCIAL        = "social"          # link TikTok/Insta/YT (IA extrai)


class SolicitacaoPersonalizada(Base):
    """Item da fila de solicitações personalizadas.

    Quando processada, dispara `Tarefa(BUSCAR_MERCADO_LIVRE)` usando o
    agente do admin central. Os produtos resultantes vão pro DB com
    `org_id = admin_org_id` e `criado_por_usuario_id = usuario_id` (do
    solicitante) — assim aparecem em `/produtos/personalizados` do
    cliente original.
    """
    __tablename__ = "solicitacoes_personalizadas"

    id:         Mapped[int] = mapped_column(Integer, primary_key=True)
    usuario_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("usuarios.id", ondelete="CASCADE"),
        index=True,
    )
    org_id_solicitante: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizacoes.id", ondelete="CASCADE"),
        comment="org do user solicitante (≠ org_id do produto criado, que é admin_org_id)",
    )
    tipo:    Mapped[str] = mapped_column(String(20))
    entrada: Mapped[str] = mapped_column(String(500))

    status:  Mapped[str] = mapped_column(
        String(20),
        default=StatusSolicitacao.PENDENTE,
        server_default=StatusSolicitacao.PENDENTE,
        index=True,
    )
    tarefa_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("tarefas.id", ondelete="SET NULL"),
        default=None,
        comment="Tarefa BUSCAR_MERCADO_LIVRE criada pelo admin processar",
    )
    produtos_criados: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0",
        comment="Quantos produtos foram ingeridos quando concluída",
    )
    mensagem_erro: Mapped[str | None] = mapped_column(
        String(500), default=None,
    )

    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow,
    )
    processado_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None,
    )
    concluido_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None,
    )

"""
Buscas automatizadas no Mercado Livre.

Cada `BuscaML` é uma configuração reutilizável: nome, termo/URL, limites,
e agendamento opcional. Roda no agente local (Selenium), nunca no servidor
(ADR-004).

Disparo:
- Manual: admin clica "rodar agora" na UI → dispatcher cria Tarefa(BUSCAR_MERCADO_LIVRE).
- Agendado: Celery beat a cada minuto varre buscas com `proxima_exec_em <= now`
  e enfileira.

Resultado vem pelo endpoint REST `/api/v1/produtos/ingest` (não via WS),
porque batches podem ser grandes e o canal WS é prioritário pra postagens.
"""
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class BuscaML(Base, TimestampMixin):
    """Configuração persistida de uma busca recorrente no ML."""
    __tablename__ = "buscas_ml"
    __table_args__ = (
        Index("ix_buscas_agenda", "ativo", "proxima_exec_em"),
    )

    id:     Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True,
    )
    criado_por_usuario_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios.id", ondelete="SET NULL"), default=None,
    )
    # Agente que executa. NULL = aceita qualquer agente online da org.
    agente_id: Mapped[int | None] = mapped_column(
        ForeignKey("agentes.id", ondelete="SET NULL"), default=None,
    )

    nome:     Mapped[str] = mapped_column(String(150))
    entrada:  Mapped[str] = mapped_column(String(2000),
        comment="Termo livre ('fone bluetooth') ou URL do ML")
    max_paginas:  Mapped[int] = mapped_column(Integer, default=3)
    max_produtos: Mapped[int] = mapped_column(Integer, default=50)

    intervalo_minutos: Mapped[int | None] = mapped_column(
        Integer, default=None,
        comment="None = manual apenas; valor = intervalo entre execuções",
    )
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)

    # Estado de execução
    ultima_exec_em:   Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None,
    )
    proxima_exec_em:  Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None,
    )
    ultima_tarefa_id: Mapped[int | None] = mapped_column(
        ForeignKey("tarefas.id", ondelete="SET NULL"), default=None,
    )
    execucoes: Mapped[int] = mapped_column(Integer, default=0)


class NichoCategoriaML(Base):
    """
    Mapping `categoria_ml → nicho_id` por organização.

    Usado no ingest pra classificar produto automaticamente: a busca devolve
    a categoria do ML ("Eletrônicos > Áudio > Fones de Ouvido"), e essa
    tabela diz qual nicho da org casa com essa categoria.

    Match: exato no campo `categoria_ml` (case-insensitive na hora da query).
    Sem mapping = produto entra sem nicho (admin associa depois).
    """
    __tablename__ = "nicho_categoria_ml"
    __table_args__ = (
        UniqueConstraint("org_id", "categoria_ml", name="uq_nicho_categoria_ml"),
    )

    id:     Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True,
    )
    categoria_ml: Mapped[str] = mapped_column(String(200))
    nicho_id:     Mapped[int] = mapped_column(
        ForeignKey("nichos.id", ondelete="CASCADE"),
    )
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
    )

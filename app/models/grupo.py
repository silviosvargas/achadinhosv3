"""
Grupos (de WhatsApp ou Telegram) e histórico de postagens.

Ambos são por organização (org_id NOT NULL).
"""
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Grupo(Base, TimestampMixin):
    """
    Grupo de destino. Pode ser WhatsApp (identificado por nome no agente)
    ou Telegram (identificado por chat_id).
    """
    __tablename__ = "grupos"
    __table_args__ = (
        UniqueConstraint("org_id", "canal_id", "identificador",
                         name="uq_grupos_org_canal_id"),
    )

    id:        Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id:    Mapped[int] = mapped_column(
        ForeignKey("organizacoes.id", ondelete="CASCADE"),
        index=True,
    )
    canal_id:  Mapped[int] = mapped_column(
        ForeignKey("canais.id", ondelete="CASCADE"),
        index=True,
    )
    proprietario_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios.id", ondelete="SET NULL"),
        index=True,
        default=None,
    )

    nome:           Mapped[str] = mapped_column(String(200))     # Visual: "Achadinhos - Ofertas 01"
    identificador:  Mapped[str] = mapped_column(String(200))     # WhatsApp: nome exato; Telegram: chat_id

    ativo:          Mapped[bool] = mapped_column(Boolean, default=True)
    precisa_atencao_admin: Mapped[bool] = mapped_column(Boolean, default=False)


class GrupoNicho(Base):
    """N:N entre grupos e nichos. Define quais nichos cada grupo aceita receber."""
    __tablename__ = "grupo_nichos"

    grupo_id: Mapped[int] = mapped_column(
        ForeignKey("grupos.id", ondelete="CASCADE"),
        primary_key=True,
    )
    nicho_id: Mapped[int] = mapped_column(
        ForeignKey("nichos.id", ondelete="CASCADE"),
        primary_key=True,
    )


class Postagem(Base):
    """
    Histórico imutável de cada postagem feita.
    Mesmo se o produto/grupo for deletado depois, o registro fica
    (pra auditoria, métricas e billing).

    Note: NÃO usa TimestampMixin porque postagem não atualiza —
    é append-only.
    """
    __tablename__ = "postagens"
    __table_args__ = (
        Index("ix_postagens_org_data", "org_id", "postado_em"),
        Index("ix_postagens_chave", "plataforma", "item_id"),
    )

    id:     Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizacoes.id", ondelete="CASCADE"),
        index=True,
    )
    canal_id:   Mapped[int | None] = mapped_column(
        ForeignKey("canais.id", ondelete="SET NULL"),
        default=None,
    )
    grupo_id:   Mapped[int | None] = mapped_column(
        ForeignKey("grupos.id", ondelete="SET NULL"),
        default=None,
    )
    produto_id: Mapped[int | None] = mapped_column(
        ForeignKey("produtos.id", ondelete="SET NULL"),
        default=None,
    )
    usuario_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios.id", ondelete="SET NULL"),
        default=None,
    )

    # Snapshots — sobrevivem mesmo se referências forem deletadas
    grupo_nome:    Mapped[str]   = mapped_column(String(200))
    plataforma:    Mapped[str]   = mapped_column(String(20))
    item_id:       Mapped[str]   = mapped_column(String(100))
    nome_produto:  Mapped[str]   = mapped_column(String(500))
    preco_postado: Mapped[float] = mapped_column(Float)
    canal_tipo:    Mapped[str]   = mapped_column(String(30))   # 'whatsapp_agente' | 'telegram_bot'

    fonte:    Mapped[str] = mapped_column(String(30), default="manual")  # manual|agendado|super
    enviado:  Mapped[bool] = mapped_column(Boolean, default=True)
    erro:     Mapped[str | None] = mapped_column(String(500), default=None)

    postado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

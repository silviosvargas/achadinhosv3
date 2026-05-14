"""
Organização = tenant. Toda entidade operacional pertence a uma org.

Estratégia de isolamento: discriminator column (org_id em cada tabela).
Mais simples que schema-per-tenant ou DB-per-tenant, e suficiente
até dezenas de milhares de orgs.
"""
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.usuario import Usuario


class Plano(Base, TimestampMixin):
    """Planos de assinatura. Definem limites e preço."""
    __tablename__ = "planos"

    id:                    Mapped[int]    = mapped_column(Integer, primary_key=True)
    slug:                  Mapped[str]    = mapped_column(String(50), unique=True)   # 'free', 'pro', 'business'
    nome:                  Mapped[str]    = mapped_column(String(100))
    preco_mensal_brl:      Mapped[int]    = mapped_column(Integer, default=0)         # centavos
    limite_afiliados:      Mapped[int]    = mapped_column(Integer, default=1)
    limite_grupos:         Mapped[int]    = mapped_column(Integer, default=10)
    limite_postagens_mes:  Mapped[int]    = mapped_column(Integer, default=500)
    permite_telegram:      Mapped[bool]   = mapped_column(Boolean, default=True)
    permite_agendamento:   Mapped[bool]   = mapped_column(Boolean, default=True)
    ativo:                 Mapped[bool]   = mapped_column(Boolean, default=True)


class Organizacao(Base, TimestampMixin):
    """
    Tenant. Container de tudo: usuários, grupos, agendamentos, postagens.

    Note que `produtos` e `super_produtos` ficam fora — são catálogo
    compartilhado entre orgs (todo mundo se beneficia dos achados).
    """
    __tablename__ = "organizacoes"

    id:        Mapped[int]    = mapped_column(Integer, primary_key=True)
    slug:      Mapped[str]    = mapped_column(String(50), unique=True, index=True)  # achadinhos-silvio
    nome:      Mapped[str]    = mapped_column(String(150))
    plano_id:  Mapped[int]    = mapped_column(ForeignKey("planos.id"), default=1)   # default: free
    ativo:     Mapped[bool]   = mapped_column(Boolean, default=True, nullable=False)
    suspensa_em:        Mapped[datetime | None] = mapped_column(default=None)
    motivo_suspensao:   Mapped[str | None]      = mapped_column(String(255), default=None)

    # Relacionamentos
    plano:     Mapped["Plano"] = relationship(lazy="joined")
    usuarios:  Mapped[list["Usuario"]] = relationship(
        back_populates="organizacao",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Organizacao id={self.id} slug={self.slug!r}>"

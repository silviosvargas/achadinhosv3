"""
Base do ORM. Todos os models herdam de `Base`.

Convenções globais:
- Nomes de tabelas em pt-BR plural (igual à V2): `usuarios`, `grupos`...
- Colunas em snake_case
- Datetime com timezone (timestamptz no Postgres)
- Soft-delete onde fizer sentido (coluna `removido_em`)
"""
from datetime import datetime
from typing import Any

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func


# Convenção de nomes — Alembic gera nomes consistentes pra constraints
# (importante pra migrações sem dor)
NAMING_CONVENTION: dict[str, Any] = {
    "ix":  "ix_%(column_0_label)s",
    "uq":  "uq_%(table_name)s_%(column_0_name)s",
    "ck":  "ck_%(table_name)s_%(constraint_name)s",
    "fk":  "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk":  "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Base declarativa — todos os models herdam daqui."""
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    """Mixin com criado_em/atualizado_em automáticos."""
    criado_em: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=func.now(),
    )
    atualizado_em: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

"""Produtos personalizados: rastreia quem cadastrou (Fase 17).

Adiciona em `produtos`:
- `criado_por_usuario_id` INTEGER FK → usuarios.id (nullable, SET NULL no delete)

Diferença de `usuario_dono_id`:
- `usuario_dono_id` = quem é DONO do produto (NULL = público; preenchido = privado)
- `criado_por_usuario_id` = quem CADASTROU o produto (sempre preenchido em
  produtos cadastrados via "Personalizados", mesmo que o produto vire público)

Permite a UI `/produtos/personalizados` filtrar "produtos que o user criou"
sem confundir com a regra de visibilidade pública/privada.

Revision ID: 0011_prod_criado_por
Revises:    0010_busca_tipo_mkt
Create Date: 2026-05-16
"""
from alembic import op
import sqlalchemy as sa


revision = "0011_prod_criado_por"
down_revision = "0010_busca_tipo_mkt"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "produtos",
        sa.Column(
            "criado_por_usuario_id",
            sa.Integer,
            sa.ForeignKey("usuarios.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_produtos_criado_por",
        "produtos",
        ["criado_por_usuario_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_produtos_criado_por", table_name="produtos")
    op.drop_column("produtos", "criado_por_usuario_id")

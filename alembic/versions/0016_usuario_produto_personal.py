"""Usuário favorita produtos do catálogo central (Fase B).

Cria tabela `usuario_produto_personalizado` M:N — quais produtos um
usuário marcou como "meus personalizados" pra acesso rápido em
`/produtos/personalizados`.

Diferente de `produtos.criado_por_usuario_id` (quem CRIOU o produto):
aqui é quem FAVORITOU produto existente. A página /produtos/personalizados
faz UNION dos dois.

Revision ID: 0016_uppp
Revises:    0015_extra
Create Date: 2026-05-17
"""
from alembic import op
import sqlalchemy as sa


revision = "0016_uppp"
down_revision = "0015_extra"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "usuario_produto_personalizado",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("usuario_id", sa.Integer(), nullable=False),
        sa.Column("produto_id", sa.Integer(), nullable=False),
        sa.Column(
            "criado_em",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["usuario_id"], ["usuarios.id"], ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["produto_id"], ["produtos.id"], ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "usuario_id", "produto_id",
            name="uq_usuario_produto_personalizado",
        ),
    )
    op.create_index(
        "ix_uppp_usuario", "usuario_produto_personalizado", ["usuario_id"],
    )
    op.create_index(
        "ix_uppp_produto", "usuario_produto_personalizado", ["produto_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_uppp_produto", table_name="usuario_produto_personalizado")
    op.drop_index("ix_uppp_usuario", table_name="usuario_produto_personalizado")
    op.drop_table("usuario_produto_personalizado")

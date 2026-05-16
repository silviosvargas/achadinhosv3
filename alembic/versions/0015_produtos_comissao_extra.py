"""Comissão EXTRA (bônus GANHOS EXTRAS) capturada da barra de afiliados ML.

Adiciona em `produtos`:
- `comissao_extra` (FLOAT, nullable) — % do bônus EXTRAS quando o produto
  está com promoção Mais por Mais. NULL = produto sem bônus.

`produto.comissao` continua sendo a comissão EFETIVA (extras se tem, base se
não). `comissao_extra` permite filtrar/ordenar especificamente os com bônus
(busca padrão `ml_comissao_extra`).

Revision ID: 0015_extra
Revises:    0014_duracao
Create Date: 2026-05-17
"""
from alembic import op
import sqlalchemy as sa


revision = "0015_extra"
down_revision = "0014_duracao"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("produtos", sa.Column(
        "comissao_extra", sa.Float(), nullable=True,
    ))


def downgrade() -> None:
    op.drop_column("produtos", "comissao_extra")

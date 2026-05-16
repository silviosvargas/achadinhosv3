"""Curadoria via nota no produto (Fase 18 — reformulada).

Adiciona em `produtos`:
- `nota`                    (FLOAT, 0..100, default 0) + índice (org_id, nota DESC)
- `is_bestseller`           (BOOL, default False)
- `is_em_alta`              (BOOL, default False)
- `total_vendidos`          (INT, default 0)
- `comissao_fonte`          (VARCHAR(30), default 'estimativa') — ml_painel|shopee_api|amazon_tabela|estimativa
- `comissao_validada`       (BOOL, default False)
- `preco_atualizado_em`     (DATETIME tz, nullable)
- `comissao_atualizada_em`  (DATETIME tz, nullable)
- `vendidos_atualizado_em`  (DATETIME tz, nullable)

Diferença do `atualizado_em` (TimestampMixin): aquele muda em qualquer UPDATE.
Os 3 timestamps específicos só mudam quando aquele campo concreto é atualizado,
útil pra UI mostrar "preço de 3 dias atrás" mesmo quando o produto sofreu
outro tipo de update mais recente.

Revision ID: 0012_curadoria
Revises:    0011_prod_criado_por
Create Date: 2026-05-17
"""
from alembic import op
import sqlalchemy as sa


revision = "0012_curadoria"
down_revision = "0011_prod_criado_por"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("produtos", sa.Column(
        "nota", sa.Float(), nullable=False, server_default="0",
    ))
    op.add_column("produtos", sa.Column(
        "is_bestseller", sa.Boolean(), nullable=False, server_default=sa.text("false"),
    ))
    op.add_column("produtos", sa.Column(
        "is_em_alta", sa.Boolean(), nullable=False, server_default=sa.text("false"),
    ))
    op.add_column("produtos", sa.Column(
        "total_vendidos", sa.Integer(), nullable=False, server_default="0",
    ))
    op.add_column("produtos", sa.Column(
        "comissao_fonte", sa.String(30), nullable=False, server_default="estimativa",
    ))
    op.add_column("produtos", sa.Column(
        "comissao_validada", sa.Boolean(), nullable=False, server_default=sa.text("false"),
    ))
    op.add_column("produtos", sa.Column(
        "preco_atualizado_em", sa.DateTime(timezone=True), nullable=True,
    ))
    op.add_column("produtos", sa.Column(
        "comissao_atualizada_em", sa.DateTime(timezone=True), nullable=True,
    ))
    op.add_column("produtos", sa.Column(
        "vendidos_atualizado_em", sa.DateTime(timezone=True), nullable=True,
    ))

    # Índice DESC pra consultas de TOP (curadoria_service.listar_top)
    op.create_index(
        "ix_produtos_org_nota",
        "produtos",
        ["org_id", sa.text("nota DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_produtos_org_nota", table_name="produtos")
    op.drop_column("produtos", "vendidos_atualizado_em")
    op.drop_column("produtos", "comissao_atualizada_em")
    op.drop_column("produtos", "preco_atualizado_em")
    op.drop_column("produtos", "comissao_validada")
    op.drop_column("produtos", "comissao_fonte")
    op.drop_column("produtos", "total_vendidos")
    op.drop_column("produtos", "is_em_alta")
    op.drop_column("produtos", "is_bestseller")
    op.drop_column("produtos", "nota")

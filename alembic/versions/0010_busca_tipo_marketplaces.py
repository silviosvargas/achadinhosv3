"""Busca multi-marketplace + tipos (Fase 16).

Adiciona em `buscas_ml`:
- `tipo` VARCHAR(30) — termo_livre | por_url | mais_vendidos | melhor_comissao | em_alta
- `marketplaces` VARCHAR(200) — JSON array com slugs (ex: '["ml","shopee"]')

Buscas existentes (criadas antes da migration) ficam com `tipo='termo_livre'`
e `marketplaces='["ml"]'` (comportamento legado, igual à V3 pré-Fase 16).

Revision ID: 0010_busca_tipo_mkt
Revises:    0009_redirects_curto
Create Date: 2026-05-15
"""
from alembic import op
import sqlalchemy as sa


revision = "0010_busca_tipo_mkt"
down_revision = "0009_redirects_curto"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "buscas_ml",
        sa.Column(
            "tipo",
            sa.String(30),
            nullable=False,
            server_default="termo_livre",
        ),
    )
    op.add_column(
        "buscas_ml",
        sa.Column(
            "marketplaces",
            sa.String(200),
            nullable=False,
            server_default='["ml"]',
        ),
    )


def downgrade() -> None:
    op.drop_column("buscas_ml", "marketplaces")
    op.drop_column("buscas_ml", "tipo")

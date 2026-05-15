"""Fase 9.9 — flags de restrição nos planos.

Adiciona 3 booleans na tabela `planos` que controlam o que o user free
pode fazer:

- `pode_cadastrar_afiliado`     — pode salvar `usuario_ml`+senha ML?
- `pode_criar_buscas`           — pode criar/rodar BuscaML?
- `pode_criar_produto_proprio`  — pode criar produto na própria org?

Plano `free` (slug='free') fica com TODAS as flags = false. Outros
planos (pro, business) ficam com TODAS = true. Quem não tem plano
mapeado entra como free por default (`server_default="false"`).

Revision ID: 0005_planos_flags_restricao
Revises:    0004_credenciais_plataformas
Create Date: 2026-05-15
"""
from alembic import op
import sqlalchemy as sa


revision = "0005_planos_flags_restricao"
down_revision = "0004_credenciais_plataformas"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Default em CREATE = false (fica restritivo até admin liberar)
    op.add_column(
        "planos",
        sa.Column(
            "pode_cadastrar_afiliado",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "planos",
        sa.Column(
            "pode_criar_buscas",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "planos",
        sa.Column(
            "pode_criar_produto_proprio",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    # Backfill: planos não-free ganham as 3 flags = true.
    # 'free' é o único plano que fica restritivo. Slug exato pode variar
    # ('free', 'gratis', 'gratuito'); aqui assumimos 'free' (igual ao seed).
    op.execute(
        """
        UPDATE planos
        SET pode_cadastrar_afiliado = true,
            pode_criar_buscas = true,
            pode_criar_produto_proprio = true
        WHERE slug != 'free'
        """
    )


def downgrade() -> None:
    op.drop_column("planos", "pode_criar_produto_proprio")
    op.drop_column("planos", "pode_criar_buscas")
    op.drop_column("planos", "pode_cadastrar_afiliado")

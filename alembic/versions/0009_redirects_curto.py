"""Encurtador próprio — tabela `redirects` (Fase 14).

Substitui a URL longa do marketplace por shortlink próprio
`https://achadinhos.maisseguidores.ia.br/r/{slug}` que faz HTTP 302
pra URL completa com tag de afiliado.

Por que: ML/Shopee/Amazon/etc. não têm API pública pra gerar shortlinks
oficiais. Encurtador próprio resolve com 3 ganhos:
1. URL bonita no WhatsApp (clicks maiores).
2. Comissão continua funcionando (302 final é a URL com tag, ML registra).
3. Métricas: contagem de clicks por produto, sem depender de painel
   externo.

Chave: 1 redirect por (produto). Se tag do user mudar e produto for
postado de novo, o mesmo slug é mantido mas `url_destino` é atualizado.
Slugs antigos no WhatsApp continuam funcionando com tag nova
(redirect é late binding).

Revision ID: 0009_redirects_curto
Revises:    0008_usuarios_afiliados
Create Date: 2026-05-15
"""
from alembic import op
import sqlalchemy as sa


revision = "0009_redirects_curto"
down_revision = "0008_usuarios_afiliados"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "redirects",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.String(20), nullable=False),
        sa.Column("produto_id", sa.Integer(),
                  sa.ForeignKey("produtos.id", ondelete="CASCADE"),
                  nullable=True),
        sa.Column("url_destino", sa.String(2000), nullable=False),
        sa.Column("total_clicks", sa.Integer(), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("ultimo_click_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("slug", name="uq_redirects_slug"),
        sa.UniqueConstraint("produto_id", name="uq_redirects_produto"),
    )
    op.create_index("ix_redirects_slug", "redirects", ["slug"])


def downgrade() -> None:
    op.drop_index("ix_redirects_slug", table_name="redirects")
    op.drop_table("redirects")

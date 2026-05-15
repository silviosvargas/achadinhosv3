"""Tabela `usuarios_afiliados` — N marketplaces por user.

Substitui as colunas mono-marketplace (`usuarios.afiliado_ml`) por uma
tabela normalizada que aceita qualquer plataforma sem migration nova:
ML, Shopee, Amazon, Magalu, AliExpress, TikTok, etc.

Migration:
1. Cria tabela `usuarios_afiliados (usuario_id, plataforma, tag, UNIQUE)`
2. Backfill: copia `usuarios.afiliado_ml` (quando NOT NULL) pra row nova
   `(usuario_id, plataforma='ml', tag=<afiliado_ml>)`.
3. MANTÉM `usuarios.afiliado_ml` por enquanto (compatibilidade) — drop
   numa migration futura quando tiver certeza que nada mais lê dele.
4. Drop dos campos VESTIGIAIS `usuario_ml` e `senha_ml_cifrada` que não
   eram usados por nada (eram pra auto-login do agente — feature abandonada
   por causa do 2FA do ML).

Revision ID: 0008_usuarios_afiliados
Revises:    0007_mappings_nichos_ml
Create Date: 2026-05-15
"""
from alembic import op
import sqlalchemy as sa


revision = "0008_usuarios_afiliados"
down_revision = "0007_mappings_nichos_ml"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "usuarios_afiliados",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("usuario_id", sa.Integer(), sa.ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False),
        sa.Column("plataforma", sa.String(20), nullable=False),
        sa.Column("tag", sa.String(200), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("usuario_id", "plataforma", name="uq_usuarios_afiliados_user_plat"),
    )
    op.create_index(
        "ix_usuarios_afiliados_usuario_id",
        "usuarios_afiliados",
        ["usuario_id"],
    )
    op.create_index(
        "ix_usuarios_afiliados_plataforma",
        "usuarios_afiliados",
        ["plataforma"],
    )

    # Backfill: copia afiliado_ml legacy pra nova tabela.
    op.execute("""
        INSERT INTO usuarios_afiliados (usuario_id, plataforma, tag)
        SELECT id, 'ml', afiliado_ml
        FROM usuarios
        WHERE afiliado_ml IS NOT NULL AND afiliado_ml != ''
        ON CONFLICT (usuario_id, plataforma) DO NOTHING
    """)

    # Drop colunas vestigiais (auto-login do ML — feature abandonada).
    # Mantemos `afiliado_ml` por enquanto pra dual-read durante transição.
    op.drop_column("usuarios", "usuario_ml")
    op.drop_column("usuarios", "senha_ml_cifrada")


def downgrade() -> None:
    op.add_column("usuarios", sa.Column("senha_ml_cifrada", sa.String(500), nullable=True))
    op.add_column("usuarios", sa.Column("usuario_ml", sa.String(150), nullable=True))
    op.drop_index("ix_usuarios_afiliados_plataforma", table_name="usuarios_afiliados")
    op.drop_index("ix_usuarios_afiliados_usuario_id", table_name="usuarios_afiliados")
    op.drop_table("usuarios_afiliados")

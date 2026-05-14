"""
Schema da Fase 4a — produtos isolados por org + templates.

Mudanças:
1. Tabela `produtos`: adiciona org_id (NOT NULL), bloqueio,
   url_afiliado, fonte. Remove campos compartilhados antigos.
   Refaz UNIQUE pra incluir org_id.
2. Cria tabela `produto_nichos` (N:N).
3. Cria tabela `templates_mensagem` (escopada por org).

Revision ID: 0002_produtos_org_e_templates
Revises:    0001_inicial_schema
Create Date: 2026-05-04
"""
from alembic import op
import sqlalchemy as sa


revision = "0002_produtos_org_e_templates"
down_revision = "0001_inicial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── PRODUTOS: ajustes ────────────────────────────────

    # Drop constraint UNIQUE antiga ((plataforma, item_id))
    op.drop_constraint("uq_produtos_chave", "produtos", type_="unique")

    # Adiciona org_id (nullable temporariamente)
    op.add_column("produtos", sa.Column("org_id", sa.Integer, nullable=True))

    # Se já tem produtos no banco, atribui à primeira org existente
    # (em ambiente real seria 1 só admin nesta fase do projeto).
    op.execute(
        "UPDATE produtos SET org_id = (SELECT id FROM organizacoes ORDER BY id LIMIT 1) "
        "WHERE org_id IS NULL"
    )

    # Agora torna NOT NULL e adiciona FK
    op.alter_column("produtos", "org_id", nullable=False)
    op.create_foreign_key(
        "fk_produtos_org",
        "produtos", "organizacoes",
        ["org_id"], ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_produtos_org_id", "produtos", ["org_id"])
    op.create_index("ix_produtos_org_plat", "produtos", ["org_id", "plataforma"])

    # Recria UNIQUE com org_id incluído
    op.create_unique_constraint(
        "uq_produtos_org_chave", "produtos",
        ["org_id", "plataforma", "item_id"],
    )

    # Adiciona colunas novas
    op.add_column("produtos",
        sa.Column("url_afiliado", sa.String(2000), nullable=True))
    op.add_column("produtos",
        sa.Column("bloqueado", sa.Boolean, nullable=False, server_default=sa.false()))
    op.add_column("produtos",
        sa.Column("bloqueado_motivo", sa.String(500), nullable=True))
    op.add_column("produtos",
        sa.Column("fonte", sa.String(50), nullable=True))
    op.add_column("produtos",
        sa.Column("descoberto_em", sa.DateTime(timezone=True), nullable=True))

    op.create_index("ix_produtos_bloqueado", "produtos", ["bloqueado"])

    # Remove colunas antigas que não fazem sentido em modelo isolado
    op.drop_column("produtos", "descoberto_por_org")
    op.drop_column("produtos", "data_descoberto")
    op.drop_column("produtos", "foto_path")

    # ── PRODUTO_NICHOS (N:N) ─────────────────────────────
    op.create_table(
        "produto_nichos",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("produto_id", sa.Integer,
                  sa.ForeignKey("produtos.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("nicho_id", sa.Integer,
                  sa.ForeignKey("nichos.id", ondelete="CASCADE"),
                  nullable=False),
        sa.UniqueConstraint("produto_id", "nicho_id", name="uq_produto_nicho"),
    )
    op.create_index("ix_produto_nichos_produto", "produto_nichos", ["produto_id"])
    op.create_index("ix_produto_nichos_nicho",   "produto_nichos", ["nicho_id"])

    # ── TEMPLATES_MENSAGEM ───────────────────────────────
    op.create_table(
        "templates_mensagem",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("org_id", sa.Integer,
                  sa.ForeignKey("organizacoes.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("nicho_id", sa.Integer,
                  sa.ForeignKey("nichos.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("nome",  sa.String(150), nullable=False),
        sa.Column("texto", sa.String(4096), nullable=False),
        sa.Column("ativo", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("ordem", sa.Integer, nullable=False, server_default="0"),
        sa.Column("vezes_usado", sa.Integer, nullable=False, server_default="0"),
        sa.Column("ultimo_uso_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.Column("atualizado_em", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_templates_org_id",    "templates_mensagem", ["org_id"])
    op.create_index("ix_templates_org_nicho", "templates_mensagem", ["org_id", "nicho_id"])


def downgrade() -> None:
    op.drop_index("ix_templates_org_nicho", "templates_mensagem")
    op.drop_index("ix_templates_org_id",    "templates_mensagem")
    op.drop_table("templates_mensagem")

    op.drop_index("ix_produto_nichos_nicho",   "produto_nichos")
    op.drop_index("ix_produto_nichos_produto", "produto_nichos")
    op.drop_table("produto_nichos")

    # Volta colunas antigas
    op.add_column("produtos",
        sa.Column("data_descoberto", sa.DateTime(timezone=True), nullable=True))
    op.add_column("produtos",
        sa.Column("descoberto_por_org", sa.Integer, nullable=True))
    op.add_column("produtos",
        sa.Column("foto_path", sa.String(500), nullable=True))

    op.drop_index("ix_produtos_bloqueado", "produtos")
    op.drop_column("produtos", "descoberto_em")
    op.drop_column("produtos", "fonte")
    op.drop_column("produtos", "bloqueado_motivo")
    op.drop_column("produtos", "bloqueado")
    op.drop_column("produtos", "url_afiliado")

    op.drop_constraint("uq_produtos_org_chave", "produtos", type_="unique")
    op.drop_index("ix_produtos_org_plat", "produtos")
    op.drop_index("ix_produtos_org_id",   "produtos")
    op.drop_constraint("fk_produtos_org", "produtos", type_="foreignkey")
    op.drop_column("produtos", "org_id")

    op.create_unique_constraint(
        "uq_produtos_chave", "produtos", ["plataforma", "item_id"],
    )

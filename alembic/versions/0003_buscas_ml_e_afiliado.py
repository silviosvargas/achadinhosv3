"""Fase 4b — buscas Mercado Livre + produtos privados de afiliado.

Mudanças:
1. Adiciona `produtos.usuario_dono_id` (FK usuarios, NULL = compartilhado da org).
2. Substitui UNIQUE (org_id, plataforma, item_id) por dois partial unique
   indexes:
   - uq_produtos_publico: válido quando usuario_dono_id IS NULL
   - uq_produtos_privado: inclui usuario_dono_id quando NOT NULL
   Permite mesmo item_id existir como público da org + privado de cada afiliado.
3. Cria tabela `nicho_categoria_ml` (mapping categoria_ml -> nicho_id, por org).
4. Cria tabela `buscas_ml` (config persistida das buscas, com agendamento).

Revision ID: 0003_buscas_ml
Revises:    0002_produtos_org_e_templates
Create Date: 2026-05-14
"""
from alembic import op
import sqlalchemy as sa


revision = "0003_buscas_ml"
down_revision = "0002_produtos_org_e_templates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── PRODUTOS: dono + partial unique indexes ──────────
    op.add_column(
        "produtos",
        sa.Column("usuario_dono_id", sa.Integer, nullable=True),
    )
    op.create_foreign_key(
        "fk_produtos_dono",
        "produtos", "usuarios",
        ["usuario_dono_id"], ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_produtos_dono", "produtos", ["usuario_dono_id"])

    # Substitui UNIQUE antigo por dois partial indexes
    op.drop_constraint("uq_produtos_org_chave", "produtos", type_="unique")
    op.create_index(
        "uq_produtos_publico",
        "produtos",
        ["org_id", "plataforma", "item_id"],
        unique=True,
        postgresql_where=sa.text("usuario_dono_id IS NULL"),
    )
    op.create_index(
        "uq_produtos_privado",
        "produtos",
        ["org_id", "usuario_dono_id", "plataforma", "item_id"],
        unique=True,
        postgresql_where=sa.text("usuario_dono_id IS NOT NULL"),
    )

    # ── NICHO_CATEGORIA_ML (mapping categoria do ML → nicho) ────
    op.create_table(
        "nicho_categoria_ml",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("org_id", sa.Integer,
                  sa.ForeignKey("organizacoes.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("categoria_ml", sa.String(200), nullable=False),
        sa.Column("nicho_id", sa.Integer,
                  sa.ForeignKey("nichos.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("org_id", "categoria_ml",
                            name="uq_nicho_categoria_ml"),
    )
    op.create_index("ix_nicho_categoria_org", "nicho_categoria_ml", ["org_id"])

    # ── BUSCAS_ML ────────────────────────────────────────
    op.create_table(
        "buscas_ml",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("org_id", sa.Integer,
                  sa.ForeignKey("organizacoes.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("criado_por_usuario_id", sa.Integer,
                  sa.ForeignKey("usuarios.id", ondelete="SET NULL")),
        sa.Column("agente_id", sa.Integer,
                  sa.ForeignKey("agentes.id", ondelete="SET NULL"),
                  nullable=True,
                  comment="Agente que executa. Null = qualquer agente da org."),

        sa.Column("nome", sa.String(150), nullable=False),
        sa.Column("entrada", sa.String(2000), nullable=False,
                  comment="Termo livre ou URL completa do ML"),
        sa.Column("max_paginas", sa.Integer, nullable=False, server_default="3"),
        sa.Column("max_produtos", sa.Integer, nullable=False, server_default="50"),

        sa.Column("intervalo_minutos", sa.Integer, nullable=True,
                  comment="Null = só manual; valor = agendamento"),
        sa.Column("ativo", sa.Boolean, nullable=False, server_default=sa.true()),

        # Estado de execução
        sa.Column("ultima_exec_em", sa.DateTime(timezone=True)),
        sa.Column("proxima_exec_em", sa.DateTime(timezone=True)),
        sa.Column("ultima_tarefa_id", sa.Integer,
                  sa.ForeignKey("tarefas.id", ondelete="SET NULL")),
        sa.Column("execucoes", sa.Integer, nullable=False, server_default="0"),

        sa.Column("criado_em", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.Column("atualizado_em", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_buscas_org", "buscas_ml", ["org_id"])
    op.create_index(
        "ix_buscas_agenda",
        "buscas_ml",
        ["ativo", "proxima_exec_em"],
    )


def downgrade() -> None:
    op.drop_index("ix_buscas_agenda", "buscas_ml")
    op.drop_index("ix_buscas_org",    "buscas_ml")
    op.drop_table("buscas_ml")

    op.drop_index("ix_nicho_categoria_org", "nicho_categoria_ml")
    op.drop_table("nicho_categoria_ml")

    op.drop_index("uq_produtos_privado", "produtos")
    op.drop_index("uq_produtos_publico", "produtos")
    op.create_unique_constraint(
        "uq_produtos_org_chave", "produtos",
        ["org_id", "plataforma", "item_id"],
    )

    op.drop_index("ix_produtos_dono", "produtos")
    op.drop_constraint("fk_produtos_dono", "produtos", type_="foreignkey")
    op.drop_column("produtos", "usuario_dono_id")

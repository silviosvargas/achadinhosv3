"""Cria tabela `log_entries` pra logs persistentes do servidor (admin diag).

INFO+ vai pra cá via custom structlog processor + worker batch insert.
TTL futuro (30 dias) via Celery beat task.

Revision ID: 0020_logs
Revises:    0019_super
Create Date: 2026-05-18
"""
from alembic import op
import sqlalchemy as sa


revision = "0020_logs"
down_revision = "0019_super"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "log_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("nivel", sa.String(length=10), nullable=False),
        sa.Column("evento", sa.String(length=120), nullable=True),
        sa.Column("mensagem", sa.Text(), nullable=True),
        sa.Column("contexto", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("source", sa.String(length=20), nullable=False, server_default="server"),
        sa.Column(
            "tarefa_id", sa.Integer(),
            sa.ForeignKey("tarefas.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "org_id", sa.Integer(),
            sa.ForeignKey("organizacoes.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "agente_id", sa.Integer(),
            sa.ForeignKey("agentes.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_log_entries_tarefa_ts", "log_entries", ["tarefa_id", "ts"],
    )
    op.create_index(
        "ix_log_entries_org_ts", "log_entries", ["org_id", "ts"],
    )
    op.create_index(
        "ix_log_entries_ts", "log_entries", ["ts"],
    )
    op.create_index(
        "ix_log_entries_tarefa_id", "log_entries", ["tarefa_id"],
    )
    op.create_index(
        "ix_log_entries_org_id", "log_entries", ["org_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_log_entries_org_id", table_name="log_entries")
    op.drop_index("ix_log_entries_tarefa_id", table_name="log_entries")
    op.drop_index("ix_log_entries_ts", table_name="log_entries")
    op.drop_index("ix_log_entries_org_ts", table_name="log_entries")
    op.drop_index("ix_log_entries_tarefa_ts", table_name="log_entries")
    op.drop_table("log_entries")

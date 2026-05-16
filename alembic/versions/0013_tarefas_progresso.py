"""Progresso de execução nas tarefas (Fase 20).

Adiciona em `tarefas`:
- `progresso_pct`         FLOAT 0..100 (default 0)
- `progresso_mensagem`    STRING(200) nullable
- `progresso_atualizado_em` DATETIME tz nullable

Usado pra UI mostrar barra de progresso em tempo real (polling 3s do
endpoint `/api/v1/tarefas/em-progresso`).

Agente reporta via WS msg `{"tipo":"tarefa_progresso", "tarefa_id", "pct", "mensagem"}`
em checkpoints lógicos (ex: a cada categoria da busca padrão).

Revision ID: 0013_progresso
Revises:    0012_curadoria
Create Date: 2026-05-16
"""
from alembic import op
import sqlalchemy as sa


revision = "0013_progresso"
down_revision = "0012_curadoria"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tarefas", sa.Column(
        "progresso_pct", sa.Float(), nullable=False, server_default="0",
    ))
    op.add_column("tarefas", sa.Column(
        "progresso_mensagem", sa.String(200), nullable=True,
    ))
    op.add_column("tarefas", sa.Column(
        "progresso_atualizado_em", sa.DateTime(timezone=True), nullable=True,
    ))


def downgrade() -> None:
    op.drop_column("tarefas", "progresso_atualizado_em")
    op.drop_column("tarefas", "progresso_mensagem")
    op.drop_column("tarefas", "progresso_pct")

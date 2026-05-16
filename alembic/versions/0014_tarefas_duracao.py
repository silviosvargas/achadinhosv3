"""Duração de execução nas tarefas (Fase 20.2).

Adiciona em `tarefas`:
- `duracao_seg` INT nullable

Preenchido em `dispatcher.marcar_concluida/falhou/cancelar` como
`int((concluido_em - iniciado_em).total_seconds())`. Usado pra:
- UI dashboard mostrar tempo total ao concluir
- Página de relatórios futuros (média, gráfico de duração)

Revision ID: 0014_duracao
Revises:    0013_progresso
Create Date: 2026-05-16
"""
from alembic import op
import sqlalchemy as sa


revision = "0014_duracao"
down_revision = "0013_progresso"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tarefas", sa.Column(
        "duracao_seg", sa.Integer(), nullable=True,
    ))


def downgrade() -> None:
    op.drop_column("tarefas", "duracao_seg")

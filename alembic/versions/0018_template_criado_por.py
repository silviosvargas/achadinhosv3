"""Templates personalizadas — adiciona `criado_por_usuario_id` em
`templates_mensagem` (Fase 17/05/2026 noite).

Pelas regras novas, qualquer user logado cria suas próprias templates.
Esta coluna registra quem criou — usada pra:
- Mostrar "criado por X" nos templates alheios na UI
- Gate de edição/exclusão (dono OU admin central)

Revision ID: 0018_tpl_cpu
Revises:    0017_solic
Create Date: 2026-05-17
"""
from alembic import op
import sqlalchemy as sa


revision = "0018_tpl_cpu"
down_revision = "0017_solic"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "templates_mensagem",
        sa.Column(
            "criado_por_usuario_id",
            sa.Integer(),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_templates_criado_por",
        "templates_mensagem", "usuarios",
        ["criado_por_usuario_id"], ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_templates_criado_por",
        "templates_mensagem",
        ["criado_por_usuario_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_templates_criado_por", table_name="templates_mensagem")
    op.drop_constraint(
        "fk_templates_criado_por",
        "templates_mensagem",
        type_="foreignkey",
    )
    op.drop_column("templates_mensagem", "criado_por_usuario_id")

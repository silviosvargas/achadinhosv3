"""Fila admin pra solicitações de produtos personalizados (Fase C).

Quando cliente cadastra produto novo em `/produtos/personalizados/novo`,
em vez de disparar busca direto no agente DELE (Fase 17), agora vai pra
esta fila. Admin processa via UI ou rotina Celery hourly.

Status:
- pendente:    aguardando processamento (estado inicial)
- processando: tarefa enviada ao agente admin, aguardando ingest
- concluida:   produtos resultantes já no catálogo
- falhou:      erro durante processamento (ver mensagem_erro)
- rejeitada:   admin recusou manualmente

Tipos (espelham `personalizado_service`):
- palavra_chave: busca termo_livre no ML
- url:           busca por_url (extrai 1 produto)
- social:        link TikTok/Insta/YT (IA extrai palavra-chave)

Revision ID: 0017_solic
Revises:    0016_uppp
Create Date: 2026-05-17
"""
from alembic import op
import sqlalchemy as sa


revision = "0017_solic"
down_revision = "0016_uppp"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "solicitacoes_personalizadas",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("usuario_id", sa.Integer(), nullable=False),
        sa.Column("org_id_solicitante", sa.Integer(), nullable=False),
        sa.Column("tipo", sa.String(20), nullable=False),
        sa.Column("entrada", sa.String(500), nullable=False),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="pendente",
        ),
        sa.Column("tarefa_id", sa.Integer(), nullable=True),
        sa.Column("produtos_criados", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("mensagem_erro", sa.String(500), nullable=True),
        sa.Column(
            "criado_em",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("processado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("concluido_em", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["usuario_id"], ["usuarios.id"], ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id_solicitante"], ["organizacoes.id"], ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tarefa_id"], ["tarefas.id"], ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_solic_status",
        "solicitacoes_personalizadas",
        ["status"],
    )
    op.create_index(
        "ix_solic_usuario",
        "solicitacoes_personalizadas",
        ["usuario_id"],
    )
    op.create_index(
        "ix_solic_pendentes",
        "solicitacoes_personalizadas",
        ["status", "criado_em"],
        postgresql_where=sa.text("status = 'pendente'"),
    )


def downgrade() -> None:
    op.drop_index("ix_solic_pendentes", table_name="solicitacoes_personalizadas")
    op.drop_index("ix_solic_usuario", table_name="solicitacoes_personalizadas")
    op.drop_index("ix_solic_status", table_name="solicitacoes_personalizadas")
    op.drop_table("solicitacoes_personalizadas")

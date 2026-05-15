"""Fase 9.9 — índice único partial em agentes (org_id, usuario_id, nome) ativo.

Garante a nível de DB que não existem 2 agentes ATIVOS com a mesma tripla
(org, dono, nome). Trabalha junto com o UPSERT em `agente_service.criar_agente`:
mesmo se houver race condition, o índice impede inserção duplicada.

Estratégia partial (WHERE ativo = true): permite manter histórico de
agentes desativados sem conflitar — só o ativo precisa ser único.

Pré-condição: o DB já não pode ter duplicatas ativas pré-existentes.
Pra evitar falha na criação do índice em prod, faz "merge" antes:
deixa só o mais recente como ativo, marca anteriores como inativos.

Revision ID: 0006_agentes_unique_org_user_nome
Revises:    0005_planos_flags_restricao
Create Date: 2026-05-15
"""
from alembic import op


revision = "0006_agentes_unique_org_user_nome"
down_revision = "0005_planos_flags_restricao"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Limpa duplicatas: mantém só o agente ativo mais recente por tripla.
    # Os mais antigos viram inativos (preservados pra auditoria).
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY org_id, usuario_id, nome
                       ORDER BY criado_em DESC, id DESC
                   ) AS rn
            FROM agentes
            WHERE ativo = true
        )
        UPDATE agentes
        SET ativo = false
        WHERE id IN (SELECT id FROM ranked WHERE rn > 1)
        """
    )

    op.create_index(
        "ux_agentes_org_user_nome_ativo",
        "agentes",
        ["org_id", "usuario_id", "nome"],
        unique=True,
        postgresql_where="ativo = true",
    )


def downgrade() -> None:
    op.drop_index("ux_agentes_org_user_nome_ativo", table_name="agentes")

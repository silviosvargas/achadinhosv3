"""Seed do primeiro super admin estrela (Fase 17/05/2026 noite).

Promove o admin mais antigo da org central (`org_id = 1` na instalação
atual) pra `papel = 'super'`, abrindo a cadeia "só super promove super"
sem precisar de comando manual no DB.

Idempotente: se já existe algum user com `papel='super'`, no-op.

`org_id = 1` é hardcoded pra não importar `app.core.config` em migration
(que precisa rodar em ambientes onde config nem sempre carrega — ex.
ferramenta autogenerate offline). Coincide com `settings.admin_org_id`
default = 1 em dev e prod (org `achadinhos`).

Revision ID: 0019_super
Revises:    0018_tpl_cpu
Create Date: 2026-05-17
"""
from alembic import op


revision = "0019_super"
down_revision = "0018_tpl_cpu"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    ja_tem = bind.exec_driver_sql(
        "SELECT COUNT(*) FROM usuarios WHERE papel = 'super'"
    ).scalar() or 0
    if ja_tem > 0:
        return

    # Promove o admin mais antigo da org central. Se a org não tiver admin
    # (instalação totalmente vazia), no-op silencioso — bootstrap criará
    # o admin depois, e este seed roda na próxima migration (ou ele faz
    # via script `scripts/promover_super.py` futuramente se quiser).
    bind.exec_driver_sql(
        """
        UPDATE usuarios
        SET papel = 'super'
        WHERE id = (
            SELECT id FROM usuarios
            WHERE org_id = 1
              AND papel = 'admin'
              AND ativo = TRUE
            ORDER BY criado_em ASC
            LIMIT 1
        )
        """
    )


def downgrade() -> None:
    # Rebaixa todos os super → admin. Operação destrutiva mas reversível
    # via upgrade subsequente. Não dá pra distinguir quem foi promovido
    # por esta migration vs quem foi promovido depois manualmente.
    bind = op.get_bind()
    bind.exec_driver_sql(
        "UPDATE usuarios SET papel = 'admin' WHERE papel = 'super'"
    )

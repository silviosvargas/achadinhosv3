"""Fase 4b.1 — credenciais de plataformas (login/senha) cifradas em `usuarios`.

Adiciona colunas pra armazenar email/login e senha (cifrada via Fernet) das
plataformas de afiliado. Servidor decifra só ao servir pro agente autorizado
(token de agente). Ver ADR-009 em docs/decisoes.md.

Escopo desta migration: ML apenas. As outras plataformas (Shopee, Amazon,
Magalu, AliExpress) serão adicionadas em migrations futuras se/quando os
handlers de login automático específicos forem implementados.

Revision ID: 0004_credenciais_plataformas
Revises:    0003_buscas_ml
Create Date: 2026-05-14
"""
from alembic import op
import sqlalchemy as sa


revision = "0004_credenciais_plataformas"
down_revision = "0003_buscas_ml"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Credenciais ML — login (plain) + senha (cifrada)
    op.add_column("usuarios", sa.Column("usuario_ml", sa.String(150), nullable=True))
    op.add_column("usuarios", sa.Column("senha_ml_cifrada", sa.String(500), nullable=True))


def downgrade() -> None:
    op.drop_column("usuarios", "senha_ml_cifrada")
    op.drop_column("usuarios", "usuario_ml")

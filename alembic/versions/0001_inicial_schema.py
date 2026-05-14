"""schema inicial v3

Revision ID: 0001_inicial
Revises:
Create Date: 2026-05-01 00:00:00

Cria todas as tabelas da V3 e seeds essenciais (planos default, nichos).
Mantém compatibilidade conceitual com a V2 mas aplica multi-tenancy
(coluna org_id em todas as entidades operacionais).
"""
from collections.abc import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa


revision:      str                              = "0001_inicial"
down_revision: Union[str, None]                 = None
branch_labels: Union[str, Sequence[str], None]  = None
depends_on:    Union[str, Sequence[str], None]  = None


# ============================================================
# UPGRADE
# ============================================================

def upgrade() -> None:
    # ── Planos (catálogo de assinaturas) ─────────────────
    op.create_table(
        "planos",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("slug", sa.String(50), nullable=False, unique=True),
        sa.Column("nome", sa.String(100), nullable=False),
        sa.Column("preco_mensal_brl", sa.Integer, nullable=False, server_default="0"),
        sa.Column("limite_afiliados", sa.Integer, nullable=False, server_default="1"),
        sa.Column("limite_grupos", sa.Integer, nullable=False, server_default="10"),
        sa.Column("limite_postagens_mes", sa.Integer, nullable=False, server_default="500"),
        sa.Column("permite_telegram", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("permite_agendamento", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("ativo", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("criado_em", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.Column("atualizado_em", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
    )

    # ── Organizações (tenants) ───────────────────────────
    op.create_table(
        "organizacoes",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("slug", sa.String(50), nullable=False, unique=True),
        sa.Column("nome", sa.String(150), nullable=False),
        sa.Column("plano_id", sa.Integer, sa.ForeignKey("planos.id"),
                  nullable=False, server_default="1"),
        sa.Column("ativo", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("suspensa_em", sa.DateTime(timezone=True)),
        sa.Column("motivo_suspensao", sa.String(255)),
        sa.Column("criado_em", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.Column("atualizado_em", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_organizacoes_slug", "organizacoes", ["slug"])

    # ── Usuários ─────────────────────────────────────────
    op.create_table(
        "usuarios",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("org_id", sa.Integer,
                  sa.ForeignKey("organizacoes.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("login", sa.String(80), nullable=False),
        sa.Column("senha_hash", sa.String(255), nullable=False),
        sa.Column("papel", sa.String(20), nullable=False, server_default="usuario"),
        sa.Column("nome_exibicao", sa.String(150)),
        sa.Column("email", sa.String(255)),
        sa.Column("ativo", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("afiliado_ml", sa.String(100)),
        sa.Column("afiliado_shopee", sa.String(100)),
        sa.Column("afiliado_amazon", sa.String(100)),
        sa.Column("afiliado_magalu", sa.String(100)),
        sa.Column("afiliado_aliexpress", sa.String(100)),
        sa.Column("limite_postagens_dia", sa.Integer),
        sa.Column("onboarding_completo", sa.Boolean,
                  nullable=False, server_default=sa.false()),
        sa.Column("ultimo_login", sa.DateTime(timezone=True)),
        sa.Column("criado_em", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.Column("atualizado_em", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("org_id", "login", name="uq_usuarios_org_login"),
    )
    op.create_index("ix_usuarios_org_id", "usuarios", ["org_id"])

    # ── Agentes (PCs locais dos afiliados) ───────────────
    op.create_table(
        "agentes",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("org_id", sa.Integer,
                  sa.ForeignKey("organizacoes.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("usuario_id", sa.Integer,
                  sa.ForeignKey("usuarios.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("nome", sa.String(100), nullable=False),
        sa.Column("token_hash", sa.String(255), nullable=False),
        sa.Column("versao_app", sa.String(30)),
        sa.Column("sistema_op", sa.String(50)),
        sa.Column("ativo", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("online", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("ultimo_ping", sa.DateTime(timezone=True)),
        sa.Column("ultimo_ip", sa.String(50)),
        sa.Column("metricas_atuais", sa.JSON, nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("criado_em", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.Column("atualizado_em", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_agentes_org_id", "agentes", ["org_id"])
    op.create_index("ix_agentes_usuario_id", "agentes", ["usuario_id"])

    # ── Canais (whatsapp_agente, telegram_bot) ───────────
    op.create_table(
        "canais",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("org_id", sa.Integer,
                  sa.ForeignKey("organizacoes.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("usuario_id", sa.Integer,
                  sa.ForeignKey("usuarios.id", ondelete="SET NULL")),
        sa.Column("tipo", sa.String(30), nullable=False),
        sa.Column("nome", sa.String(100), nullable=False),
        sa.Column("ativo", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("config", sa.JSON, nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("ultima_postagem_em", sa.DateTime(timezone=True)),
        sa.Column("ultima_falha_em", sa.DateTime(timezone=True)),
        sa.Column("ultima_falha_msg", sa.String(500)),
        sa.Column("criado_em", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.Column("atualizado_em", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_canais_org_id", "canais", ["org_id"])
    op.create_index("ix_canais_tipo", "canais", ["tipo"])
    op.create_index("ix_canais_usuario_id", "canais", ["usuario_id"])

    # ── Catálogo: produtos (compartilhado entre orgs) ────
    op.create_table(
        "produtos",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("plataforma", sa.String(20), nullable=False),
        sa.Column("item_id", sa.String(100), nullable=False),
        sa.Column("nome", sa.String(500), nullable=False),
        sa.Column("categoria", sa.String(200)),
        sa.Column("preco", sa.Float, nullable=False, server_default="0"),
        sa.Column("preco_orig", sa.Float),
        sa.Column("desconto", sa.Float),
        sa.Column("comissao", sa.Float),
        sa.Column("frete_gratis", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("url_canonica", sa.String(2000)),
        sa.Column("foto_url", sa.String(2000)),
        sa.Column("foto_path", sa.String(500)),
        sa.Column("data_descoberto", sa.DateTime(timezone=True)),
        sa.Column("descoberto_por_org", sa.Integer),
        sa.Column("criado_em", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.Column("atualizado_em", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("plataforma", "item_id", name="uq_produtos_chave"),
    )
    op.create_index("ix_produtos_plataforma", "produtos", ["plataforma"])
    op.create_index("ix_produtos_atualizado", "produtos", ["atualizado_em"])

    # ── Nichos (catálogo global) ─────────────────────────
    op.create_table(
        "nichos",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("slug", sa.String(50), nullable=False, unique=True),
        sa.Column("label", sa.String(150), nullable=False),
        sa.Column("icone", sa.String(10)),
        sa.Column("ativo", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("ordem", sa.Integer, nullable=False, server_default="0"),
        sa.Column("criado_em", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.Column("atualizado_em", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_nichos_slug", "nichos", ["slug"])

    # ── Grupos ───────────────────────────────────────────
    op.create_table(
        "grupos",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("org_id", sa.Integer,
                  sa.ForeignKey("organizacoes.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("canal_id", sa.Integer,
                  sa.ForeignKey("canais.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("proprietario_id", sa.Integer,
                  sa.ForeignKey("usuarios.id", ondelete="SET NULL")),
        sa.Column("nome", sa.String(200), nullable=False),
        sa.Column("identificador", sa.String(200), nullable=False),
        sa.Column("ativo", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("precisa_atencao_admin", sa.Boolean,
                  nullable=False, server_default=sa.false()),
        sa.Column("criado_em", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.Column("atualizado_em", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("org_id", "canal_id", "identificador",
                            name="uq_grupos_org_canal_id"),
    )
    op.create_index("ix_grupos_org_id", "grupos", ["org_id"])
    op.create_index("ix_grupos_canal_id", "grupos", ["canal_id"])
    op.create_index("ix_grupos_proprietario_id", "grupos", ["proprietario_id"])

    # ── Grupo↔Nicho (N:N) ────────────────────────────────
    op.create_table(
        "grupo_nichos",
        sa.Column("grupo_id", sa.Integer,
                  sa.ForeignKey("grupos.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("nicho_id", sa.Integer,
                  sa.ForeignKey("nichos.id", ondelete="CASCADE"),
                  primary_key=True),
    )

    # ── Postagens (histórico imutável) ───────────────────
    op.create_table(
        "postagens",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("org_id", sa.Integer,
                  sa.ForeignKey("organizacoes.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("canal_id", sa.Integer,
                  sa.ForeignKey("canais.id", ondelete="SET NULL")),
        sa.Column("grupo_id", sa.Integer,
                  sa.ForeignKey("grupos.id", ondelete="SET NULL")),
        sa.Column("produto_id", sa.Integer,
                  sa.ForeignKey("produtos.id", ondelete="SET NULL")),
        sa.Column("usuario_id", sa.Integer,
                  sa.ForeignKey("usuarios.id", ondelete="SET NULL")),
        sa.Column("grupo_nome", sa.String(200), nullable=False),
        sa.Column("plataforma", sa.String(20), nullable=False),
        sa.Column("item_id", sa.String(100), nullable=False),
        sa.Column("nome_produto", sa.String(500), nullable=False),
        sa.Column("preco_postado", sa.Float, nullable=False),
        sa.Column("canal_tipo", sa.String(30), nullable=False),
        sa.Column("fonte", sa.String(30), nullable=False, server_default="manual"),
        sa.Column("enviado", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("erro", sa.String(500)),
        sa.Column("postado_em", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_postagens_org_id", "postagens", ["org_id"])
    op.create_index("ix_postagens_org_data", "postagens", ["org_id", "postado_em"])
    op.create_index("ix_postagens_chave", "postagens", ["plataforma", "item_id"])

    # ── Tarefas (fila de comandos) ───────────────────────
    op.create_table(
        "tarefas",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("org_id", sa.Integer,
                  sa.ForeignKey("organizacoes.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("tipo", sa.String(50), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="pendente"),
        sa.Column("agente_id", sa.Integer,
                  sa.ForeignKey("agentes.id", ondelete="SET NULL")),
        sa.Column("payload", sa.JSON, nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("resultado", sa.JSON),
        sa.Column("erro", sa.String(1000)),
        sa.Column("iniciado_em", sa.DateTime(timezone=True)),
        sa.Column("concluido_em", sa.DateTime(timezone=True)),
        sa.Column("proxima_tentativa_em", sa.DateTime(timezone=True)),
        sa.Column("tentativas", sa.Integer, nullable=False, server_default="0"),
        sa.Column("max_tentativas", sa.Integer, nullable=False, server_default="3"),
        sa.Column("criado_por_usuario_id", sa.Integer,
                  sa.ForeignKey("usuarios.id", ondelete="SET NULL")),
        sa.Column("criado_em", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.Column("atualizado_em", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_tarefas_org_id", "tarefas", ["org_id"])
    op.create_index("ix_tarefas_status", "tarefas", ["status"])
    op.create_index("ix_tarefas_agente_id", "tarefas", ["agente_id"])
    op.create_index("ix_tarefas_pendentes", "tarefas", ["agente_id", "status"])
    op.create_index("ix_tarefas_org_data", "tarefas", ["org_id", "criado_em"])

    # ============================================================
    # SEEDS — dados iniciais
    # ============================================================
    _seed_planos()
    _seed_nichos()


def _seed_planos() -> None:
    """3 planos default: free, pro, business."""
    op.bulk_insert(
        sa.table(
            "planos",
            sa.column("id", sa.Integer),
            sa.column("slug", sa.String),
            sa.column("nome", sa.String),
            sa.column("preco_mensal_brl", sa.Integer),
            sa.column("limite_afiliados", sa.Integer),
            sa.column("limite_grupos", sa.Integer),
            sa.column("limite_postagens_mes", sa.Integer),
            sa.column("permite_telegram", sa.Boolean),
            sa.column("permite_agendamento", sa.Boolean),
            sa.column("ativo", sa.Boolean),
        ),
        [
            {"id": 1, "slug": "free", "nome": "Grátis",
             "preco_mensal_brl": 0,
             "limite_afiliados": 1, "limite_grupos": 5, "limite_postagens_mes": 100,
             "permite_telegram": True, "permite_agendamento": False, "ativo": True},
            {"id": 2, "slug": "pro", "nome": "Pro",
             "preco_mensal_brl": 4990,           # R$ 49,90
             "limite_afiliados": 5, "limite_grupos": 50, "limite_postagens_mes": 5000,
             "permite_telegram": True, "permite_agendamento": True, "ativo": True},
            {"id": 3, "slug": "business", "nome": "Business",
             "preco_mensal_brl": 19990,          # R$ 199,90
             "limite_afiliados": 20, "limite_grupos": 200, "limite_postagens_mes": 50000,
             "permite_telegram": True, "permite_agendamento": True, "ativo": True},
        ],
    )


def _seed_nichos() -> None:
    """16 nichos canônicos (vindos da V2 — src/nichos/categorias.py)."""
    op.bulk_insert(
        sa.table(
            "nichos",
            sa.column("slug", sa.String),
            sa.column("label", sa.String),
            sa.column("icone", sa.String),
            sa.column("ativo", sa.Boolean),
            sa.column("ordem", sa.Integer),
        ),
        [
            {"slug": "beleza",          "label": "Beleza & Cosméticos",  "icone": "💄", "ativo": True, "ordem": 1},
            {"slug": "moda_feminina",   "label": "Moda Feminina",        "icone": "👗", "ativo": True, "ordem": 2},
            {"slug": "moda_masculina",  "label": "Moda Masculina",       "icone": "👔", "ativo": True, "ordem": 3},
            {"slug": "casa_decoracao",  "label": "Casa & Decoração",     "icone": "🏠", "ativo": True, "ordem": 4},
            {"slug": "cozinha",         "label": "Cozinha",              "icone": "🍳", "ativo": True, "ordem": 5},
            {"slug": "eletrodomesticos", "label": "Eletrodomésticos",    "icone": "🔌", "ativo": True, "ordem": 6},
            {"slug": "tecnologia",      "label": "Tecnologia",           "icone": "💻", "ativo": True, "ordem": 7},
            {"slug": "construcao",      "label": "Construção",           "icone": "🔨", "ativo": True, "ordem": 8},
            {"slug": "agro",            "label": "Agro",                 "icone": "🌾", "ativo": True, "ordem": 9},
            {"slug": "fitness",         "label": "Fitness & Esporte",    "icone": "🏋️", "ativo": True, "ordem": 10},
            {"slug": "bebe_infantil",   "label": "Bebê & Infantil",      "icone": "🍼", "ativo": True, "ordem": 11},
            {"slug": "pet",             "label": "Pet",                  "icone": "🐶", "ativo": True, "ordem": 12},
            {"slug": "auto_moto",       "label": "Automotivo",           "icone": "🚗", "ativo": True, "ordem": 13},
            {"slug": "escritorio",      "label": "Escritório",           "icone": "📎", "ativo": True, "ordem": 14},
            {"slug": "saude",           "label": "Saúde & Bem-estar",    "icone": "💊", "ativo": True, "ordem": 15},
            {"slug": "jardim_quintal",  "label": "Jardim & Quintal",     "icone": "🌳", "ativo": True, "ordem": 16},
        ],
    )


# ============================================================
# DOWNGRADE
# ============================================================

def downgrade() -> None:
    # Ordem inversa pra respeitar FKs
    op.drop_table("tarefas")
    op.drop_table("postagens")
    op.drop_table("grupo_nichos")
    op.drop_table("grupos")
    op.drop_table("nichos")
    op.drop_table("produtos")
    op.drop_table("canais")
    op.drop_table("agentes")
    op.drop_table("usuarios")
    op.drop_table("organizacoes")
    op.drop_table("planos")

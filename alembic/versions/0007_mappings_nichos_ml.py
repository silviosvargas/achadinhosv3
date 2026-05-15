"""Fase 9.9 hotfix — seed mappings categoria_ml → nicho + backfill produtos.

Auto-classificação no ingest do agente (busca_service.ingerir_produtos)
JÁ existia: ele lê `nicho_categoria_ml` da org e atrela nicho ao produto
via match exato ou prefixo. Mas a tabela estava vazia pra org admin,
então os 50 produtos importados pela primeira busca em prod ficaram
SEM nicho — e o lote `produtos_elegiveis` exige nicho → resultado 0.

Esta migration:
1. Insere mappings padrão pra org admin (settings.admin_org_id=1)
   cobrindo as ~16 categorias raiz mais comuns do ML (slug minúsculo
   pra bater com `busca_service.py:196` que faz `.lower()`).
2. Backfilla produtos existentes da org 1: pra cada produto sem nicho
   que tem `categoria` preenchida, tenta match (exato ou prefixo) e
   insere em `produto_nichos`.

Idempotente: usa ON CONFLICT DO NOTHING. Pode rodar 2 vezes sem efeito
duplicado.

Pré-condição: nichos 1..16 já existem (inseridos pelo seed inicial
0001_inicial_schema.py). IDs alinhados:
  1 beleza · 2 moda_feminina · 3 moda_masculina · 4 casa_decoracao
  5 cozinha · 6 eletrodomesticos · 7 tecnologia · 8 construcao
  9 agro · 10 fitness · 11 bebe_infantil · 12 pet · 13 auto_moto
  14 escritorio · 15 saude · 16 jardim_quintal

Revision ID: 0007_mappings_nichos_ml
Revises:    0006_agentes_unique
Create Date: 2026-05-15
"""
from alembic import op


revision = "0007_mappings_nichos_ml"
down_revision = "0006_agentes_unique"
branch_labels = None
depends_on = None


# Mappings categoria-raiz-ML → nicho_id. Tudo lowercase porque o lookup
# em busca_service.py:196 normaliza com `.lower()`.
MAPPINGS_ORG_ADMIN = [
    # Tecnologia
    ("celulares e telefones",              7),
    ("informática",                        7),
    ("eletrônicos, áudio e vídeo",         7),
    ("games",                              7),
    # Casa / cozinha
    ("casa, móveis e decoração",           4),
    ("eletrodomésticos",                   6),
    # Moda
    ("calçados, roupas e bolsas",          3),
    ("roupas, calçados e acessórios",      3),
    ("acessórios de moda",                 2),
    ("relógios e joias",                   2),
    # Beleza / saúde
    ("beleza e cuidado pessoal",           1),
    ("saúde",                              15),
    # Esportes
    ("esportes e fitness",                 10),
    # Pet / bebê
    ("animais",                            12),
    ("bebês",                              11),
    ("brinquedos e hobbies",               11),
    # Veículos / construção / agro
    ("acessórios para veículos",           13),
    ("ferramentas",                        8),
    ("agro",                               9),
    ("indústria e comércio",               14),
    # Jardim
    ("ar livre, jardim e piscinas",        16),
]


def upgrade() -> None:
    # 1. Insere mappings na org admin (id=1). Idempotente via ON CONFLICT.
    values_sql = ", ".join(
        f"(1, '{cat.replace(chr(39), chr(39)+chr(39))}', {nid})"
        for cat, nid in MAPPINGS_ORG_ADMIN
    )
    op.execute(f"""
        INSERT INTO nicho_categoria_ml (org_id, categoria_ml, nicho_id)
        VALUES {values_sql}
        ON CONFLICT (org_id, categoria_ml) DO NOTHING
    """)

    # 2. Backfill produto_nichos pra produtos da org 1 que ainda não têm
    #    nicho. Match: exato primeiro, depois prefixo (igual busca_service.py).
    #    Faz tudo em UMA query usando LEFT JOIN no mapping + filtro.
    op.execute("""
        INSERT INTO produto_nichos (produto_id, nicho_id)
        SELECT DISTINCT p.id, m.nicho_id
        FROM produtos p
        JOIN nicho_categoria_ml m
          ON m.org_id = p.org_id
         AND (
              LOWER(p.categoria) = LOWER(m.categoria_ml)
           OR LOWER(p.categoria) LIKE LOWER(m.categoria_ml) || ' >%'
           OR LOWER(p.categoria) LIKE LOWER(m.categoria_ml) || '%'
         )
        WHERE p.org_id = 1
          AND p.categoria IS NOT NULL
          AND p.categoria != ''
          AND NOT EXISTS (
              SELECT 1 FROM produto_nichos pn WHERE pn.produto_id = p.id
          )
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    # Remove só os mappings que esta migration inseriu (categoria_ml exato).
    cats = ", ".join(
        f"'{cat.replace(chr(39), chr(39)+chr(39))}'"
        for cat, _ in MAPPINGS_ORG_ADMIN
    )
    op.execute(f"""
        DELETE FROM nicho_categoria_ml
        WHERE org_id = 1 AND categoria_ml IN ({cats})
    """)
    # Backfill em produtos não dá pra reverter cirurgicamente — deixa stay.

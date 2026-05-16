"""
Tabela de comissão Mercado Livre por categoria — estimativa atualizada 2026.

Quando o `linkbuilder_ml.py` (agente) não consegue capturar a comissão real
do painel ML (DOM mudou, sessão expirou, ou produto não exibe a %), o
servidor usa essa tabela como FALLBACK INTELIGENTE durante o ingest.

Match hierárquico case-insensitive: a `categoria` do produto vem como path
estilo "Eletrodomésticos > Pequenos Eletrodomésticos > Liquidificadores" —
procura cada chave do dict como SUBSTRING. Ordem importa: chaves mais
específicas vêm ANTES (matches primeiro).

Fonte: tabela ML afiliados BR aproximada (valores médios). Promoções do
programa "Mais por Mais" podem dobrar temporariamente — capturar o valor
REAL do painel via `comissao_fonte="ml_painel"` é sempre preferível.

⚠ Por que essa tabela existe:
A tabela hardcoded no agente (`CATEGORIAS_MAIS_VENDIDOS` em busca_ml.py)
só tem 8 entradas e é otimista demais:
- Casa = 10% (real é ~8%)
- Eletrônicos = 8% (real é ~5%)
- Ferramentas = 8% (real é ~6-7%)
- Pequenos Eletrodomésticos: nem existe lá → caía em 8% genérico

Resultado: Liquidificador Mondial chegava com 10% (Casa), Percarbonato com 12%
(quando real é ~8% pra Limpeza). Aqui temos ~50 chaves mapeadas com
valores mais próximos do real.
"""
from __future__ import annotations


# Pares (chave_lower_substring, comissao_pct).
# **Ordem importa**: chave mais específica antes da mais genérica.
# Em runtime, primeira chave que aparece como substring na categoria do
# produto define a comissão. Sem match → None (caller usa estimativa antiga).
COMISSOES_ML_POR_CATEGORIA: list[tuple[str, float]] = [
    # ── Alta comissão (10-12%) ─────────────────────────────────
    ("suplementos alimentares",        12.0),
    ("suplementos",                    12.0),
    ("beleza e cuidado pessoal",       12.0),
    ("cuidado da pele",                12.0),
    ("perfumaria",                     12.0),
    ("cabelos",                        12.0),
    ("maquiagem",                      12.0),
    ("esportes e fitness",             12.0),
    ("moda esportiva",                 12.0),
    ("calçados, roupas e bolsas",      12.0),
    ("calçados",                       12.0),
    ("roupas",                         12.0),
    ("bolsas e acessórios",            12.0),
    ("saúde",                          10.0),
    ("bebês",                          10.0),
    ("alimentação para bebês",         10.0),

    # ── Média comissão (8%) ────────────────────────────────────
    ("limpeza doméstica",               8.0),
    ("limpeza",                         8.0),
    ("casa, móveis e decoração",        8.0),
    ("móveis para casa",                8.0),
    ("decoração para casa",             8.0),
    ("animais e pets",                  8.0),
    ("pet shop",                        8.0),
    ("brinquedos e hobbies",            8.0),
    ("brinquedos",                      8.0),
    ("joias e relógios",                8.0),
    ("relógios",                        8.0),
    ("festas e lembrancinhas",          8.0),
    ("artigos para festas",             8.0),

    # ── Média-baixa (6-7%) ─────────────────────────────────────
    ("ferramentas",                     6.0),
    ("construção",                      6.0),
    ("instrumentos musicais",           6.0),
    ("agro",                            6.0),
    ("indústria e comércio",            6.0),

    # ── Baixa comissão (4-5%) ──────────────────────────────────
    ("pequenos eletrodomésticos",       5.0),
    ("câmeras e acessórios",            5.0),
    ("informática",                     5.0),
    ("celulares e telefones",           5.0),
    ("games",                           5.0),
    ("livros, revistas e comics",       5.0),
    ("música, filmes e seriados",       5.0),
    ("eletrônicos, áudio e vídeo",      5.0),
    ("áudio",                           5.0),
    ("eletrodomésticos",                4.0),
    ("ar e ventilação",                 4.0),
    ("alimentos e bebidas",             4.0),
    ("carros, motos e outros",          4.0),
    ("imóveis",                         4.0),
    ("antiguidades e coleções",         4.0),
]


def estimar_comissao_ml_categoria(categoria: str | None) -> float | None:
    """Match hierárquico case-insensitive na categoria do produto ML.

    Args:
        categoria: path completo tipo "Eletrodomésticos > Pequenos
                   Eletrodomésticos > Liquidificadores" (vem do ingest).

    Returns:
        % de comissão (float) ou None se nenhuma chave bate.

    Estratégia:
    - Normaliza categoria pra lowercase
    - Itera lista NA ORDEM (mais específico primeiro)
    - Primeiro `chave in cat_lower` que casa → retorna

    A ordem garante que "pequenos eletrodomésticos" (5%) bate ANTES de
    "eletrodomésticos" (4%) — produto path como "Eletrodomésticos > Pequenos
    Eletrodomésticos > Liquidificador" recebe 5%, não 4%.
    """
    if not categoria:
        return None
    cat_lower = categoria.lower()
    for chave, pct in COMISSOES_ML_POR_CATEGORIA:
        if chave in cat_lower:
            return pct
    return None

"""
Validação de comissão por marketplace (Fase 18).

Cada marketplace tem um RANGE razoável de comissão. Valores fora do range
indicam:
- Bug no scraper (parseou unidade errada, ex: pegou "5.50" como 550%)
- Painel ML pifou e retornou "0%" pra todos (sessão expirada)
- Produto novo sem comissão definida pela plataforma

Quando `comissao_validada = False`:
- `score_comissao` da nota vira 0 (não premia comissão suspeita)
- UI mostra badge "⚠ comissão suspeita" pro admin revisar
- O produto NÃO é bloqueado — só perde a parcela de comissão da nota

Fontes oficiais:
- Mercado Livre Afiliados: 0,5% a 25% (variável por categoria, com bumps temporários)
- Shopee Afiliados BR: 0,5% a 30% (varia muito, "open deals" chegam a 30%)
- Amazon Associates BR: 1% (eletrônicos high-end) a 10-12% (moda, beleza)
  Tabela: https://afiliados.amazon.com.br/help/operating/schedule
"""
from __future__ import annotations


# Ranges (min%, max%) considerados normais por marketplace.
# Floor > 0 captura bugs comuns ("0%" indica painel pifou).
# Ceiling permite folga acima da tabela oficial (promoções temporárias).
RANGES_VALIDOS: dict[str, tuple[float, float]] = {
    "ml":     (0.5, 25.0),
    "shopee": (0.5, 30.0),
    "amazon": (1.0, 12.0),
    # Marketplaces ainda sem scraper — ranges em prep
    "magalu":     (0.5, 15.0),
    "aliexpress": (1.0, 30.0),
    "tiktok":     (0.5, 30.0),
}


# Comissão de fallback (média segura) quando produto não tem comissao
# definida. Igual à V2.
COMISSAO_FALLBACK_PCT = 8.0


def validar_comissao(plataforma: str, comissao_pct: float | None) -> bool:
    """
    Retorna True se a comissão cai no range esperado pra plataforma.

    None / 0 / fora do range → False.

    Plataforma desconhecida → True por default (não bloqueia produtos
    de marketplaces ainda sem range definido aqui).
    """
    if comissao_pct is None or comissao_pct <= 0:
        return False
    plat = (plataforma or "").lower()
    if plat not in RANGES_VALIDOS:
        return True
    minimo, maximo = RANGES_VALIDOS[plat]
    return minimo <= comissao_pct <= maximo


def fonte_default(plataforma: str) -> str:
    """
    Fonte da comissão pra um produto recém-criado sem `comissao_fonte`
    explícito. Cada scraper deve sobrescrever isso quando capturar o
    valor REAL (linkbuilder ML, API Shopee, etc).
    """
    return "estimativa"

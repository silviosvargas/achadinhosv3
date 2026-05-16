"""
Cálculo da nota de curadoria + validação de comissão (Fase 18).

Função PURA — input dict, output dict. Sem dependência de DB, sem efeitos
colaterais. Roda no ingest (`busca_service._upsert_produto`) e no recálculo
em massa (`POST /api/v1/curadoria/recalcular-notas`).

### Fórmula da nota (0-100)

score_preco    = 0..30   (desconto: ≥30% → 30; 15-29% → 20; 1-14% → 10; 0% → 0)
score_comissao = 0..40   (comissao/15 × 40, cap 40; **ZERO se !comissao_validada**)
score_vendas   = 0..30   (bestseller → +20; em_alta → +10;
                          total_vendidos: log10(v+1) × 10, cap 30)

**Pesos**: comissão pesa mais (40%) porque é o que vira receita pra você.
Preço (30%) é o gancho do click. Vendas (30%) é qualidade do produto.

### Por que score_comissao = 0 quando !validada

Comissão fora do range esperado (ex: ML retornou 0% porque sessão expirou,
ou bug parseou unidade errada) indica DADO RUIM. Premiar a comissão nesse
caso enviesa a curadoria. Melhor zerar a parcela e o produto compete só
em preço + vendas.

### Como usar

    from app.services.scoring import calcular_nota, validar_comissao_real

    nota_info = calcular_nota({
        "plataforma":    "ml",
        "preco":         99.90,
        "preco_orig":    159.90,
        "desconto":      37.5,
        "comissao":      14.0,
        "comissao_fonte":"ml_painel",   # ou "estimativa", "shopee_api", "amazon_tabela"
        "total_vendidos":5000,
        "is_bestseller": True,
        "is_em_alta":    False,
    })
    # → {"nota": 87.0, "score_preco": 30, "score_comissao": 37.3,
    #    "score_vendas": 20, "comissao_validada": True}
"""
from __future__ import annotations

import math
from typing import Any

from app.core.comissoes import RANGES_VALIDOS, validar_comissao


# ── Constantes da fórmula (peso máximo por dimensão) ─────────
PESO_PRECO    = 30.0
PESO_COMISSAO = 40.0
PESO_VENDAS   = 30.0

# Comissão "ótima" — atinge 100% do peso_comissao com esse valor
COMISSAO_OTIMA_PCT = 15.0

# Bonificações específicas do score_vendas
BONUS_BESTSELLER = 20.0    # se is_bestseller=True
BONUS_EM_ALTA    = 10.0    # se is_em_alta=True

# log10(total_vendidos + 1) × esse fator → cap em PESO_VENDAS
FATOR_LOG_VENDAS = 10.0


def _score_preco(desconto_pct: float | None) -> float:
    """0..30 baseado no % de desconto."""
    if desconto_pct is None or desconto_pct <= 0:
        return 0.0
    if desconto_pct >= 30:
        return 30.0
    if desconto_pct >= 15:
        return 20.0
    return 10.0


def _score_comissao(comissao_pct: float | None, validada: bool) -> float:
    """0..40 proporcional à comissão, MAS zera se !validada."""
    if not validada or comissao_pct is None or comissao_pct <= 0:
        return 0.0
    # Linear até COMISSAO_OTIMA_PCT, depois cap em PESO_COMISSAO
    proporcao = min(comissao_pct / COMISSAO_OTIMA_PCT, 1.0)
    return round(proporcao * PESO_COMISSAO, 2)


def _score_vendas(
    *,
    total_vendidos: int | None,
    is_bestseller: bool,
    is_em_alta: bool,
) -> float:
    """0..30 a partir de flags + total absoluto.

    Estratégia: usa o MAIOR sinal disponível.
    - Se temos total_vendidos: log10(v+1) × 10, cap 30
    - Se não, usa flags: bestseller=+20, em_alta=+10
    - Pode somar: bestseller=+20 E v=1000 → ainda cap em 30 (não duplica)
    """
    score = 0.0
    if total_vendidos and total_vendidos > 0:
        score = min(math.log10(total_vendidos + 1) * FATOR_LOG_VENDAS, PESO_VENDAS)
    # Flags adicionam mas respeitando cap
    bonus_flags = 0.0
    if is_bestseller:
        bonus_flags += BONUS_BESTSELLER
    if is_em_alta:
        bonus_flags += BONUS_EM_ALTA
    score = min(score + bonus_flags, PESO_VENDAS)
    return round(score, 2)


def calcular_nota(produto: dict[str, Any]) -> dict[str, Any]:
    """
    Calcula a nota completa de um produto.

    Args:
        produto: dict com chaves opcionais:
            plataforma, preco, preco_orig, desconto, comissao,
            total_vendidos, is_bestseller, is_em_alta

    Returns:
        {
            "nota":              float 0..100,
            "score_preco":       float 0..30,
            "score_comissao":    float 0..40,
            "score_vendas":      float 0..30,
            "comissao_validada": bool,
        }
    """
    plataforma = (produto.get("plataforma") or "").lower()
    comissao   = produto.get("comissao")
    desconto   = produto.get("desconto")

    # Se desconto não vem mas preço original > preço, infere
    if desconto is None:
        preco      = produto.get("preco") or 0
        preco_orig = produto.get("preco_orig") or 0
        if preco_orig > preco > 0:
            desconto = (preco_orig - preco) / preco_orig * 100

    validada = validar_comissao(plataforma, comissao)

    sp = _score_preco(desconto)
    sc = _score_comissao(comissao, validada)
    sv = _score_vendas(
        total_vendidos=produto.get("total_vendidos"),
        is_bestseller=bool(produto.get("is_bestseller")),
        is_em_alta=bool(produto.get("is_em_alta")),
    )

    return {
        "nota":              round(sp + sc + sv, 2),
        "score_preco":       sp,
        "score_comissao":    sc,
        "score_vendas":      sv,
        "comissao_validada": validada,
    }


# ── Helpers expostos pra uso direto fora do scoring ──────────

def validar_comissao_real(plataforma: str, comissao_pct: float | None) -> bool:
    """Re-export pra quem importa só o validador."""
    return validar_comissao(plataforma, comissao_pct)


def ranges_por_marketplace() -> dict[str, tuple[float, float]]:
    """Re-export — útil pra UI exibir 'esperado: 0.5%–25%'."""
    return dict(RANGES_VALIDOS)

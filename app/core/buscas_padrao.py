"""
Buscas PADRÃO do sistema (Fase 19).

Fixadas no código (sem tabela de DB) porque:
- Não precisam ser editáveis por org (são oficiais do sistema)
- Versionadas com o código → atualizar via deploy
- Aparecem numa seção FIXA no topo de `/buscas` (separadas das custom)

Cada busca padrão é um dict com slug, nome, descricao e parâmetros que
o servidor passa ao agente via Tarefa(BUSCAR_MERCADO_LIVRE) com
`tipo_busca` dedicado.

Quando agente recebe `tipo_busca=padrao_mais_vendidos_completo`:
1. Itera categorias mais vendidas do ML (hardcoded no agente também)
2. Pra cada categoria: extrai N candidatos, gera meli.la, abre cada
   link → /social/ → clica "Ir para produto" → captura comissão REAL
   + preço REAL na barra preta
3. Ordena candidatos por `preço × comissão_real` DESC
4. Mantém top 10 por categoria
5. Ingest com `comissao_fonte=ml_barra_afiliados`
"""
from __future__ import annotations


BUSCAS_PADRAO: list[dict] = [
    {
        "slug":         "ml_mais_vendidos_completo",
        "nome":         "🛒 Mercado Livre — Mais vendidos por categoria",
        "descricao":    (
            "Top 10 mais vendidos de cada categoria do ML, filtrados pela "
            "COMBINAÇÃO de melhor preço + maior comissão REAL (capturada "
            "da barra preta de afiliados, não estimativa). "
            "Pega ~20 candidatos por categoria, abre cada um pelo meli.la "
            "pra extrair comissão real, mantém os 10 melhores. "
            "Demora ~8min por execução."
        ),
        "marketplaces": ["ml"],
        "tipo_busca":   "padrao_mais_vendidos_completo",
        "max_produtos": 80,    # 8 categorias × 10 finais
        "candidatos_por_categoria": 20,
        "ordem":        1,
        "ativa":        True,
    },
    # Futuras: Shopee mais ofertas, Amazon bestsellers, etc.
]


def buscar_por_slug(slug: str) -> dict | None:
    """Retorna busca padrão pelo slug, ou None."""
    for b in BUSCAS_PADRAO:
        if b["slug"] == slug:
            return b
    return None


def listar_ativas() -> list[dict]:
    """Lista ordenada por `ordem`, só ativas."""
    return sorted(
        [b for b in BUSCAS_PADRAO if b["ativa"]],
        key=lambda b: b["ordem"],
    )

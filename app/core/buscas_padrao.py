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
            "Top 10 por categoria do ML, filtrados pela COMBINAÇÃO de melhor "
            "preço + maior comissão REAL (capturada da barra preta de afiliados). "
            "v3.7.0: agora abre URL canônica DIRETO (sem passar pelo meli.la). "
            "Pega ~30 candidatos por categoria, abre cada um, captura comissão "
            "+ preço da barra, descarta produtos onde captura falha, mantém os "
            "10 melhores. Demora ~6min (era ~12min com fluxo antigo)."
        ),
        "marketplaces": ["ml"],
        "tipo_busca":   "padrao_mais_vendidos_completo",
        "max_produtos": 80,    # 8 categorias × 10 finais
        # v3.5.1: aumentado de 20 → 30 pra compensar descartes (produtos
        # onde captura da barra falhou são DESCARTADOS, não vão pro catálogo
        # com estimativa errada). Folga: 30 candidatos → idealmente 10 com
        # captura ok → top 10 final.
        "candidatos_por_categoria": 30,
        "ordem":        1,
        "ativa":        True,
    },
    {
        "slug":         "ml_comissao_extra",
        "nome":         "🎁 Mercado Livre — Top 10 com comissão EXTRA",
        "descricao":    (
            "Itera as mesmas categorias mais vendidas, mas mantém SOMENTE "
            "produtos com bônus 'GANHOS EXTRAS' (promoção Mais por Mais ML — "
            "comissão temporariamente alta). Para de buscar ao juntar 10 com "
            "extras. Marca `comissao_extra` no DB pra filtrar depois. ~3min."
        ),
        "marketplaces": ["ml"],
        "tipo_busca":   "padrao_comissao_extra",
        "max_produtos": 10,
        "candidatos_por_categoria": 30,
        "alvo_total":   10,    # para ao juntar N com extras
        "ordem":        2,
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

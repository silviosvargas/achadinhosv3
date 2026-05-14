"""
Engine de templates de mensagem.

Portado de V2/src/postar/templates_engine.py com adaptações:
- Multi-tenant: busca templates da org, não global.
- Sem dependência de IA (placeholder pra Fase 4d).
- Sem rotação complexa (apenas escolha aleatória entre ativos do nicho).

Fluxo:
1. selecionar_template(produto) — escolhe um template do nicho do produto.
2. renderizar(template, produto, org_tag) — substitui placeholders.

Placeholders suportados:
  {nome}            nome do produto
  {preco}           "R$ 89,90"
  {preco_orig}      "R$ 159,00" (riscado, se houver)
  {desconto}        "44%" (se houver)
  {bloco_preco}     "De ~R$ 159,00~ por R$ 89,90 (44% OFF)"
  {plataforma}      "Mercado Livre" / "Shopee" / etc
  {url}             URL afiliada
  {chamada}         frase aleatória ("Corre que vai esgotar!")
  {chamada_emoji}   emoji aleatório de chamada
"""
from __future__ import annotations

import random
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import Produto, ProdutoNicho, TemplateMensagem

log = get_logger(__name__)


# ============================================================
# Constantes — banco de frases (portadas da V2)
# ============================================================

CHAMADAS = [
    "Corre que vai esgotar!",
    "Oferta por tempo limitado!",
    "Garante o seu agora!",
    "Poucos em estoque!",
    "Aproveita antes que acabe!",
    "Não perca essa chance!",
    "Vai voar do estoque!",
    "Aproveita essa oportunidade!",
]

CHAMADAS_EMOJI = ["🔥", "⚡", "💥", "🎯", "✨", "💎", "🚀", "👀"]

PLATAFORMA_LABEL = {
    "ml":         "Mercado Livre",
    "shopee":     "Shopee",
    "amazon":     "Amazon",
    "magalu":     "Magalu",
    "aliexpress": "AliExpress",
}


# ============================================================
# Renderização de placeholders
# ============================================================

def formatar_preco(valor: float | None) -> str:
    """Formata float pra 'R$ 89,90' (BRL)."""
    if valor is None or valor <= 0:
        return ""
    return f"R$ {valor:.2f}".replace(".", ",")


def montar_bloco_preco(produto: Produto) -> str:
    """
    Bloco completo de preço — escolhe formato baseado no que tem:
    - Tem preco_orig + desconto: "De ~R$ 159,00~ por R$ 89,90 (44% OFF)"
    - Tem só preco_orig:         "De ~R$ 159,00~ por R$ 89,90"
    - Só preco:                  "R$ 89,90"
    """
    preco_str = formatar_preco(produto.preco)
    if not preco_str:
        return ""

    if produto.preco_orig and produto.preco_orig > produto.preco:
        orig_str = formatar_preco(produto.preco_orig)
        if produto.desconto:
            return f"De ~{orig_str}~ por {preco_str} ({int(produto.desconto)}% OFF)"
        return f"De ~{orig_str}~ por {preco_str}"

    return preco_str


def renderizar(
    template: TemplateMensagem | str,
    produto: Produto,
    *,
    url_override: str | None = None,
) -> str:
    """
    Substitui placeholders no texto. Retorna texto pronto pra postar.

    `template` pode ser TemplateMensagem ou string (fallback se nicho não tem template).
    """
    if isinstance(template, TemplateMensagem):
        texto = template.texto
    else:
        texto = template

    # Escolhas aleatórias (uma por chamada de renderização)
    chamada = random.choice(CHAMADAS)
    chamada_emoji = random.choice(CHAMADAS_EMOJI)

    # URL: override > url_afiliado > url_canonica > vazio
    url = url_override or produto.url_afiliado or produto.url_canonica or ""

    # Mapa de substituições
    subst = {
        "{nome}":          produto.nome or "",
        "{preco}":         formatar_preco(produto.preco),
        "{preco_orig}":    formatar_preco(produto.preco_orig),
        "{desconto}":      f"{int(produto.desconto)}%" if produto.desconto else "",
        "{bloco_preco}":   montar_bloco_preco(produto),
        "{plataforma}":    PLATAFORMA_LABEL.get(produto.plataforma, produto.plataforma),
        "{url}":           url,
        "{chamada}":       chamada,
        "{chamada_emoji}": chamada_emoji,
    }

    for placeholder, valor in subst.items():
        texto = texto.replace(placeholder, valor)

    # Limpa linhas vazias consecutivas (acontece quando placeholder é "")
    texto = re.sub(r"\n{3,}", "\n\n", texto.strip())
    return texto


# ============================================================
# Seleção de template
# ============================================================

async def selecionar_template(
    db: AsyncSession,
    *,
    org_id: int,
    nicho_ids: list[int],
) -> TemplateMensagem | None:
    """
    Escolhe um template ativo da org pra um produto com `nicho_ids`.

    Estratégia:
    1. Busca templates ativos da org cujo nicho_id está em `nicho_ids`.
    2. Se houver: escolhe aleatoriamente.
    3. Se não houver: usa template padrão (nicho_id IS NULL).
    4. Se também não houver: retorna None (chamador usa fallback hardcoded).
    """
    # Tenta com nichos do produto
    if nicho_ids:
        result = await db.execute(
            select(TemplateMensagem)
            .where(
                TemplateMensagem.org_id == org_id,
                TemplateMensagem.ativo.is_(True),
                TemplateMensagem.nicho_id.in_(nicho_ids),
            )
        )
        candidatos = list(result.scalars().all())
        if candidatos:
            return random.choice(candidatos)

    # Fallback: template padrão da org (nicho_id NULL)
    result = await db.execute(
        select(TemplateMensagem).where(
            TemplateMensagem.org_id == org_id,
            TemplateMensagem.ativo.is_(True),
            TemplateMensagem.nicho_id.is_(None),
        )
    )
    candidatos = list(result.scalars().all())
    if candidatos:
        return random.choice(candidatos)

    return None


async def registrar_uso(
    db: AsyncSession, *, template_id: int,
) -> None:
    """Incrementa vezes_usado e atualiza ultimo_uso_em."""
    template = await db.get(TemplateMensagem, template_id)
    if template is None:
        return
    template.vezes_usado += 1
    template.ultimo_uso_em = datetime.now(tz=timezone.utc)
    await db.commit()


# ============================================================
# Fallback hardcoded (quando org não tem nenhum template)
# ============================================================

TEMPLATE_FALLBACK = """{chamada_emoji} {chamada}

{nome}

{bloco_preco}

🛒 {url}"""


def renderizar_com_fallback(produto: Produto) -> str:
    """Renderiza com TEMPLATE_FALLBACK — útil se org não tem nenhum template."""
    return renderizar(TEMPLATE_FALLBACK, produto)

"""
Marketplaces suportados pelo sistema.

Lista canônica de plataformas com slug interno + display name + ícone.
Adicionar marketplace novo aqui é o ÚNICO ponto de extensão — não exige
migration nem mudança no schema (tabela `usuarios_afiliados` aceita qualquer
plataforma).

Usado por:
- UI `/usuarios/{id}/afiliados` pra renderizar dropdown "+ Adicionar"
- API pra validar slug em POST/PATCH
- Linkbuilder pra dispatch por plataforma (já existia, parâmetro `plataforma`)
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Marketplace:
    slug: str         # 'ml', 'shopee', etc. — case-sensitive, lower
    nome: str         # 'Mercado Livre', 'Shopee', etc.
    icone: str        # emoji curto
    placeholder_tag: str = ""   # exemplo pra mostrar no form


MARKETPLACES: tuple[Marketplace, ...] = (
    Marketplace("ml",         "Mercado Livre",   "🛒", "achadinhos"),
    Marketplace("shopee",     "Shopee",          "🛍️", "AFEKWN8"),
    Marketplace("amazon",     "Amazon",          "📦", "silvio-20"),
    Marketplace("magalu",     "Magazine Luiza",  "🌟", "magalink123"),
    Marketplace("aliexpress", "AliExpress",      "🌏", "abc123def"),
    Marketplace("tiktok",     "TikTok Shop",     "🎵", "ttshop_silvio"),
)


def por_slug(slug: str) -> Marketplace | None:
    """Acha Marketplace pelo slug. None se não suportado."""
    slug = (slug or "").lower().strip()
    for m in MARKETPLACES:
        if m.slug == slug:
            return m
    return None


def slugs_validos() -> set[str]:
    """Conjunto dos slugs aceitos. Usado pra validar input."""
    return {m.slug for m in MARKETPLACES}

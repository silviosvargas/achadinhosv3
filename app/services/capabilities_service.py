"""Capabilities por agente — define o que cada agente PODE fazer (Fase D
— 17/05/2026).

Regra do user (mensagem 17/05 noite):
- admin_central → tudo (whatsapp + ml + shopee + amazon)
- afiliado      → whatsapp + cada marketplace que o user tem tag cadastrada
- usuário comum → só whatsapp

Capability é uma string slug:
- "whatsapp"  → módulo WhatsApp Web
- "ml"        → Selenium Mercado Livre (busca + linkbuilder + barra afiliados)
- "shopee"    → API Shopee Afiliados
- "amazon"    → Selenium Amazon + SiteStripe
- "magalu" / "aliexpress" / "tiktok" → futuro

Servidor envia capabilities no handshake WS (mensagem `capabilities`)
logo após `accept()`. Agente armazena em memória e usa pra:
1. Decidir quais módulos carregar (executar busca ML só se tem capability "ml")
2. Mostrar status na UI local (/ping inclui capabilities)
3. Servidor TAMBÉM valida antes de criar Tarefa (defesa em camadas).
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import Agente, Usuario, UsuarioAfiliado

log = get_logger(__name__)


# Capability sempre presente em qualquer agente — todo user pode usar WhatsApp.
_BASE_CAPABILITIES = ["whatsapp"]

# Marketplaces conhecidos que requerem Selenium/API e tag de afiliado.
# Match com slugs em `UsuarioAfiliado.plataforma`.
_MARKETPLACES_CAPABILITIES = {
    "ml":         "ml",
    "shopee":     "shopee",
    "amazon":     "amazon",
    "magalu":     "magalu",
    "aliexpress": "aliexpress",
}


async def capabilities_do_agente(
    db: AsyncSession, *, agente_id: int,
) -> list[str]:
    """Calcula capabilities do agente baseado no user dono.

    - Admin central: TODAS (whatsapp + ml + shopee + amazon)
    - Afiliado:      whatsapp + marketplaces com tag em `usuarios_afiliados`
    - Outros:        só whatsapp

    Returns lista de strings (slugs de capabilities).
    """
    agente = await db.get(Agente, agente_id)
    if agente is None:
        return list(_BASE_CAPABILITIES)

    user = await db.get(Usuario, agente.usuario_id)
    if user is None:
        return list(_BASE_CAPABILITIES)

    if user.eh_admin_central:
        # Admin central tem tudo
        return _BASE_CAPABILITIES + list(_MARKETPLACES_CAPABILITIES.values())

    if user.eh_afiliado:
        # Afiliado: lista os marketplaces com tag cadastrada
        tags = (await db.execute(
            select(UsuarioAfiliado.plataforma).where(
                UsuarioAfiliado.usuario_id == user.id,
            )
        )).scalars().all()
        marketplaces = [
            _MARKETPLACES_CAPABILITIES[slug]
            for slug in tags
            if slug in _MARKETPLACES_CAPABILITIES
        ]
        return _BASE_CAPABILITIES + marketplaces

    # Usuário comum: só whatsapp
    return list(_BASE_CAPABILITIES)


def capability_requerida_pra_busca(marketplace: str) -> str | None:
    """Marketplace → slug de capability requerida pra executar busca.

    Returns None se marketplace desconhecido (não bloqueia).
    """
    return _MARKETPLACES_CAPABILITIES.get(marketplace.lower())


async def agente_pode(
    db: AsyncSession, *, agente_id: int, capability: str,
) -> bool:
    """Helper pra validar uma capability específica."""
    caps = await capabilities_do_agente(db, agente_id=agente_id)
    return capability in caps

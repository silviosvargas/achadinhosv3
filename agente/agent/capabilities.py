"""Singleton de capabilities do agente (Fase D — v3.9.0).

Servidor envia capabilities no handshake WS (`tipo: "capabilities"`).
`ws_client.WSClient` recebe e chama `set_capabilities(...)` aqui.

Outros módulos (busca_ml, busca_shopee, busca_amazon, postar_whatsapp)
consultam via `tem(slug)` antes de executar — se não tem capability,
recusam graciosamente em vez de chamar Selenium/API.

Capabilities possíveis (ver `app/services/capabilities_service.py`):
- "whatsapp"  → módulo WhatsApp Web
- "ml"        → busca Mercado Livre + linkbuilder
- "shopee"    → API Shopee Afiliados
- "amazon"    → SiteStripe Amazon
- "magalu" / "aliexpress" / "tiktok" → futuro

Importante: enquanto o servidor não enviou capabilities (estado inicial),
considera vazio = recusa tudo exceto whatsapp (defensivo). Se o servidor
enviou lista vazia ou sem whatsapp, mantém o que recebeu.
"""
from __future__ import annotations

import threading
from typing import Optional

import structlog

log = structlog.get_logger(__name__)


_lock = threading.Lock()
_capabilities: Optional[list[str]] = None  # None = ainda não recebeu do servidor


def set_capabilities(caps: list[str]) -> None:
    """Chamado por `ws_client._loop_recebimento` quando recebe `tipo:capabilities`."""
    global _capabilities
    with _lock:
        _capabilities = [str(c).lower() for c in (caps or [])]
    log.info("agent.capabilities_atualizadas", caps=_capabilities)


def listar() -> list[str]:
    """Retorna cópia das capabilities atuais. Lista vazia se ainda
    não recebeu do servidor."""
    with _lock:
        return list(_capabilities or [])


def tem(slug: str) -> bool:
    """True se o agente tem essa capability. Default conservador:
    se ainda não recebeu do servidor (None), nega tudo exceto whatsapp
    pra evitar disparar Selenium num agente que não deveria."""
    slug = str(slug).lower()
    with _lock:
        if _capabilities is None:
            # Não recebeu ainda — default conservador
            return slug == "whatsapp"
        return slug in _capabilities


def recusar_se_sem(slug: str, contexto: str = "") -> dict | None:
    """Helper pra handlers: se não tem a capability, retorna dict de
    resposta `ok=False` pronto. Senão retorna None (deixa caller continuar).

    Uso:
        async def handler_iniciar_busca_ml(msg, cfg):
            resp = capabilities.recusar_se_sem("ml", contexto="busca ML")
            if resp is not None:
                return resp
            ... executa ...
    """
    if tem(slug):
        return None
    log.warning("agent.capability_ausente",
                slug=slug, capabilities=listar(), contexto=contexto)
    return {
        "ok":    False,
        "erro":  "capability_ausente",
        "slug":  slug,
        "mensagem": (
            f"Seu agente não tem permissão pra '{slug}'. Pra usar "
            f"este marketplace, cadastre suas credenciais em "
            f"/usuarios/<seu_id>/afiliados e reconecte o agente."
        ),
    }

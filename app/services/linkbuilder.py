"""
Gera URLs de afiliado a partir da URL canônica + tag do afiliado.

Cada plataforma tem um padrão diferente:
- Mercado Livre: link curto encurtado pelo painel de afiliado, ou query param
  `?matt_word=...&matt_tool=...`. Como não temos API pra gerar shortlink, a
  abordagem MVP é montar URL com query params padrão de tracking. Para
  shortlinks (meli.la/abc), entra fase futura via API do afiliado.
- Shopee:       https://s.shopee.com.br/<id>  (precisa do painel pra gerar)
- Amazon:       https://www.amazon.com.br/dp/<asin>?tag=<tag>
- Magalu / AliExpress: similar (query param).

Esta camada é estrita: se não conseguir compor, devolve a URL canônica como
fallback (`url_afiliado = url_canonica`). Admin pode editar manualmente
depois pra colar shortlink encurtado.
"""
from __future__ import annotations

from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl

from app.core.logging import get_logger

log = get_logger(__name__)


def gerar_url_afiliado(
    *, plataforma: str, url_canonica: str | None, tag: str | None,
) -> str | None:
    """
    Devolve URL com tag de afiliado embutida, ou None se não há canônica.

    Sem tag → devolve a canônica (não força afiliação).
    Plataforma desconhecida → devolve a canônica também.
    """
    if not url_canonica:
        return None
    if not tag:
        return url_canonica

    plat = plataforma.lower().strip()
    try:
        if plat == "ml":
            return _aplicar_query(url_canonica, {
                "matt_word": tag,
                "matt_tool": "achadinhos",
            })
        if plat == "amazon":
            return _aplicar_query(url_canonica, {"tag": tag})
        if plat in ("shopee", "magalu", "aliexpress"):
            # Plataformas que dependem de painel pra gerar shortlink real.
            # Aplicamos query param genérico de tracking — não é shortlink,
            # mas registra a origem se a plataforma respeitar.
            return _aplicar_query(url_canonica, {"utm_source": tag})
    except Exception as e:
        log.warning("linkbuilder.erro", plataforma=plat, erro=str(e))

    return url_canonica


def _aplicar_query(url: str, novos_params: dict[str, str]) -> str:
    """Mescla query params na URL preservando os existentes."""
    parts = urlparse(url)
    atuais = dict(parse_qsl(parts.query, keep_blank_values=True))
    atuais.update(novos_params)
    return urlunparse(parts._replace(query=urlencode(atuais)))

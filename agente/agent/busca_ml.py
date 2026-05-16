"""
Scraper Mercado Livre + handler do comando WS `iniciar_busca_ml`.

Fluxo:
1. Recebe payload do servidor (ver protocolo_agente.md): `entrada`, `tipo_entrada`,
   `max_paginas`, `max_produtos`, `busca_id`, `tarefa_id`.
2. Monta URL inicial (termo → URL canônica, ou URL direta).
3. Abre Chrome SEPARADO do WhatsApp (porta+perfil próprios) — isolamento.
4. Varre páginas até o limite, extrai produtos com Selenium.
5. Faz POST /produtos/ingest com o lote.
6. Retorna `{ok, encontrados, criados, atualizados, ...}` ao servidor via WS.

Decisões:
- Chrome separado (porta 9223) pra não bagunçar sessão WhatsApp.
- Headless OFF em dev (cfg.ml_headless); ON em produção.
- Selenium síncrono → rodado via `asyncio.to_thread` pra não bloquear o WS.
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Any
from urllib.parse import quote, urlparse, urlunparse, parse_qsl, urlencode

import structlog
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException, TimeoutException

from agent.config import Config
from agent.ingest_client import IngestError, enviar_lote

log = structlog.get_logger(__name__)


# ============================================================
# URL da busca
# ============================================================

ML_LISTA_BASE = "https://lista.mercadolivre.com.br"


def montar_url_inicial(*, tipo_entrada: str, entrada: str) -> str:
    """
    Pra termo livre, monta URL de listagem do ML.
    Pra URL completa, devolve como veio (com sanity check de http).
    """
    if tipo_entrada == "url":
        if not entrada.lower().startswith(("http://", "https://")):
            raise ValueError("tipo_entrada=url mas entrada não começa com http")
        return entrada

    # Termo livre — vira slug-com-hifens
    termo = re.sub(r"\s+", "-", entrada.strip().lower())
    termo = quote(termo, safe="-")
    return f"{ML_LISTA_BASE}/{termo}"


def url_pagina(url_base: str, pagina: int) -> str:
    """
    Adiciona _From=N na query pra paginar.
    Pag 1 = sem parâmetro; pag 2 = _From=51; pag 3 = _From=101; etc (50/página).
    """
    if pagina <= 1:
        return url_base
    offset = (pagina - 1) * 50 + 1
    # ML aceita tanto `_From=` na query quanto no path; query é mais simples
    parts = urlparse(url_base)
    qs = dict(parse_qsl(parts.query))
    qs["_From"] = str(offset)
    return urlunparse(parts._replace(query=urlencode(qs)))


# ============================================================
# Chrome dedicado ao ML
# ============================================================

def _criar_driver_ml(cfg: Config) -> uc.Chrome:
    """
    Chrome anti-detecção via undetected-chromedriver.

    O ML usa /gz/account-verification pra bloquear Selenium puro (mesmo com
    --disable-blink-features=AutomationControlled). undetected-chromedriver
    aplica patches no chromedriver binary pra esconder os marcadores que o
    ML detecta. É o padrão pra scraping de marketplaces hoje.

    Mantém o perfil persistente em cfg.chrome_perfil_ml — se o user fizer
    login no ML uma vez, sessão é reaproveitada.
    """
    opts = uc.ChromeOptions()
    if cfg.ml_headless:
        opts.add_argument("--headless=new")
    opts.add_argument(f"--user-data-dir={cfg.chrome_perfil_ml}")
    # Pula a tela de seletor de perfil ("Quem esta usando?") e checks
    # de browser padrão. Usa direto o perfil Default dentro do user-data-dir
    # (compartilhado com login_ml.py — assim a sessão logada é reaproveitada).
    opts.add_argument("--profile-directory=Default")
    opts.add_argument("--no-first-run")
    opts.add_argument("--no-default-browser-check")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1366,900")
    # undetected-chromedriver já aplica os truques anti-detecção;
    # não precisa --disable-blink-features nem mexer em navigator.webdriver

    return uc.Chrome(options=opts, use_subprocess=True)


# ============================================================
# Extração de produtos
# ============================================================

# Seletores CSS — múltiplos pra robustez (ML muda layout periódicamente).
# Tentamos em ordem; o primeiro que retornar elementos vence.
SELETORES_CARD = [
    "li.ui-search-layout__item",
    "div.ui-search-result__wrapper",
]
SELETORES_TITULO = [
    "h3.poly-component__title-wrapper a",
    "a.poly-component__title",
    "h2.ui-search-item__title",
    "a.ui-search-link",
]
SELETORES_PRECO_INT = [
    ".andes-money-amount__fraction",
    ".price-tag-fraction",
]
SELETORES_PRECO_CENTS = [
    ".andes-money-amount__cents",
    ".price-tag-cents",
]
SELETORES_PRECO_ORIG = [
    "s.andes-money-amount",
    ".ui-search-price__original-value .andes-money-amount__fraction",
]
SELETORES_IMG = [
    "img.poly-component__picture",
    "img.ui-search-result-image__element",
]
SELETORES_FRETE = [
    ".poly-component__shipping",
    ".ui-search-item__shipping",
]


def _primeiro(elem, seletores: list[str]):
    """Tenta cada seletor; devolve primeiro elemento achado ou None."""
    for sel in seletores:
        try:
            return elem.find_element(By.CSS_SELECTOR, sel)
        except NoSuchElementException:
            continue
    return None


def _texto(elem) -> str:
    return (elem.text or "").strip() if elem else ""


def _parse_preco(elem_int, elem_cents) -> float | None:
    if not elem_int:
        return None
    inteiro = re.sub(r"[^\d]", "", _texto(elem_int))
    if not inteiro:
        return None
    base = float(inteiro)
    if elem_cents:
        c = re.sub(r"[^\d]", "", _texto(elem_cents))
        if c:
            base += float(c) / 100.0
    return base


def _extrair_item_id(url: str) -> str | None:
    """
    URLs do ML carregam o MLB no slug:
      https://produto.mercadolivre.com.br/MLB-1234567890-titulo-...
      https://www.mercadolivre.com.br/.../p/MLB12345  (catalog)
    Aceita ambas; normaliza pra 'MLBxxxxxxxxxx'.
    """
    if not url:
        return None
    m = re.search(r"(MLB-?\d{8,15})", url)
    if not m:
        return None
    return m.group(1).replace("-", "")


_SELETORES_URL_CARD = [
    "a.poly-component__title",
    "a.poly-component__image-link",
    "a[class*='title']",
    "a[href*='mercadolivre.com.br']",
]


def _achar_url(card) -> str | None:
    """Link clicável do card.

    Portado da V2 (`src/buscar/ml.py:117-128`): tenta múltiplos seletores
    de anchor e retorna URL JÁ LIMPA (sem `?` query nem `#` fragment).

    Por que limpar AGORA na extração e não em camadas depois:
    - URL limpa entra direto no DB → sem `#polycard_client=...&tracking_id=...`
    - URL limpa vai pro painel ML linkbuilder → ML aceita sem ambiguidade
    - URL limpa volta no mapping → match exato com DB no `aplicar_mapping`
    - Sem retrabalho em N lugares (que era a fonte dos bugs v3.0.4-3.0.7)

    Também filtra anchors de PUBLICIDADE / CLICK TRACKING — produtos
    patrocinados não rendem comissão e poluem o catálogo.
    """
    for sel in _SELETORES_URL_CARD:
        try:
            anchors = card.find_elements(By.CSS_SELECTOR, sel)
        except Exception:
            continue
        for a in anchors:
            href_raw = a.get_attribute("href") or ""
            # V2 fazia: split('?')[0].split('#')[0]
            href = href_raw.split("?", 1)[0].split("#", 1)[0]
            if (
                "mercadolivre.com.br" in href
                and "click1" not in href            # tracking de clique patrocinado
                and "publicidade" not in href       # produto patrocinado, sem comissão
                and len(href) > 35                  # URL absurdamente curta = inválida
            ):
                return href
    return None


def _achar_titulo(card) -> str:
    titulo_el = _primeiro(card, SELETORES_TITULO)
    if titulo_el is None:
        return ""
    # Às vezes o título está no atributo 'title' do anchor, mais limpo
    t = titulo_el.get_attribute("title") or _texto(titulo_el)
    return (t or "").strip()


def _achar_imagem(card) -> str | None:
    img = _primeiro(card, SELETORES_IMG)
    if img is None:
        return None
    src = img.get_attribute("src") or img.get_attribute("data-src")
    if src and src.startswith("http") and "data:image" not in src:
        return src
    return None


def _achar_frete_gratis(card) -> bool:
    el = _primeiro(card, SELETORES_FRETE)
    if el is None:
        return False
    return "grátis" in _texto(el).lower() or "gratis" in _texto(el).lower()


def _categoria_de_jsonld(driver) -> str | None:
    """
    Tenta extrair categoria via JSON-LD (Schema.org BreadcrumbList).
    Mais confiável que CSS porque o ML mantém pra SEO.

    Procura `<script type="application/ld+json">` que contenha BreadcrumbList,
    e retorna os nomes dos itens concatenados " > ".
    """
    import json
    try:
        scripts = driver.find_elements(
            By.CSS_SELECTOR, "script[type='application/ld+json']"
        )
        for s in scripts:
            try:
                content = s.get_attribute("textContent") or s.get_attribute("innerText") or ""
                if "BreadcrumbList" not in content:
                    continue
                data = json.loads(content)
                # Pode ser dict ou lista de dicts
                blocos = data if isinstance(data, list) else [data]
                for bloco in blocos:
                    if (bloco.get("@type") == "BreadcrumbList"
                            and "itemListElement" in bloco):
                        nomes = []
                        for item in bloco["itemListElement"]:
                            n = (item.get("name")
                                 or (item.get("item") or {}).get("name"))
                            if n:
                                nomes.append(str(n).strip())
                        if nomes:
                            return " > ".join(nomes)
            except Exception:
                continue
    except Exception:
        pass
    return None


SELETORES_BREADCRUMB = [
    "ol.andes-breadcrumb li.andes-breadcrumb__item",
    "ol.andes-breadcrumb li",
    "nav.breadcrumb a",
    "nav[aria-label*='readcrumb'] a",
    ".ui-search-breadcrumb a, .ui-search-breadcrumb span",
    "[itemtype*='BreadcrumbList'] [itemprop='name']",
]


def _categoria_de_css(driver) -> str | None:
    """Tenta múltiplos seletores CSS de breadcrumb."""
    for sel in SELETORES_BREADCRUMB:
        try:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            partes = [_texto(e) for e in els if _texto(e)]
            if partes and len(partes) >= 2:  # exige pelo menos 2 níveis
                return " > ".join(partes)
        except Exception:
            continue
    return None


def _categoria_via_primeiro_produto(
    driver, urls_produtos: list[str], voltar: bool = False,
) -> str | None:
    """
    Fallback caro: entra no primeiro produto da página e pega o breadcrumb
    da página de detalhe (que sempre tem). Usado quando a listagem não expõe
    categoria.

    Premissa: produtos da mesma listagem geralmente compartilham categoria.

    voltar=False (default): NÃO retorna pra listagem após pegar a categoria.
    Caller responsável por re-navegar se precisar. Evita StaleElementException
    quando o caller já tem referências de DOM da listagem.
    """
    if not urls_produtos:
        return None
    url_atual = driver.current_url
    try:
        driver.get(urls_produtos[0])
        time.sleep(2)
        cat = _categoria_de_jsonld(driver) or _categoria_de_css(driver)
        log.info("ml.categoria_via_produto", url_produto=urls_produtos[0][:80], cat=cat)
        return cat
    except Exception as e:
        log.debug("ml.categoria_produto_falhou", erro=str(e))
        return None
    finally:
        if voltar:
            try:
                driver.get(url_atual)
                time.sleep(1.5)
            except Exception:
                pass


def _extrair_categoria_pagina(driver, urls_produtos: list[str] | None = None) -> str | None:
    """
    Pega o breadcrumb/categoria da página de listagem (categoria principal).

    Estratégias em cascata:
    1. JSON-LD BreadcrumbList (mais confiável, ML usa pra SEO)
    2. CSS de breadcrumb (múltiplos seletores)
    3. Se vieram urls_produtos: entra no primeiro produto e pega breadcrumb dele
    """
    cat = _categoria_de_jsonld(driver)
    if cat:
        log.info("ml.categoria_via_jsonld", cat=cat)
        return cat

    cat = _categoria_de_css(driver)
    if cat:
        log.info("ml.categoria_via_css", cat=cat)
        return cat

    if urls_produtos:
        cat = _categoria_via_primeiro_produto(driver, urls_produtos)
        if cat:
            return cat

    log.warning("ml.sem_categoria")
    return None


def _extrair_cards_da_pagina(driver) -> list[dict[str, Any]]:
    """Pega lista de produtos da página atual.

    Estratégia:
    1. Tenta categoria via JSON-LD/CSS direto na listagem (sem sair).
    2. Itera cards e monta dicts de produtos.
    3. Se categoria ainda é None: faz fallback que entra em 1 produto
       (descarta a página atual — mas já temos tudo dos cards) e aplica
       a mesma categoria a todos.
    """
    cards = []
    for sel in SELETORES_CARD:
        elems = driver.find_elements(By.CSS_SELECTOR, sel)
        if elems:
            cards = elems
            break

    if not cards:
        log.warning("ml.sem_cards")
        return []

    # Tenta categoria SEM sair da listagem
    categoria = _categoria_de_jsonld(driver) or _categoria_de_css(driver)
    if categoria:
        log.info("ml.categoria_listagem", cat=categoria)
    produtos: list[dict[str, Any]] = []

    for card in cards:
        try:
            url = _achar_url(card)
            item_id = _extrair_item_id(url) if url else None
            if not item_id:
                continue  # sem MLB válido, descarta

            preco = _parse_preco(
                _primeiro(card, SELETORES_PRECO_INT),
                _primeiro(card, SELETORES_PRECO_CENTS),
            )
            if preco is None or preco <= 0:
                continue

            # Preço original (riscado) — opcional
            preco_orig: float | None = None
            try:
                orig = card.find_element(By.CSS_SELECTOR,
                    "s.andes-money-amount .andes-money-amount__fraction")
                preco_orig = _parse_preco(orig, None)
            except NoSuchElementException:
                pass

            desconto: float | None = None
            if preco_orig and preco_orig > preco:
                desconto = round((1 - preco / preco_orig) * 100, 0)

            produto = {
                "plataforma":   "ml",
                "item_id":      item_id,
                "nome":         _achar_titulo(card)[:500] or item_id,
                "preco":        preco,
                "preco_orig":   preco_orig,
                "desconto":     desconto,
                "frete_gratis": _achar_frete_gratis(card),
                "categoria":    categoria,
                "url_canonica": url,
                "foto_url":     _achar_imagem(card),
            }
            produtos.append(produto)
        except Exception as e:
            log.debug("ml.card_falhou", erro=str(e))
            continue

    # Fallback retroativo: se ainda não temos categoria, entra em 1 produto
    # pra pegar (já temos tudo dos cards, então não precisa voltar à listagem).
    if not categoria and produtos:
        urls_amostra = [p["url_canonica"] for p in produtos if p.get("url_canonica")][:1]
        if urls_amostra:
            categoria_fb = _categoria_via_primeiro_produto(
                driver, urls_amostra, voltar=False,
            )
            if categoria_fb:
                # Aplica retroativamente a todos os produtos da listagem
                for p in produtos:
                    if not p.get("categoria"):
                        p["categoria"] = categoria_fb
                log.info("ml.categoria_fallback_aplicada",
                         cat=categoria_fb, total=len(produtos))

    return produtos


# ============================================================
# Varredura (síncrona — chamada via to_thread)
# ============================================================

# Fase 16.3 — mapping categoria → URL "mais vendidos" + comissão média.
# Portado da V2 (`src/buscar/ml.py:32-41`). Comissões são ESTIMATIVA pra
# ranqueamento; valor exato vem do painel ML afiliados.
CATEGORIAS_MAIS_VENDIDOS = [
    # (categoria_display, url, comissao_estimada)
    # Display name BATE com mappings em `nicho_categoria_ml` (migration 0007)
    # pra auto-classificação por nicho funcionar sem mappings adicionais.
    ("Roupas, Calçados e Acessórios", "https://www.mercadolivre.com.br/mais-vendidos/MLB1430", 14.0),
    ("Esportes e Fitness",            "https://www.mercadolivre.com.br/mais-vendidos/MLB1276", 12.0),
    ("Beleza e Cuidado Pessoal",      "https://www.mercadolivre.com.br/mais-vendidos/MLB1246", 12.0),
    ("Bebês",                          "https://www.mercadolivre.com.br/mais-vendidos/MLB5726", 10.0),
    ("Casa, Móveis e Decoração",      "https://www.mercadolivre.com.br/mais-vendidos/MLB1574", 10.0),
    ("Eletrônicos, Áudio e Vídeo",    "https://www.mercadolivre.com.br/mais-vendidos/MLB1051",  8.0),
    ("Informática",                    "https://www.mercadolivre.com.br/mais-vendidos/MLB1648",  8.0),
    ("Ferramentas",                    "https://www.mercadolivre.com.br/mais-vendidos/MLB1499",  8.0),
]


# Fase 16.5 — URLs "em alta" / ofertas relâmpago ML.
# `/ofertas` é a landing global de promoções diárias do ML.
URLS_EM_ALTA = [
    ("Ofertas do dia", "https://www.mercadolivre.com.br/ofertas",  None),
]


# ============================================================
# Helpers comuns às varreduras (Fase 16.5)
# ============================================================

_SINAIS_LOGIN = (
    "/gz/account-verification",
    "/login",
    "/jms/mlb/lgz",
)


def _bloqueado_por_login(url_final: str) -> bool:
    """True se o ML redirecionou pra página de login/verificação."""
    u = (url_final or "").lower()
    return any(s in u for s in _SINAIS_LOGIN)


def _scroll_lazy_load(driver) -> None:
    """Scroll progressivo pra disparar lazy load de cards do ML."""
    try:
        for pos in (500, 1500, 3000, 5000):
            driver.execute_script(f"window.scrollTo(0, {pos});")
            time.sleep(0.4)
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(0.4)
    except Exception:
        pass


def _gerar_meli_la_no_driver(
    driver, produtos: list[dict[str, Any]], *, log_prefixo: str,
) -> None:
    """
    Gera meli.la pros produtos extraídos, IN-PLACE, no mesmo driver Chrome.

    Igual V2 (`src/buscar/ml.py:gerar_todos_links` chamado de `executar`):
    a busca e a geração de links compartilham 1 driver. Sem ronda WS,
    sem conflito de perfil, sem tarefa GERAR_LINK separada.
    Produto fica com `url_afiliado=meli.la/XXX` ANTES do ingest.

    Falhas (sessão expirada, painel mudou) deixam `url_afiliado` ausente —
    servidor aplica fallback genérico `?matt_word=...` no ingest.
    """
    from agent.linkbuilder_ml import _gerar_lote_sync, LOTE_TAMANHO

    urls = list({
        p["url_canonica"] for p in produtos
        if p.get("url_canonica")
    })
    if not urls:
        return

    log.info(f"{log_prefixo}.linkbuilder_inline", urls=len(urls))
    mapa: dict[str, str] = {}
    for i in range(0, len(urls), LOTE_TAMANHO):
        lote = urls[i:i + LOTE_TAMANHO]
        try:
            mapa.update(_gerar_lote_sync(driver, lote))
        except Exception as e:
            log.warning(f"{log_prefixo}.linkbuilder_lote_falhou",
                        n=i // LOTE_TAMANHO + 1, erro=str(e)[:120])

    aplicados = 0
    for p in produtos:
        url_c = p.get("url_canonica")
        meli = mapa.get(url_c) if url_c else None
        if meli and "meli.la/" in meli:
            p["url_afiliado"] = meli
            aplicados += 1
    log.info(f"{log_prefixo}.linkbuilder_aplicado",
             gerados=len(mapa), aplicados=aplicados, total_produtos=len(produtos))


def _varrer_lista_urls_sync(
    cfg: Config,
    *,
    urls_com_meta: list[tuple[str, str, float | None]],
    max_produtos: int,
    log_prefixo: str,
) -> list[dict[str, Any]]:
    """
    Template comum: itera lista de (nome_unidade, url, comissao_estimada),
    abre cada URL, extrai cards, balanceia por unidade.

    Usado por mais_vendidos / melhor_comissao / em_alta. Mesmo padrão da V2
    `buscar_mais_vendidos` + `gerar_todos_links` no MESMO driver: extrai
    cards, depois gera os meli.la inline antes de fechar o Chrome.
    """
    driver = _criar_driver_ml(cfg)
    todos: list[dict[str, Any]] = []
    vistos: set[str] = set()
    por_unidade = max(3, max_produtos // max(1, len(urls_com_meta)) + 2)

    try:
        for nome_unidade, url, comissao_est in urls_com_meta:
            if len(todos) >= max_produtos:
                break
            log.info(f"{log_prefixo}.unidade", nome=nome_unidade, url=url)
            try:
                driver.get(url)
            except Exception as e:
                log.warning(f"{log_prefixo}.get_falhou", nome=nome_unidade, erro=str(e)[:120])
                continue
            try:
                WebDriverWait(driver, 15).until(
                    EC.presence_of_element_located((By.TAG_NAME, "body"))
                )
            except TimeoutException:
                log.warning(f"{log_prefixo}.timeout", nome=nome_unidade)
                continue

            if _bloqueado_por_login(driver.current_url):
                raise RuntimeError(
                    f"ML exige login ({nome_unidade} → {driver.current_url[:120]}). "
                    "Rode `python -m agent.login_ml` uma vez."
                )

            _scroll_lazy_load(driver)
            cards = _extrair_cards_da_pagina(driver)
            adicionados = 0
            for c in cards:
                if len(todos) >= max_produtos or adicionados >= por_unidade:
                    break
                item_id = c.get("item_id")
                if not item_id or item_id in vistos:
                    continue
                vistos.add(item_id)
                # Enriquece com categoria e comissão se a unidade trouxer
                if not c.get("categoria") and nome_unidade:
                    c["categoria"] = nome_unidade
                if comissao_est is not None:
                    c["comissao"] = comissao_est
                todos.append(c)
                adicionados += 1
            log.info(f"{log_prefixo}.unidade_ok",
                     nome=nome_unidade, extraidos=adicionados, total=len(todos))

        # Gera meli.la INLINE no mesmo driver — antes de fechar (igual V2)
        _gerar_meli_la_no_driver(driver, todos, log_prefixo=log_prefixo)

    finally:
        try:
            driver.quit()
        except Exception:
            pass

    return todos


# ============================================================
# Busca por URL — 1 produto específico (Fase 16.4)
# ============================================================

def _texto_ld_product(driver) -> dict[str, Any] | None:
    """JSON-LD Product schema (ML mantém pra SEO). Mais confiável que CSS."""
    import json
    try:
        scripts = driver.find_elements(
            By.CSS_SELECTOR, "script[type='application/ld+json']"
        )
        for s in scripts:
            raw = s.get_attribute("textContent") or s.get_attribute("innerText") or ""
            if '"Product"' not in raw:
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            blocos = data if isinstance(data, list) else [data]
            for b in blocos:
                if b.get("@type") == "Product":
                    return b
    except Exception:
        pass
    return None


def _meta_property(driver, prop: str) -> str | None:
    """Lê <meta property='og:...' content='...'>."""
    try:
        el = driver.find_element(By.CSS_SELECTOR, f"meta[property='{prop}']")
        v = el.get_attribute("content")
        return v.strip() if v else None
    except NoSuchElementException:
        return None


def _extrair_produto_unico(driver, url: str) -> dict[str, Any] | None:
    """
    Extrai 1 produto de uma página de detalhe do ML.
    Cascata: JSON-LD Product → OpenGraph meta → CSS direto.
    Retorna None se URL não é página de produto válida.
    """
    # Limpa a URL ANTES de qualquer coisa — mesma estratégia do V2.
    # Garante que `url_canonica` que vai pro DB já está sem fragment/query.
    url = (url or "").split("?", 1)[0].split("#", 1)[0]

    item_id = _extrair_item_id(url)
    if not item_id:
        log.warning("ml.por_url.sem_mlb", url=url[:120])
        return None

    nome: str | None = None
    preco: float | None = None
    foto: str | None = None
    categoria: str | None = None

    ld = _texto_ld_product(driver)
    if ld:
        nome = (ld.get("name") or "").strip() or None
        img = ld.get("image")
        if isinstance(img, list):
            foto = img[0] if img else None
        elif isinstance(img, str):
            foto = img
        offers = ld.get("offers") or {}
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        try:
            p = offers.get("price") or offers.get("lowPrice")
            if p is not None:
                preco = float(str(p).replace(",", "."))
        except (TypeError, ValueError):
            preco = None

    # Fallback nome/foto via OG meta
    if not nome:
        nome = _meta_property(driver, "og:title")
    if not foto:
        foto = _meta_property(driver, "og:image")

    # Fallback preço via CSS — pega a primeira instância de preço atual
    # (`.ui-pdp-price__second-line` cobre tanto preço único quanto promocional).
    if preco is None:
        try:
            preco_box = driver.find_element(
                By.CSS_SELECTOR,
                ".ui-pdp-price__second-line .andes-money-amount, "
                ".andes-money-amount[aria-hidden='true']",
            )
            inteiro_el = preco_box.find_element(
                By.CSS_SELECTOR, ".andes-money-amount__fraction",
            )
            try:
                cents_el = preco_box.find_element(
                    By.CSS_SELECTOR, ".andes-money-amount__cents",
                )
            except NoSuchElementException:
                cents_el = None
            preco = _parse_preco(inteiro_el, cents_el)
        except NoSuchElementException:
            pass

    # Fallback nome via h1
    if not nome:
        try:
            h1 = driver.find_element(By.CSS_SELECTOR, "h1.ui-pdp-title, h1")
            nome = (h1.text or "").strip() or None
        except NoSuchElementException:
            pass

    # Categoria via JSON-LD BreadcrumbList ou breadcrumb CSS — já existe util
    categoria = _categoria_de_jsonld(driver) or _categoria_de_css(driver)

    if not nome or preco is None or preco <= 0:
        log.warning("ml.por_url.dados_insuficientes",
                    url=url[:120], item_id=item_id,
                    tem_nome=bool(nome), preco=preco)
        return None

    return {
        "plataforma":   "ml",
        "item_id":      item_id,
        "nome":         nome[:500],
        "preco":        preco,
        "preco_orig":   None,
        "desconto":     None,
        "frete_gratis": False,
        "categoria":    categoria,
        "url_canonica": url,
        "foto_url":     foto if (foto and foto.startswith("http")) else None,
    }


def _varrer_produto_unico_sync(
    cfg: Config,
    *,
    url: str,
) -> list[dict[str, Any]]:
    """Abre 1 URL de produto ML e extrai dados — retorna lista com 0 ou 1 item."""
    if not url.lower().startswith(("http://", "https://")):
        log.warning("ml.por_url.url_invalida", url=url[:120])
        return []

    driver = _criar_driver_ml(cfg)
    try:
        log.info("ml.por_url.abrindo", url=url[:120])
        driver.get(url)
        try:
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
        except TimeoutException:
            log.warning("ml.por_url.timeout_body")
            return []

        # Detecta bloqueio/login do ML — mesma lógica de _varrer_sync
        url_final = driver.current_url.lower()
        if any(s in url_final for s in ("/gz/account-verification", "/login", "/jms/mlb/lgz")):
            raise RuntimeError(
                f"ML exige login (redirecionou pra {url_final[:120]}). "
                "Rode UMA vez: python -m agent.login_ml — loga manualmente, "
                "fecha o Chrome, e tenta de novo."
            )

        # Pequeno scroll pra deixar lazy load de imagens disparar antes do og:image
        try:
            driver.execute_script("window.scrollTo(0, 300);")
            time.sleep(0.8)
            driver.execute_script("window.scrollTo(0, 0);")
        except Exception:
            pass

        produto = _extrair_produto_unico(driver, driver.current_url)
        if not produto:
            return []
        log.info("ml.por_url.ok", item_id=produto["item_id"], nome=produto["nome"][:60])

        # Gera meli.la INLINE no mesmo driver — igual V2
        _gerar_meli_la_no_driver(driver, [produto], log_prefixo="ml.por_url")
        return [produto]
    finally:
        try:
            driver.quit()
        except Exception:
            pass


def _varrer_mais_vendidos_sync(
    cfg: Config,
    *,
    max_produtos: int,
) -> list[dict[str, Any]]:
    """Itera as 8 categorias de 'mais vendidos' do ML, balanceando produtos."""
    return _varrer_lista_urls_sync(
        cfg,
        urls_com_meta=CATEGORIAS_MAIS_VENDIDOS,
        max_produtos=max_produtos,
        log_prefixo="ml.mais_vendidos",
    )


def _varrer_melhor_comissao_sync(
    cfg: Config,
    *,
    max_produtos: int,
) -> list[dict[str, Any]]:
    """Top categorias por comissão estimada — Roupas (14%), Esportes/Beleza (12%).

    Mesma URL pattern das mais_vendidos, filtrado por comissão DESC. Resultado:
    produtos que tendem a render mais R$ por click (priorização explícita).
    """
    top = sorted(CATEGORIAS_MAIS_VENDIDOS, key=lambda x: -x[2])[:4]
    return _varrer_lista_urls_sync(
        cfg,
        urls_com_meta=top,
        max_produtos=max_produtos,
        log_prefixo="ml.melhor_comissao",
    )


def _varrer_em_alta_sync(
    cfg: Config,
    *,
    max_produtos: int,
) -> list[dict[str, Any]]:
    """Produtos em alta / ofertas relâmpago — usa landing de ofertas do ML."""
    return _varrer_lista_urls_sync(
        cfg,
        urls_com_meta=URLS_EM_ALTA,
        max_produtos=max_produtos,
        log_prefixo="ml.em_alta",
    )


def _varrer_termo_livre_sync(
    cfg: Config,
    *,
    url_inicial: str,
    max_paginas: int,
    max_produtos: int,
) -> list[dict[str, Any]]:
    """Termo livre: itera páginas (1..N) da listagem ML via `_From=N`.

    Mesmo template das outras varreduras: get → wait body → detecta login
    → scroll lazy load → extrai cards → balanceia/limita.
    """
    driver = _criar_driver_ml(cfg)
    todos: list[dict[str, Any]] = []
    vistos: set[str] = set()

    try:
        for pag in range(1, max_paginas + 1):
            if len(todos) >= max_produtos:
                break
            url = url_pagina(url_inicial, pag)
            log.info("ml.termo_livre.pagina", numero=pag, url=url)
            try:
                driver.get(url)
            except Exception as e:
                log.warning("ml.termo_livre.get_falhou", numero=pag, erro=str(e)[:120])
                continue

            try:
                WebDriverWait(driver, 15).until(
                    EC.presence_of_element_located((By.TAG_NAME, "body"))
                )
            except TimeoutException:
                log.warning("ml.termo_livre.timeout", numero=pag)
                continue

            if _bloqueado_por_login(driver.current_url):
                raise RuntimeError(
                    f"ML exige login (redirecionou pra {driver.current_url[:120]}). "
                    "Rode `python -m agent.login_ml` uma vez."
                )

            _scroll_lazy_load(driver)

            produtos = _extrair_cards_da_pagina(driver)
            if not produtos:
                # Diagnóstico: salva HTML pra inspeção quando 0 cards
                try:
                    from pathlib import Path
                    debug_dir = Path(cfg.config_dir) / "debug"
                    debug_dir.mkdir(parents=True, exist_ok=True)
                    stamp = int(time.time())
                    driver.save_screenshot(str(debug_dir / f"ml_termo_pag{pag}_{stamp}.png"))
                    (debug_dir / f"ml_termo_pag{pag}_{stamp}.html").write_text(
                        driver.page_source[:200_000], encoding="utf-8", errors="ignore",
                    )
                    log.warning("ml.termo_livre.sem_cards",
                                numero=pag, debug_dir=str(debug_dir),
                                url_final=driver.current_url,
                                titulo=driver.title[:120])
                except Exception:
                    pass
                continue

            adicionados = 0
            for p in produtos:
                if len(todos) >= max_produtos:
                    break
                item_id = p.get("item_id")
                if not item_id or item_id in vistos:
                    continue
                vistos.add(item_id)
                todos.append(p)
                adicionados += 1

            log.info("ml.termo_livre.pagina_ok",
                     numero=pag, extraidos=adicionados, total=len(todos))

        # Gera meli.la INLINE no mesmo driver — igual V2
        _gerar_meli_la_no_driver(driver, todos, log_prefixo="ml.termo_livre")

    finally:
        try:
            driver.quit()
        except Exception:
            pass

    return todos[:max_produtos]


# Alias retrocompat — caminho legado ainda chamado por `executar_busca` antigo
_varrer_sync = _varrer_termo_livre_sync


# ============================================================
# Handler async — chamado pelo WSClient
# ============================================================

async def _coletar_produtos_ml(
    msg: dict[str, Any], cfg: Config,
) -> tuple[list[dict[str, Any]], str | None]:
    """Coleta produtos do ML conforme `tipo_busca`. Retorna (produtos, erro).

    Quando `erro` não é None, o caller deve PARAR o pipeline desse marketplace.
    `produtos=[]` SEM erro = ok mas vazio (sem produtos).
    """
    tipo_entrada = msg.get("tipo_entrada", "termo")
    entrada      = msg.get("entrada", "") or ""
    max_paginas  = int(msg.get("max_paginas", 3))
    max_produtos = int(msg.get("max_produtos", 50))
    tipo_busca   = msg.get("tipo_busca", "termo_livre")

    try:
        if tipo_busca == "mais_vendidos":
            produtos = await asyncio.to_thread(
                _varrer_mais_vendidos_sync, cfg, max_produtos=max_produtos,
            )
        elif tipo_busca == "melhor_comissao":
            produtos = await asyncio.to_thread(
                _varrer_melhor_comissao_sync, cfg, max_produtos=max_produtos,
            )
        elif tipo_busca == "em_alta":
            produtos = await asyncio.to_thread(
                _varrer_em_alta_sync, cfg, max_produtos=max_produtos,
            )
        elif tipo_busca == "por_url":
            if not entrada.lower().startswith(("http://", "https://")):
                return [], "por_url exige URL com http(s)://"
            produtos = await asyncio.to_thread(
                _varrer_produto_unico_sync, cfg, url=entrada,
            )
        else:
            # termo_livre — também é fallback pra tipo desconhecido
            if not entrada.strip():
                return [], "termo_livre exige texto na entrada"
            try:
                url_inicial = montar_url_inicial(
                    tipo_entrada=tipo_entrada, entrada=entrada,
                )
            except ValueError as e:
                return [], f"entrada_invalida: {e}"
            produtos = await asyncio.to_thread(
                _varrer_termo_livre_sync,
                cfg,
                url_inicial=url_inicial,
                max_paginas=max_paginas,
                max_produtos=max_produtos,
            )
    except RuntimeError as e:
        return [], f"ml_bloqueou: {str(e)[:300]}"
    except Exception as e:
        log.exception("busca_ml.crash_selenium", erro=str(e))
        return [], f"ml_crash: {type(e).__name__}: {str(e)[:200]}"

    return produtos, None


async def _coletar_produtos_shopee(
    msg: dict[str, Any], cfg: Config,
) -> tuple[list[dict[str, Any]], str | None]:
    """Coleta produtos da Shopee via API interna (Fase 16.5)."""
    max_produtos = int(msg.get("max_produtos", 50))
    try:
        from agent.busca_shopee import buscar_shopee
        produtos = await buscar_shopee(cfg, max_produtos=max_produtos)
    except RuntimeError as e:
        return [], f"shopee_bloqueou: {str(e)[:300]}"
    except Exception as e:
        log.exception("busca_shopee.crash", erro=str(e))
        return [], f"shopee_crash: {type(e).__name__}: {str(e)[:200]}"
    return produtos, None


async def _coletar_produtos_amazon(
    msg: dict[str, Any], cfg: Config,
) -> tuple[list[dict[str, Any]], str | None]:
    """Coleta produtos da Amazon via scraping bestsellers + SiteStripe (Fase 16.6)."""
    max_produtos = int(msg.get("max_produtos", 50))
    try:
        from agent.busca_amazon import buscar_amazon
        produtos = await buscar_amazon(cfg, max_produtos=max_produtos)
    except RuntimeError as e:
        return [], f"amazon_bloqueou: {str(e)[:300]}"
    except Exception as e:
        log.exception("busca_amazon.crash", erro=str(e))
        return [], f"amazon_crash: {type(e).__name__}: {str(e)[:200]}"
    return produtos, None


# Roteamento marketplace → coletor. Centralizado pra adicionar Magalu/AliExpress
# etc no futuro só precisar registrar aqui + criar o módulo.
_COLETORES_POR_MARKETPLACE = {
    "ml":     _coletar_produtos_ml,
    "shopee": _coletar_produtos_shopee,
    "amazon": _coletar_produtos_amazon,
}


async def executar_busca(msg: dict[str, Any], cfg: Config) -> dict[str, Any]:
    """
    Handler do comando `iniciar_busca_ml`. Retorna dict no formato esperado
    pelo WSClient (`{ok, ...}` → vira `tarefa_concluida`).

    Orquestra MÚLTIPLOS marketplaces (Fase 16.5):
    - `msg["marketplaces"]` lista de slugs (ex: `["ml", "shopee"]`)
    - pra cada marketplace, chama o coletor dedicado
    - acumula produtos + envia tudo num único ingest

    Por marketplace, roteamento por `tipo_busca`:
      - ML: termo_livre | por_url | mais_vendidos | melhor_comissao | em_alta
      - Shopee: ignora tipo_busca, usa list_type=2 (melhor performance)

    Fallback: `marketplaces` ausente/inválido → assume `["ml"]`.
    """
    busca_id     = msg.get("busca_id")
    tarefa_id    = msg.get("tarefa_id")
    tipo_busca   = msg.get("tipo_busca", "termo_livre")
    marketplaces = msg.get("marketplaces") or ["ml"]
    if not isinstance(marketplaces, list) or not marketplaces:
        marketplaces = ["ml"]

    log.info("busca.iniciando",
             busca_id=busca_id, tarefa_id=tarefa_id,
             tipo_busca=tipo_busca, marketplaces=marketplaces,
             entrada=(msg.get("entrada") or "")[:80])

    todos_produtos: list[dict[str, Any]] = []
    erros_por_mkt: dict[str, str] = {}

    for mkt in marketplaces:
        coletor = _COLETORES_POR_MARKETPLACE.get(mkt)
        if coletor is None:
            log.warning("busca.marketplace_nao_suportado", marketplace=mkt)
            erros_por_mkt[mkt] = "marketplace_nao_implementado"
            continue

        log.info("busca.marketplace_iniciado", marketplace=mkt)
        produtos, erro = await coletor(msg, cfg)
        if erro:
            log.warning("busca.marketplace_erro", marketplace=mkt, erro=erro[:200])
            erros_por_mkt[mkt] = erro
            continue
        log.info("busca.marketplace_ok", marketplace=mkt, extraidos=len(produtos))
        todos_produtos.extend(produtos)

    # Se NENHUM marketplace funcionou, retorna erro
    if not todos_produtos and erros_por_mkt:
        return {
            "ok": False,
            "erro": "todos_marketplaces_falharam: " + "; ".join(
                f"{m}={e[:100]}" for m, e in erros_por_mkt.items()
            ),
            "tentar_de_novo": False,
        }

    # Senão, envia tudo (mesmo com falhas parciais — o que deu certo vai)
    resposta = await _enviar_produtos_e_responder(
        todos_produtos, busca_id=busca_id, tarefa_id=tarefa_id, cfg=cfg,
    )
    if erros_por_mkt:
        resposta["marketplaces_com_erro"] = erros_por_mkt
    return resposta


async def _enviar_produtos_e_responder(
    produtos: list[dict[str, Any]],
    *,
    busca_id: int | None,
    tarefa_id: int | None,
    cfg: Config,
) -> dict[str, Any]:
    """Envia lote pra servidor e formata resposta padrão pra WS."""
    if not produtos:
        return {
            "ok": True,
            "encontrados": 0,
            "ingest": {"recebidos": 0, "criados": 0, "atualizados": 0},
            "detalhe": "nenhum produto extraído",
        }

    try:
        resultado = await enviar_lote(
            cfg,
            produtos=produtos,
            busca_id=busca_id,
            tarefa_id=tarefa_id,
        )
    except IngestError as e:
        log.exception("busca.ingest_falhou", erro=str(e))
        return {
            "ok": False,
            "erro": f"ingest_falhou: {str(e)[:200]}",
            "tentar_de_novo": True,
        }

    return {
        "ok": True,
        "encontrados": len(produtos),
        "ingest": resultado,
    }

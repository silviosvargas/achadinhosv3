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


def _achar_url(card) -> str | None:
    """Link clicável do card (primeiro <a href> que aponta pra mercadolivre)."""
    try:
        a = card.find_element(By.CSS_SELECTOR, "a[href*='mercadolivre.com.br']")
        return a.get_attribute("href")
    except NoSuchElementException:
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

def _varrer_sync(
    cfg: Config,
    *,
    url_inicial: str,
    max_paginas: int,
    max_produtos: int,
) -> list[dict[str, Any]]:
    """Versão síncrona do scraping (rodada em thread separada)."""
    driver = _criar_driver_ml(cfg)
    todos: list[dict[str, Any]] = []
    vistos: set[str] = set()  # dedup interno por item_id

    try:
        for pag in range(1, max_paginas + 1):
            url = url_pagina(url_inicial, pag)
            log.info("ml.pagina", numero=pag, url=url)
            driver.get(url)

            # Espera o body carregar (genérico)
            try:
                WebDriverWait(driver, 15).until(
                    EC.presence_of_element_located((By.TAG_NAME, "body"))
                )
            except TimeoutException:
                log.warning("ml.timeout_body", numero=pag)
                continue

            url_final = driver.current_url
            log.info("ml.pagina_carregou",
                     numero=pag,
                     url_final=url_final,
                     titulo=driver.title[:120])

            # Detecta páginas de bloqueio/login do ML — falha rápido com
            # mensagem clara em vez de varrer um body vazio.
            sinais_login = (
                "/gz/account-verification",
                "/login",
                "/jms/mlb/lgz",  # cobre /jms/mlb/lgz/msl/login/ e variantes
            )
            if any(s in url_final.lower() for s in sinais_login):
                msg = (
                    f"ML exige login (redirecionou pra {url_final[:120]}). "
                    "Rode UMA vez: python -m agent.login_ml — loga manualmente, "
                    "fecha o Chrome, e tenta de novo."
                )
                log.warning("ml.precisa_login", url=url_final)
                # Sai do loop e propaga; quem chamou (executar_busca) vê 0 produtos
                # e nós aproveitamos pra retornar erro claro abaixo.
                raise RuntimeError(msg)

            # Scroll pra ativar lazy load ANTES de procurar cards
            try:
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight*0.5);")
                time.sleep(1.2)
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(1.2)
                driver.execute_script("window.scrollTo(0, 0);")
                time.sleep(0.5)
            except Exception:
                pass

            # Tenta achar cards com qualquer um dos seletores conhecidos
            cards_achados = False
            for sel in SELETORES_CARD:
                if driver.find_elements(By.CSS_SELECTOR, sel):
                    cards_achados = True
                    log.info("ml.cards_via_seletor", seletor=sel)
                    break

            if not cards_achados:
                # Diagnóstico: salva screenshot + HTML pra inspeção
                from pathlib import Path
                debug_dir = Path(cfg.config_dir) / "debug"
                debug_dir.mkdir(parents=True, exist_ok=True)
                stamp = int(time.time())
                try:
                    driver.save_screenshot(str(debug_dir / f"ml_pag{pag}_{stamp}.png"))
                    (debug_dir / f"ml_pag{pag}_{stamp}.html").write_text(
                        driver.page_source[:200_000], encoding="utf-8", errors="ignore",
                    )
                    log.warning("ml.diagnostico_salvo",
                                numero=pag, dir=str(debug_dir),
                                url_final=driver.current_url,
                                titulo=driver.title)
                except Exception as e:
                    log.debug("ml.diagnostico_falhou", erro=str(e))
                continue

            produtos = _extrair_cards_da_pagina(driver)

            for p in produtos:
                if p["item_id"] in vistos:
                    continue
                vistos.add(p["item_id"])
                todos.append(p)
                if len(todos) >= max_produtos:
                    break

            log.info("ml.pagina_concluida",
                     numero=pag, extraidos_na_pagina=len(produtos),
                     total_ate_agora=len(todos))

            if len(todos) >= max_produtos:
                break

    finally:
        try:
            driver.quit()
        except Exception:
            pass

    return todos[:max_produtos]


# ============================================================
# Handler async — chamado pelo WSClient
# ============================================================

async def executar_busca(msg: dict[str, Any], cfg: Config) -> dict[str, Any]:
    """
    Handler do comando `iniciar_busca_ml`. Retorna dict no formato esperado
    pelo WSClient (`{ok, ...}` → vira `tarefa_concluida` automaticamente).
    """
    busca_id     = msg.get("busca_id")
    tarefa_id    = msg.get("tarefa_id")
    tipo_entrada = msg.get("tipo_entrada", "termo")
    entrada      = msg.get("entrada", "")
    max_paginas  = int(msg.get("max_paginas", 3))
    max_produtos = int(msg.get("max_produtos", 50))

    log.info("busca.iniciando",
             busca_id=busca_id, tarefa_id=tarefa_id,
             tipo_entrada=tipo_entrada, entrada=entrada[:80],
             max_paginas=max_paginas, max_produtos=max_produtos)

    try:
        url_inicial = montar_url_inicial(
            tipo_entrada=tipo_entrada, entrada=entrada,
        )
    except ValueError as e:
        return {"ok": False, "erro": f"entrada_invalida: {e}", "tentar_de_novo": False}

    # Selenium é síncrono — roda em thread pra não bloquear o loop async
    try:
        produtos = await asyncio.to_thread(
            _varrer_sync,
            cfg,
            url_inicial=url_inicial,
            max_paginas=max_paginas,
            max_produtos=max_produtos,
        )
    except RuntimeError as e:
        # Erros conhecidos (ex: ML pediu login) — não tenta de novo, retry
        # automático sem login não vai resolver. Admin precisa intervir.
        msg = str(e)
        log.warning("busca.bloqueada", motivo=msg[:200])
        return {
            "ok": False,
            "erro": f"ml_bloqueou: {msg[:300]}",
            "tentar_de_novo": False,
        }
    except Exception as e:
        log.exception("busca.crash_selenium", erro=str(e))
        return {
            "ok": False,
            "erro": f"selenium_crash: {type(e).__name__}: {str(e)[:200]}",
            "tentar_de_novo": True,
        }

    if not produtos:
        return {
            "ok": True,
            "encontrados": 0,
            "ingest": {"recebidos": 0, "criados": 0, "atualizados": 0},
            "detalhe": "nenhum produto extraído",
        }

    # Envia lote pra cloud
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

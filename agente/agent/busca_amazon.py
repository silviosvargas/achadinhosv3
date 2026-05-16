"""
Scraper Amazon Brasil (Fase 16.6).

Estratégia (portada da V2 `src/buscar/amazon.py`):
1. Itera 10 categorias hardcoded de "mais vendidos" em /gp/bestsellers/<cat>
2. Extrai cards (`div.p13n-sc-uncoverable-faceout`) — nome, preço, ASIN
3. Pra cada produto coletado, gera shortlink `amzn.to/XXX` via SiteStripe
   (extensão de afiliado da Amazon — requer login em Associates BR)
4. Fallback: URL canônica + `?tag=<sua_tag>` quando SiteStripe falha

Diferenças importantes vs Shopee:
- Amazon NÃO tem API interna afiliado — só scraping
- SiteStripe é lento (~3-5s por produto, abre cada um individualmente)
- Detecção de bot mais agressiva — undetected-chromedriver é essencial

Pré-condição: user precisa estar logado em
`associados.amazon.com.br` no perfil Chrome dedicado `chrome_perfil_amazon`.
Sem isso, SiteStripe não aparece e cai em fallback genérico.

Sinais de bloqueio Amazon:
- /errors/validateCaptcha — captcha
- /ap/signin — redirect pra login
- Página em branco / 503 — rate limit
"""
from __future__ import annotations

import asyncio
import random
import re
import threading
import time
from typing import Any

import structlog
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import NoSuchElementException, TimeoutException

from agent.config import Config

log = structlog.get_logger(__name__)


# 10 categorias de "mais vendidos" da Amazon BR — mesma lista da V2.
# Tuplas: (nome_categoria, url, comissao_estimada_pct).
# Comissões Amazon variam por categoria — números abaixo são médias
# da tabela oficial Amazon Associates BR (2024-2026).
CATEGORIAS_AMAZON = [
    ("Beleza",            "https://www.amazon.com.br/gp/bestsellers/beauty",      10.0),
    ("Eletrônicos",        "https://www.amazon.com.br/gp/bestsellers/electronics",  3.5),
    ("Casa",               "https://www.amazon.com.br/gp/bestsellers/home",         8.0),
    ("Esportes e Fitness", "https://www.amazon.com.br/gp/bestsellers/sports",       8.0),
    ("Bebês",               "https://www.amazon.com.br/gp/bestsellers/baby",         8.0),
    ("Brinquedos",         "https://www.amazon.com.br/gp/bestsellers/toys",         5.0),
    ("Cozinha",            "https://www.amazon.com.br/gp/bestsellers/kitchen",      8.0),
    ("Eletrodomésticos",   "https://www.amazon.com.br/gp/bestsellers/appliances",   5.0),
    ("Informática",        "https://www.amazon.com.br/gp/bestsellers/computers",    3.0),
    ("Ferramentas",        "https://www.amazon.com.br/gp/bestsellers/tools",        5.0),
]

URL_BESTSELLERS = "https://www.amazon.com.br/gp/bestsellers/"

PRODUTOS_POR_CAT = 5      # 10 cats × 5 = ~50 candidatos brutos
TIMEOUT_PAGINA   = 20
ESPERA_CARDS_MS  = 2000

# Lock — só 1 Chrome Amazon por vez (mesmo perfil)
_LOCK_CHROME_AMAZON = threading.Lock()


# ============================================================
# Driver Amazon
# ============================================================

def _criar_driver_amazon(cfg: Config) -> uc.Chrome:
    """Chrome dedicado pra Amazon, perfil persistente com sessão Associates."""
    opts = uc.ChromeOptions()
    # Amazon é MUITO sensível a headless — sempre visible
    opts.add_argument(f"--user-data-dir={cfg.chrome_perfil_amazon}")
    opts.add_argument("--profile-directory=Default")
    opts.add_argument("--no-first-run")
    opts.add_argument("--no-default-browser-check")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1366,900")
    # User-agent normal (sem mexer pra não chamar atenção)
    return uc.Chrome(options=opts, use_subprocess=True)


# ============================================================
# Detecção de bloqueio + login
# ============================================================

_SINAIS_BLOQUEIO = (
    "/errors/validatecaptcha",
    "/ap/signin",
    "captcha",
    "cf-error",
)


def _detectou_bloqueio(driver: uc.Chrome) -> tuple[str, str] | None:
    """Retorna (motivo, instrucao_user) ou None se OK."""
    url = (driver.current_url or "").lower()
    titulo = (driver.title or "").lower()

    if "validatecaptcha" in url or "captcha" in url or "captcha" in titulo:
        return (
            "captcha",
            "🤖 Amazon pediu CAPTCHA. Resolva o desafio nesta janela do Chrome.",
        )
    if "/ap/signin" in url or "signin" in titulo:
        return (
            "login_expirado",
            "🔐 Faça login na sua conta Amazon Associates aqui, depois volte pra "
            "Mais Vendidos. Vou esperar você terminar.",
        )
    if any(s in url for s in _SINAIS_BLOQUEIO):
        return (
            "bloqueio",
            "⚠ Amazon bloqueou o acesso. Pode ser rate limit — aguarde alguns minutos.",
        )
    return None


# ============================================================
# Banner Chrome + aviso dashboard (reutiliza estratégia do Shopee)
# ============================================================

_BANNER_JS = """
(function(mensagem) {
  var antigo = document.getElementById('achadinhos-aviso');
  if (antigo) antigo.remove();
  var d = document.createElement('div');
  d.id = 'achadinhos-aviso';
  d.style.cssText = (
    'position:fixed;top:0;left:0;right:0;z-index:2147483647;' +
    'background:linear-gradient(90deg,#f59e0b,#fbbf24);color:#1f2937;' +
    'padding:14px 20px;font-family:-apple-system,BlinkMacSystemFont,sans-serif;' +
    'font-size:15px;font-weight:600;text-align:center;' +
    'box-shadow:0 4px 16px rgba(0,0,0,0.25);border-bottom:2px solid #d97706;'
  );
  d.textContent = mensagem;
  function attach() {
    if (document.body) document.body.appendChild(d);
    else setTimeout(attach, 100);
  }
  attach();
})(arguments[0]);
"""


def _mostrar_banner(driver: uc.Chrome, mensagem: str) -> None:
    try:
        driver.execute_script(_BANNER_JS, mensagem)
        driver.execute_script("window.focus();")
        driver.maximize_window()
    except Exception as e:
        log.debug("amazon.banner_falhou", erro=str(e)[:120])


def _remover_banner(driver: uc.Chrome) -> None:
    try:
        driver.execute_script(
            "var b=document.getElementById('achadinhos-aviso');if(b)b.remove();"
        )
    except Exception:
        pass


CAPTCHA_ESPERA_FIXA_SEG = 30
CAPTCHA_MAX_TENTATIVAS  = 3
LOGIN_TIMEOUT_SEG       = 300


def _aguardar_login(driver: uc.Chrome, *, mensagem: str) -> bool:
    """Polling até URL voltar pra bestsellers ou timeout 5min."""
    from agent import avisos

    log.warning("amazon.precisa_login", url=(driver.current_url or "")[:200])
    _mostrar_banner(driver, mensagem)
    avisos.publicar(
        "login_expirado", mensagem,
        detalhe="Abra o Chrome do agente e logue na sua conta Amazon Associates.",
        marketplace="amazon", ttl_seg=LOGIN_TIMEOUT_SEG + 30,
    )

    inicio = time.time()
    try:
        while time.time() - inicio < LOGIN_TIMEOUT_SEG:
            time.sleep(5)
            try:
                url_atual = (driver.current_url or "").lower()
            except Exception:
                return False
            if "bestsellers" in url_atual or "/gp/" in url_atual:
                log.info("amazon.user_logou",
                         duracao=int(time.time() - inicio))
                _remover_banner(driver)
                time.sleep(2)
                return True
            if not any(s in url_atual for s in _SINAIS_BLOQUEIO):
                try:
                    driver.get(URL_BESTSELLERS)
                    time.sleep(3)
                except Exception:
                    pass
                return True
        log.warning("amazon.timeout_aguardando_login")
        return False
    finally:
        avisos.limpar(marketplace="amazon")


def _aguardar_captcha(driver: uc.Chrome, *, mensagem: str) -> bool:
    """30s fixos × até 3 tentativas (igual Shopee)."""
    from agent import avisos

    for tentativa in range(1, CAPTCHA_MAX_TENTATIVAS + 1):
        msg_tentativa = (
            f"{mensagem}\n\nTentativa {tentativa}/{CAPTCHA_MAX_TENTATIVAS} — "
            f"aguardando {CAPTCHA_ESPERA_FIXA_SEG}s..."
        )
        log.warning("amazon.captcha_tentativa",
                    tentativa=tentativa, max=CAPTCHA_MAX_TENTATIVAS)
        _mostrar_banner(driver, msg_tentativa)
        avisos.publicar(
            "captcha", mensagem,
            detalhe=f"Tentativa {tentativa}/{CAPTCHA_MAX_TENTATIVAS}",
            marketplace="amazon", ttl_seg=CAPTCHA_ESPERA_FIXA_SEG + 30,
        )

        time.sleep(CAPTCHA_ESPERA_FIXA_SEG)

        try:
            driver.get(URL_BESTSELLERS)
            time.sleep(3)
        except Exception as e:
            log.warning("amazon.captcha_recarga_falhou", erro=str(e)[:120])
            continue

        problema = _detectou_bloqueio(driver)
        if problema is None:
            log.info("amazon.captcha_resolvido", tentativa=tentativa)
            _remover_banner(driver)
            avisos.limpar(marketplace="amazon")
            return True
        if problema[0] == "login_expirado":
            avisos.limpar(marketplace="amazon")
            return False
        log.info("amazon.captcha_persiste", tentativa=tentativa)

    log.warning("amazon.captcha_esgotou_tentativas")
    avisos.limpar(marketplace="amazon")
    return False


def _resolver_bloqueio(
    driver: uc.Chrome, motivo: str, mensagem: str,
) -> bool:
    if motivo == "captcha":
        return _aguardar_captcha(driver, mensagem=mensagem)
    return _aguardar_login(driver, mensagem=mensagem)


# ============================================================
# Parsing dos cards
# ============================================================

_RE_ASIN = re.compile(r"/dp/([A-Z0-9]{10})")


def _extrair_asin(url_ou_id: str) -> str | None:
    """ASIN é alfanumérico 10 chars. Vem do id do card ou do path /dp/ASIN."""
    if not url_ou_id:
        return None
    s = url_ou_id.strip()
    # Padrão direto: 10 chars maiúsculos
    if len(s) == 10 and s.isalnum() and s.isupper():
        return s
    m = _RE_ASIN.search(s)
    if m:
        return m.group(1)
    return None


def _limpar_preco(s: str) -> float:
    """Converte string '199,90' / 'R$ 1.299,00' → float."""
    if not s:
        return 0.0
    m = re.search(r"(\d[\d.]*[,.]?\d*)", s.replace("R$", "").strip())
    if not m:
        return 0.0
    raw = m.group(1)
    # Formato BR: "1.299,00" → "1299.00"; "199,90" → "199.90"
    if "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return 0.0


def _achar_nome_card(card) -> str:
    """Tenta múltiplos seletores pra nome do produto."""
    seletores = [
        "a.a-link-normal span",
        "div._cDEzb_p13n-sc-css-line-clamp-3_g3dy1 span",
        "div[class*='p13n-sc-truncate'] span",
        "div[class*='p13n-grid-cell'] span",
    ]
    for sel in seletores:
        try:
            for el in card.find_elements(By.CSS_SELECTOR, sel):
                t = (el.text or "").strip()
                if len(t) > 8 and "R$" not in t:
                    return t
        except Exception:
            continue
    # Fallback: pega 1ª linha longa do texto bruto do card
    linhas = [l.strip() for l in (card.text or "").split("\n") if len(l.strip()) > 10]
    return linhas[0] if linhas else ""


def _achar_preco_card(card) -> float:
    """Preço atual do card."""
    seletores = [
        "span.p13n-sc-price",
        "span._cDEzb_p13n-sc-price_3mJ9Z",
        "span[class*='p13n-sc-price']",
        "span.a-price > span.a-offscreen",
    ]
    for sel in seletores:
        try:
            for el in card.find_elements(By.CSS_SELECTOR, sel):
                txt = (el.text or el.get_attribute("textContent") or "").strip()
                p = _limpar_preco(txt)
                if p > 0:
                    return p
        except Exception:
            continue
    # Fallback: regex no texto completo
    m = re.search(r"R\$\s*(\d{1,4}[.,]?\d{0,3}[.,]\d{2})", card.text or "")
    if m:
        return _limpar_preco(m.group(1))
    return 0.0


def _achar_url_card(card) -> str | None:
    """Link clicável do card. Limpa query/fragment (igual ML)."""
    try:
        for a in card.find_elements(By.CSS_SELECTOR, "a[href*='/dp/']"):
            href = a.get_attribute("href") or ""
            href = href.split("?", 1)[0].split("#", 1)[0]
            if "amazon.com.br" in href and "/dp/" in href:
                return href
    except Exception:
        pass
    return None


def _achar_foto_card(card) -> str | None:
    try:
        for img in card.find_elements(By.CSS_SELECTOR, "img"):
            src = img.get_attribute("src") or img.get_attribute("data-src") or ""
            if src.startswith("http") and "media-amazon" in src:
                return src
    except Exception:
        pass
    return None


def _extrair_card(
    card, *, categoria: str, comissao_est: float, vistos: set[str],
) -> dict[str, Any] | None:
    """Extrai 1 produto do card de bestsellers da Amazon. Retorna None se inválido."""
    # ASIN — atributo `id` ou URL /dp/
    asin = (card.get_attribute("id") or "").strip()
    if not asin or len(asin) != 10:
        url_anchor = _achar_url_card(card)
        if url_anchor:
            asin = _extrair_asin(url_anchor) or ""
    if not asin:
        return None
    if asin in vistos:
        return None

    nome = _achar_nome_card(card)
    if not nome:
        return None

    preco = _achar_preco_card(card)
    if preco <= 0 or preco > 100_000:  # sanity (limite alto pra eletrônicos)
        return None

    url_canonica = f"https://www.amazon.com.br/dp/{asin}"
    foto = _achar_foto_card(card)

    return {
        "plataforma":   "amazon",
        "item_id":      asin,
        "nome":         nome[:500],
        "preco":        preco,
        "preco_orig":   None,    # Amazon raramente mostra preço original em bestsellers
        "desconto":     None,
        "comissao":     comissao_est,
        "frete_gratis": False,
        "categoria":    categoria,
        "url_canonica": url_canonica,
        "url_afiliado": None,    # preenchido depois via SiteStripe
        "foto_url":     foto,
    }


# ============================================================
# SiteStripe — gera amzn.to por produto
# ============================================================

def _gerar_link_sitestripe(driver: uc.Chrome, asin: str) -> str | None:
    """
    Abre /dp/<asin>, clica no botão SiteStripe e captura amzn.to/XXX.

    Retorna URL afiliada (`amzn.to/...` ou `amazon.com.br/...?tag=...`)
    ou None se falhou (caller aplica fallback).
    """
    try:
        driver.get(f"https://www.amazon.com.br/dp/{asin}")
        wait = WebDriverWait(driver, 12)

        # Bloqueio? Aborta
        if _detectou_bloqueio(driver):
            return None

        # Botão "Obter link" do SiteStripe
        try:
            btn = wait.until(EC.element_to_be_clickable(
                (By.ID, "amzn-ss-get-link-button"),
            ))
        except TimeoutException:
            # SiteStripe não apareceu — provavelmente não logado
            return None

        time.sleep(0.6)
        driver.execute_script("arguments[0].click();", btn)
        time.sleep(2)

        # Captura o amzn.to/XXX gerado em qualquer textarea/input
        for sel in [
            "input[id*='shortlink']",
            "textarea[id*='link']",
            "input[value*='amzn']",
            "textarea[value*='amzn']",
        ]:
            try:
                for el in driver.find_elements(By.CSS_SELECTOR, sel):
                    val = (el.get_attribute("value") or el.text or "").strip()
                    if "amzn.to" in val:
                        return val
                    if "amazon.com.br" in val and "tag=" in val:
                        return val
            except Exception:
                continue
    except Exception as e:
        log.debug("amazon.sitestripe_falhou", asin=asin, erro=str(e)[:120])
    return None


def _gerar_links_em_lote(
    driver: uc.Chrome, produtos: list[dict[str, Any]],
) -> int:
    """
    Pra cada produto, abre o /dp/ASIN e captura amzn.to via SiteStripe.
    Atualiza `produto["url_afiliado"]` in-place.

    Retorna número de links oficiais (amzn.to) gerados.
    """
    if not produtos:
        return 0
    gerados = 0
    url_anterior = driver.current_url

    log.info("amazon.sitestripe.iniciando", total=len(produtos))
    for i, p in enumerate(produtos, start=1):
        asin = p.get("item_id")
        if not asin:
            continue
        link = _gerar_link_sitestripe(driver, asin)
        if link and "amzn.to" in link:
            p["url_afiliado"] = link
            gerados += 1
            log.info("amazon.sitestripe.ok",
                     n=i, total=len(produtos), asin=asin, link=link[:60])
        else:
            log.info("amazon.sitestripe.fallback",
                     n=i, total=len(produtos), asin=asin)
        # Espaça pra não estourar rate limit
        time.sleep(random.uniform(1.0, 1.8))

    # Tenta voltar pra página anterior pra não atrapalhar próxima iteração
    try:
        driver.get(url_anterior or URL_BESTSELLERS)
        time.sleep(1)
    except Exception:
        pass

    return gerados


# ============================================================
# Loop principal de busca
# ============================================================

def _varrer_sync(
    cfg: Config, *, max_produtos: int = 50,
) -> list[dict[str, Any]]:
    """Loop síncrono — itera categorias e gera links via SiteStripe."""
    log.info("amazon.aguardando_lock", max_produtos=max_produtos)
    with _LOCK_CHROME_AMAZON:
        log.info("amazon.lock_adquirido")
        driver = _criar_driver_amazon(cfg)
        todos: list[dict[str, Any]] = []
        vistos_asin: set[str] = set()

        try:
            # 1. Carrega bestsellers (autentica via cookies salvos)
            driver.get(URL_BESTSELLERS)
            time.sleep(3)
            problema = _detectou_bloqueio(driver)
            if problema:
                motivo, msg = problema
                if not _resolver_bloqueio(driver, motivo, msg):
                    raise RuntimeError(
                        f"Amazon {motivo} — não foi possível prosseguir. "
                        "Refaça login: `python -m agent.login_amazon`."
                    )

            # 2. Itera categorias
            por_cat = max(2, max_produtos // len(CATEGORIAS_AMAZON) + 1)

            for nome_cat, url_cat, comissao_est in CATEGORIAS_AMAZON:
                if len(todos) >= max_produtos:
                    break
                log.info("amazon.categoria", nome=nome_cat, url=url_cat)
                try:
                    driver.get(url_cat)
                except Exception as e:
                    log.warning("amazon.get_falhou", cat=nome_cat, erro=str(e)[:120])
                    continue

                # Espera cards aparecerem (timeout curto pra não travar)
                try:
                    WebDriverWait(driver, 15).until(
                        EC.presence_of_element_located(
                            (By.CSS_SELECTOR, "div.p13n-sc-uncoverable-faceout, "
                                              "div[id^='B0']"),
                        )
                    )
                except TimeoutException:
                    log.warning("amazon.timeout_cards", cat=nome_cat)
                    # Pode ter bloqueio
                    problema = _detectou_bloqueio(driver)
                    if problema:
                        if not _resolver_bloqueio(driver, *problema):
                            raise RuntimeError("Amazon bloqueou no meio da busca.")
                        # Retry da categoria
                        driver.get(url_cat)
                        time.sleep(3)
                    else:
                        continue

                # Scroll leve pra lazy-load
                try:
                    for pos in (600, 1500, 2400):
                        driver.execute_script(f"window.scrollTo(0,{pos});")
                        time.sleep(0.3)
                    driver.execute_script("window.scrollTo(0,0);")
                    time.sleep(0.3)
                except Exception:
                    pass

                # Coleta cards
                cards = driver.find_elements(
                    By.CSS_SELECTOR, "div.p13n-sc-uncoverable-faceout",
                )
                if not cards:
                    # Fallback: tentar outro seletor
                    cards = driver.find_elements(By.CSS_SELECTOR, "div[id^='B0']")

                adicionados = 0
                for card in cards:
                    if len(todos) >= max_produtos or adicionados >= por_cat:
                        break
                    produto = _extrair_card(
                        card, categoria=nome_cat,
                        comissao_est=comissao_est, vistos=vistos_asin,
                    )
                    if not produto:
                        continue
                    vistos_asin.add(produto["item_id"])
                    todos.append(produto)
                    adicionados += 1

                log.info("amazon.categoria_ok",
                         nome=nome_cat, extraidos=adicionados, total=len(todos))
                time.sleep(random.uniform(1.5, 3))

            # 3. Gera links via SiteStripe pra TODOS os produtos coletados
            if todos:
                gerados = _gerar_links_em_lote(driver, todos)
                log.info("amazon.sitestripe.concluido",
                         total=len(todos), com_link=gerados,
                         sem_link_pct=int(100 * (len(todos) - gerados) / max(1, len(todos))))

        finally:
            try:
                driver.quit()
            except Exception:
                pass
            time.sleep(1.0)

        return todos


# ============================================================
# Async wrapper
# ============================================================

async def buscar_amazon(
    cfg: Config, *, max_produtos: int = 50,
) -> list[dict[str, Any]]:
    """
    Async wrapper — Selenium em thread separada.

    Retorna lista de produtos no formato V3 com `url_afiliado` preenchido
    via SiteStripe quando possível, ou None se falhou (servidor aplica
    fallback `?tag=<sua_tag>`).
    """
    log.info("amazon.iniciando", max_produtos=max_produtos)
    produtos = await asyncio.to_thread(_varrer_sync, cfg, max_produtos=max_produtos)
    com_afiliado = sum(1 for p in produtos if p.get("url_afiliado") and "amzn.to" in (p.get("url_afiliado") or ""))
    log.info("amazon.concluido",
             total=len(produtos), com_amzn_to=com_afiliado)
    return produtos

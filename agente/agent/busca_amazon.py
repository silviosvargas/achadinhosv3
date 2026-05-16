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


def _verificar_login_amazon(driver: uc.Chrome) -> tuple[str, str] | None:
    """
    Confere se o user está logado em Amazon Associates ANTES de começar a busca.

    Visita uma página de Associates (account-home) que SÓ carrega se logado;
    se não tem cookie, redireciona pra /ap/signin. Mais confiável que abrir
    bestsellers (acessível sem login).

    Retorna (motivo, instrucao_user) se NÃO logado, None se OK.
    """
    try:
        # Página de home do programa Associates BR — só acessível logado
        driver.get("https://associados.amazon.com.br/home")
        time.sleep(3)
    except Exception as e:
        log.warning("amazon.verificar_login_falhou", erro=str(e)[:120])
        return ("erro_rede", "⚠ Não consegui acessar Amazon — verifique conexão.")

    problema = _detectou_bloqueio(driver)
    if problema:
        return problema

    # Sanity adicional: verifica se conseguiu chegar em alguma URL de Associates
    # ou bestsellers logado. Se foi parar em um signin custom, ainda pega.
    url = (driver.current_url or "").lower()
    if "signin" in url or "/ap/signin" in url:
        return (
            "login_expirado",
            "🔐 Faça login na sua conta Amazon Associates aqui. Vou esperar.",
        )

    log.info("amazon.login_confirmado", url=url[:200])
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


ESPERA_FIXA_SEG = 30
MAX_TENTATIVAS  = 3


def _aguardar_com_retry(
    driver: uc.Chrome,
    *,
    tipo_aviso: str,
    mensagem: str,
    url_revalidacao: str = URL_BESTSELLERS,
) -> bool:
    """
    Estratégia ÚNICA pra captcha + login: 30s fixos × até 3 tentativas.

    Pra cada tentativa:
    1. Mostra banner amarelo no Chrome com a instrução
    2. Publica aviso no dashboard (toast persistente)
    3. Espera 30s FIXOS (independente do que user fizer — porque captcha às
       vezes abre nova janela e polling falha; e pra login dá tempo de logar)
    4. Recarrega URL de validação
    5. Verifica se desbloqueou — se sim, retorna True
    6. Se não, próxima tentativa

    Retorna True se desbloqueou em até 3 tentativas, False senão.
    """
    from agent import avisos

    log.warning("amazon.aguardando_intervencao",
                tipo=tipo_aviso, url_atual=(driver.current_url or "")[:200])

    try:
        for tentativa in range(1, MAX_TENTATIVAS + 1):
            msg_tentativa = (
                f"{mensagem}\n\nTentativa {tentativa}/{MAX_TENTATIVAS} — "
                f"aguardando {ESPERA_FIXA_SEG}s para você resolver..."
            )
            log.warning(f"amazon.{tipo_aviso}.tentativa",
                        tentativa=tentativa, max=MAX_TENTATIVAS,
                        espera_seg=ESPERA_FIXA_SEG)

            _mostrar_banner(driver, msg_tentativa)
            avisos.publicar(
                tipo_aviso, mensagem,
                detalhe=f"Tentativa {tentativa}/{MAX_TENTATIVAS} — "
                        f"aguardando {ESPERA_FIXA_SEG}s. "
                        f"Após o login/resolução, vou re-testar automaticamente.",
                marketplace="amazon", ttl_seg=ESPERA_FIXA_SEG + 30,
            )

            # Espera fixa de 30s — não polling, pra captcha em nova aba/janela
            time.sleep(ESPERA_FIXA_SEG)

            # Revalida acessando a URL de teste
            try:
                driver.get(url_revalidacao)
                time.sleep(3)
            except Exception as e:
                log.warning(f"amazon.{tipo_aviso}.revalidacao_falhou",
                            erro=str(e)[:120])
                continue

            problema = _detectou_bloqueio(driver)
            if problema is None:
                # Pra login_expirado: confirma com verificação dupla via /home
                if tipo_aviso == "login_expirado":
                    verif = _verificar_login_amazon(driver)
                    if verif is None:
                        log.info("amazon.login_confirmado_apos_tentativa",
                                 tentativa=tentativa)
                        _remover_banner(driver)
                        return True
                    # Ainda não logou — segue pra próxima tentativa
                    log.info(f"amazon.{tipo_aviso}.persiste", tentativa=tentativa)
                    continue
                log.info(f"amazon.{tipo_aviso}.resolvido", tentativa=tentativa)
                _remover_banner(driver)
                return True

            # Se mudou de problema (login → captcha ou vice-versa), continua
            # tentando com a mesma estratégia
            log.info(f"amazon.{tipo_aviso}.persiste",
                     tentativa=tentativa, problema_atual=problema[0])

        log.warning(f"amazon.{tipo_aviso}.esgotou_tentativas",
                    tentativas=MAX_TENTATIVAS)
        return False
    finally:
        avisos.limpar(marketplace="amazon")


def _resolver_bloqueio(
    driver: uc.Chrome, motivo: str, mensagem: str,
) -> bool:
    """Encaminha pro retry padronizado. Captcha e login compartilham estratégia."""
    return _aguardar_com_retry(
        driver,
        tipo_aviso=motivo,
        mensagem=mensagem,
        url_revalidacao=URL_BESTSELLERS,
    )


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
    rank: int | None = None,
) -> dict[str, Any] | None:
    """Extrai 1 produto do card de bestsellers da Amazon. Retorna None se inválido.

    Fase 18: `rank` (1..N na lista de bestsellers) é usado como proxy de
    vendas — quanto melhor o rank, mais vendido. Sem número absoluto porque
    Amazon não expõe.
    """
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

    # Fase 18 — proxy de vendas baseado em rank. Bestseller rank 1 → 5000,
    # rank 50 → 100. Log decay pra dar curva realista. Sem rank → 1000.
    total_vendidos = _rank_para_vendas_estimadas(rank)

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
        # Fase 18 — todo produto Amazon vem de /gp/bestsellers, é bestseller
        # por definição. Comissão veio da tabela oficial por categoria, não
        # do produto específico — fonte "amazon_tabela" diz isso ao servidor.
        "total_vendidos": total_vendidos,
        "is_bestseller":  True,
        "comissao_fonte": "amazon_tabela",
    }


def _rank_para_vendas_estimadas(rank: int | None) -> int:
    """Converte posição no bestseller (1..50) em estimativa de volume vendido.

    Sem dado real disponível na Amazon — usa curva inversa pra que rank 1
    pareça muito vendido e rank 50 ainda razoável.

    rank=1  → 5000      rank=10 → 1500      rank=50 → 100
    rank=None / inválido → 1000 (default conservador)
    """
    if rank is None or rank <= 0:
        return 1000
    # Inversa decay-style. Tunada pra dar valores razoáveis em log10.
    return max(int(5500 / rank), 100)


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
            # 1. PRIMEIRO: verifica se está logado em Amazon Associates.
            # SiteStripe só renderiza se logado; sem login, geramos só fallback.
            # Estratégia: tenta /home do Associates (página protegida) → se
            # redireciona pra signin, pede login com banner + 30s × 3 retry.
            log.info("amazon.verificando_login")
            problema_login = _verificar_login_amazon(driver)
            if problema_login:
                motivo, msg = problema_login
                if not _resolver_bloqueio(driver, motivo, msg):
                    raise RuntimeError(
                        f"Amazon {motivo} — usuário não resolveu em "
                        f"{MAX_TENTATIVAS} tentativas de {ESPERA_FIXA_SEG}s. "
                        "Rode `python -m agent.login_amazon` pra logar manualmente."
                    )

            # 2. Carrega bestsellers (autentica via cookies salvos)
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
                # Fase 18: enumerate dá o rank do produto na lista de bestsellers
                # (1..N). Passamos pro _extrair_card que converte em proxy de vendas.
                for rank_pos, card in enumerate(cards, start=1):
                    if len(todos) >= max_produtos or adicionados >= por_cat:
                        break
                    produto = _extrair_card(
                        card, categoria=nome_cat,
                        comissao_est=comissao_est, vistos=vistos_asin,
                        rank=rank_pos,
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

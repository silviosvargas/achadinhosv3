"""
Scraper Shopee via API interna do painel de afiliados (Fase 16.5).

Diferente do ML: a Shopee tem uma API REST interna em
`affiliate.shopee.com.br/api/v3/offer/product/list` que retorna o
`long_link` JÁ COM A TAG DO AFILIADO LOGADO. Não precisa segundo
passo de linkbuilder (como o `meli.la` no ML).

Portado da V2 (`src/buscar/shopee.py`), adaptado pra V3:
- Função pura `buscar_shopee_sync(cfg, max_produtos)` retorna lista
  de produtos no formato V3 (`url_canonica`, `url_afiliado`, etc).
- Sem dependência de planilha/historico (V3 ingere via REST no servidor).
- Reusa o lock de Chrome se necessário; perfil dedicado `chrome_perfil_shopee`.

Pré-condição: user precisa logar UMA VEZ no painel afiliados Shopee
(`affiliate.shopee.com.br/offer/product_offer`) usando o perfil Chrome
dedicado do agente. Sem cookies de sessão, a API responde 401/captcha.

API:
  GET https://affiliate.shopee.com.br/api/v3/offer/product/list
      ?list_type=2          # 1=novidades, 2=melhor performance, 3=promoções
      &sort_type=1
      &page_offset=0
      &page_limit=20
      &client_type=1
      &second_category_id=  # opcional

Resposta (campos relevantes):
  data.list[].item_id                        → ID do produto
  data.list[].long_link                      → URL afiliada (PRONTA)
  data.list[].product_link                   → URL canônica do produto
  data.list[].seller_commission_rate         → "5.50%"
  data.list[].default_commission_rate        → "3.00%"
  data.list[].batch_item_for_item_card_full  → {name, price_min, price_max, ...}
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Any

import structlog
import undetected_chromedriver as uc

from agent.config import Config

log = structlog.get_logger(__name__)


URL_PAINEL = "https://affiliate.shopee.com.br/offer/product_offer"
URL_API    = "https://affiliate.shopee.com.br/api/v3/offer/product/list"

# Abas/list_types da API (V2 só usava 2 = "melhor performance"; mantemos
# como default mais lucrativo, mas estrutura permite mais).
ABAS_BUSCA: list[dict[str, Any]] = [
    {"nome": "Melhor performance", "list_type": 2, "cat": None},
]

PRODUTOS_POR_PAGINA = 20
MAX_PAGINAS         = 30
COMISSAO_MINIMA     = 5.0     # filtra produtos com comissão < 5%

# Lock análogo ao do ML — só 1 Chrome Shopee por vez no agente
_LOCK_CHROME_SHOPEE = threading.Lock()


# ============================================================
# Driver
# ============================================================

def _criar_driver_shopee(cfg: Config) -> uc.Chrome:
    """Chrome dedicado pra Shopee — perfil persistente, sessão preservada."""
    opts = uc.ChromeOptions()
    # Shopee detecta headless mais agressivamente que ML — mantém visível
    opts.add_argument(f"--user-data-dir={cfg.chrome_perfil_shopee}")
    opts.add_argument("--profile-directory=Default")
    opts.add_argument("--no-first-run")
    opts.add_argument("--no-default-browser-check")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1366,900")
    return uc.Chrome(options=opts, use_subprocess=True)


# ============================================================
# Helpers de parsing
# ============================================================

def _parse_pct(s: Any) -> float:
    """'5.50%' → 5.5; '' → 0.0; None → 0.0."""
    try:
        return float(str(s).replace("%", "").strip())
    except (ValueError, AttributeError, TypeError):
        return 0.0


def _normalizar_preco(raw: Any) -> float:
    """Shopee armazena preços em centavos × 100 ou × 100000. Normaliza pra reais."""
    try:
        p = float(raw or 0)
    except (ValueError, TypeError):
        return 0.0
    if p > 100_000:
        p = p / 100_000
    elif p > 1_000:
        p = p / 100
    return p


# ============================================================
# Chamada à API via fetch JS (usa cookies de sessão do painel)
# ============================================================

def _chamar_api(
    driver: uc.Chrome,
    *,
    list_type: int,
    page_offset: int,
    page_limit: int = 20,
    cat: int | None = None,
) -> dict[str, Any]:
    """
    Chama a API interna via `fetch()` dentro do contexto do painel afiliados,
    pra reutilizar cookies de sessão. Retorna `{status, body}`.
    """
    url = (f"{URL_API}?list_type={list_type}&sort_type=1"
           f"&page_offset={page_offset}&page_limit={page_limit}&client_type=1")
    if cat:
        url += f"&second_category_id={cat}"

    # Inicializa estado (apaga lixo de chamada anterior)
    driver.execute_script("""
        window.__shopee_api_done = false;
        window.__shopee_api_result = {status: 0, body: ''};
    """)

    # Dispara fetch async
    driver.execute_script(f"""
        fetch('{url}', {{
            method: 'GET',
            credentials: 'include',
            headers: {{'Content-Type': 'application/json'}}
        }})
        .then(r => {{
            var s = r.status;
            return r.text().then(b => {{
                window.__shopee_api_result = {{status: s, body: b}};
                window.__shopee_api_done = true;
            }});
        }})
        .catch(e => {{
            window.__shopee_api_result = {{status: 0, body: e.toString()}};
            window.__shopee_api_done = true;
        }});
    """)

    # Polling — 15s max
    for _ in range(30):
        time.sleep(0.5)
        if driver.execute_script("return window.__shopee_api_done;"):
            break

    return driver.execute_script("return window.__shopee_api_result;") or {
        "status": 0, "body": "",
    }


# ============================================================
# Parsing dos itens da API
# ============================================================

def _item_pra_produto_v3(
    item: dict[str, Any], *, categoria_nome: str,
) -> dict[str, Any] | None:
    """
    Converte 1 item da resposta da API em dict no formato V3 que o
    `ingest_client.enviar_lote` espera.

    Retorna None se produto não passa nos filtros (comissão mínima, sem nome,
    sem link, preço 0, etc).
    """
    try:
        item_id    = str(item.get("item_id", "") or "").strip()
        long_link  = (item.get("long_link") or "").strip()
        link_prod  = (item.get("product_link") or "").strip()

        # Comissão = melhor entre as 3 taxas
        comissao = max(
            _parse_pct(item.get("seller_commission_rate", "0%")),
            _parse_pct(item.get("default_commission_rate", "0%")),
            _parse_pct(item.get("max_commission_rate", "0%")),
        )
        if comissao < COMISSAO_MINIMA:
            return None

        # Card aninhado tem nome + preço + foto
        card = item.get("batch_item_for_item_card_full") or {}
        nome = (
            card.get("name")
            or card.get("item_name")
            or card.get("title")
            or ""
        ).strip()
        if not nome:
            return None

        preco = _normalizar_preco(
            card.get("price_min", 0)
            or card.get("price", 0)
            or card.get("min_price", 0)
            or 0
        )
        if preco <= 0:
            return None

        preco_orig = _normalizar_preco(
            card.get("price_max", 0)
            or card.get("original_price", 0)
            or (card.get("price_min", 0) or 0)
        )
        if preco_orig < preco:
            preco_orig = preco

        desconto = round((1 - preco / preco_orig) * 100, 1) if preco_orig > preco else 0.0

        # Foto principal (campo varia, tentamos múltiplos)
        imagem = (
            card.get("image")
            or card.get("image_url")
            or item.get("image")
            or item.get("offer_image")
            or None
        )
        if imagem and not imagem.startswith("http"):
            # Shopee às vezes devolve só o hash — monta URL CDN
            imagem = f"https://down-br.img.susercontent.com/file/{imagem}"

        # Limpa URL canônica (fragment + query estranhos)
        if link_prod:
            link_prod = link_prod.split("?", 1)[0].split("#", 1)[0]

        return {
            "plataforma":   "shopee",
            "item_id":      item_id,
            "nome":         nome[:500],
            "preco":        preco,
            "preco_orig":   preco_orig if preco_orig > preco else None,
            "desconto":     desconto if desconto > 0 else None,
            "comissao":     comissao,
            "frete_gratis": False,    # API não traz esse campo de forma confiável
            "categoria":    categoria_nome,
            "url_canonica": link_prod or None,
            "url_afiliado": long_link or None,   # Shopee devolve PRONTO!
            "foto_url":     imagem if isinstance(imagem, str) and imagem.startswith("http") else None,
        }
    except Exception as e:
        log.debug("shopee.item_falhou", erro=str(e)[:120])
        return None


# ============================================================
# Loop principal de busca
# ============================================================

def _detectou_login_ou_captcha(driver: uc.Chrome) -> tuple[str, str] | None:
    """Detecta se o painel pediu login ou captcha.

    Retorna (motivo, instrucao_user) ou None se tudo OK.
    `motivo` vai pros logs; `instrucao_user` é mostrada num banner no Chrome.
    """
    url = (driver.current_url or "").lower()
    if "login" in url or "buyer/login" in url:
        return (
            "login_expirado",
            "🔐 Faça login na sua conta Shopee Afiliados, depois volte para a página "
            "Ofertas de Produtos. Vou esperar você terminar.",
        )
    if "captcha" in url or "verify" in url or "vcode" in url:
        return (
            "captcha",
            "🤖 Shopee pediu CAPTCHA. Resolva o desafio nesta janela do Chrome. "
            "Quando voltar pra Ofertas de Produtos, vou continuar.",
        )
    return None


# JS que injeta um banner amarelo fixo no topo da página com instruções.
# Mantém em strings escapadas pra passar pro driver.execute_script.
_BANNER_AVISO_JS = """
(function(mensagem) {
  // Remove banner anterior se existir
  var antigo = document.getElementById('achadinhos-aviso');
  if (antigo) antigo.remove();
  // Cria div fixo no topo
  var d = document.createElement('div');
  d.id = 'achadinhos-aviso';
  d.style.cssText = (
    'position:fixed;top:0;left:0;right:0;z-index:2147483647;' +
    'background:linear-gradient(90deg,#f59e0b,#fbbf24);' +
    'color:#1f2937;padding:14px 20px;' +
    'font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;' +
    'font-size:15px;font-weight:600;text-align:center;' +
    'box-shadow:0 4px 16px rgba(0,0,0,0.25);' +
    'border-bottom:2px solid #d97706;'
  );
  d.textContent = mensagem;
  // Aguarda body existir (algumas páginas de login têm body só depois do JS)
  function attach() {
    if (document.body) {
      document.body.appendChild(d);
    } else {
      setTimeout(attach, 100);
    }
  }
  attach();
})(arguments[0]);
"""


def _aguardar_resolucao_manual(
    driver: uc.Chrome,
    *,
    motivo: str,
    mensagem_usuario: str,
    timeout_seg: int = 300,
) -> bool:
    """
    Mostra banner no Chrome com instruções e fica em polling até o user
    resolver (URL voltar pra painel afiliados) ou estourar `timeout_seg`.

    Igual V2 (`src/buscar/shopee.py:344`) — que fazia `input("ENTER")`.
    Aqui não temos stdin (agente roda em background), então pollamos a URL.

    Retorna True se user resolveu dentro do timeout, False senão.
    """
    log.warning(
        "shopee.precisa_intervencao_manual",
        motivo=motivo, timeout_seg=timeout_seg,
        url_atual=(driver.current_url or "")[:200],
    )

    # 1. Injeta banner amarelo no Chrome com instruções
    try:
        driver.execute_script(_BANNER_AVISO_JS, mensagem_usuario)
    except Exception as e:
        log.debug("shopee.banner_falhou", erro=str(e)[:120])

    # 2. Tenta trazer a janela pro primeiro plano (best-effort)
    try:
        driver.execute_script("window.focus();")
        driver.maximize_window()
    except Exception:
        pass

    # 3. Polling de 5s até URL voltar pra painel ou timeout
    inicio = time.time()
    while time.time() - inicio < timeout_seg:
        time.sleep(5)
        try:
            url_atual = (driver.current_url or "").lower()
        except Exception:
            # Provavelmente user fechou a janela
            log.warning("shopee.janela_fechada_durante_espera")
            return False

        # Resolveu se voltou pra painel afiliados ou saiu de login/captcha
        if "/offer/product_offer" in url_atual or "/offer/" in url_atual:
            log.info("shopee.user_resolveu",
                     duracao=int(time.time() - inicio), url=url_atual[:120])
            # Remove banner — não polui screenshots posteriores
            try:
                driver.execute_script(
                    "var b=document.getElementById('achadinhos-aviso');"
                    "if(b)b.remove();"
                )
            except Exception:
                pass
            time.sleep(2)   # estabiliza após user clicar
            return True

        # Se URL deixou de ter login/captcha, recarrega o painel
        if not any(
            s in url_atual for s in ("login", "captcha", "verify", "vcode")
        ):
            log.info("shopee.fora_de_login_recarregando", url=url_atual[:120])
            try:
                driver.get(URL_PAINEL)
                time.sleep(3)
            except Exception:
                pass
            return True

    log.warning("shopee.timeout_aguardando_user", timeout_seg=timeout_seg)
    return False


def _varrer_sync(cfg: Config, *, max_produtos: int) -> list[dict[str, Any]]:
    """Versão síncrona — chama API em loop até atingir `max_produtos`."""
    log.info("shopee.aguardando_lock", max_produtos=max_produtos)
    with _LOCK_CHROME_SHOPEE:
        log.info("shopee.lock_adquirido")
        driver = _criar_driver_shopee(cfg)
        todos: list[dict[str, Any]] = []
        vistos: set[str] = set()

        try:
            # 1. Carrega painel (autentica via cookies persistidos)
            driver.get(URL_PAINEL)
            time.sleep(4)
            problema = _detectou_login_ou_captcha(driver)
            if problema:
                motivo, instrucao = problema
                resolveu = _aguardar_resolucao_manual(
                    driver,
                    motivo=motivo,
                    mensagem_usuario=instrucao,
                    timeout_seg=300,   # 5 minutos pra user logar/resolver captcha
                )
                if not resolveu:
                    raise RuntimeError(
                        f"Shopee {motivo} — usuário não resolveu em 5 minutos. "
                        "Refaça login: `python -m agent.login_shopee`."
                    )
                # Após resolver, garante que estamos no painel certo
                if "/offer/product_offer" not in (driver.current_url or "").lower():
                    driver.get(URL_PAINEL)
                    time.sleep(3)

            # 2. Itera abas + páginas
            por_aba = max(5, max_produtos // len(ABAS_BUSCA) + 2)

            for aba in ABAS_BUSCA:
                nome_aba = aba["nome"]
                log.info("shopee.aba", nome=nome_aba, list_type=aba["list_type"])
                coletados_aba = 0

                for pagina in range(MAX_PAGINAS):
                    if coletados_aba >= por_aba or len(todos) >= max_produtos:
                        break

                    offset = pagina * PRODUTOS_POR_PAGINA
                    result = _chamar_api(
                        driver,
                        list_type=aba["list_type"],
                        page_offset=offset,
                        page_limit=PRODUTOS_POR_PAGINA,
                        cat=aba.get("cat"),
                    )

                    status = result.get("status", 0)
                    if status != 200:
                        log.warning("shopee.api_status",
                                    aba=nome_aba, pag=pagina + 1,
                                    status=status, body=str(result.get("body"))[:200])
                        # Status 0 = network error / sem cookies; 401/403 = sessão
                        # expirou no meio da busca. Tenta reautenticar abrindo o
                        # painel e aguardando user resolver.
                        if status in (0, 401, 403):
                            driver.get(URL_PAINEL)
                            time.sleep(3)
                            problema = _detectou_login_ou_captcha(driver)
                            if problema:
                                motivo_re, instrucao_re = problema
                                if not _aguardar_resolucao_manual(
                                    driver,
                                    motivo=f"meio_busca_{motivo_re}",
                                    mensagem_usuario=instrucao_re,
                                    timeout_seg=300,
                                ):
                                    raise RuntimeError(
                                        f"Shopee sessão expirou no meio (HTTP {status}) e "
                                        "user não resolveu em 5 minutos."
                                    )
                            # Retry: refaz a chamada da mesma página
                            result = _chamar_api(
                                driver,
                                list_type=aba["list_type"],
                                page_offset=offset,
                                page_limit=PRODUTOS_POR_PAGINA,
                                cat=aba.get("cat"),
                            )
                            if result.get("status") != 200:
                                log.warning("shopee.retry_falhou",
                                            status=result.get("status"))
                                break
                        else:
                            break

                    try:
                        data = json.loads(result["body"])
                    except (json.JSONDecodeError, TypeError):
                        log.warning("shopee.json_invalido", aba=nome_aba, pag=pagina + 1)
                        break

                    items_raw = (data.get("data") or {}).get("list") or []
                    if not items_raw:
                        log.info("shopee.aba_fim", nome=nome_aba, pag=pagina + 1)
                        break

                    for item in items_raw:
                        if coletados_aba >= por_aba or len(todos) >= max_produtos:
                            break
                        produto = _item_pra_produto_v3(item, categoria_nome=nome_aba)
                        if produto is None:
                            continue
                        chave = produto.get("item_id") or produto.get("url_canonica")
                        if not chave or chave in vistos:
                            continue
                        vistos.add(chave)
                        todos.append(produto)
                        coletados_aba += 1

                    log.info("shopee.pagina_ok",
                             aba=nome_aba, pag=pagina + 1,
                             extraidos_pag=len(items_raw),
                             coletados_aba=coletados_aba,
                             total=len(todos))

                    if len(items_raw) < PRODUTOS_POR_PAGINA:
                        break
                    time.sleep(0.5)

                log.info("shopee.aba_ok", nome=nome_aba, coletados=coletados_aba)

        finally:
            try:
                driver.quit()
            except Exception:
                pass
            time.sleep(1.0)   # libera user-data-dir antes do próximo lock_acquire

        return todos


# ============================================================
# Handler async — chamado pelo executar_busca
# ============================================================

async def buscar_shopee(
    cfg: Config, *, max_produtos: int = 50,
) -> list[dict[str, Any]]:
    """
    Async wrapper — roda Selenium em thread separada pra não bloquear o
    event loop do agente.

    Retorna lista de produtos no formato V3 (com `url_afiliado` JÁ COMO
    long_link da Shopee — não precisa segundo passo de linkbuilder).
    """
    log.info("shopee.iniciando", max_produtos=max_produtos)
    produtos = await asyncio.to_thread(_varrer_sync, cfg, max_produtos=max_produtos)
    com_afiliado = sum(1 for p in produtos if p.get("url_afiliado"))
    log.info("shopee.concluido",
             total=len(produtos), com_afiliado=com_afiliado)
    return produtos

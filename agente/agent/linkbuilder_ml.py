"""
Linkbuilder Mercado Livre (Fase 15) — gera shortlinks `meli.la/XXX` oficiais
em lote via scraping do painel oficial de afiliados.

Por que precisa: ML não tem API pública pra gerar links de afiliado.
A ÚNICA forma de obter um link que ML credita comissão é via o painel
em `mercadolivre.com.br/afiliados/linkbuilder` — admin cola URLs cruas,
ML responde com `meli.la/XXX`. Tag de afiliado é implícita pela sessão
Chrome logada (o admin precisa ter feito login no painel uma vez no
`chrome_perfil_ml`).

Portado da V2 (`src/buscar/ml.py:214-285`). Adaptado pra V3:
- Função async (chamada de handler WS)
- Cria driver em thread separada (Selenium é síncrono)
- Lote de 10 URLs por submissão (limite empírico do painel ML)
- Retorna mapping {url_canonica: meli.la}
- Vazio se falhar — caller decide fallback (URL crua)
"""
from __future__ import annotations

import asyncio
import re
import threading
import time
from urllib.parse import urlparse, urlunparse

import structlog
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from agent.config import Config

log = structlog.get_logger(__name__)


URL_LINKBUILDER = "https://www.mercadolivre.com.br/afiliados/linkbuilder"
LOTE_TAMANHO = 10  # ML aceita ~10 URLs por submissão sem rate limit
TIMEOUT_PAGINA = 20
ESPERA_GERACAO = 6  # segundos pra ML processar e renderizar os meli.la

# Lock global: garante 1 Chrome ML rodando por vez. Sem isso, duas tarefas
# GERAR_LINK chegando próximas (reentrega após reconexão WS, ou clique duplo
# no botão Regenerar) crashavam com `cannot connect to chrome at 127.0.0.1:XXXX`
# — undetected-chromedriver não tolera 2 sessões com mesmo `--user-data-dir`.
_LOCK_CHROME_ML = threading.Lock()

# Flag de "já salvei debug do painel nesta sessão do agente". Evita poluir
# o disco quando o user tem o agente rodando o dia todo e a captura de
# comissão sempre falha (ex: layout do painel ML mudou).
_DEBUG_PAINEL_JA_SALVO = False


def _salvar_debug_painel_uma_vez(driver) -> None:
    """Salva HTML+screenshot do painel ML linkbuilder pra próxima análise.

    Disparado quando capturamos meli.la mas ZERO comissões — sintoma típico
    de layout novo do painel que nossos seletores não cobrem mais. User
    manda os arquivos pra ajustarmos.

    Salva em `%APPDATA%\\Achadinhos\\debug\\ml_linkbuilder_<timestamp>.{html,png}`.
    """
    global _DEBUG_PAINEL_JA_SALVO
    if _DEBUG_PAINEL_JA_SALVO:
        return
    try:
        import os
        from datetime import datetime as _dt
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        pasta = os.path.join(base, "Achadinhos", "debug")
        os.makedirs(pasta, exist_ok=True)
        ts = _dt.now().strftime("%Y%m%d_%H%M%S")
        prefixo = os.path.join(pasta, f"ml_linkbuilder_{ts}")
        # HTML do painel
        try:
            with open(prefixo + ".html", "w", encoding="utf-8") as f:
                f.write(driver.page_source or "")
        except Exception:
            pass
        # Screenshot
        try:
            driver.save_screenshot(prefixo + ".png")
        except Exception:
            pass
        log.warning("linkbuilder_ml.debug_salvo",
                    pasta=pasta, prefixo=os.path.basename(prefixo),
                    motivo="captura sem nenhuma comissão — layout pode ter mudado")
        _DEBUG_PAINEL_JA_SALVO = True
    except Exception as e:
        log.debug("linkbuilder_ml.debug_falhou", erro=str(e)[:120])


def _normalizar_url(url: str) -> str:
    """
    Remove fragment + query string pra obter URL canônica "limpa".

    Por que: cards do ML carregam fragment `#polycard_client=...&tracking_id=...`
    e query strings de busca/categoria. Quando essa URL "suja" é submetida ao
    painel linkbuilder do ML, o painel às vezes:
    - rejeita silenciosamente (não retorna meli.la pra ela)
    - normaliza internamente e o `meli.la` gerado aponta pra versão limpa
    - retorna meli.la mas o índice no array de saída fica desalinhado

    Solução: enviamos a URL LIMPA ao painel, mas o mapping de retorno guarda
    a URL ORIGINAL como chave — assim quando o servidor for fazer match com
    `produtos.url_canonica` (que tem o fragment), bate exato.
    """
    if not url:
        return url
    parts = urlparse(url)
    return urlunparse(parts._replace(query="", fragment=""))


def _gerar_lote_sync(driver: uc.Chrome, urls: list[str]) -> dict[str, dict]:
    """Submete UM lote de até 10 URLs ao linkbuilder e captura os meli.la.

    **Retorno expandido na Fase 18**:
        `{url_original: {"link": "https://meli.la/XXX", "comissao_pct": 12.5 | None}}`

    A comissão é a % REAL que o ML paga pra você naquele produto, extraída
    da tabela de resultados do painel após "Gerar". `None` quando o painel
    não exibe (raro, mas pode acontecer em produtos sem programa).

    URLs que não geraram link ficam ausentes do dict (não vazias). Caller
    pode detectar com `.get(url)`.

    URLs enviadas ao painel são NORMALIZADAS (sem fragment/query) pra evitar
    rejeição silenciosa. Mas a chave do mapping é a URL ORIGINAL, pra match
    exato com `produtos.url_canonica` no servidor.
    """
    resultado: dict[str, dict] = {}
    url_anterior = driver.current_url

    # Limpa URLs pra submissão (mantém URLs originais pra chave do mapping)
    urls_limpas = [_normalizar_url(u) for u in urls]
    log.info("linkbuilder_ml.lote_iniciado",
             total=len(urls), exemplo_original=urls[0][:120] if urls else None,
             exemplo_limpa=urls_limpas[0][:120] if urls_limpas else None)

    try:
        driver.get(URL_LINKBUILDER)
        wait = WebDriverWait(driver, TIMEOUT_PAGINA)
        textarea = wait.until(EC.presence_of_element_located(
            (By.CSS_SELECTOR, "textarea")))
        time.sleep(2)  # estabiliza React/SPA

        # Sanity: confere se o painel ML carregou em estado logado. Se
        # redirecionou pra login, scraping vai voltar vazio — loga warning.
        url_atual = driver.current_url.lower()
        if "/afiliados/linkbuilder" not in url_atual:
            log.warning("linkbuilder_ml.painel_nao_carregou",
                        url_atual=url_atual[:200])
            return resultado

        # Injeta URLs LIMPAS no textarea via setter nativo. React intercepta
        # mudanças do React state, então setter direto + dispatch de events
        # é a forma confiável (V2 original).
        texto = "\n".join(urls_limpas)
        driver.execute_script("""
            var el = arguments[0], val = arguments[1];
            var setter = Object.getOwnPropertyDescriptor(
                window.HTMLTextAreaElement.prototype, 'value').set;
            setter.call(el, val);
            el.dispatchEvent(new Event('input', {bubbles:true}));
            el.dispatchEvent(new Event('change', {bubbles:true}));
        """, textarea, texto)
        time.sleep(2)

        # Botão "Gerar" — XPath flexível porque o ML às vezes muda label
        btn = wait.until(EC.element_to_be_clickable((
            By.XPATH,
            "//button[contains(text(),'Gerar') or "
            "contains(text(),'Criar') or contains(@class,'loud')]"
        )))
        driver.execute_script(
            "arguments[0].scrollIntoView(); arguments[0].click();", btn,
        )
        time.sleep(ESPERA_GERACAO)

        # Captura meli.la + comissão das linhas da tabela de resultados.
        # ML mostra após "Gerar" uma tabela com (URL original, meli.la, comissão%).
        # Estratégia "subir do meli.la": pra CADA elemento que contém meli.la,
        # sobe pelos pais até achar um que tem N% no texto local (filhos não-meli).
        # Mais robusto que enumerar seletores possíveis de "row".
        capturas = driver.execute_script(r"""
            var resultado = [];
            var visto = new Set();

            function acharPctNoEscopo(rootEl) {
                // Busca N% no texto direto do escopo, sem cair em "outros meli.la"
                // (irmãos que poderiam contaminar). Limita a 5 níveis de subida.
                if (!rootEl) return null;
                var el = rootEl;
                for (var i = 0; i < 5; i++) {
                    if (!el || el === document.body) break;
                    var texto = el.textContent || '';
                    // Captura percentual razoável: 0.5..50%
                    var m = texto.match(/(\d{1,2}(?:[.,]\d{1,2})?)\s*%/);
                    if (m) {
                        var num = parseFloat(m[1].replace(',', '.'));
                        if (!isNaN(num) && num > 0 && num < 50) return num;
                    }
                    el = el.parentElement;
                }
                return null;
            }

            // 1. Busca anchors <a href="meli.la/..."> — caso mais comum
            document.querySelectorAll('a[href*="meli.la/"]').forEach(function(a) {
                var link = a.href;
                if (!link || visto.has(link)) return;
                visto.add(link);
                resultado.push({link: link, comissao_pct: acharPctNoEscopo(a)});
            });

            // 2. Busca textos que contém meli.la (não-anchor, ex: copy-to-clipboard)
            document.querySelectorAll('input[value*="meli.la/"], textarea').forEach(function(el) {
                var val = el.value || '';
                var m = val.match(/https?:\/\/meli\.la\/[A-Za-z0-9]+/g);
                if (!m) return;
                m.forEach(function(link) {
                    if (visto.has(link)) return;
                    visto.add(link);
                    resultado.push({link: link, comissao_pct: acharPctNoEscopo(el)});
                });
            });

            // 3. Walker pra qualquer outro elemento com meli.la em data attribute
            //    ou title (alguns layouts do ML colocam o link assim)
            document.querySelectorAll('[data-link*="meli.la/"], [title*="meli.la/"], [data-clipboard-text*="meli.la/"]').forEach(function(el) {
                var s = el.getAttribute('data-link') || el.getAttribute('title') || el.getAttribute('data-clipboard-text') || '';
                var m = s.match(/https?:\/\/meli\.la\/[A-Za-z0-9]+/);
                if (!m || visto.has(m[0])) return;
                visto.add(m[0]);
                resultado.push({link: m[0], comissao_pct: acharPctNoEscopo(el)});
            });

            // 4. FALLBACK absoluto: regex no innerHTML inteiro (só links, SEM comissão)
            if (resultado.length === 0) {
                var todos = document.body.innerHTML.match(
                    /https?:\/\/meli\.la\/[A-Za-z0-9]+/g
                ) || [];
                [...new Set(todos)].forEach(function(l) {
                    resultado.push({link: l, comissao_pct: null});
                });
            }

            return resultado;
        """) or []

        # Debug: se NENHUMA captura veio com comissão, salva um snapshot do
        # HTML do painel pra análise (o user pode mandar pra ajustarmos os
        # seletores). Só salva 1× por sessão pra não poluir disco.
        if capturas and not any(c.get("comissao_pct") for c in capturas):
            _salvar_debug_painel_uma_vez(driver)

        com_comissao = sum(1 for c in capturas if c.get("comissao_pct"))
        log.info("linkbuilder_ml.lote_capturado",
                 enviadas=len(urls_limpas), capturadas=len(capturas),
                 com_comissao=com_comissao,
                 amostra=[(c["link"], c.get("comissao_pct")) for c in capturas[:3]])

        # ML retorna na MESMA ORDEM das URLs submetidas. Assume índice.
        # Mapping usa a URL ORIGINAL como chave (não a limpa) pra bater
        # com `produtos.url_canonica` no servidor.
        for i, url_original in enumerate(urls):
            if i < len(capturas):
                c = capturas[i]
                resultado[url_original] = {
                    "link":         c["link"],
                    "comissao_pct": c.get("comissao_pct"),
                }

        if not capturas:
            log.warning("linkbuilder_ml.zero_meli_la",
                        provavel="sessão ML expirou — rode `python -m agent.login_ml` "
                                 "ou abra o painel afiliados ML no Chrome do agente uma vez")

    except Exception as e:
        log.warning("linkbuilder_ml.lote_falhou", erro=str(e)[:200])
    finally:
        # Volta pra página anterior pra não atrapalhar outras tarefas
        try:
            driver.get(url_anterior)
            time.sleep(2)
        except Exception:
            pass

    return resultado


def _gerar_todos_sync(cfg: Config, urls: list[str]) -> dict[str, dict]:
    """Loop blocking — abre Chrome, processa lotes, devolve mapping completo.

    Protegido por `_LOCK_CHROME_ML`: se outra GERAR_LINK estiver rodando,
    espera ela terminar antes de criar o driver. Evita conflito de
    `--user-data-dir` que crasha undetected-chromedriver.

    Retorna mapping rico: `{url: {"link", "comissao_pct"}}` (Fase 18).
    """
    if not urls:
        return {}

    # Adquire lock — bloqueia até a outra instância terminar.
    # `acquire(blocking=True)` é o default; usando explícito pra deixar claro.
    log.info("linkbuilder_ml.aguardando_lock", urls=len(urls))
    with _LOCK_CHROME_ML:
        log.info("linkbuilder_ml.lock_adquirido", urls=len(urls))
        # Reaproveita o driver setup do busca_ml.py
        from agent.busca_ml import _criar_driver_ml
        driver = _criar_driver_ml(cfg)
        mapa_total: dict[str, dict] = {}
        try:
            for i in range(0, len(urls), LOTE_TAMANHO):
                lote = urls[i:i + LOTE_TAMANHO]
                log.info("linkbuilder_ml.lote",
                         n=i // LOTE_TAMANHO + 1, total=len(lote))
                mapa_total.update(_gerar_lote_sync(driver, lote))
        finally:
            try:
                driver.quit()
            except Exception:
                pass
            # Pequena espera pra o processo Chrome terminar antes do próximo
            # lock_acquire — undetected-chromedriver demora pra liberar lock
            # do user-data-dir mesmo depois de `quit()`.
            time.sleep(1.5)
        return mapa_total


async def gerar_links_em_lote(
    cfg: Config, urls: list[str],
) -> dict[str, str]:
    """Async wrapper — roda Selenium em thread separada pra não bloquear
    o event loop do agente.

    ⚠ Retorna formato LEGADO `{url: link_meli_la}` (sem comissão) pra manter
    compatibilidade com `handler_gerar_links_ml` (main.py) que envia o
    mapping pro servidor via WS — servidor consome em `afiliado_ml_writer
    .aplicar_mapping` que espera strings.

    Pra obter o mapping com comissão (Fase 18), use `gerar_links_completos_em_lote`.

    Args:
        cfg: Config (precisa cfg.chrome_perfil_ml + sessão ML afiliados logada).
        urls: lista de URLs canônicas (ex: mercadolivre.com.br/.../p/MLB...).

    Returns:
        mapping {url_canonica: meli.la_link}. URLs que falharam ficam ausentes.
    """
    if not urls:
        return {}
    log.info("linkbuilder_ml.iniciado", total=len(urls))
    mapa_rico = await asyncio.to_thread(_gerar_todos_sync, cfg, urls)
    # Reduz pra formato legado
    mapa: dict[str, str] = {
        url: info["link"]
        for url, info in mapa_rico.items()
        if info.get("link")
    }
    log.info("linkbuilder_ml.concluido",
             pedidos=len(urls), gerados=len(mapa))
    return mapa


async def gerar_links_completos_em_lote(
    cfg: Config, urls: list[str],
) -> dict[str, dict]:
    """Versão rica do `gerar_links_em_lote` — retorna comissão também.

    Returns:
        mapping `{url_canonica: {"link": meli_la, "comissao_pct": float | None}}`.

    Use no FLUXO INLINE (busca_ml.py) onde o agente precisa anotar
    `comissao` + `comissao_fonte=ml_painel` direto no dict do produto antes
    do ingest. NÃO use no fluxo WS legado (handler_gerar_links_ml) — esse
    espera `dict[str, str]`.
    """
    if not urls:
        return {}
    log.info("linkbuilder_ml.iniciado_rico", total=len(urls))
    mapa = await asyncio.to_thread(_gerar_todos_sync, cfg, urls)
    com_pct = sum(1 for info in mapa.values() if info.get("comissao_pct"))
    log.info("linkbuilder_ml.concluido_rico",
             pedidos=len(urls), gerados=len(mapa), com_comissao=com_pct)
    return mapa

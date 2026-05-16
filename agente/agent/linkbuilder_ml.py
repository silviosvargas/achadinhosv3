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


def _gerar_lote_sync(driver: uc.Chrome, urls: list[str]) -> dict[str, str]:
    """Submete UM lote de até 10 URLs ao linkbuilder e captura os meli.la.

    Retorna mapping `{url_original: meli.la_link}`. URLs que não geraram
    link ficam ausentes do dict (não vazias). Caller pode detectar com `get`.

    URLs enviadas ao painel são NORMALIZADAS (sem fragment/query) pra evitar
    rejeição silenciosa. Mas a chave do mapping é a URL ORIGINAL, pra match
    exato com `produtos.url_canonica` no servidor.
    """
    resultado: dict[str, str] = {}
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

        # Captura meli.la dos textareas (alguns lugares ML mostra), depois
        # fallback pro innerHTML completo. Regex `[A-Za-z0-9]+` (sem hífens)
        # bate o formato oficial do shortener do ML.
        links = driver.execute_script(r"""
            var links = [];
            document.querySelectorAll('textarea').forEach(function(el){
                var m = (el.value||'').match(/https?:\/\/meli\.la\/[A-Za-z0-9]+/g);
                if(m) links = links.concat(m);
            });
            if(!links.length){
                var m = document.body.innerHTML.match(
                    /https?:\/\/meli\.la\/[A-Za-z0-9]+/g
                );
                if(m) links = links.concat(m);
            }
            return [...new Set(links)];
        """) or []

        log.info("linkbuilder_ml.lote_capturado",
                 enviadas=len(urls_limpas), capturadas=len(links),
                 amostra=links[:3])

        # ML retorna na MESMA ORDEM das URLs submetidas. Assume índice.
        # Mapping usa a URL ORIGINAL como chave (não a limpa) pra bater
        # com `produtos.url_canonica` no servidor.
        for i, url_original in enumerate(urls):
            if i < len(links):
                resultado[url_original] = links[i]

        if len(links) == 0:
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


def _gerar_todos_sync(cfg: Config, urls: list[str]) -> dict[str, str]:
    """Loop blocking — abre Chrome, processa lotes, devolve mapping completo."""
    if not urls:
        return {}

    # Reaproveita o driver setup do busca_ml.py
    from agent.busca_ml import _criar_driver_ml
    driver = _criar_driver_ml(cfg)
    mapa_total: dict[str, str] = {}
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
    return mapa_total


async def gerar_links_em_lote(
    cfg: Config, urls: list[str],
) -> dict[str, str]:
    """Async wrapper — roda Selenium em thread separada pra não bloquear
    o event loop do agente.

    Args:
        cfg: Config (precisa cfg.chrome_perfil_ml + sessão ML afiliados logada).
        urls: lista de URLs canônicas (ex: mercadolivre.com.br/.../p/MLB...).

    Returns:
        mapping {url_canonica: meli.la_link}. URLs que falharam ficam ausentes.
    """
    if not urls:
        return {}
    log.info("linkbuilder_ml.iniciado", total=len(urls))
    mapa = await asyncio.to_thread(_gerar_todos_sync, cfg, urls)
    log.info("linkbuilder_ml.concluido",
             pedidos=len(urls), gerados=len(mapa))
    return mapa

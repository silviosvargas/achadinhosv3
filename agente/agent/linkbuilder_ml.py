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


def _gerar_lote_sync(driver: uc.Chrome, urls: list[str]) -> dict[str, str]:
    """Submete UM lote de até 10 URLs ao linkbuilder e captura os meli.la.

    Retorna mapping `{url_original: meli.la_link}`. URLs que não geraram
    link ficam ausentes do dict (não vazias). Caller pode detectar com `get`.
    """
    resultado: dict[str, str] = {}
    url_anterior = driver.current_url

    try:
        driver.get(URL_LINKBUILDER)
        wait = WebDriverWait(driver, TIMEOUT_PAGINA)
        textarea = wait.until(EC.presence_of_element_located(
            (By.CSS_SELECTOR, "textarea")))
        time.sleep(2)  # estabiliza React/SPA

        # Injeta URLs no textarea via setter nativo. React intercepta
        # mudanças do React state, então setter direto + dispatch de
        # events é a forma confiável (V2 original).
        texto = "\n".join(urls)
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

        # ML retorna na MESMA ORDEM das URLs submetidas. Assume índice.
        for i, url in enumerate(urls):
            if i < len(links):
                resultado[url] = links[i]

    except Exception as e:
        log.warning("linkbuilder_ml.lote_falhou", erro=str(e)[:120])
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

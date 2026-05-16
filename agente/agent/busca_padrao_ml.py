"""
Busca padrão ML — top 10 mais vendidos por categoria com comissão REAL
(Fase 19, v3.5.0).

Fluxo por categoria:
1. Abre página `mais-vendidos/MLBxxx` da categoria
2. Extrai N candidatos (default 20) via `_extrair_cards_da_pagina`
3. Gera `meli.la` pra TODOS os candidatos via `_gerar_meli_la_no_driver`
4. Pra cada candidato com meli.la: abre meli.la → /social/ → clica "Ir
   para produto" → captura comissão REAL + preço REAL via
   `_capturar_comissao_e_preco_no_destino`
5. Ordena candidatos por `(preço × comissão_real)` DESC
6. Mantém top 10 da categoria
7. Devolve lista pro orquestrador (que faz ingest)

Custo: ~20 candidatos × ~3s/captura = ~1min por categoria.
8 categorias = ~8min por execução.

⚠ NÃO inventar abordagem alternativa. Documentado em CLAUDE.md:
"meli.la → /social/ → clicar 'Ir para produto' → barra preta".
"""
from __future__ import annotations

import asyncio
import random
import threading
import time
from typing import Any

import structlog
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from agent.config import Config

log = structlog.get_logger(__name__)


# Mesmas 8 categorias do mais_vendidos (busca_ml.py CATEGORIAS_MAIS_VENDIDOS),
# replicadas aqui pra evitar import circular e permitir evolução independente
# (ex: adicionar subcategorias no futuro sem mexer no busca_ml).
CATEGORIAS_PADRAO = [
    # (nome_display, url_mais_vendidos, comissao_estimada_para_fallback)
    ("Roupas, Calçados e Acessórios", "https://www.mercadolivre.com.br/mais-vendidos/MLB1430", 14.0),
    ("Esportes e Fitness",            "https://www.mercadolivre.com.br/mais-vendidos/MLB1276", 12.0),
    ("Beleza e Cuidado Pessoal",      "https://www.mercadolivre.com.br/mais-vendidos/MLB1246", 12.0),
    ("Bebês",                          "https://www.mercadolivre.com.br/mais-vendidos/MLB5726", 10.0),
    ("Casa, Móveis e Decoração",      "https://www.mercadolivre.com.br/mais-vendidos/MLB1574", 10.0),
    ("Eletrônicos, Áudio e Vídeo",    "https://www.mercadolivre.com.br/mais-vendidos/MLB1051",  8.0),
    ("Informática",                    "https://www.mercadolivre.com.br/mais-vendidos/MLB1648",  8.0),
    ("Ferramentas",                    "https://www.mercadolivre.com.br/mais-vendidos/MLB1499",  8.0),
]

# Quantos finais por categoria (depois da filtragem por preço × comissão_real)
TOP_FINAL_POR_CATEGORIA = 10


def _capturar_comissao_e_preco_no_destino(
    driver,
) -> tuple[float | None, float | None, float | None]:
    """Captura comissão (barra preta) + preço atual via JS na página atual.

    Pré-condição: driver já está na página DO PRODUTO (URL canônica aberta
    direto — Chrome do agente logado como afiliado mostra a barra preta).

    Returns:
        (comissao_efetiva, comissao_extra, preco)
        - comissao_efetiva: % final paga (EXTRAS se houver, senão BASE). None se nada.
        - comissao_extra:   % do bônus EXTRAS quando presente. None se sem bônus.
        - preco:            preço atual (excluindo riscado). None se não capturou.

        v3.8.0: passou a retornar tupla de 3 (antes 2). Quem chama precisa
        atualizar pra desempacotar `comissao_extra` separadamente — usado pela
        busca padrão `padrao_comissao_extra` que filtra só os com bônus.
    """
    dados = driver.execute_script(r"""
        // Helper: busca GANHOS [EXTRAS] X% no texto. Retorna extras E base
        // separados (chamador decide qual usar).
        function buscarComissao(scopo) {
            var res = {extras: null, base: null};
            var txt = scopo.textContent || '';
            var mE = txt.match(/GANHOS\s+EXTRAS\s+(\d{1,2}(?:[.,]\d{1,2})?)\s*%/i);
            if (mE) {
                var nE = parseFloat(mE[1].replace(',', '.'));
                if (!isNaN(nE) && nE > 0 && nE <= 50) res.extras = nE;
            }
            var mB = txt.match(/GANHOS\s+(\d{1,2}(?:[.,]\d{1,2})?)\s*%/i);
            if (mB) {
                var nB = parseFloat(mB[1].replace(',', '.'));
                if (!isNaN(nB) && nB > 0 && nB <= 50) res.base = nB;
            }
            return res;
        }

        // Procura comissão em elementos prováveis (header/banner afiliados)
        var seletores = [
            'header', '[class*="affiliate"]', '[class*="afiliad"]',
            '[class*="banner"]', '[id*="banner"]',
        ];
        var melhorCom = {extras: null, base: null};
        for (var i = 0; i < seletores.length; i++) {
            var els = document.querySelectorAll(seletores[i]);
            for (var j = 0; j < els.length; j++) {
                var r = buscarComissao(els[j]);
                if (r.extras !== null && melhorCom.extras === null) melhorCom.extras = r.extras;
                if (r.base   !== null && melhorCom.base   === null) melhorCom.base   = r.base;
            }
            if (melhorCom.extras !== null) break;
        }
        if (melhorCom.extras === null && melhorCom.base === null) {
            melhorCom = buscarComissao(document.body);
        }

        // Captura preço atual — XPath excluindo <s> (riscado) pra não pegar o original
        var preco = null;
        var precoEls = document.evaluate(
            ".//*[contains(concat(' ', normalize-space(@class), ' '), ' andes-money-amount__fraction ') and not(ancestor::s)]",
            document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null,
        );
        if (precoEls.snapshotLength > 0) {
            var int_el = precoEls.snapshotItem(0);
            var inteiro = (int_el.textContent || '').replace(/[^\d]/g, '');
            if (inteiro) {
                var base = parseFloat(inteiro);
                // Cents (opcional)
                var centsEls = document.evaluate(
                    ".//*[contains(concat(' ', normalize-space(@class), ' '), ' andes-money-amount__cents ') and not(ancestor::s)]",
                    document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null,
                );
                if (centsEls.snapshotLength > 0) {
                    var c = (centsEls.snapshotItem(0).textContent || '').replace(/[^\d]/g, '');
                    if (c) base += parseFloat(c) / 100;
                }
                if (!isNaN(base) && base > 0) preco = base;
            }
        }

        return {extras: melhorCom.extras, base: melhorCom.base, preco: preco};
    """) or {}

    extras_raw = dados.get("extras")
    base_raw   = dados.get("base")
    pre_raw    = dados.get("preco")

    def _to_float(v):
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    extras_pct = _to_float(extras_raw)
    base_pct   = _to_float(base_raw)
    preco      = _to_float(pre_raw)

    # Efetiva = extras se tem, base senão
    efetiva = extras_pct if extras_pct is not None else base_pct
    return efetiva, extras_pct, preco


def _entrar_produto_via_meli_la(driver, meli_la_url: str) -> bool:
    """Fluxo OBRIGATÓRIO (documentado em CLAUDE.md):
    1. Abre meli.la
    2. ML redireciona pra /social/<usuario>
    3. Procura botão "Ir para produto" → extrai href ou clica
    4. Navega pra página do produto

    Returns: True se conseguiu chegar na página do produto, False caso contrário.
    """
    try:
        driver.get(meli_la_url)
        time.sleep(2.0)  # aguarda redirect

        url_atual = (driver.current_url or "").lower()
        if "/social/" in url_atual:
            destino = driver.execute_script(r"""
                var els = document.querySelectorAll('a, button');
                for (var i = 0; i < els.length; i++) {
                    var el = els[i];
                    var t = (el.textContent || '').trim().toLowerCase();
                    if (t.indexOf('ir para produto') !== -1
                            || t.indexOf('ver produto') !== -1) {
                        if (el.tagName === 'A' && el.href) return el.href;
                        el.click();
                        return 'CLICKED';
                    }
                }
                return null;
            """)
            if isinstance(destino, str) and destino.startswith("http"):
                driver.get(destino)
            elif destino == "CLICKED":
                pass  # clicou, aguarda navegação
            else:
                log.warning("ml.padrao.botao_ausente", meli=meli_la_url[:80])
                return False
            time.sleep(2.0)
        return True
    except Exception as e:
        log.debug("ml.padrao.entrar_falhou", meli=meli_la_url[:80], erro=str(e)[:120])
        return False


def _processar_categoria(
    driver, *, nome_categoria: str, url_categoria: str,
    comissao_estimada: float, candidatos_por_categoria: int,
    tarefa_id: int | str | None = None,
) -> list[dict[str, Any]]:
    """Processa UMA categoria — v3.7.0 fluxo SIMPLIFICADO:
    1. Extrai N candidatos da página de mais-vendidos
    2. Pra cada um: abre `url_canonica` DIRETO → captura comissão+preço da barra
    3. Filtra só os que tiveram captura real
    4. Ordena por (preço × comissão_real) DESC, mantém top N
    5. Gera meli.la NO FINAL (só pros que vão entrar no catálogo)

    Decisão do user (v3.7.0): pulamos o passo meli.la → /social/ → clicar
    "Ir para produto". Chrome do agente está logado como afiliado, então
    abrir URL canônica direto já mostra a barra com a comissão correta.
    Mais rápido (~1.5s/produto vs ~3s antes).
    """
    # Imports tardios pra reusar lógica de busca_ml SEM duplicar
    from agent.busca_ml import (
        _extrair_cards_da_pagina,
        _gerar_meli_la_no_driver,
        _bloqueado_por_login,
        _scroll_lazy_load,
    )

    log.info("ml.padrao.categoria_iniciada",
             nome=nome_categoria, url=url_categoria,
             candidatos_alvo=candidatos_por_categoria)

    # 1. Abre página de mais-vendidos e extrai candidatos
    try:
        driver.get(url_categoria)
    except Exception as e:
        log.warning("ml.padrao.get_categoria_falhou",
                    nome=nome_categoria, erro=str(e)[:120])
        return []

    try:
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
    except TimeoutException:
        log.warning("ml.padrao.timeout_categoria", nome=nome_categoria)
        return []

    if _bloqueado_por_login(driver.current_url):
        raise RuntimeError(
            f"ML exige login na busca padrão ({nome_categoria}). "
            "Rode `python -m agent.login_ml` uma vez."
        )

    _scroll_lazy_load(driver)
    candidatos = _extrair_cards_da_pagina(driver)
    candidatos = candidatos[:candidatos_por_categoria]

    if not candidatos:
        log.warning("ml.padrao.categoria_vazia", nome=nome_categoria)
        return []

    # Preenche metadados: categoria + flag bestseller + fonte default
    for p in candidatos:
        p["categoria"] = nome_categoria
        p["is_bestseller"] = True
        p.setdefault("comissao", comissao_estimada)
        p.setdefault("comissao_fonte", "estimativa")

    log.info("ml.padrao.candidatos_extraidos",
             nome=nome_categoria, total=len(candidatos))

    # v3.7.1: gera meli.la INCREMENTAL a cada N capturados (em vez de
    # esperar todos terminarem). Garante que se cancelar no meio, os
    # produtos já capturados terão link de afiliado quando voltarem.
    from agent import cancelamento
    BATCH_LINKBUILDER = 10

    capturados_total: list[dict] = []   # todos com captura ok (pra ranking final)
    buffer_sem_meli: list[dict] = []    # capturados que ainda não tiveram meli.la
    cancelada_no_meio = False

    # 2. Loop de captura com check de cancelamento + meli.la incremental
    for i, p in enumerate(candidatos, start=1):
        # Check de cancelamento ANTES de processar cada produto.
        # Pedido do user: "caso seja cancelado na busca do quarto produto
        # por exemplo, ele deve fazer a busca do link do afiliado dos
        # quatro produtos e depois parar".
        if cancelamento.foi_cancelada(tarefa_id):
            log.info("ml.padrao.cancelada_durante_captura",
                     tarefa_id=tarefa_id, nome=nome_categoria,
                     capturados_ate_aqui=len(capturados_total))
            cancelada_no_meio = True
            break

        url_can = p.get("url_canonica") or ""
        if not url_can:
            continue

        try:
            driver.get(url_can)
            time.sleep(1.5)
        except Exception:
            continue

        com_real, com_extra, preco_real = _capturar_comissao_e_preco_no_destino(driver)

        if com_real and com_real > 0:
            p["comissao"]       = com_real
            p["comissao_fonte"] = "ml_barra_afiliados"
            # Marca o bônus EXTRAS quando presente (None se sem bônus).
            # Servidor usa pra filtro `comissao_extra IS NOT NULL`.
            p["comissao_extra"] = com_extra
            capturados_total.append(p)
            buffer_sem_meli.append(p)
        if preco_real and preco_real > 0 and abs(preco_real - (p.get("preco") or 0)) > 0.01:
            log.info("ml.padrao.preco_atualizado",
                     item_id=p.get("item_id"),
                     antes=p.get("preco"), depois=preco_real)
            p["preco"] = preco_real

        log.info("ml.padrao.captura",
                 n=i, total=len(candidatos),
                 item_id=p.get("item_id"),
                 comissao=p.get("comissao"),
                 extra=p.get("comissao_extra"),
                 fonte=p.get("comissao_fonte"),
                 preco=p.get("preco"))

        # A cada BATCH_LINKBUILDER capturados com sucesso, gera meli.la
        # IMEDIATAMENTE. Se cancelar antes do próximo lote completar, os
        # produtos do buffer parcial são processados no fechamento abaixo.
        if len(buffer_sem_meli) >= BATCH_LINKBUILDER:
            log.info("ml.padrao.gerando_meli_la_lote",
                     nome=nome_categoria, tamanho=len(buffer_sem_meli))
            _gerar_meli_la_no_driver(
                driver, buffer_sem_meli,
                log_prefixo=f"ml.padrao[{nome_categoria}]",
            )
            buffer_sem_meli = []

        # Delay anti rate-limit
        time.sleep(random.uniform(0.3, 0.6))

    # 3. Buffer restante (parcial OU cancelamento no meio): gera meli.la
    # pros produtos que ainda não receberam. Garante que TODO produto
    # capturado vai pro ingest com url_afiliado válido.
    if buffer_sem_meli:
        log.info("ml.padrao.gerando_meli_la_resto",
                 nome=nome_categoria, tamanho=len(buffer_sem_meli),
                 cancelada=cancelada_no_meio)
        _gerar_meli_la_no_driver(
            driver, buffer_sem_meli,
            log_prefixo=f"ml.padrao[{nome_categoria}].resto",
        )
        buffer_sem_meli = []

    # 4. Ordena capturados por (preço × comissão_real) DESC, mantém top N.
    # Se cancelado, `capturados_total` pode ter < 10 — retorna o que tem.
    def _score_ranking(prod: dict) -> float:
        preco = prod.get("preco") or 0
        com   = prod.get("comissao") or 0
        return float(preco) * float(com) / 100.0

    capturados_total.sort(key=_score_ranking, reverse=True)
    top = capturados_total[:TOP_FINAL_POR_CATEGORIA]
    descartados = max(0, len(candidatos) - len(capturados_total))

    log.info("ml.padrao.categoria_concluida",
             nome=nome_categoria,
             candidatos=len(candidatos),
             com_captura_real=len(capturados_total),
             descartados_sem_captura=descartados,
             top_selecionados=len(top),
             cancelada_no_meio=cancelada_no_meio)
    return top


def _varrer_padrao_completo_sync(
    cfg: Config, *, candidatos_por_categoria: int = 30,
    tarefa_id: int | str | None = None,
) -> list[dict[str, Any]]:
    """Loop principal: itera categorias em CATEGORIAS_PADRAO.

    Fase 20 (v3.6.0): se `tarefa_id` for fornecido, reporta progresso via
    `ws_progresso.reportar(tarefa_id, pct, mensagem)` em checkpoints:
    - 0% início
    - (i/N)*100% após cada categoria
    - 100% final
    Servidor persiste em `tarefas.progresso_pct` → UI dashboard mostra
    barra com polling 3s.
    """
    # Reusa lock e driver ML do módulo principal
    from agent.busca_ml import _criar_driver_ml
    from agent.linkbuilder_ml import _LOCK_CHROME_ML
    from agent import ws_progresso, cancelamento

    total_cat = len(CATEGORIAS_PADRAO)
    log.info("ml.padrao.iniciando",
             categorias=total_cat,
             candidatos_por_categoria=candidatos_por_categoria,
             tarefa_id=tarefa_id)
    ws_progresso.reportar(tarefa_id, 0.0, "Iniciando busca padrão ML…")

    cancelada = False
    with _LOCK_CHROME_ML:
        driver = _criar_driver_ml(cfg)
        todos: list[dict[str, Any]] = []
        try:
            for i, (nome, url, com_est) in enumerate(CATEGORIAS_PADRAO, start=1):
                # Fase 20.1: checa cancelamento ANTES de cada categoria.
                # Se cancelado, para gracioso retornando o que tem até aqui.
                if cancelamento.foi_cancelada(tarefa_id):
                    log.info("ml.padrao.cancelada", tarefa_id=tarefa_id,
                             concluido_ate=i-1, total=total_cat)
                    ws_progresso.reportar(
                        tarefa_id, ((i - 1) / total_cat) * 100.0,
                        f"⏹ Cancelado após {i-1}/{total_cat} categorias — {len(todos)} produtos parciais",
                    )
                    cancelada = True
                    break

                # Reporta INÍCIO da categoria
                pct_inicio = ((i - 1) / total_cat) * 100.0
                ws_progresso.reportar(
                    tarefa_id, pct_inicio,
                    f"Categoria {i}/{total_cat}: {nome}",
                )
                log.info("ml.padrao.categoria",
                         n=i, total=total_cat, nome=nome)
                try:
                    top_cat = _processar_categoria(
                        driver,
                        nome_categoria=nome,
                        url_categoria=url,
                        comissao_estimada=com_est,
                        candidatos_por_categoria=candidatos_por_categoria,
                        tarefa_id=tarefa_id,   # v3.7.1: propaga pra check cancelamento no loop
                    )
                    todos.extend(top_cat)
                except Exception as e:
                    log.exception("ml.padrao.categoria_crash",
                                  nome=nome, erro=str(e)[:200])
                    continue
                # Se cancelamento foi detectado DENTRO da categoria,
                # _processar_categoria já gerou meli.la pros parciais e
                # retornou. Aqui propaga: para o loop e não vai pra próxima.
                if cancelamento.foi_cancelada(tarefa_id):
                    cancelada = True
                    ws_progresso.reportar(
                        tarefa_id, (i / total_cat) * 100.0,
                        f"⏹ Cancelado durante categoria {i}/{total_cat} — {len(todos)} produtos parciais com meli.la",
                    )
                    break
                # Reporta FIM da categoria
                pct_fim = (i / total_cat) * 100.0
                ws_progresso.reportar(
                    tarefa_id, pct_fim,
                    f"Categoria {i}/{total_cat} concluída — {len(todos)} produtos no total",
                )
        finally:
            try:
                driver.quit()
            except Exception:
                pass
            time.sleep(1.5)

    # Limpa flag de cancelamento (consome) pra próxima execução
    cancelamento.consumir(tarefa_id)

    if not cancelada:
        ws_progresso.reportar(
            tarefa_id, 100.0,
            f"Concluído — {len(todos)} produtos com comissão real",
        )
    log.info("ml.padrao.concluido",
             categorias=total_cat,
             produtos_finais=len(todos),
             cancelada=cancelada)
    return todos


async def varrer_padrao_completo(
    cfg: Config, *, candidatos_por_categoria: int = 30,
    tarefa_id: int | str | None = None,
) -> list[dict[str, Any]]:
    """Async wrapper — roda Selenium em thread separada."""
    return await asyncio.to_thread(
        _varrer_padrao_completo_sync, cfg,
        candidatos_por_categoria=candidatos_por_categoria,
        tarefa_id=tarefa_id,
    )


# ============================================================
# Busca padrão: SÓ produtos com bônus EXTRAS (v3.8.0)
# ============================================================

def _processar_categoria_para_extras(
    driver, *, nome_categoria: str, url_categoria: str,
    candidatos_por_categoria: int, faltam: int,
    tarefa_id: int | str | None = None,
) -> list[dict[str, Any]]:
    """Igual `_processar_categoria` mas:
    1. Filtra: só mantém produtos com `comissao_extra > 0` (bônus GANHOS EXTRAS).
    2. Para ao juntar `faltam` produtos válidos.
    3. Gera meli.la INCREMENTAL (a cada N) só pros COM extras.

    Returns: lista de produtos com extras, no máximo `faltam`.
    """
    from agent.busca_ml import (
        _extrair_cards_da_pagina,
        _gerar_meli_la_no_driver,
        _bloqueado_por_login,
        _scroll_lazy_load,
    )
    from agent import cancelamento

    log.info("ml.padrao_extra.categoria_iniciada",
             nome=nome_categoria, url=url_categoria,
             candidatos_alvo=candidatos_por_categoria, faltam=faltam)

    try:
        driver.get(url_categoria)
    except Exception as e:
        log.warning("ml.padrao_extra.get_categoria_falhou",
                    nome=nome_categoria, erro=str(e)[:120])
        return []

    try:
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
    except TimeoutException:
        log.warning("ml.padrao_extra.timeout_categoria", nome=nome_categoria)
        return []

    if _bloqueado_por_login(driver.current_url):
        raise RuntimeError(
            f"ML exige login na busca padrão extras ({nome_categoria}). "
            "Rode `python -m agent.login_ml` uma vez."
        )

    _scroll_lazy_load(driver)
    candidatos = _extrair_cards_da_pagina(driver)
    candidatos = candidatos[:candidatos_por_categoria]
    if not candidatos:
        log.warning("ml.padrao_extra.categoria_vazia", nome=nome_categoria)
        return []

    for p in candidatos:
        p["categoria"] = nome_categoria
        p["is_bestseller"] = True

    BATCH_LINKBUILDER = 5  # batch menor — total esperado é pequeno (~10)

    com_extras: list[dict] = []        # acumula só os com bônus EXTRAS
    buffer_sem_meli: list[dict] = []    # com extras que ainda não tiveram meli.la

    for i, p in enumerate(candidatos, start=1):
        if cancelamento.foi_cancelada(tarefa_id):
            log.info("ml.padrao_extra.cancelada_durante_captura",
                     tarefa_id=tarefa_id, nome=nome_categoria,
                     com_extras_ate_aqui=len(com_extras))
            break

        if len(com_extras) >= faltam:
            log.info("ml.padrao_extra.alvo_atingido",
                     nome=nome_categoria, total=len(com_extras))
            break

        url_can = p.get("url_canonica") or ""
        if not url_can:
            continue

        try:
            driver.get(url_can)
            time.sleep(1.5)
        except Exception:
            continue

        com_real, com_extra, preco_real = _capturar_comissao_e_preco_no_destino(driver)

        # FILTRO PRINCIPAL: só mantém se tem bônus EXTRAS > 0
        if not (com_extra and com_extra > 0):
            log.debug("ml.padrao_extra.sem_bonus",
                      item_id=p.get("item_id"), com_efetiva=com_real)
            continue

        p["comissao"]       = com_real
        p["comissao_extra"] = com_extra
        p["comissao_fonte"] = "ml_barra_afiliados"
        if preco_real and preco_real > 0:
            p["preco"] = preco_real

        com_extras.append(p)
        buffer_sem_meli.append(p)

        log.info("ml.padrao_extra.captura_com_bonus",
                 n=i, total=len(candidatos),
                 com_extras=len(com_extras), alvo=faltam,
                 item_id=p.get("item_id"),
                 comissao=p.get("comissao"),
                 extra=com_extra, preco=p.get("preco"))

        if len(buffer_sem_meli) >= BATCH_LINKBUILDER:
            log.info("ml.padrao_extra.gerando_meli_la_lote",
                     nome=nome_categoria, tamanho=len(buffer_sem_meli))
            _gerar_meli_la_no_driver(
                driver, buffer_sem_meli,
                log_prefixo=f"ml.padrao_extra[{nome_categoria}]",
            )
            buffer_sem_meli = []

        time.sleep(random.uniform(0.3, 0.6))

    # Resto do buffer (parcial ou alvo atingido) — gera meli.la garantido
    if buffer_sem_meli:
        log.info("ml.padrao_extra.gerando_meli_la_resto",
                 nome=nome_categoria, tamanho=len(buffer_sem_meli))
        _gerar_meli_la_no_driver(
            driver, buffer_sem_meli,
            log_prefixo=f"ml.padrao_extra[{nome_categoria}].resto",
        )

    log.info("ml.padrao_extra.categoria_concluida",
             nome=nome_categoria,
             candidatos=len(candidatos),
             com_extras=len(com_extras))
    return com_extras[:faltam]


def _varrer_padrao_comissao_extra_sync(
    cfg: Config, *, candidatos_por_categoria: int = 30,
    alvo_total: int = 10, tarefa_id: int | str | None = None,
) -> list[dict[str, Any]]:
    """Itera CATEGORIAS_PADRAO até juntar `alvo_total` produtos com bônus
    GANHOS EXTRAS. Para cedo (não passa por todas categorias se já achou N).
    """
    from agent.busca_ml import _criar_driver_ml
    from agent.linkbuilder_ml import _LOCK_CHROME_ML
    from agent import ws_progresso, cancelamento

    total_cat = len(CATEGORIAS_PADRAO)
    log.info("ml.padrao_extra.iniciando",
             categorias=total_cat, alvo_total=alvo_total,
             candidatos_por_categoria=candidatos_por_categoria,
             tarefa_id=tarefa_id)
    ws_progresso.reportar(tarefa_id, 0.0,
                          f"Buscando {alvo_total} produtos com comissão EXTRA…")

    cancelada = False
    with _LOCK_CHROME_ML:
        driver = _criar_driver_ml(cfg)
        todos: list[dict[str, Any]] = []
        try:
            for i, (nome, url, _com_est) in enumerate(CATEGORIAS_PADRAO, start=1):
                if cancelamento.foi_cancelada(tarefa_id):
                    log.info("ml.padrao_extra.cancelada", tarefa_id=tarefa_id,
                             concluido_ate=i-1, total=total_cat)
                    ws_progresso.reportar(
                        tarefa_id, ((i - 1) / total_cat) * 100.0,
                        f"⏹ Cancelado após {i-1}/{total_cat} categorias — {len(todos)} com EXTRAS",
                    )
                    cancelada = True
                    break

                faltam = alvo_total - len(todos)
                if faltam <= 0:
                    log.info("ml.padrao_extra.alvo_global_atingido",
                             total=len(todos), categorias_visitadas=i-1)
                    # Reporta 100% explicitamente — pulou as categorias restantes
                    break

                pct_inicio = ((i - 1) / total_cat) * 100.0
                ws_progresso.reportar(
                    tarefa_id, pct_inicio,
                    f"Categoria {i}/{total_cat}: {nome} ({len(todos)}/{alvo_total} já com EXTRAS)",
                )
                log.info("ml.padrao_extra.categoria",
                         n=i, total=total_cat, nome=nome, faltam=faltam)
                try:
                    achados = _processar_categoria_para_extras(
                        driver,
                        nome_categoria=nome,
                        url_categoria=url,
                        candidatos_por_categoria=candidatos_por_categoria,
                        faltam=faltam,
                        tarefa_id=tarefa_id,
                    )
                    todos.extend(achados)
                except Exception as e:
                    log.exception("ml.padrao_extra.categoria_crash",
                                  nome=nome, erro=str(e)[:200])
                    continue

                if cancelamento.foi_cancelada(tarefa_id):
                    cancelada = True
                    ws_progresso.reportar(
                        tarefa_id, (i / total_cat) * 100.0,
                        f"⏹ Cancelado durante categoria {i}/{total_cat} — {len(todos)} com EXTRAS",
                    )
                    break

                pct_fim = (i / total_cat) * 100.0
                ws_progresso.reportar(
                    tarefa_id, pct_fim,
                    f"Categoria {i}/{total_cat} OK — {len(todos)}/{alvo_total} com EXTRAS",
                )
        finally:
            try:
                driver.quit()
            except Exception:
                pass
            time.sleep(1.5)

    cancelamento.consumir(tarefa_id)

    if not cancelada:
        ws_progresso.reportar(
            tarefa_id, 100.0,
            f"Concluído — {len(todos)} produtos com comissão EXTRA",
        )
    log.info("ml.padrao_extra.concluido",
             produtos_finais=len(todos), alvo=alvo_total, cancelada=cancelada)
    return todos[:alvo_total]


async def varrer_padrao_comissao_extra(
    cfg: Config, *, candidatos_por_categoria: int = 30,
    alvo_total: int = 10, tarefa_id: int | str | None = None,
) -> list[dict[str, Any]]:
    """Async wrapper — roda Selenium em thread separada."""
    return await asyncio.to_thread(
        _varrer_padrao_comissao_extra_sync, cfg,
        candidatos_por_categoria=candidatos_por_categoria,
        alvo_total=alvo_total,
        tarefa_id=tarefa_id,
    )

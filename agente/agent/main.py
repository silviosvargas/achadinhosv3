"""
Achadinhos Agent — entrypoint.

Uso (dev):
    python -m agent.main --token "XXX" --servidor "ws://localhost:8000/api/v1/ws/agente"

Uso (produção, depois do PyInstaller):
    AchadinhosAgent.exe          (lê config salva em %APPDATA%\\Achadinhos)
    AchadinhosAgent.exe --token "XXX" --servidor "wss://achadinhos.app/api/v1/ws/agente"
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys

import structlog

from agent.busca_ml import executar_busca
from agent.config import Config
from agent.local_server import LocalServer
from agent.postador import whatsapp
from agent.tray import Tray
from agent.ws_client import WSClient


# ── Logging ─────────────────────────────────────────
def _suporta_cores() -> bool:
    """No Windows, structlog precisa de colorama pra colorir.
    Em Linux/Mac, ANSI funciona nativamente.
    """
    if sys.platform != "win32":
        return True
    try:
        import colorama  # noqa: F401
        return True
    except ImportError:
        return False


def configurar_logging(nivel: str = "INFO") -> None:
    logging.basicConfig(level=nivel, format="%(message)s", stream=sys.stdout)
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.dev.ConsoleRenderer(colors=_suporta_cores()),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, nivel.upper(), logging.INFO)
        ),
    )


log = structlog.get_logger(__name__)


# ── Argumentos CLI ──────────────────────────────────
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Achadinhos Agent")
    p.add_argument("--token", help="JWT do agente. Se omitido, lê config salva.")
    p.add_argument("--servidor", help="URL do WS, ex: wss://achadinhos.app/api/v1/ws/agente")
    p.add_argument("--chrome-porta", type=int, default=9222)
    p.add_argument("--log-level", default="INFO")
    p.add_argument("--sem-tray", action="store_true",
                   help="Roda só headless, sem ícone (útil em dev).")
    p.add_argument("--uri",
                   help="URI achadinhos:// vinda do URL protocol handler (Fase 9.6). "
                        "Se outra instância já está rodando, encaminha pra ela e sai.")
    return p.parse_args()


def _handoff_uri_pra_instancia_rodando(uri: str) -> bool:
    """Tenta encaminhar uma URI achadinhos:// pra outra instância do agente
    já rodando (Fase 9.6). Retorna True se conseguiu — caller deve sair.

    Por que: se o user já tem o agente rodando em background, e clica num link
    achadinhos:// no browser, o Windows lança UM NOVO processo do .exe com
    `--uri`. Não queremos 2 agentes rodando — encaminhamos a URI pra instância
    existente e o novo processo termina.
    """
    import urllib.error
    import urllib.request
    import json as _json

    payload = _json.dumps({"uri": uri}).encode("utf-8")
    for porta in (5577, 5578, 5579):
        req = urllib.request.Request(
            f"http://127.0.0.1:{porta}/uri-trigger",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=2) as r:
                if 200 <= r.status < 300:
                    return True
        except (urllib.error.URLError, OSError, TimeoutError):
            continue
    return False


def montar_config(args: argparse.Namespace) -> Config | None:
    """Combina args e config salva. Retorna None se ainda não há token —
    nesse caso o agente sobe local_server e aguarda `POST /pair` (Fase 9.3).
    """
    salvo = Config.carregar()
    token = args.token or (salvo.token if salvo else None)
    servidor = args.servidor or (salvo.servidor_ws if salvo else None)

    if not token or not servidor:
        return None

    cfg = Config.from_args(
        token=token,
        servidor_ws=servidor,
        chrome_porta=args.chrome_porta,
    )
    cfg.salvar()  # persiste pra próxima execução
    return cfg


# ── Main async ──────────────────────────────────────
async def main_async(
    cfg: Config | None,
    *,
    com_tray: bool = True,
    uri_inicial: str | None = None,
) -> None:
    parar_evento = asyncio.Event()
    cfg_disponivel = asyncio.Event()
    if cfg is not None:
        cfg_disponivel.set()

    # Holder mutável pra cfg + cliente WS (callback do /pair atualiza ambos).
    # `cliente` só existe APÓS o pareamento inicial — antes disso é None.
    estado_cfg: dict[str, Config | None] = {"cfg": cfg}
    estado_cliente: dict[str, "WSClient | None"] = {"cliente": None}

    def on_paired(novo_cfg: Config) -> None:
        if estado_cfg["cfg"] is None:
            # Primeiro pareamento — destrava o boot do WS
            estado_cfg["cfg"] = novo_cfg
            cfg_disponivel.set()
            log.info("agent.pareado_inicial", servidor=novo_cfg.servidor_ws)
            return

        # Re-pareamento durante runtime (v3.9.1+): trocar o cfg no WSClient
        # e fechar a conexão atual pra forçar reconexão com o token novo.
        # Antes disso (até v3.9.0), o user precisava reiniciar o .exe — UX
        # ruim, especialmente após reset do banco em que o agente_id antigo
        # some e o WS fica em loop infinito sem nunca conseguir conectar.
        estado_cfg["cfg"] = novo_cfg
        cli = estado_cliente["cliente"]
        if cli is not None:
            try:
                asyncio.create_task(cli.aplicar_novo_cfg(novo_cfg))
                log.info("agent.repareado_em_runtime",
                         servidor=novo_cfg.servidor_ws)
            except Exception as e:
                log.warning("agent.repair_falhou", erro=str(e)[:200])
        else:
            log.warning("agent.repair_sem_cliente_vivo",
                        msg="Cliente WS ainda não estava ativo — config salva, "
                            "vai pegar no próximo boot.")

    # Tray (opcional)
    tray: Tray | None = None
    if com_tray:
        try:
            tray = Tray(on_sair=lambda: parar_evento.set())
            tray.iniciar()
        except Exception as e:
            log.warning("tray.indisponivel", erro=str(e))
            tray = None

    # Local server SEMPRE sobe (mesmo sem cfg — pra aceitar /pair)
    local_srv = LocalServer(cfg=cfg, on_paired=on_paired)
    try:
        await local_srv.iniciar()
    except Exception as e:
        log.error("local_server.indisponivel", erro=str(e))
        local_srv = None

    # Fase 9.6: se essa instância foi acionada com `--uri` (URL protocol),
    # processa AGORA, após o servidor local estar pronto.
    if uri_inicial and local_srv is not None:
        await local_srv.processar_uri(uri_inicial)

    # Se ainda não há cfg, aguarda /pair (ou Ctrl+C)
    if cfg is None:
        log.info(
            "agent.aguardando_pareamento",
            porta_http=(local_srv.porta if local_srv else None),
            dica="Abra o dashboard e clique em 'Conectar meu WhatsApp'",
        )
        if tray:
            try:
                tray.atualizar_status("offline")
            except Exception:
                pass
        pair_task = asyncio.create_task(cfg_disponivel.wait())
        parar_pair_task = asyncio.create_task(parar_evento.wait())
        done, pending = await asyncio.wait(
            [pair_task, parar_pair_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
        if parar_pair_task in done:
            # User parou antes de parear
            if local_srv is not None:
                await local_srv.parar()
            return
        cfg = estado_cfg["cfg"]
        assert cfg is not None  # post-pair, garantido

    # Daqui em diante, cfg é válido — boot normal do agente
    whatsapp.configurar(porta=cfg.chrome_porta, perfil=cfg.chrome_perfil)

    # Cliente WS
    cliente = WSClient(cfg)
    # Registra no holder pra que `on_paired` consiga trocar o cfg do
    # cliente em runtime (Fase 9.x — re-pareamento sem restart).
    estado_cliente["cliente"] = cliente

    # Linka módulo de avisos pro WS — permite que código sync (thread)
    # publique mensagens user-facing no dashboard via `avisos.publicar(...)`.
    from agent import avisos
    avisos.configurar(cliente, asyncio.get_event_loop())

    # Fase 20 — módulo de progresso (UI dashboard mostra barra em tempo real).
    # `ws_progresso.reportar(tarefa_id, pct, mensagem)` pode ser chamado de
    # qualquer thread (asyncio.to_thread) — agenda envio no loop principal.
    from agent import ws_progresso
    ws_progresso.configurar(cliente, asyncio.get_event_loop())

    # Handler de postagem WhatsApp
    async def handler_postar(msg: dict) -> dict:
        if tray:
            tray.atualizar_status("postando")
        try:
            resultado = await whatsapp.postar(
                grupo_nome=msg.get("identificador") or msg.get("grupo_nome", ""),
                texto=msg["texto"],
                imagem_url=msg.get("imagem_url"),
            )
            return resultado
        finally:
            if tray:
                tray.atualizar_status("online")

    cliente.on_comando("postar_whatsapp", handler_postar)

    # Handler de busca Mercado Livre (Fase 4b)
    async def handler_busca_ml(msg: dict) -> dict:
        # v3.9.2: lê do estado_cfg, NÃO da closure. Re-pareamento em
        # runtime (v3.9.1) trocava `estado_cfg["cfg"]` mas a closure
        # mantinha cfg antigo → POST /ingest com token antigo → 401.
        cfg_atual = estado_cfg["cfg"]
        if cfg_atual is None:
            return {"ok": False, "erro": "agente_sem_cfg"}
        if tray:
            tray.atualizar_status("postando")  # reusa "postando" como "ocupado"
        try:
            return await executar_busca(msg, cfg_atual)
        finally:
            if tray:
                tray.atualizar_status("online")

    cliente.on_comando("iniciar_busca_ml", handler_busca_ml)

    # Handler de geração de links de afiliado ML (Fase 15)
    async def handler_gerar_links_ml(msg: dict) -> dict:
        """Recebe lista de URLs canônicas, retorna `meli.la/XXX` em lote.

        Body esperado: {"urls": ["https://mercadolivre.com.br/.../p/MLB...", ...]}
        Resposta: {"ok": True, "mapping": {"url": "meli.la/XXX"}, "total": N}

        ⚠️ Retorno PRECISA ter `"ok": True` — ws_client._executar_handler
        considera resultado sem `ok` como falha e envia `tarefa_falhou` ao
        servidor (ao invés de `tarefa_concluida`). Bug em prod até v3.0.9
        fazia o servidor NUNCA chamar `aplicar_mapping` porque a tarefa
        nunca era marcada como concluída.

        Pré-condição: chrome_perfil_ml já fez login no painel ML afiliados
        (uma vez manual via `python -m agent.login_ml` num path adequado, ou
        login direto no painel). Sem isso, scraping volta vazio.
        """
        from agent.linkbuilder_ml import gerar_links_em_lote

        # v3.9.2: lê do estado_cfg (re-pareamento runtime).
        cfg_atual = estado_cfg["cfg"]
        if cfg_atual is None:
            return {"ok": False, "erro": "agente_sem_cfg",
                    "mapping": {}, "total": 0}
        urls = msg.get("urls") or []
        if not isinstance(urls, list) or not urls:
            return {"ok": False, "erro": "lista_vazia", "mapping": {}, "total": 0}

        if tray:
            tray.atualizar_status("postando")
        try:
            mapping = await gerar_links_em_lote(cfg_atual, urls)
            return {
                "ok":      True,
                "mapping": mapping,
                "total":   len(mapping),
                "pedidos": len(urls),
            }
        finally:
            if tray:
                tray.atualizar_status("online")

    cliente.on_comando("gerar_links_afiliado_ml", handler_gerar_links_ml)

    # Handler de revalidação de comissões via barra ML (Fase 18.3 / v3.4.2)
    async def handler_revalidar_comissao_ml(msg: dict) -> dict:
        """Abre o link de afiliado de cada produto do TOP, captura comissão
        da barra preta, devolve mapping `{produto_id: comissao_pct}`.

        Body esperado: {"items": [{"produto_id": int, "url_afiliado": "meli.la/XXX"}, ...]}
        Resposta: {"ok": True, "mapping_por_id": {produto_id: pct}, "total": N}

        Por que abre o `url_afiliado` (meli.la):
        ML registra como clique de afiliado real → barra preta aparece
        com a comissão CORRETA do programa (incluindo bônus EXTRAS).
        Abrir só a URL canônica pode mostrar comissão genérica.

        ⚠️ Retorno PRECISA ter `"ok": True` — ws_client._executar_handler
        decide tarefa_concluida vs tarefa_falhou por isso.
        """
        from agent.busca_ml import revalidar_comissoes_em_lote

        # v3.9.2: lê do estado_cfg (re-pareamento runtime).
        cfg_atual = estado_cfg["cfg"]
        if cfg_atual is None:
            return {"ok": False, "erro": "agente_sem_cfg",
                    "mapping_por_id": {}, "total": 0}
        items = msg.get("items") or []
        if not isinstance(items, list) or not items:
            return {"ok": False, "erro": "lista_vazia",
                    "mapping_por_id": {}, "total": 0}

        if tray:
            tray.atualizar_status("postando")
        try:
            mapping = await revalidar_comissoes_em_lote(cfg_atual, items)
            # JSON exige chaves string — converte int keys
            mapping_json = {str(k): v for k, v in mapping.items()}
            return {
                "ok":             True,
                "mapping_por_id": mapping_json,
                "total":          len(mapping),
                "pedidos":        len(items),
            }
        finally:
            if tray:
                tray.atualizar_status("online")

    cliente.on_comando("revalidar_comissao_ml", handler_revalidar_comissao_ml)

    # Fase 20.1 — Cancelamento cooperativo de tarefa em andamento
    async def handler_cancelar_tarefa(msg: dict) -> dict:
        """User clicou '✕ Cancelar' na UI. Sinaliza pro loop longo parar.

        Body: {"tarefa_id": N}
        Resposta: {"ok": True, "marcada": True}

        Python não permite matar thread bruto. A flag é checada por loops
        longos (ex: busca_padrao_ml entre categorias) que param graciosamente
        e retornam o que tinham até ali.
        """
        from agent import cancelamento

        tarefa_id = msg.get("tarefa_id")
        cancelamento.marcar(tarefa_id)
        return {"ok": True, "marcada": True, "tarefa_id": tarefa_id}

    cliente.on_comando("cancelar_tarefa", handler_cancelar_tarefa)

    if tray:
        tray.atualizar_status("online")

    # Sinais de parada (Ctrl+C, kill)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, parar_evento.set)
        except NotImplementedError:
            # Windows não suporta add_signal_handler — Ctrl+C ainda funciona via KeyboardInterrupt
            pass

    # Roda WS até alguém chamar parar_evento
    ws_task = asyncio.create_task(cliente.run_forever())
    parar_task = asyncio.create_task(parar_evento.wait())

    done, pending = await asyncio.wait(
        [ws_task, parar_task],
        return_when=asyncio.FIRST_COMPLETED,
    )

    log.info("agent.parando")
    await cliente.parar()
    if local_srv is not None:
        await local_srv.parar()
    for t in pending:
        t.cancel()


def main() -> None:
    args = parse_args()
    configurar_logging(args.log_level)

    # Fase 9.6: se foi invocado pelo URL protocol handler (achadinhos://...),
    # tenta encaminhar pra instância já rodando antes de subir um 2º agente.
    if args.uri:
        if _handoff_uri_pra_instancia_rodando(args.uri):
            log.info("uri.handoff_ok", uri=args.uri)
            return
        log.info("uri.sem_instancia_rodando_inicia_normal", uri=args.uri)

    cfg = montar_config(args)
    if cfg is None:
        log.info("agent.iniciando", servidor=None,
                 modo="aguardando_pareamento_via_dashboard")
    else:
        log.info("agent.iniciando", servidor=cfg.servidor_ws)

    try:
        asyncio.run(main_async(cfg, com_tray=not args.sem_tray, uri_inicial=args.uri))
    except KeyboardInterrupt:
        log.info("agent.encerrado_por_usuario")


if __name__ == "__main__":
    main()

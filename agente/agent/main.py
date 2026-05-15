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
    return p.parse_args()


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
async def main_async(cfg: Config | None, *, com_tray: bool = True) -> None:
    parar_evento = asyncio.Event()
    cfg_disponivel = asyncio.Event()
    if cfg is not None:
        cfg_disponivel.set()

    # Holder mutável pra cfg (callback do /pair atualiza)
    estado_cfg: dict[str, Config | None] = {"cfg": cfg}

    def on_paired(novo_cfg: Config) -> None:
        if estado_cfg["cfg"] is None:
            # Primeiro pareamento — destrava o boot do WS
            estado_cfg["cfg"] = novo_cfg
            cfg_disponivel.set()
            log.info("agent.pareado_inicial", servidor=novo_cfg.servidor_ws)
        else:
            # Re-pareamento durante runtime — não dá pra trocar token do WS
            # vivo sem refazer o handshake. Por enquanto: salva e pede restart.
            log.warning(
                "agent.repareado_restart_necessario",
                msg="Token novo salvo no config. Reinicie o agente pra usar.",
            )

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
        if tray:
            tray.atualizar_status("postando")  # reusa "postando" como "ocupado"
        try:
            return await executar_busca(msg, cfg)
        finally:
            if tray:
                tray.atualizar_status("online")

    cliente.on_comando("iniciar_busca_ml", handler_busca_ml)

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
    cfg = montar_config(args)
    if cfg is None:
        log.info("agent.iniciando", servidor=None,
                 modo="aguardando_pareamento_via_dashboard")
    else:
        log.info("agent.iniciando", servidor=cfg.servidor_ws)

    try:
        asyncio.run(main_async(cfg, com_tray=not args.sem_tray))
    except KeyboardInterrupt:
        log.info("agent.encerrado_por_usuario")


if __name__ == "__main__":
    main()

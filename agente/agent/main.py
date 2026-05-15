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


def montar_config(args: argparse.Namespace) -> Config:
    """Combina args e config salva."""
    salvo = Config.carregar()
    token = args.token or (salvo.token if salvo else None)
    servidor = args.servidor or (salvo.servidor_ws if salvo else None)

    if not token or not servidor:
        print()
        print("=" * 70)
        print("Config nao encontrada — rode primeiro o setup:")
        print()
        print("    python -m agent.setup")
        print()
        print("(O setup vai pedir email/senha da sua conta no dashboard,")
        print(" registrar este PC como agente, e gravar a config localmente.)")
        print("=" * 70)
        print()
        log.error("config.faltando",
                  detalhe="Forneça --token e --servidor (ou rode python -m agent.setup)")
        sys.exit(1)

    cfg = Config.from_args(
        token=token,
        servidor_ws=servidor,
        chrome_porta=args.chrome_porta,
    )
    cfg.salvar()  # persiste pra próxima execução
    return cfg


# ── Main async ──────────────────────────────────────
async def main_async(cfg: Config, *, com_tray: bool = True) -> None:
    parar_evento = asyncio.Event()

    # Tray (opcional)
    tray: Tray | None = None
    if com_tray:
        try:
            tray = Tray(on_sair=lambda: parar_evento.set())
            tray.iniciar()
        except Exception as e:
            log.warning("tray.indisponivel", erro=str(e))
            tray = None

    # Configura o postador (porta + perfil do Chrome)
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
    for t in pending:
        t.cancel()


def main() -> None:
    args = parse_args()
    configurar_logging(args.log_level)
    cfg = montar_config(args)
    log.info("agent.iniciando", servidor=cfg.servidor_ws)

    try:
        asyncio.run(main_async(cfg, com_tray=not args.sem_tray))
    except KeyboardInterrupt:
        log.info("agent.encerrado_por_usuario")


if __name__ == "__main__":
    main()

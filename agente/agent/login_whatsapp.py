"""
Login manual no WhatsApp Web — abre Chrome em modo debug remoto com o
perfil persistente do agente.

Use 1× pra ler o QR code do WhatsApp com seu celular. Depois disso, a
sessão fica salva no perfil — toda vez que o agente rodar uma postagem,
ele reusa essa sessão (WhatsApp não pede QR de novo, dura semanas).

Diferenças do `login_ml.py`:
- Usa o módulo `agent.chrome.garantir_chrome` (Selenium puro + remote debug
  port 9222), mesmo modelo usado pelo postador `agent.postador.whatsapp`.
- Perfil dedicado ao WhatsApp (`chrome_perfil`), separado do ML
  (`chrome_perfil_ml`) — sessões nunca se misturam.

Uso:
    python -m agent.login_whatsapp
"""
from __future__ import annotations

import sys
import time

import structlog

from agent.chrome import garantir_chrome
from agent.config import Config


def configurar_logging() -> None:
    import logging
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.dev.ConsoleRenderer(colors=False),
        ],
    )


log = structlog.get_logger(__name__)


URL_WHATSAPP = "https://web.whatsapp.com"


def main() -> int:
    configurar_logging()

    cfg = Config.carregar()
    if cfg is None:
        print(
            "ERRO: config nao encontrada. Rode o agente uma vez primeiro:\n"
            "  python -m agent.main --token <SEU_TOKEN> --servidor ws://...",
            file=sys.stderr,
        )
        return 1

    print()
    print("=" * 70)
    print("LOGIN MANUAL NO WHATSAPP WEB")
    print("=" * 70)
    print("1. Vou abrir o Chrome no perfil que o agente usa pra postar.")
    print("2. Espera a pagina do WhatsApp carregar.")
    print("3. No celular, abra WhatsApp -> Aparelhos conectados -> Conectar")
    print("   um aparelho -> Aponte pro QR na tela.")
    print(f"4. Perfil salvo em: {cfg.chrome_perfil}")
    print("5. Quando ver suas conversas, voce pode FECHAR a janela (X).")
    print("   A sessao fica salva e o agente vai reusar nas postagens.")
    print("=" * 70)
    print()

    driver = garantir_chrome(porta=cfg.chrome_porta, perfil=cfg.chrome_perfil)
    if driver is None:
        print("ERRO: nao consegui abrir/conectar no Chrome.", file=sys.stderr)
        return 1

    try:
        # Procura aba já aberta no WhatsApp; senão abre nova
        ja_aberta = False
        for handle in driver.window_handles:
            driver.switch_to.window(handle)
            if "web.whatsapp.com" in (driver.current_url or "").lower():
                ja_aberta = True
                log.info("whatsapp.aba_existente", handle=handle)
                break

        if not ja_aberta:
            log.info("whatsapp.abrindo_aba", url=URL_WHATSAPP)
            driver.execute_script(f"window.open('{URL_WHATSAPP}', '_blank');")
            time.sleep(2)
            # Volta foco pra nova aba
            handles = driver.window_handles
            driver.switch_to.window(handles[-1])

        log.info("whatsapp.aguardando_fechamento_manual")

        # Loop até user fechar a janela (todas abas)
        while True:
            try:
                _ = len(driver.window_handles)
                time.sleep(2)
            except Exception:
                log.info("whatsapp.janela_fechada_pelo_user")
                break

    except KeyboardInterrupt:
        print("\nInterrompido por Ctrl+C — fechando Chrome.")
    finally:
        # NÃO chamamos driver.quit() — driver foi conectado ao Chrome aberto
        # via remote debugging port; quit fecharia o Chrome inteiro. Em vez
        # disso, deixamos o Chrome aberto sob controle do user.
        pass

    print()
    print("Pronto. Sessao do WhatsApp salva no perfil.")
    print("Pode rodar `python -m agent.main` e disparar lotes/postagens.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

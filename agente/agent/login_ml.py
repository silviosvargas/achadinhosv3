"""
Login manual no Mercado Livre — abre Chrome com perfil persistente.

Use 1x antes de rodar o agente. Loga manualmente, fecha o Chrome quando
quiser, sessão fica salva em %APPDATA%\\Achadinhos\\chrome_perfil_ml.

Próximas execuções do agente reaproveitam essa sessão automaticamente —
ML reconhece o login e não exige verificação.

Uso:
    python -m agent.login_ml
"""
from __future__ import annotations

import sys
import time

import structlog
import undetected_chromedriver as uc

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


def main() -> int:
    configurar_logging()

    cfg = Config.carregar()
    if cfg is None:
        print(
            "ERRO: config nao encontrada. Rode o agente uma vez primeiro pra "
            "salvar a config:\n"
            "  python -m agent.main --token <SEU_TOKEN> --servidor ws://...",
            file=sys.stderr,
        )
        return 1

    print()
    print("=" * 70)
    print("LOGIN MANUAL NO MERCADO LIVRE")
    print("=" * 70)
    print("1. Vou abrir o Chrome com o mesmo perfil que o agente usa.")
    print("2. Faça login com sua conta do ML.")
    print(f"3. Perfil salvo em: {cfg.chrome_perfil_ml}")
    print("4. Quando terminar, FECHE A JANELA do Chrome (X) — pronto.")
    print()
    print("Próximas buscas vão usar essa sessão (ML não pede login de novo).")
    print("=" * 70)
    print()

    opts = uc.ChromeOptions()
    opts.add_argument(f"--user-data-dir={cfg.chrome_perfil_ml}")
    # Evita a tela "Quem esta usando o Chrome?" e "Quer ser o browser padrao?"
    opts.add_argument("--profile-directory=Default")
    opts.add_argument("--no-first-run")
    opts.add_argument("--no-default-browser-check")
    opts.add_argument("--window-size=1366,900")

    log.info("login.chrome_abrindo", perfil=cfg.chrome_perfil_ml)
    driver = uc.Chrome(options=opts, use_subprocess=True)

    try:
        driver.get("https://www.mercadolivre.com.br")
        log.info("login.aguardando_fechamento_manual")

        # Loop até user fechar a janela
        while True:
            try:
                # driver.title acessa o browser. Se janela fechada → exception.
                _ = driver.title
                time.sleep(2)
            except Exception:
                log.info("login.janela_fechada_pelo_user")
                break
    except KeyboardInterrupt:
        print("\nInterrompido por Ctrl+C — fechando Chrome.")
    finally:
        try:
            driver.quit()
        except Exception:
            pass

    print()
    print("Pronto. Sessao do ML salva. Pode rodar `python -m agent.main` agora.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

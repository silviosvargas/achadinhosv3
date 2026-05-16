"""
Login manual no Amazon Associates BR — abre Chrome com perfil persistente.

Use 1× antes de rodar buscas Amazon. Loga em
`associados.amazon.com.br` (programa de afiliados), navega pra um produto
qualquer e confirma que o SiteStripe aparece no topo da página. Fecha o
Chrome — sessão fica salva em `%APPDATA%\\Achadinhos\\chrome_perfil_amazon`.

Sem login válido, o SiteStripe não renderiza e o agente cai em fallback
`?tag=<sua_tag>` (que ainda funciona, mas não usa o link encurtado oficial).

Uso:
    python -m agent.login_amazon
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
    print("LOGIN MANUAL NO AMAZON ASSOCIATES BR")
    print("=" * 70)
    print("1. Vou abrir o Chrome com o perfil dedicado da Amazon.")
    print("2. Logue na sua conta de Amazon Associates (mesma conta que tem")
    print("   tag de afiliado configurada).")
    print(f"3. Perfil salvo em: {cfg.chrome_perfil_amazon}")
    print("4. Navegue pra um produto qualquer e confirme que o SiteStripe")
    print("   aparece no topo da página (barra cinza com 'Obter link').")
    print("5. Quando confirmar, FECHE A JANELA do Chrome (X) — pronto.")
    print()
    print("Proximas buscas Amazon vao usar essa sessao automaticamente.")
    print("Sem isso, o agente usa fallback ?tag=<sua_tag> no servidor.")
    print("=" * 70)
    print()

    opts = uc.ChromeOptions()
    opts.add_argument(f"--user-data-dir={cfg.chrome_perfil_amazon}")
    opts.add_argument("--profile-directory=Default")
    opts.add_argument("--no-first-run")
    opts.add_argument("--no-default-browser-check")
    opts.add_argument("--window-size=1366,900")

    log.info("login_amazon.chrome_abrindo", perfil=cfg.chrome_perfil_amazon)
    driver = uc.Chrome(options=opts, use_subprocess=True)

    try:
        # Abre a página de login do programa de afiliados
        driver.get("https://associados.amazon.com.br/")
        log.info("login_amazon.aguardando_fechamento_manual")

        while True:
            try:
                _ = driver.title
                time.sleep(2)
            except Exception:
                log.info("login_amazon.janela_fechada_pelo_user")
                break
    except KeyboardInterrupt:
        print("\nInterrompido por Ctrl+C — fechando Chrome.")
    finally:
        try:
            driver.quit()
        except Exception:
            pass

    print()
    print("Pronto. Sessao do Amazon Associates salva.")
    print("Pode rodar buscas Amazon agora.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

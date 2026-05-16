"""
Login manual no painel de afiliados Shopee — abre Chrome com perfil persistente.

Use 1× antes de rodar buscas Shopee. Loga manualmente, resolve CAPTCHA
se aparecer, fecha o Chrome — sessão fica salva em
`%APPDATA%\\Achadinhos\\chrome_perfil_shopee`.

Próximas buscas reaproveitam essa sessão. Sem login válido, a API
`affiliate.shopee.com.br/api/v3/offer/product/list` retorna 401/captcha.

Uso:
    python -m agent.login_shopee
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
    print("LOGIN MANUAL NO PAINEL DE AFILIADOS SHOPEE")
    print("=" * 70)
    print("1. Vou abrir o Chrome com o perfil dedicado da Shopee.")
    print("2. Faça login no Shopee Affiliate (mesma conta que tem afiliado configurado).")
    print(f"3. Perfil salvo em: {cfg.chrome_perfil_shopee}")
    print("4. Se aparecer CAPTCHA, resolva manualmente.")
    print("5. Quando estiver na pagina /offer/product_offer (lista de produtos),")
    print("   FECHE A JANELA do Chrome (X) — pronto.")
    print()
    print("Proximas buscas Shopee vao usar essa sessao automaticamente.")
    print("=" * 70)
    print()

    opts = uc.ChromeOptions()
    opts.add_argument(f"--user-data-dir={cfg.chrome_perfil_shopee}")
    opts.add_argument("--profile-directory=Default")
    opts.add_argument("--no-first-run")
    opts.add_argument("--no-default-browser-check")
    opts.add_argument("--window-size=1366,900")

    log.info("login_shopee.chrome_abrindo", perfil=cfg.chrome_perfil_shopee)
    driver = uc.Chrome(options=opts, use_subprocess=True)

    try:
        # Vai direto pro painel — se não logado, Shopee redireciona pra login
        driver.get("https://affiliate.shopee.com.br/offer/product_offer")
        log.info("login_shopee.aguardando_fechamento_manual")

        while True:
            try:
                _ = driver.title
                time.sleep(2)
            except Exception:
                log.info("login_shopee.janela_fechada_pelo_user")
                break
    except KeyboardInterrupt:
        print("\nInterrompido por Ctrl+C — fechando Chrome.")
    finally:
        try:
            driver.quit()
        except Exception:
            pass

    print()
    print("Pronto. Sessao do painel Shopee salva. Pode rodar buscas Shopee agora.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

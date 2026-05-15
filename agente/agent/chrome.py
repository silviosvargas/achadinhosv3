"""
Gerenciamento do Chrome em modo debug remoto.

Portado de V2/src/core/chrome.py com 2 mudanças:
- Não usa config global — recebe porta/perfil via argumentos
- Não importa nada de src.core (independente da V2)

O Chrome roda no PC do afiliado em modo `--remote-debugging-port`.
O Selenium se conecta a esse Chrome já aberto (não abre um novo).

Por que assim?
- O afiliado faz login MANUAL uma vez (WhatsApp QR, Mercado Livre, etc).
- A sessão fica persistida no perfil em disco.
- Próximas execuções: Selenium se conecta no Chrome aberto, sem novo login.
"""
from __future__ import annotations

import os
import socket
import subprocess
import time
from pathlib import Path

import structlog
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

log = structlog.get_logger(__name__)


CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.join(
        os.environ.get("LOCALAPPDATA", ""),
        "Google", "Chrome", "Application", "chrome.exe",
    ),
]


def porta_aberta(porta: int, timeout: float = 1.0) -> bool:
    """Confere se há algo escutando na porta CDP."""
    try:
        with socket.create_connection(("127.0.0.1", porta), timeout=timeout):
            return True
    except (TimeoutError, ConnectionRefusedError, OSError):
        return False


def encontrar_chrome() -> str | None:
    """Retorna o caminho do chrome.exe ou None se não achar."""
    for c in CHROME_PATHS:
        if c and os.path.exists(c):
            return c
    return None


def abrir_chrome(*, porta: int, perfil: str | Path, timeout_seg: int = 30) -> bool:
    """
    Garante que o Chrome está aberto na porta de debug.
    Se já estiver, retorna imediatamente.
    """
    if porta_aberta(porta):
        log.info("chrome.ja_aberto", porta=porta)
        return True

    exe = encontrar_chrome()
    if not exe:
        log.error(
            "chrome.nao_encontrado",
            instale="https://www.google.com/chrome/",
        )
        return False

    perfil_str = str(perfil)
    Path(perfil_str).mkdir(parents=True, exist_ok=True)

    log.info("chrome.abrindo", porta=porta, perfil=perfil_str)
    subprocess.Popen([
        exe,
        f"--remote-debugging-port={porta}",
        f"--user-data-dir={perfil_str}",
    ])

    # Espera Chrome subir
    for _ in range(timeout_seg):
        time.sleep(1)
        if porta_aberta(porta):
            time.sleep(2)  # delay extra pro Chrome estabilizar
            log.info("chrome.iniciado")
            return True

    log.error("chrome.timeout", segundos=timeout_seg)
    return False


def conectar_chrome(*, porta: int) -> webdriver.Chrome:
    """Conecta o Selenium ao Chrome já aberto na porta indicada."""
    opts = Options()
    opts.add_experimental_option("debuggerAddress", f"127.0.0.1:{porta}")
    return webdriver.Chrome(options=opts)


def garantir_chrome(*, porta: int, perfil: str | Path) -> webdriver.Chrome | None:
    """
    Abre Chrome se necessário e retorna driver conectado.
    Retorna None se algo falhar.
    """
    if not abrir_chrome(porta=porta, perfil=perfil):
        return None
    try:
        driver = conectar_chrome(porta=porta)
        log.info(
            "chrome.conectado",
            porta=porta,
            abas=len(driver.window_handles),
        )
        return driver
    except Exception as e:
        log.error("chrome.conectar_falhou", erro=str(e))
        return None

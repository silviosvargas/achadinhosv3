"""
Ícone na barra de tarefas.

Mostra status (online/offline/postando) e dá menu de opções:
- Abrir log
- Configurar token
- Sair

Implementação simples com pystray. Em PyInstaller, é importante incluir
os ícones .ico/.png como data dentro do .spec.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

from PIL import Image
import pystray

import structlog

log = structlog.get_logger(__name__)


class Tray:
    """Wrapper simples ao redor do pystray.

    Como o pystray é síncrono, roda em thread separada.
    """

    def __init__(self, *, on_sair: Callable[[], None]) -> None:
        self._on_sair = on_sair
        self._icon: pystray.Icon | None = None
        self._status = "offline"

    def _criar_imagem(self, status: str) -> Image.Image:
        """Gera ícone procedural (cor varia conforme status)."""
        # 64x64 quadrado colorido — placeholder até ter ícones de verdade
        cor = {
            "online":  (0x16, 0xa3, 0x4a),     # verde
            "postando":(0x25, 0x63, 0xeb),     # azul
            "erro":    (0xdc, 0x26, 0x26),     # vermelho
            "offline": (0x9c, 0xa3, 0xaf),     # cinza
        }.get(status, (0x6b, 0x72, 0x80))
        img = Image.new("RGB", (64, 64), cor)
        return img

    def iniciar(self) -> None:
        """Inicia o ícone numa thread daemon (não bloqueia)."""
        def _run() -> None:
            menu = pystray.Menu(
                pystray.MenuItem("Achadinhos Agent", None, enabled=False),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(
                    lambda _: f"Status: {self._status}",
                    None, enabled=False,
                ),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Sair", lambda icon, _: self._sair()),
            )
            self._icon = pystray.Icon(
                "achadinhos-agent",
                self._criar_imagem("offline"),
                "Achadinhos Agent",
                menu,
            )
            self._icon.run()

        threading.Thread(target=_run, daemon=True).start()

    def atualizar_status(self, status: str) -> None:
        """Muda cor do ícone conforme status."""
        self._status = status
        if self._icon is not None:
            try:
                self._icon.icon = self._criar_imagem(status)
            except Exception as e:
                log.debug("tray.atualizar_falhou", erro=str(e))

    def _sair(self) -> None:
        if self._icon is not None:
            self._icon.stop()
        self._on_sair()

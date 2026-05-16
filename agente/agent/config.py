"""
Configuração do agente local.

Lê de:
- Argumentos de linha de comando
- Arquivo %APPDATA%\\Achadinhos\\config.json
- Variáveis de ambiente (fallback)

O token é o segredo mais sensível. Em produção, ofuscar com DPAPI
(Data Protection API do Windows). Por enquanto fica em JSON simples.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


def _config_dir() -> Path:
    """Diretório onde guardamos config + perfil Chrome."""
    if os.name == "nt":  # Windows
        base = Path(os.environ.get("APPDATA", str(Path.home())))
    else:
        base = Path.home() / ".config"
    d = base / "Achadinhos"
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass
class Config:
    """Configuração efetiva do agente em runtime."""
    token: str
    servidor_ws: str
    chrome_perfil: str
    chrome_porta: int = 9222
    log_level: str = "INFO"
    # Fase 4b: Chrome separado pra buscas ML (porta+perfil próprios, não polui WhatsApp)
    chrome_porta_ml: int = 9223
    ml_headless: bool = False        # em dev, visível pra debug

    @property
    def config_dir(self) -> Path:
        return _config_dir()

    @property
    def log_file(self) -> Path:
        return self.config_dir / "agent.log"

    @property
    def chrome_perfil_ml(self) -> str:
        return str(_config_dir() / "chrome_perfil_ml")

    @property
    def chrome_perfil_shopee(self) -> str:
        """Perfil Chrome dedicado pra sessão do painel afiliados Shopee."""
        return str(_config_dir() / "chrome_perfil_shopee")

    @property
    def servidor_api(self) -> str:
        """
        Deriva URL da API REST a partir do servidor_ws.
        ws://host:port/api/v1/ws/agente  →  http://host:port
        wss://host/api/v1/ws/agente      →  https://host
        """
        ws = self.servidor_ws
        if ws.startswith("wss://"):
            base = "https://" + ws[len("wss://"):]
        elif ws.startswith("ws://"):
            base = "http://" + ws[len("ws://"):]
        else:
            base = ws
        # corta o path do WS — fica só scheme + host[:port]
        from urllib.parse import urlparse
        p = urlparse(base)
        host = p.netloc or p.path.split("/", 1)[0]
        return f"{p.scheme}://{host}"

    def salvar(self) -> None:
        """Persiste config (sem token em produção — usar DPAPI)."""
        payload = {
            "token":          self.token,
            "servidor_ws":    self.servidor_ws,
            "chrome_perfil":  self.chrome_perfil,
            "chrome_porta":   self.chrome_porta,
            "log_level":      self.log_level,
            "chrome_porta_ml": self.chrome_porta_ml,
            "ml_headless":     self.ml_headless,
        }
        (_config_dir() / "config.json").write_text(
            json.dumps(payload, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def carregar(cls) -> "Config | None":
        """Lê config persistida. Retorna None se não existir.

        Tolerante a chaves novas/ausentes (config antiga não quebra ao ler).
        """
        f = _config_dir() / "config.json"
        if not f.exists():
            return None
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            # Filtra só chaves que o dataclass aceita
            campos = {f.name for f in cls.__dataclass_fields__.values()}
            data_limpa = {k: v for k, v in data.items() if k in campos}
            return cls(**data_limpa)
        except (json.JSONDecodeError, TypeError):
            return None

    @classmethod
    def from_args(
        cls,
        *,
        token: str,
        servidor_ws: str,
        chrome_porta: int = 9222,
    ) -> "Config":
        return cls(
            token=token,
            servidor_ws=servidor_ws,
            chrome_perfil=str(_config_dir() / "chrome_perfil"),
            chrome_porta=chrome_porta,
        )

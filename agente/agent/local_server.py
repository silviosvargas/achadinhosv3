"""
Servidor HTTP local do agente — ponte browser ↔ agente (Fase 9.2).

Roda em `127.0.0.1:5577` (fallback 5578, 5579) em paralelo ao WebSocket
client. O dashboard (https://achadinhos.maisseguidores.ia.br) fala com
esse servidor pra:

- Detectar se o agente está instalado e ativo (`GET /ping`).
- Saber o status atual do agente (`GET /status`).
- Parear automaticamente (`POST /pair`) — Fase 9.3.
- Abrir WhatsApp Web + tabs de marketplaces (`POST /abrir-tudo`) — Fase 9.x.

CORS habilitado pra origem do dashboard prod + localhost dev.

Decisão arquitetural completa: docs/decisoes.md ADR-009.
"""
from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING, Awaitable, Callable

import structlog
from aiohttp import web

if TYPE_CHECKING:
    from agent.config import Config


log = structlog.get_logger(__name__)


VERSAO_AGENTE = "3.0.0"

# Origens permitidas pelo CORS. Adicionar aqui qualquer host que vá
# falar com o agente local pelo browser.
CORS_ORIGENS_PERMITIDAS = frozenset({
    "https://achadinhos.maisseguidores.ia.br",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
})

# Portas a tentar em ordem. A primeira disponível é usada.
PORTAS_CANDIDATAS = (5577, 5578, 5579)


@web.middleware
async def _cors_middleware(
    request: web.Request,
    handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
) -> web.StreamResponse:
    """Adiciona headers CORS pra origens conhecidas. Responde preflight."""
    origin = request.headers.get("Origin", "")

    if request.method == "OPTIONS":
        resp: web.StreamResponse = web.Response(status=204)
    else:
        resp = await handler(request)

    if origin in CORS_ORIGENS_PERMITIDAS:
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        resp.headers["Access-Control-Allow-Credentials"] = "true"
        resp.headers["Vary"] = "Origin"
    return resp


def _extrair_agente_id_do_jwt(token: str) -> int | None:
    """Decodifica payload do JWT (sem validar assinatura) pra pegar `agente`.

    O token já é validado pelo servidor quando usado em WS — aqui só queremos
    o claim pra mostrar no `/ping` / `/status`.
    """
    try:
        payload_b64 = token.split(".")[1]
        # Padding base64url
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        return payload.get("agente")
    except Exception:
        return None


class LocalServer:
    """HTTP server local — ponte browser ↔ agente.

    Uso:
        srv = LocalServer(cfg=cfg)
        porta = await srv.iniciar()        # tenta 5577, 5578, 5579
        # ... agente roda ...
        await srv.parar()
    """

    def __init__(self, cfg: "Config | None" = None) -> None:
        self.cfg = cfg
        self.porta: int | None = None
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        # Estado compartilhado com o WS client (preenchido externamente)
        self.ws_conectado: bool = False
        self.ultimo_erro: str | None = None

    # ── Handlers ──────────────────────────────────────────────────────

    async def _handle_ping(self, request: web.Request) -> web.Response:
        """Detecção: 'o agente tá vivo?'. Sem auth."""
        return web.json_response({
            "ok": True,
            "versao": VERSAO_AGENTE,
            "agente_id": _extrair_agente_id_do_jwt(self.cfg.token) if self.cfg else None,
            "porta": self.porta,
        })

    async def _handle_status(self, request: web.Request) -> web.Response:
        """Estado detalhado do agente."""
        return web.json_response({
            "ok": True,
            "versao": VERSAO_AGENTE,
            "agente_id": _extrair_agente_id_do_jwt(self.cfg.token) if self.cfg else None,
            "configurado": self.cfg is not None,
            "servidor_ws": self.cfg.servidor_ws if self.cfg else None,
            "ws_conectado": self.ws_conectado,
            "ultimo_erro": self.ultimo_erro,
        })

    async def _handle_pair(self, request: web.Request) -> web.Response:
        """Pareamento via JWT — implementado na Fase 9.3."""
        return web.json_response(
            {"erro": "not_implemented", "msg": "Pareamento via JWT vem na Fase 9.3"},
            status=501,
        )

    async def _handle_abrir_tudo(self, request: web.Request) -> web.Response:
        """Abre WhatsApp Web + tabs de marketplaces — implementado em fase futura."""
        return web.json_response(
            {"erro": "not_implemented", "msg": "Abertura de tabs vem em fase futura"},
            status=501,
        )

    async def _handle_options_catchall(self, request: web.Request) -> web.Response:
        """Preflight OPTIONS pra qualquer rota."""
        return web.Response(status=204)

    # ── Lifecycle ─────────────────────────────────────────────────────

    def _criar_app(self) -> web.Application:
        app = web.Application(middlewares=[_cors_middleware])
        app.router.add_get("/ping", self._handle_ping)
        app.router.add_get("/status", self._handle_status)
        app.router.add_post("/pair", self._handle_pair)
        app.router.add_post("/abrir-tudo", self._handle_abrir_tudo)
        app.router.add_route("OPTIONS", "/{tail:.*}", self._handle_options_catchall)
        return app

    async def iniciar(self) -> int:
        """Tenta subir na primeira porta disponível. Retorna porta usada.

        Levanta RuntimeError se todas as `PORTAS_CANDIDATAS` estão em uso.
        """
        app = self._criar_app()
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()

        for porta in PORTAS_CANDIDATAS:
            site = web.TCPSite(self._runner, host="127.0.0.1", port=porta)
            try:
                await site.start()
                self._site = site
                self.porta = porta
                log.info("local_server.iniciado", porta=porta)
                return porta
            except OSError as e:
                log.warning("local_server.porta_ocupada", porta=porta, erro=str(e))
                # site não startou — não precisa stop. Tenta próxima porta.
                continue

        # Nenhuma porta disponível — cleanup
        await self._runner.cleanup()
        self._runner = None
        raise RuntimeError(
            f"Todas as portas {PORTAS_CANDIDATAS} estão ocupadas. "
            f"Outro agente Achadinhos rodando? Feche e tente de novo."
        )

    async def parar(self) -> None:
        """Encerra servidor limpo."""
        if self._site is not None:
            await self._site.stop()
            self._site = None
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
        self.porta = None
        log.info("local_server.parado")

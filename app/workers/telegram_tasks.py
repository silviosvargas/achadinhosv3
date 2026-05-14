"""
Tasks Celery do Telegram.

Duas tasks:

1. validar_canal_telegram(canal_id)
   - Roda quando o canal é criado (via .delay() do endpoint)
   - Bate em /getMe pra validar o token
   - Salva bot_username em config se OK
   - Marca ultima_falha_msg se token inválido (admin vê o erro na UI)

2. postar_telegram(tarefa_id)
   - Roda quando dispatcher detecta canal telegram_bot
   - Lê payload da tarefa, monta requisição, chama sendMessage/sendPhoto
   - Atualiza tarefa: concluida ou falhou (com retry inteligente)

Usa sessão SÍNCRONA do SQLAlchemy — Celery não é async-friendly.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import select

from app.db import sessao_sync
from app.models import Canal, Grupo, StatusTarefa, Tarefa
from app.services.telegram_client import (
    TelegramError,
    get_me,
    send_message,
    send_photo,
)
from app.workers.celery_app import celery_app

log = structlog.get_logger(__name__)


# ============================================================
# Validação do bot_token (assíncrona — em background)
# ============================================================

@celery_app.task(name="validar_canal_telegram", max_retries=2, default_retry_delay=60)
def validar_canal_telegram(canal_id: int) -> dict[str, Any]:
    """
    Valida o bot_token batendo em /getMe. Atualiza canal:
    - Sucesso: salva bot_username em config, limpa ultima_falha_msg.
    - Falha:   grava ultima_falha_msg + ultima_falha_em.
               Não desativa o canal — admin pode corrigir o token.
    """
    with sessao_sync() as db:
        canal = db.get(Canal, canal_id)
        if canal is None:
            log.warning("validar.canal_inexistente", canal_id=canal_id)
            return {"ok": False, "erro": "canal_nao_encontrado"}

        if canal.tipo != "telegram_bot":
            return {"ok": False, "erro": "tipo_invalido"}

        token = (canal.config or {}).get("bot_token")
        if not token:
            canal.ultima_falha_em = datetime.now(tz=timezone.utc)
            canal.ultima_falha_msg = "config sem bot_token"
            db.commit()
            return {"ok": False, "erro": "sem_token"}

        try:
            info = get_me(token)
        except TelegramError as e:
            canal.ultima_falha_em = datetime.now(tz=timezone.utc)
            canal.ultima_falha_msg = f"validacao: {e.codigo} — {e.descricao or str(e)}"[:500]
            db.commit()
            log.warning("validar.token_invalido", canal_id=canal_id, codigo=e.codigo)
            return {"ok": False, "erro": e.codigo, "detalhes": e.descricao}
        except Exception as e:
            log.exception("validar.erro_inesperado", canal_id=canal_id, erro=str(e))
            canal.ultima_falha_em = datetime.now(tz=timezone.utc)
            canal.ultima_falha_msg = f"validacao: erro inesperado — {type(e).__name__}: {str(e)[:200]}"
            db.commit()
            return {"ok": False, "erro": "erro_rede"}

        # Sucesso: enriquece config com info do bot
        novo_config = dict(canal.config or {})
        novo_config["bot_id"]       = info.get("id")
        novo_config["bot_username"] = info.get("username")
        novo_config["bot_nome"]     = info.get("first_name")
        canal.config = novo_config
        canal.ultima_falha_em  = None
        canal.ultima_falha_msg = None
        db.commit()

        log.info("validar.ok", canal_id=canal_id, bot_username=info.get("username"))
        return {
            "ok": True,
            "bot_username": info.get("username"),
            "bot_id": info.get("id"),
        }


# ============================================================
# Postagem (consome tarefa do banco)
# ============================================================

@celery_app.task(
    name="postar_telegram",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def postar_telegram(self, tarefa_id: int) -> dict[str, Any]:
    """
    Executa a postagem no Telegram a partir de uma tarefa pendente.

    Estratégia:
    - Se há imagem_url: sendPhoto + caption (até 1024 chars)
                        + sendMessage com o restante do texto se for maior
    - Se não há imagem: sendMessage simples
    """
    with sessao_sync() as db:
        tarefa = db.get(Tarefa, tarefa_id)
        if tarefa is None:
            log.warning("postar.tarefa_inexistente", tarefa_id=tarefa_id)
            return {"ok": False, "erro": "tarefa_nao_encontrada"}

        # Marca processando
        tarefa.status = StatusTarefa.PROCESSANDO
        tarefa.iniciado_em = datetime.now(tz=timezone.utc)
        tarefa.tentativas += 1
        db.commit()

        payload = tarefa.payload or {}
        grupo_id = payload.get("grupo_id")
        texto    = payload.get("texto", "")
        img_url  = payload.get("imagem_url")

        # Carrega grupo + canal
        grupo = db.get(Grupo, grupo_id) if grupo_id else None
        if grupo is None:
            return _falhou_definitivo(db, tarefa, "grupo_nao_encontrado")

        canal = db.get(Canal, grupo.canal_id)
        if canal is None or canal.tipo != "telegram_bot":
            return _falhou_definitivo(db, tarefa, "canal_invalido")

        token = (canal.config or {}).get("bot_token")
        if not token:
            return _falhou_definitivo(db, tarefa, "canal_sem_token")

        chat_id = grupo.identificador  # ex: "-1001234567890"

        # Executa
        try:
            resultado = _executar_postagem(
                token=token, chat_id=chat_id,
                texto=texto, imagem_url=img_url,
            )
        except TelegramError as e:
            return _tratar_erro_telegram(db, tarefa, canal, e, retry_handle=self)
        except Exception as e:
            log.exception("postar.erro_inesperado", tarefa_id=tarefa_id)
            try:
                self.retry(exc=e)
            except self.MaxRetriesExceededError:
                return _falhou_definitivo(db, tarefa,
                                           f"erro_inesperado: {type(e).__name__}: {str(e)[:200]}")
            return {"ok": False, "erro": "retry"}

        # Sucesso
        tarefa.status        = StatusTarefa.CONCLUIDA
        tarefa.resultado     = resultado
        tarefa.concluido_em  = datetime.now(tz=timezone.utc)
        canal.ultima_postagem_em = datetime.now(tz=timezone.utc)
        db.commit()

        log.info("postar.ok", tarefa_id=tarefa_id, chat_id=chat_id)
        return {"ok": True, **resultado}


def _executar_postagem(
    *,
    token: str,
    chat_id: str,
    texto: str,
    imagem_url: str | None,
) -> dict[str, Any]:
    """Decide entre sendPhoto, sendMessage ou os dois (caption + complemento)."""
    if not imagem_url:
        info = send_message(token, chat_id, texto)
        return {
            "modo": "texto",
            "message_id": info.get("message_id"),
        }

    # Com imagem — caption tem limite de 1024
    from app.services.telegram_client import TELEGRAM_CAPTION_MAX

    caption = texto[:TELEGRAM_CAPTION_MAX] if texto else None
    info_foto = send_photo(token, chat_id, imagem_url, caption=caption)

    resultado: dict[str, Any] = {
        "modo": "foto_com_caption",
        "photo_message_id": info_foto.get("message_id"),
    }

    # Texto sobrou? Manda como mensagem separada
    if texto and len(texto) > TELEGRAM_CAPTION_MAX:
        complemento = texto[TELEGRAM_CAPTION_MAX:]
        info_txt = send_message(token, chat_id, complemento)
        resultado["modo"] = "foto_com_caption_e_complemento"
        resultado["texto_message_id"] = info_txt.get("message_id")

    return resultado


def _tratar_erro_telegram(db, tarefa, canal, e: TelegramError, retry_handle):
    """Decide entre retry, falha definitiva e qual mensagem registrar no canal."""
    # Erros que NÃO adianta retentar
    nao_retentaveis = {
        "token_invalido",
        "chat_nao_encontrado",
        "bot_sem_permissao",
        "texto_muito_longo",
        "caption_muito_longa",
        "request_invalido",
    }

    erro_msg = f"{e.codigo}: {e.descricao or str(e)}"[:500]

    # Erros do canal (token/permissão) — atualiza canal pra UI mostrar
    if e.codigo in {"token_invalido", "bot_sem_permissao"}:
        canal.ultima_falha_em = datetime.now(tz=timezone.utc)
        canal.ultima_falha_msg = erro_msg
        db.commit()

    if e.codigo in nao_retentaveis:
        return _falhou_definitivo(db, tarefa, erro_msg)

    # Retentável (rate_limit, telegram_indisponivel, etc)
    try:
        retry_handle.retry(exc=e)
    except retry_handle.MaxRetriesExceededError:
        return _falhou_definitivo(db, tarefa, f"max_retries_excedido: {erro_msg}")
    return {"ok": False, "erro": "retry", "detalhes": erro_msg}


def _falhou_definitivo(db, tarefa, msg: str) -> dict[str, Any]:
    """Marca tarefa como falhou de vez (sem retry adicional)."""
    tarefa.status        = StatusTarefa.FALHOU
    tarefa.erro          = msg[:1000]
    tarefa.concluido_em  = datetime.now(tz=timezone.utc)
    db.commit()
    return {"ok": False, "erro": msg}

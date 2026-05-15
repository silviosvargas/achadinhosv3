"""
Service do encurtador (Fase 14).

Upsert: 1 redirect por produto. Quando a tag de afiliado muda e o lote
roda de novo, atualiza `url_destino` mas mantém o slug — links antigos
no WhatsApp continuam funcionando e passam a redirecionar pra URL com
tag nova.

Slug: 8 caracteres `secrets.token_urlsafe(...)` — alfanumérico sem
ambiguidade visual. Espaço de ~218 trilhões; conflito é improvável
mas tratamos com retry (1-2 tentativas, IntegrityError pega).
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import Redirect

log = get_logger(__name__)


SLUG_LEN_BYTES = 6   # token_urlsafe(6) → 8 chars base64url


def _gerar_slug() -> str:
    """Gera slug curto único (probabilisticamente)."""
    return secrets.token_urlsafe(SLUG_LEN_BYTES)


async def criar_ou_atualizar_pro_produto(
    db: AsyncSession,
    *,
    produto_id: int,
    url_destino: str,
) -> Redirect:
    """Garante 1 redirect por produto.

    - Existe? Atualiza `url_destino` e `atualizado_em`. Slug mantido.
    - Não existe? Cria com slug novo.

    Idempotente. Retorna o Redirect (com `.slug` pronto pra usar).
    """
    agora = datetime.now(tz=timezone.utc)
    existing = (await db.execute(
        select(Redirect).where(Redirect.produto_id == produto_id)
    )).scalar_one_or_none()

    if existing is not None:
        if existing.url_destino != url_destino:
            existing.url_destino = url_destino
            existing.atualizado_em = agora
            await db.commit()
            await db.refresh(existing)
        return existing

    # Criar — tentar até achar slug livre (raríssimo precisar de retry)
    for _ in range(5):
        slug = _gerar_slug()
        novo = Redirect(
            slug=slug,
            produto_id=produto_id,
            url_destino=url_destino,
            criado_em=agora,
            atualizado_em=agora,
        )
        db.add(novo)
        try:
            await db.commit()
            await db.refresh(novo)
            return novo
        except IntegrityError:
            await db.rollback()
            continue

    raise RuntimeError(
        "Não consegui gerar slug único pra redirect após 5 tentativas "
        "— probabilidade ínfima, vale investigar."
    )


async def resolver(
    db: AsyncSession, *, slug: str,
) -> Redirect | None:
    """Acha o redirect pelo slug. Não incrementa contador (caller decide)."""
    return (await db.execute(
        select(Redirect).where(Redirect.slug == slug)
    )).scalar_one_or_none()


async def registrar_click(db: AsyncSession, *, redirect_id: int) -> None:
    """Incrementa contador + atualiza ultimo_click. Não bloqueia se falhar."""
    try:
        await db.execute(
            update(Redirect)
            .where(Redirect.id == redirect_id)
            .values(
                total_clicks=Redirect.total_clicks + 1,
                ultimo_click_em=datetime.now(tz=timezone.utc),
            )
        )
        await db.commit()
    except Exception as e:
        # Não queremos quebrar o redirect só por causa de métrica
        log.warning("redirect.click_falhou", redirect_id=redirect_id, erro=str(e))
        await db.rollback()

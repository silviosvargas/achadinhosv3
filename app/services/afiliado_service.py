"""
Service de tags de afiliado por marketplace (Fase 13).

Substituiu o lookup mono-marketplace (`Usuario.afiliado_ml`) por uma cascata
genérica baseada em `usuarios_afiliados` (1 row por user × plataforma).

Cascata pra obter a tag a aplicar numa postagem:
  1. user.afiliado pra plataforma (na tabela nova)
  2. fallback: tag do admin da org dele
  3. fallback: tag do admin da org central (settings.admin_org_id)
  4. fallback: settings.{plataforma}_affiliate_id (env var global)
  5. None se nada → linkbuilder devolve URL canônica crua

`afiliado_ml` legacy ainda é lido como fallback DENTRO de cada nível durante
a transição (migration 0008 backfilla pra nova tabela, mas leitura cobre
casos onde a tabela nova esteja vazia).
"""
from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.models import Usuario, UsuarioAfiliado

log = get_logger(__name__)


# ──────────────────────────────────────────────────────────────────────────
# Listagem e CRUD
# ──────────────────────────────────────────────────────────────────────────

async def listar_por_usuario(
    db: AsyncSession, *, usuario_id: int,
) -> list[UsuarioAfiliado]:
    """Todos os afiliados cadastrados pelo user, ordenado por plataforma."""
    rows = await db.execute(
        select(UsuarioAfiliado)
        .where(UsuarioAfiliado.usuario_id == usuario_id)
        .order_by(UsuarioAfiliado.plataforma)
    )
    return list(rows.scalars().all())


async def upsert(
    db: AsyncSession, *, usuario_id: int, plataforma: str, tag: str,
) -> UsuarioAfiliado:
    """Cria ou atualiza a tag do user pra plataforma. Idempotente.

    `tag` é stripado; vazio levanta ValueError. Validação de plataforma
    é responsabilidade do caller (use `marketplaces.por_slug` antes).
    """
    tag = tag.strip()
    if not tag:
        raise ValueError("Tag vazia")

    existing = (await db.execute(
        select(UsuarioAfiliado).where(
            UsuarioAfiliado.usuario_id == usuario_id,
            UsuarioAfiliado.plataforma == plataforma,
        )
    )).scalar_one_or_none()

    if existing is None:
        existing = UsuarioAfiliado(
            usuario_id=usuario_id, plataforma=plataforma, tag=tag,
        )
        db.add(existing)
    else:
        existing.tag = tag
    await db.commit()
    await db.refresh(existing)
    return existing


async def remover(
    db: AsyncSession, *, usuario_id: int, plataforma: str,
) -> bool:
    """Apaga o cadastro de uma plataforma do user. Retorna True se removeu."""
    result = await db.execute(
        delete(UsuarioAfiliado).where(
            UsuarioAfiliado.usuario_id == usuario_id,
            UsuarioAfiliado.plataforma == plataforma,
        )
    )
    await db.commit()
    return (result.rowcount or 0) > 0


# ──────────────────────────────────────────────────────────────────────────
# Lookup com cascata (usado por busca_service / linkbuilder)
# ──────────────────────────────────────────────────────────────────────────

async def _tag_do_usuario(
    db: AsyncSession, *, usuario_id: int, plataforma: str,
) -> str | None:
    """Tag do user pra plataforma. Inclui dual-read do legacy `afiliado_ml`."""
    row = (await db.execute(
        select(UsuarioAfiliado.tag).where(
            UsuarioAfiliado.usuario_id == usuario_id,
            UsuarioAfiliado.plataforma == plataforma,
        )
    )).scalar_one_or_none()
    if row:
        return row

    # Dual-read legacy só pra ML (transição). Remover depois.
    if plataforma == "ml":
        legacy = (await db.execute(
            select(Usuario.afiliado_ml).where(Usuario.id == usuario_id)
        )).scalar_one_or_none()
        if legacy:
            return legacy
    return None


async def _tag_do_admin_da_org(
    db: AsyncSession, *, org_id: int, plataforma: str,
) -> str | None:
    """Tag do primeiro admin ativo da org. Usado em fallback."""
    admin_id = (await db.execute(
        select(Usuario.id).where(
            Usuario.org_id == org_id,
            Usuario.ativo.is_(True),
            Usuario.papel.in_(("admin", "super")),
        ).order_by(Usuario.id).limit(1)
    )).scalar_one_or_none()
    if admin_id is None:
        return None
    return await _tag_do_usuario(db, usuario_id=admin_id, plataforma=plataforma)


async def tag_com_cascata(
    db: AsyncSession,
    *,
    plataforma: str,
    usuario_id: int | None = None,
    org_id: int | None = None,
) -> str | None:
    """Resolve a tag a aplicar pra uma plataforma seguindo a cascata.

    Args:
        plataforma: slug (ex: 'ml', 'shopee').
        usuario_id: user que disparou a busca/postagem. None = pula nível 1.
        org_id: org do disparador. None = pula nível 2.

    Returns:
        Tag string OU None se ninguém na cascata tem. Linkbuilder cai
        no fallback URL canônica nesse caso.
    """
    # 1) user que disparou
    if usuario_id is not None:
        tag = await _tag_do_usuario(db, usuario_id=usuario_id, plataforma=plataforma)
        if tag:
            return tag

    # 2) admin da org do disparador
    if org_id is not None:
        tag = await _tag_do_admin_da_org(db, org_id=org_id, plataforma=plataforma)
        if tag:
            return tag

    # 3) admin da org central (Achadinhos) — pra plano free usar afiliado do admin
    if org_id is None or org_id != settings.admin_org_id:
        tag = await _tag_do_admin_da_org(
            db, org_id=settings.admin_org_id, plataforma=plataforma,
        )
        if tag:
            return tag

    # 4) env var global (settings.{plat}_affiliate_id)
    map_envvar = {
        "ml":         settings.ml_affiliate_id,
        "shopee":     settings.shopee_affiliate_id,
        "amazon":     settings.amazon_affiliate_tag,
        "magalu":     settings.magalu_affiliate_id,
        "aliexpress": settings.aliexpress_affiliate_id,
    }
    fallback_env = (map_envvar.get(plataforma) or "").strip()
    return fallback_env or None

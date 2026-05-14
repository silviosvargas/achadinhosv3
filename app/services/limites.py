"""
Verificação de limites de plano (Fase 5).

Cada Organizacao tem um Plano com limites:
- limite_afiliados:     máximo de usuários (qualquer papel) na org
- limite_grupos:        máximo de grupos ativos
- limite_postagens_mes: máximo de Postagens em um mês corrido

Helpers retornam tupla (pode, mensagem) — `pode=False` significa estourou
o limite. Caller mostra a mensagem ao usuário (HTTP 402 ou 400).

Plano default é "free" (id=1) seedado na migration 0001:
- limite_afiliados=1, limite_grupos=5, limite_postagens_mes=100
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Grupo, Organizacao, Plano, Postagem, Usuario


async def _get_plano(db: AsyncSession, *, org_id: int) -> Plano | None:
    """Carrega Plano associado à org."""
    row = await db.execute(
        select(Plano).join(Organizacao, Organizacao.plano_id == Plano.id)
        .where(Organizacao.id == org_id)
    )
    return row.scalar_one_or_none()


async def pode_criar_usuario(
    db: AsyncSession, *, org_id: int,
) -> tuple[bool, str]:
    """Verifica se a org pode adicionar mais um usuário."""
    plano = await _get_plano(db, org_id=org_id)
    if plano is None:
        return False, "Organização sem plano associado"

    atual = await db.scalar(
        select(func.count()).select_from(Usuario).where(Usuario.org_id == org_id)
    ) or 0

    if atual >= plano.limite_afiliados:
        return False, (
            f"Plano '{plano.nome}' permite até {plano.limite_afiliados} usuário(s). "
            f"Você já tem {atual}. Faça upgrade pra adicionar mais."
        )
    return True, ""


async def pode_criar_grupo(
    db: AsyncSession, *, org_id: int,
) -> tuple[bool, str]:
    """Verifica se a org pode adicionar mais um grupo ativo."""
    plano = await _get_plano(db, org_id=org_id)
    if plano is None:
        return False, "Organização sem plano associado"

    atual = await db.scalar(
        select(func.count()).select_from(Grupo).where(
            Grupo.org_id == org_id, Grupo.ativo.is_(True),
        )
    ) or 0

    if atual >= plano.limite_grupos:
        return False, (
            f"Plano '{plano.nome}' permite até {plano.limite_grupos} grupo(s) ativo(s). "
            f"Você já tem {atual}. Faça upgrade ou desative algum."
        )
    return True, ""


async def pode_postar(
    db: AsyncSession, *, org_id: int,
) -> tuple[bool, str, int, int]:
    """
    Verifica se a org ainda tem postagens no mês.

    Retorna (pode, mensagem, postagens_no_mes, limite).
    Mês = janela rolante de 30 dias (simplificação — depois calendário civil).
    """
    plano = await _get_plano(db, org_id=org_id)
    if plano is None:
        return False, "Organização sem plano associado", 0, 0

    desde = datetime.now(tz=timezone.utc) - timedelta(days=30)
    atual = await db.scalar(
        select(func.count()).select_from(Postagem).where(
            Postagem.org_id == org_id,
            Postagem.postado_em >= desde,
            Postagem.enviado.is_(True),
        )
    ) or 0

    if atual >= plano.limite_postagens_mes:
        return False, (
            f"Plano '{plano.nome}' permite até {plano.limite_postagens_mes} postagens/mês. "
            f"Você já fez {atual} nos últimos 30 dias. Faça upgrade pra continuar."
        ), atual, plano.limite_postagens_mes
    return True, "", atual, plano.limite_postagens_mes

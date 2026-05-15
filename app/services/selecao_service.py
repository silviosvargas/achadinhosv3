"""
Service de seleção de produtos pra postagem em lote.

Responsabilidade: dado uma org, decidir QUE produtos postar em QUAIS grupos,
respeitando:
- Produtos não bloqueados, com preço válido
- Produtos não postados nos últimos N dias no grupo (dedup via Postagem)
- Compatibilidade nicho-do-produto × nichos-do-grupo
- Canais ativos da org (filtra por tipo se solicitado)

Resultado: lista de tuplas (produto, grupo) prontas pra enfileirar.

Não faz a postagem em si — quem faz é o dispatcher (já existente).
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import NamedTuple

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import (
    Canal,
    Grupo,
    GrupoNicho,
    Postagem,
    Produto,
    ProdutoNicho,
    Usuario,
)

log = get_logger(__name__)


# Janela anti-repetição: não postar mesmo produto no mesmo grupo
# em menos de 7 dias.
JANELA_DEDUP_DIAS = 7


class CombinacaoPostagem(NamedTuple):
    """Uma combinação produto × grupo pronta pra ser postada."""
    produto: Produto
    grupo: Grupo
    nichos_do_produto: list[int]


# ============================================================
# Carregar produtos elegíveis
# ============================================================

async def produtos_elegiveis(
    db: AsyncSession,
    *,
    org_id: int,
    limite: int = 100,
    usuario: Usuario | None = None,
) -> list[tuple[Produto, list[int]]]:
    """
    Lista produtos da org que podem ser postados:
    - não bloqueados
    - preço > 0
    - tem pelo menos 1 nicho associado

    Visibilidade (ADR-008):
    - Admin/usuário comum / sem usuário: vê só produtos públicos (dono=NULL).
    - Afiliado: vê públicos da org + os seus privados.

    Fase 11: se o user é de plano free (não pode_criar_produto_proprio),
    também inclui produtos da org admin (`settings.admin_org_id`) — assim
    o user free posta o catálogo Achadinhos com afiliado do admin.

    Retorna tuplas (produto, lista_de_nicho_ids).
    """
    from app.core.config import settings as _settings

    org_ids: list[int] = [org_id]
    if usuario is not None and usuario.organizacao and usuario.organizacao.plano:
        plano = usuario.organizacao.plano
        if not plano.pode_criar_produto_proprio and _settings.admin_org_id != org_id:
            org_ids.append(_settings.admin_org_id)

    base = (
        select(Produto)
        .where(
            Produto.org_id.in_(org_ids),
            Produto.bloqueado.is_(False),
            Produto.preco > 0,
        )
    )

    if usuario is not None and usuario.eh_afiliado:
        base = base.where(
            or_(
                Produto.usuario_dono_id.is_(None),
                Produto.usuario_dono_id == usuario.id,
            )
        )
    else:
        # Admin/usuário comum: só públicos da org
        base = base.where(Produto.usuario_dono_id.is_(None))

    result = await db.execute(
        base.order_by(Produto.atualizado_em.desc())
            .limit(limite * 3)   # folga após filtro de dedup
    )
    produtos = list(result.scalars().all())

    if not produtos:
        return []

    # Carrega nichos de todos de uma vez (1 query, não N+1)
    ids = [p.id for p in produtos]
    rows = (await db.execute(
        select(ProdutoNicho.produto_id, ProdutoNicho.nicho_id)
        .where(ProdutoNicho.produto_id.in_(ids))
    )).all()

    nichos_map: dict[int, list[int]] = {}
    for produto_id, nicho_id in rows:
        nichos_map.setdefault(produto_id, []).append(nicho_id)

    # Filtra: só produtos que têm pelo menos 1 nicho
    out = []
    for p in produtos:
        nichos = nichos_map.get(p.id, [])
        if nichos:
            out.append((p, nichos))
        if len(out) >= limite:
            break

    return out


# ============================================================
# Carregar grupos com nichos
# ============================================================

async def grupos_com_nichos(
    db: AsyncSession, *, org_id: int, canal_tipo: str | None = None,
) -> list[tuple[Grupo, list[int]]]:
    """
    Lista grupos ativos da org com seus nichos vinculados.

    Filtra por canal_tipo se passado (whatsapp_agente | telegram_bot).
    Grupo cujo canal está inativo é ignorado.

    Retorna tuplas (grupo, lista_de_nicho_ids).
    Grupos SEM nichos vinculados aceitam qualquer produto (lista vazia = curinga).
    """
    # Carrega grupos ativos da org com canais ativos
    query = (
        select(Grupo)
        .join(Canal, Canal.id == Grupo.canal_id)
        .where(
            Grupo.org_id == org_id,
            Grupo.ativo.is_(True),
            Canal.ativo.is_(True),
        )
    )
    if canal_tipo:
        query = query.where(Canal.tipo == canal_tipo)

    grupos = list((await db.execute(query)).scalars().all())
    if not grupos:
        return []

    # Carrega nichos vinculados (1 query)
    ids = [g.id for g in grupos]
    rows = (await db.execute(
        select(GrupoNicho.grupo_id, GrupoNicho.nicho_id)
        .where(GrupoNicho.grupo_id.in_(ids))
    )).all()

    nichos_map: dict[int, list[int]] = {}
    for grupo_id, nicho_id in rows:
        nichos_map.setdefault(grupo_id, []).append(nicho_id)

    return [(g, nichos_map.get(g.id, [])) for g in grupos]


# ============================================================
# Dedup — evita repetir mesmo produto no mesmo grupo
# ============================================================

async def chaves_postadas_recentemente(
    db: AsyncSession, *, org_id: int, dias: int = JANELA_DEDUP_DIAS,
) -> set[tuple[int, int]]:
    """
    Conjunto {(produto_id, grupo_id)} já postados nos últimos N dias.
    Usado pra dedup antes de criar tarefa nova.

    Note: `Postagem` é append-only (sem TimestampMixin), então usa
    `postado_em` em vez de `criado_em`.
    """
    desde = datetime.now(tz=timezone.utc) - timedelta(days=dias)
    rows = (await db.execute(
        select(Postagem.produto_id, Postagem.grupo_id)
        .where(
            Postagem.org_id == org_id,
            Postagem.postado_em >= desde,
            Postagem.produto_id.is_not(None),
        )
    )).all()
    return {(pid, gid) for pid, gid in rows}


# ============================================================
# Combinação produto × grupo
# ============================================================

def _grupo_aceita(produto_nichos: list[int], grupo_nichos: list[int]) -> bool:
    """Grupo sem nichos = aceita qualquer (curinga). Senão, precisa interseção."""
    if not grupo_nichos:
        return True
    return bool(set(produto_nichos) & set(grupo_nichos))


async def montar_combinacoes(
    db: AsyncSession,
    *,
    org_id: int,
    max_produtos: int = 10,
    canal_tipo: str | None = None,
    usuario: Usuario | None = None,
) -> tuple[list[CombinacaoPostagem], dict[str, int]]:
    """
    Função principal — orquestra:
    1. Carrega produtos elegíveis
    2. Carrega grupos compatíveis
    3. Para cada produto, escolhe UM grupo compatível ainda não postado recentemente
    4. Retorna lista de (produto, grupo) prontas pra dispatcher

    Retorna também stats: {avaliados, sem_grupo_compativel, ja_postado_recentemente}
    """
    stats = {
        "avaliados": 0,
        "sem_grupo_compativel": 0,
        "ja_postado_recentemente": 0,
    }

    produtos = await produtos_elegiveis(
        db, org_id=org_id, limite=max_produtos * 3, usuario=usuario,
    )
    if not produtos:
        return [], stats

    grupos = await grupos_com_nichos(db, org_id=org_id, canal_tipo=canal_tipo)
    if not grupos:
        log.warning("selecao.sem_grupos", org_id=org_id)
        return [], stats

    ja_postados = await chaves_postadas_recentemente(db, org_id=org_id)

    combinacoes: list[CombinacaoPostagem] = []
    grupos_aleatorios = list(grupos)

    for produto, nichos_prod in produtos:
        stats["avaliados"] += 1

        # Embaralha grupos pra distribuir
        random.shuffle(grupos_aleatorios)

        compativel: Grupo | None = None
        ja_postou_em_todos = True

        for grupo, nichos_grupo in grupos_aleatorios:
            if not _grupo_aceita(nichos_prod, nichos_grupo):
                continue
            if (produto.id, grupo.id) in ja_postados:
                continue
            compativel = grupo
            ja_postou_em_todos = False
            break

        if compativel is None:
            # Verifica se tinha algum grupo compatível mas todos já postados
            tem_compat = any(_grupo_aceita(nichos_prod, ng) for _, ng in grupos)
            if tem_compat:
                stats["ja_postado_recentemente"] += 1
            else:
                stats["sem_grupo_compativel"] += 1
            continue

        combinacoes.append(CombinacaoPostagem(
            produto=produto,
            grupo=compativel,
            nichos_do_produto=nichos_prod,
        ))

        if len(combinacoes) >= max_produtos:
            break

    log.info(
        "selecao.concluida",
        org_id=org_id,
        combinacoes=len(combinacoes),
        **stats,
    )
    return combinacoes, stats

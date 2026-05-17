"""
Curadoria via nota no produto (Fase 18 — reformulada).

A nota é calculada NO INGEST (`busca_service._upsert_produto`) usando
`scoring.calcular_nota` e gravada na coluna `produtos.nota`. Esse service
**não calcula nada** — só filtra/ordena.

Não tem snapshot diário, não tem Celery beat. TOP é live, sempre os
produtos com nota mais alta no DB.

Cascata de fallback (Fase 11 catálogo compartilhado):
1. Org do user
2. Org admin (`settings.admin_org_id`) — pra plano free
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.models import Postagem, Produto, ProdutoNicho
from app.services.scoring import calcular_nota

log = get_logger(__name__)


# Janela: produto postado nessa org nos últimos N dias NÃO entra no TOP
JANELA_DEDUP_DIAS = 7

# Nota mínima pra aparecer no TOP. Configurável por chamada.
NOTA_MINIMA_DEFAULT = 30.0


# --------------------------------------------------------------------
# Leitura — usada por endpoints API/web
# --------------------------------------------------------------------

def _base_query_top(
    *,
    org_id: int,
    nota_minima: float,
    janela_dedup_dias: int,
    incluir_postados_recentemente: bool,
):
    """Constrói query base do TOP (sem limit/offset/order)."""
    desde_dedup = datetime.now(tz=timezone.utc) - timedelta(days=janela_dedup_dias)

    ja_postado = exists().where(and_(
        Postagem.produto_id == Produto.id,
        Postagem.org_id == org_id,
        Postagem.postado_em >= desde_dedup,
    ))
    tem_nicho = exists().where(ProdutoNicho.produto_id == Produto.id)

    q = select(Produto).where(
        Produto.org_id == org_id,
        Produto.usuario_dono_id.is_(None),
        Produto.bloqueado.is_(False),
        Produto.preco > 0,
        Produto.foto_url.is_not(None),
        Produto.foto_url != "",
        Produto.nota >= nota_minima,
        tem_nicho,
    )
    if not incluir_postados_recentemente:
        q = q.where(~ja_postado)
    return q


async def listar_top(
    db: AsyncSession,
    *,
    org_id: int,
    limite: int = 50,
    offset: int = 0,
    nota_minima: float = NOTA_MINIMA_DEFAULT,
    janela_dedup_dias: int = JANELA_DEDUP_DIAS,
    incluir_postados_recentemente: bool = False,
) -> list[Produto]:
    """Retorna produtos ordenados por nota DESC. Suporta paginação via
    `limite` (tamanho da página) e `offset`."""
    q = _base_query_top(
        org_id=org_id, nota_minima=nota_minima,
        janela_dedup_dias=janela_dedup_dias,
        incluir_postados_recentemente=incluir_postados_recentemente,
    )
    q = q.order_by(
        Produto.nota.desc(), Produto.atualizado_em.desc(),
    ).limit(limite).offset(offset)
    rows = (await db.execute(q)).scalars().all()
    return list(rows)


async def contar_top(
    db: AsyncSession,
    *,
    org_id: int,
    nota_minima: float = NOTA_MINIMA_DEFAULT,
    janela_dedup_dias: int = JANELA_DEDUP_DIAS,
    incluir_postados_recentemente: bool = False,
) -> int:
    """Conta total de produtos elegíveis (sem limit/offset) pra paginação."""
    from sqlalchemy import func as _f
    q = _base_query_top(
        org_id=org_id, nota_minima=nota_minima,
        janela_dedup_dias=janela_dedup_dias,
        incluir_postados_recentemente=incluir_postados_recentemente,
    )
    return await db.scalar(
        select(_f.count()).select_from(q.subquery())
    ) or 0


async def listar_top_com_fallback(
    db: AsyncSession,
    *,
    org_id: int,
    limite: int = 50,
    offset: int = 0,
    nota_minima: float = NOTA_MINIMA_DEFAULT,
) -> tuple[list[Produto], str, int]:
    """
    Mesma `listar_top`, mas com cascata de fallback Fase 11 + total.

    Retorna `(produtos, fonte, total)` onde fonte ∈ {"propria", "admin_org", "vazio"}.
    `total` é a contagem da fonte usada (pra calcular paginação).
    """
    total = await contar_top(db, org_id=org_id, nota_minima=nota_minima)
    if total > 0:
        produtos = await listar_top(
            db, org_id=org_id, limite=limite, offset=offset,
            nota_minima=nota_minima,
        )
        return produtos, "propria", total

    if org_id != settings.admin_org_id:
        total = await contar_top(
            db, org_id=settings.admin_org_id, nota_minima=nota_minima,
        )
        if total > 0:
            produtos = await listar_top(
                db, org_id=settings.admin_org_id,
                limite=limite, offset=offset, nota_minima=nota_minima,
            )
            return produtos, "admin_org", total

    return [], "vazio", 0


# --------------------------------------------------------------------
# Tools admin: recalcular notas + revalidar comissões
# --------------------------------------------------------------------

async def recalcular_notas_da_org(
    db: AsyncSession, *, org_id: int,
) -> dict[str, int]:
    """
    Re-aplica `calcular_nota` em TODOS produtos da org (públicos e privados).

    Útil quando:
    - A fórmula de nota muda (PESO_COMISSAO ajustado, novo bônus)
    - Backfill após deploy da Fase 18 (produtos antigos têm nota=0)

    Não toca em outros campos — só `nota` e `comissao_validada`.
    """
    produtos = list((await db.execute(
        select(Produto).where(Produto.org_id == org_id)
    )).scalars().all())

    atualizados = 0
    for p in produtos:
        info = calcular_nota({
            "plataforma":     p.plataforma,
            "preco":          p.preco,
            "preco_orig":     p.preco_orig,
            "desconto":       p.desconto,
            "comissao":       p.comissao,
            "total_vendidos": p.total_vendidos,
            "is_bestseller":  p.is_bestseller,
            "is_em_alta":     p.is_em_alta,
        })
        if (p.nota != info["nota"]
                or p.comissao_validada != info["comissao_validada"]):
            p.nota              = info["nota"]
            p.comissao_validada = info["comissao_validada"]
            atualizados += 1

    await db.flush()
    log.info("curadoria.recalcular_notas",
             org_id=org_id, total=len(produtos), atualizados=atualizados)
    return {
        "total":       len(produtos),
        "atualizados": atualizados,
    }


async def revalidar_comissoes_da_org(
    db: AsyncSession, *, org_id: int,
) -> dict[str, int]:
    """LEGADO: passa todas comissões pela validação de range + recalcula nota.

    NÃO consulta o agente — só re-executa `calcular_nota` com valores
    já no DB. Para captura REAL via barra ML, use `disparar_revalidacao_comissoes_via_agente`.
    """
    return await recalcular_notas_da_org(db, org_id=org_id)


async def disparar_revalidacao_comissoes_via_agente(
    db: AsyncSession,
    *,
    org_id: int,
    limite: int = 50,
) -> dict:
    """Fase 18.3 (v3.4.3) — dispara tarefa pro agente abrir o LINK DE AFILIADO
    (meli.la) de cada produto DO TOP, navegar via "Ir para produto" na
    /social/, capturar a comissão real da barra preta no destino.

    Fluxo no agente (definido pelo user):
    1. Abre `meli.la/XXX` → ML redireciona pra `/social/<usuario>`
    2. Acha botão "Ir para produto" → navega pra página completa
    3. Captura `GANHOS [EXTRAS] X%` da barra preta

    Estratégia servidor:
    1. Pega os produtos do TOP atual (ordenados por nota DESC)
    2. Filtra ML COM `meli.la/` em `url_afiliado` + sem comissao_fonte=ml_barra_afiliados
    3. Cria 1 tarefa `REVALIDAR_COMISSAO_ML` com `items=[{produto_id, url_afiliado}]`
    4. Hook `marcar_concluida` aplica via `aplicar_mapping_comissoes_por_id`

    ⚠ Lições:
    - SEMPRE envia `url_afiliado` (meli.la), NUNCA `url_canonica`. O user
      diz: "buscar pelo link de afiliado, clicar Ir para produto, ler
      comissão". Documentado em CLAUDE.md.
    - SE produto não tem meli.la, é skipado (linkbuilder falhou antes —
      revalidar isso não dá; precisaria regenerar meli.la primeiro).

    Custo: ~3s por produto. TOP de 50 com meli.la = ~2.5min.

    Returns:
        {"ok": bool, "tarefa_id": N, "items_enfileirados": M, "mensagem": "..."}
    """
    from app.models import Agente, StatusTarefa, Tarefa, TipoTarefa
    from app.services import dispatcher
    from app.services.agente_registry import registry

    # 1. Pega produtos do TOP atual (sem filtro de nota — pega todos
    #    pra revalidar o máximo possível)
    produtos_top = await listar_top(
        db, org_id=org_id, limite=limite, nota_minima=0,
        incluir_postados_recentemente=True,
    )
    # Fallback admin_org se org do user não tem TOP (catálogo compartilhado)
    if not produtos_top:
        from app.core.config import settings as _s
        if org_id != _s.admin_org_id:
            produtos_top = await listar_top(
                db, org_id=_s.admin_org_id, limite=limite, nota_minima=0,
                incluir_postados_recentemente=True,
            )

    # 2. v3.7.0: filtra ML com url_canonica (não exige mais meli.la,
    # agente abre direto na canônica)
    items: list[dict] = []
    sem_url = 0
    for p in produtos_top:
        if p.plataforma != "ml":
            continue
        if not p.url_canonica:
            sem_url += 1
            continue
        if p.comissao_fonte == "ml_barra_afiliados":
            continue  # já revalidado
        items.append({"produto_id": p.id, "url_canonica": p.url_canonica})

    if not items:
        return {
            "ok":                True,
            "tarefa_id":         None,
            "items_enfileirados": 0,
            "mensagem":          (
                f"Nenhum produto do TOP pra revalidar "
                f"(outros já tem ✅ ML barra ou sem URL canônica: {sem_url})"
            ),
        }

    # 4. Pega 1º agente online da org
    agentes = list((await db.execute(
        select(Agente).where(
            Agente.org_id == org_id, Agente.ativo.is_(True),
        )
    )).scalars().all())
    agente = next((a for a in agentes if registry.esta_online(a.id)), None)
    if agente is None:
        return {
            "ok":    False,
            "erro":  "Nenhum agente online — abra o AchadinhosAgent no PC primeiro",
        }

    tarefa = Tarefa(
        org_id=org_id,
        tipo=TipoTarefa.REVALIDAR_COMISSAO_ML,
        status=StatusTarefa.PENDENTE,
        agente_id=agente.id,
        payload={"items": items},
    )
    db.add(tarefa)
    await db.commit()
    await db.refresh(tarefa)

    await dispatcher._tentar_entrega(db, tarefa)

    log.info("curadoria.revalidar_via_agente.disparado",
             org_id=org_id, tarefa_id=tarefa.id, items=len(items))
    return {
        "ok":                True,
        "tarefa_id":         tarefa.id,
        "items_enfileirados": len(items),
        "mensagem":          (
            f"Tarefa #{tarefa.id} enfileirada com {len(items)} produtos do TOP. "
            f"Agente leva ~{len(items) * 3}s (abre meli.la → social → produto → captura). "
            f"Recarregue em ~{max(1, len(items) // 20)}min."
        ),
    }

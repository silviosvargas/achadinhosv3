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

async def listar_top(
    db: AsyncSession,
    *,
    org_id: int,
    limite: int = 50,
    nota_minima: float = NOTA_MINIMA_DEFAULT,
    janela_dedup_dias: int = JANELA_DEDUP_DIAS,
    incluir_postados_recentemente: bool = False,
) -> list[Produto]:
    """
    Retorna produtos ordenados por nota DESC.

    Filtros:
    - org_id (catálogo da org)
    - público (usuario_dono_id IS NULL) — privados de afiliado ficam fora
    - não bloqueado, preco > 0, foto_url NOT NULL
    - tem pelo menos 1 nicho associado (precisa pra postagem)
    - nota >= nota_minima
    - NÃO postado nos últimos N dias (default 7) — opcional

    Args:
        org_id: org do user
        limite: máx produtos (default 50)
        nota_minima: mínimo de nota (default 30 — descarta produtos sem comissão
            real + sem desconto + sem sinal de venda)
        janela_dedup_dias: ignora produtos postados nessa janela
        incluir_postados_recentemente: se True, ignora dedup (admin manual)
    """
    desde_dedup = datetime.now(tz=timezone.utc) - timedelta(days=janela_dedup_dias)

    ja_postado = exists().where(and_(
        Postagem.produto_id == Produto.id,
        Postagem.org_id == org_id,
        Postagem.postado_em >= desde_dedup,
    ))
    tem_nicho = exists().where(ProdutoNicho.produto_id == Produto.id)

    base = (
        select(Produto)
        .where(
            Produto.org_id == org_id,
            Produto.usuario_dono_id.is_(None),
            Produto.bloqueado.is_(False),
            Produto.preco > 0,
            Produto.foto_url.is_not(None),
            Produto.foto_url != "",
            Produto.nota >= nota_minima,
            tem_nicho,
        )
        .order_by(Produto.nota.desc(), Produto.atualizado_em.desc())
        .limit(limite)
    )
    if not incluir_postados_recentemente:
        base = base.where(~ja_postado)

    rows = (await db.execute(base)).scalars().all()
    return list(rows)


async def listar_top_com_fallback(
    db: AsyncSession,
    *,
    org_id: int,
    limite: int = 50,
    nota_minima: float = NOTA_MINIMA_DEFAULT,
) -> tuple[list[Produto], str]:
    """
    Mesma `listar_top`, mas com cascata de fallback Fase 11.

    Retorna `(produtos, fonte)` onde fonte ∈ {"propria", "admin_org"}.
    """
    produtos = await listar_top(
        db, org_id=org_id, limite=limite, nota_minima=nota_minima,
    )
    if produtos:
        return produtos, "propria"

    if org_id != settings.admin_org_id:
        produtos = await listar_top(
            db, org_id=settings.admin_org_id,
            limite=limite, nota_minima=nota_minima,
        )
        if produtos:
            return produtos, "admin_org"

    return [], "vazio"


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
    limite: int = 100,
) -> dict:
    """Fase 18.3 (v3.4.1) — dispara tarefa pro agente abrir cada produto ML
    da org e capturar comissão REAL da barra preta de afiliados.

    Estratégia:
    1. Pega N produtos ML da org cuja `comissao_fonte` NÃO É 'ml_barra_afiliados'
       (ou seja, produtos que ainda não foram revalidados)
    2. Cria 1 tarefa `REVALIDAR_COMISSAO_ML` com `urls=[...]`
    3. Entrega via dispatcher (tenta entregar via WS se agente online)
    4. Hook em `dispatcher.marcar_concluida` aplica resultado via
       `afiliado_ml_writer.aplicar_mapping_comissoes_barra`

    Custo: agente leva ~2s por URL. Pra 100 URLs = ~3min.

    Returns:
        {"tarefa_id": N, "urls_enfileiradas": M, "produtos_pendentes": K}
        ou {"ok": False, "erro": "..."}
    """
    from app.models import Agente, Produto, StatusTarefa, Tarefa, TipoTarefa
    from app.services import dispatcher
    from app.services.agente_registry import registry

    # Produtos ML da org sem comissão real ainda
    rows = (await db.execute(
        select(Produto.url_canonica).where(
            Produto.org_id == org_id,
            Produto.plataforma == "ml",
            Produto.url_canonica.is_not(None),
            Produto.bloqueado.is_(False),
            Produto.comissao_fonte != "ml_barra_afiliados",
        ).limit(limite)
    )).all()

    urls = [u for (u,) in rows if u]
    if not urls:
        return {
            "ok":               True,
            "tarefa_id":        None,
            "urls_enfileiradas": 0,
            "mensagem":         "Todos os produtos já tem comissão da barra ML — nada a revalidar",
        }

    # Pega 1º agente online da org
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
        payload={"urls": urls},
    )
    db.add(tarefa)
    await db.commit()
    await db.refresh(tarefa)

    await dispatcher._tentar_entrega(db, tarefa)

    log.info("curadoria.revalidar_via_agente.disparado",
             org_id=org_id, tarefa_id=tarefa.id, urls=len(urls))
    return {
        "ok":               True,
        "tarefa_id":        tarefa.id,
        "urls_enfileiradas": len(urls),
        "mensagem":         (
            f"Tarefa #{tarefa.id} enfileirada com {len(urls)} produtos. "
            f"Agente leva ~{len(urls) * 2}s pra processar. "
            "Recarregue a página em ~{}min".format(max(1, len(urls) // 30))
        ),
    }

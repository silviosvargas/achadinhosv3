"""
Curadoria automática (Fase 18 — reformulada).

GET  /curadoria/top                       lista produtos ordenados por nota
POST /curadoria/recalcular-notas          admin: re-aplica fórmula em todos
POST /curadoria/revalidar-comissoes       admin: re-valida ranges + recalcula

A nota é populada NO INGEST (busca_service._upsert_produto). Esses endpoints
apenas lêem `produtos.nota` — sem cálculo pesado em runtime.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import usuario_admin, usuario_atual
from app.core.comissoes import RANGES_VALIDOS
from app.db import get_db_async
from app.models import Usuario
from app.services import curadoria_service

router = APIRouter(prefix="/curadoria", tags=["curadoria"])


@router.get("/top")
async def top_por_nota(
    limite: int = Query(default=50, ge=1, le=100),
    nota_minima: float = Query(
        default=30.0, ge=0, le=100,
        description="Nota mínima (0..100). Default 30 corta produtos sem "
                    "desconto/sem comissão real/sem sinal de venda.",
    ),
    user: Usuario = Depends(usuario_atual),
    db:   AsyncSession = Depends(get_db_async),
) -> dict:
    """Retorna o TOP atual ordenado por nota DESC.

    Cascata de fallback (Fase 11):
    - Sem produtos na org do user → tenta admin_org_id.
    """
    produtos, fonte, _total = await curadoria_service.listar_top_com_fallback(
        db, org_id=user.org_id, limite=limite, nota_minima=nota_minima,
    )
    items = [
        {
            "id":              p.id,
            "plataforma":      p.plataforma,
            "nome":            p.nome,
            "preco":           p.preco,
            "preco_orig":      p.preco_orig,
            "desconto":        p.desconto,
            "comissao":        p.comissao,
            "comissao_fonte":  p.comissao_fonte,
            "comissao_validada": p.comissao_validada,
            "total_vendidos":  p.total_vendidos,
            "is_bestseller":   p.is_bestseller,
            "is_em_alta":      p.is_em_alta,
            "nota":            p.nota,
            "url_canonica":    p.url_canonica,
            "url_afiliado":    p.url_afiliado,
            "foto_url":        p.foto_url,
            "categoria":       p.categoria,
            "preco_atualizado_em":    p.preco_atualizado_em.isoformat() if p.preco_atualizado_em else None,
            "comissao_atualizada_em": p.comissao_atualizada_em.isoformat() if p.comissao_atualizada_em else None,
        }
        for p in produtos
    ]
    return {
        "fonte":      fonte,
        "total":      len(items),
        "items":      items,
        "ranges_validacao_comissao": {
            plat: {"min_pct": rng[0], "max_pct": rng[1]}
            for plat, rng in RANGES_VALIDOS.items()
        },
    }


@router.post("/recalcular-notas")
async def recalcular_notas(
    admin: Usuario = Depends(usuario_admin),
    db:    AsyncSession = Depends(get_db_async),
) -> dict:
    """Admin: re-aplica `calcular_nota` em todos produtos da org dele.

    Roda síncrono no request — ~1s pra org com 10k produtos.
    """
    resultado = await curadoria_service.recalcular_notas_da_org(
        db, org_id=admin.org_id,
    )
    await db.commit()
    return {"org_id": admin.org_id, **resultado}


@router.post("/revalidar-comissoes")
async def revalidar_comissoes(
    admin: Usuario = Depends(usuario_admin),
    db:    AsyncSession = Depends(get_db_async),
) -> dict:
    """Admin: dispara tarefa pro agente capturar comissão REAL via barra
    de afiliados ML (Fase 18.3, v3.4.1).

    Diferente do legado: agora abre cada URL no agente em vez de só
    recalcular nota. Resposta imediata = `tarefa_id` + `urls_enfileiradas`.
    Resultado real chega via callback WS (~2s por URL).
    """
    resultado = await curadoria_service.disparar_revalidacao_comissoes_via_agente(
        db, org_id=admin.org_id, limite=100,
    )
    return {"org_id": admin.org_id, **resultado}


@router.post("/revalidar-comissoes-local")
async def revalidar_comissoes_local(
    admin: Usuario = Depends(usuario_admin),
    db:    AsyncSession = Depends(get_db_async),
) -> dict:
    """LEGADO: só re-executa `validar_comissao` no DB sem consultar agente.

    Útil pra rodar validação de range sem disparar o agente (mais rápido
    quando você só ajustou a fórmula/range).
    """
    resultado = await curadoria_service.revalidar_comissoes_da_org(
        db, org_id=admin.org_id,
    )
    await db.commit()
    return {"org_id": admin.org_id, **resultado}

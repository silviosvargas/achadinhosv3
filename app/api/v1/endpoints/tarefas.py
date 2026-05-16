"""
Tarefas — listagem + enfileiramento manual.

GET  /tarefas?status=...&pagina=1   lista filtrada
GET  /tarefas/{id}                  detalhe
POST /tarefas/postar                enfileira postagem manual
POST /tarefas/{id}/cancelar         cancela tarefa pendente
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import usuario_atual
from app.db import get_db_async
from app.models import StatusTarefa, Tarefa, Usuario
from app.schemas.comum import Mensagem, Pagina
from app.schemas.tarefa import (
    EnfileirarPostagemRequest,
    FiltroTarefas,
    TarefaPublica,
)
from app.services import dispatcher

router = APIRouter(prefix="/tarefas", tags=["tarefas"])


@router.get("/em-progresso")
async def em_progresso(
    user: Usuario = Depends(usuario_atual),
    db: AsyncSession = Depends(get_db_async),
) -> dict:
    """Fase 20 — tarefas PROCESSANDO da org com seu progresso atual.

    Usado pela UI do dashboard pra mostrar barra de progresso em tempo
    real (polling 3s). Retorna lista enxuta com só o necessário pro card.
    """
    rows = list((await db.execute(
        select(Tarefa).where(
            Tarefa.org_id == user.org_id,
            Tarefa.status == StatusTarefa.PROCESSANDO,
        ).order_by(Tarefa.iniciado_em.desc().nullslast()).limit(10)
    )).scalars().all())

    items = [
        {
            "id":                  t.id,
            "tipo":                t.tipo,
            "progresso_pct":       round(float(t.progresso_pct or 0), 1),
            "progresso_mensagem":  t.progresso_mensagem or "",
            "iniciado_em":         t.iniciado_em.isoformat() if t.iniciado_em else None,
            "progresso_atualizado_em": (
                t.progresso_atualizado_em.isoformat()
                if t.progresso_atualizado_em else None
            ),
        }
        for t in rows
    ]
    return {"total": len(items), "items": items}


@router.get("", response_model=Pagina[TarefaPublica])
async def listar(
    filtro: FiltroTarefas = Depends(),
    user: Usuario = Depends(usuario_atual),
    db: AsyncSession = Depends(get_db_async),
) -> Pagina[TarefaPublica]:
    """Lista tarefas da org com filtros e paginação."""
    base = select(Tarefa).where(Tarefa.org_id == user.org_id)

    if filtro.status:
        base = base.where(Tarefa.status == filtro.status)
    if filtro.tipo:
        base = base.where(Tarefa.tipo == filtro.tipo)
    if filtro.agente_id is not None:
        base = base.where(Tarefa.agente_id == filtro.agente_id)

    # Total
    total = await db.scalar(
        select(func.count()).select_from(base.subquery())
    ) or 0

    # Página
    offset = (filtro.pagina - 1) * filtro.por_pagina
    result = await db.execute(
        base.order_by(Tarefa.criado_em.desc())
            .limit(filtro.por_pagina)
            .offset(offset)
    )
    items = [TarefaPublica.model_validate(t) for t in result.scalars().all()]

    return Pagina[TarefaPublica](
        items=items,
        total=total,
        pagina=filtro.pagina,
        por_pagina=filtro.por_pagina,
    )


@router.get("/{tarefa_id}", response_model=TarefaPublica)
async def detalhe(
    tarefa_id: int,
    user: Usuario = Depends(usuario_atual),
    db: AsyncSession = Depends(get_db_async),
) -> TarefaPublica:
    tarefa = await db.get(Tarefa, tarefa_id)
    if tarefa is None or tarefa.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada")
    return TarefaPublica.model_validate(tarefa)


@router.post("/postar", response_model=TarefaPublica, status_code=status.HTTP_201_CREATED)
async def postar(
    body: EnfileirarPostagemRequest,
    user: Usuario = Depends(usuario_atual),
    db: AsyncSession = Depends(get_db_async),
) -> TarefaPublica:
    """Enfileira uma postagem manual."""
    try:
        tarefa = await dispatcher.enfileirar_postagem(
            db,
            org_id=user.org_id,
            grupo_id=body.grupo_id,
            texto=body.texto,
            imagem_url=body.imagem_url,
            produto_id=body.produto_id,
            criado_por_usuario_id=user.id,
        )
    except dispatcher.DispatcherError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None

    return TarefaPublica.model_validate(tarefa)


@router.post("/{tarefa_id}/cancelar")
async def cancelar(
    tarefa_id: int,
    user: Usuario = Depends(usuario_atual),
    db: AsyncSession = Depends(get_db_async),
) -> dict:
    """Fase 20.1 — cancela tarefa em andamento.

    Envia comando WS `cancelar_tarefa` pro agente (cancelamento cooperativo
    — agente para na próxima checagem entre etapas, até ~1min) e marca
    CANCELADA no DB imediatamente.
    """
    tarefa = await db.get(Tarefa, tarefa_id)
    if tarefa is None or tarefa.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada")

    resultado = await dispatcher.cancelar(db, tarefa_id=tarefa_id)
    if not resultado.get("ok"):
        raise HTTPException(status_code=400, detail=resultado.get("mensagem"))
    return resultado

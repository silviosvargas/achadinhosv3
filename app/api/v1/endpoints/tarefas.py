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


@router.post("/{tarefa_id}/cancelar", response_model=TarefaPublica)
async def cancelar(
    tarefa_id: int,
    user: Usuario = Depends(usuario_atual),
    db: AsyncSession = Depends(get_db_async),
) -> TarefaPublica:
    """Cancela uma tarefa que ainda não foi processada."""
    tarefa = await db.get(Tarefa, tarefa_id)
    if tarefa is None or tarefa.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada")

    if tarefa.status not in (StatusTarefa.PENDENTE, StatusTarefa.PROCESSANDO):
        raise HTTPException(
            status_code=400,
            detail=f"Tarefa em status '{tarefa.status}' não pode ser cancelada",
        )

    tarefa.status = StatusTarefa.CANCELADA
    await db.commit()
    await db.refresh(tarefa)
    return TarefaPublica.model_validate(tarefa)

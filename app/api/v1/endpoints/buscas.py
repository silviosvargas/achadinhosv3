"""
CRUD de buscas Mercado Livre + disparo manual.

GET    /buscas              lista
GET    /buscas/{id}         detalhe
POST   /buscas              cria
PATCH  /buscas/{id}         atualiza
DELETE /buscas/{id}         remove
POST   /buscas/{id}/rodar   dispara execução agora (cria Tarefa)
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import usuario_admin, usuario_atual
from app.db import get_db_async
from app.models import Agente, BuscaML, Tarefa, Usuario
from app.schemas.busca import (
    AtualizarBuscaRequest,
    BuscaPublica,
    CriarBuscaRequest,
)
from app.schemas.comum import Mensagem
from app.services import busca_service

router = APIRouter(prefix="/buscas", tags=["buscas"])


@router.get("", response_model=list[BuscaPublica])
async def listar(
    user: Usuario = Depends(usuario_atual),
    db: AsyncSession = Depends(get_db_async),
) -> list[BuscaPublica]:
    result = await db.execute(
        select(BuscaML)
        .where(BuscaML.org_id == user.org_id)
        .order_by(BuscaML.criado_em.desc())
    )
    return [BuscaPublica.model_validate(b) for b in result.scalars().all()]


@router.get("/{busca_id}", response_model=BuscaPublica)
async def detalhe(
    busca_id: int,
    user: Usuario = Depends(usuario_atual),
    db: AsyncSession = Depends(get_db_async),
) -> BuscaPublica:
    b = await _get_da_org(db, org_id=user.org_id, busca_id=busca_id)
    return BuscaPublica.model_validate(b)


@router.post("", response_model=BuscaPublica, status_code=status.HTTP_201_CREATED)
async def criar(
    body: CriarBuscaRequest,
    user: Usuario = Depends(usuario_atual),
    db: AsyncSession = Depends(get_db_async),
) -> BuscaPublica:
    # Fase 9.9: plano free não cria buscas — usa catálogo do admin.
    if not getattr(user.organizacao.plano, "pode_criar_buscas", False):
        raise HTTPException(
            status_code=403,
            detail="Seu plano não permite criar buscas. Use os produtos "
                   "já cadastrados pelo administrador. Faça upgrade pra criar suas próprias.",
        )
    # Valida agente_id se passado
    if body.agente_id is not None:
        agente = await db.get(Agente, body.agente_id)
        if agente is None or agente.org_id != user.org_id:
            raise HTTPException(status_code=400, detail="Agente inválido")

    # Próxima exec se agendada: imediato
    agora = datetime.now(tz=timezone.utc)
    proxima = agora if body.intervalo_minutos else None

    nova = BuscaML(
        org_id=user.org_id,
        criado_por_usuario_id=user.id,
        agente_id=body.agente_id,
        nome=body.nome.strip(),
        entrada=body.entrada.strip(),
        max_paginas=body.max_paginas,
        max_produtos=body.max_produtos,
        intervalo_minutos=body.intervalo_minutos,
        ativo=body.ativo,
        proxima_exec_em=proxima,
    )
    db.add(nova)
    await db.commit()
    await db.refresh(nova)
    return BuscaPublica.model_validate(nova)


@router.patch("/{busca_id}", response_model=BuscaPublica)
async def atualizar(
    busca_id: int,
    body: AtualizarBuscaRequest,
    admin: Usuario = Depends(usuario_admin),
    db: AsyncSession = Depends(get_db_async),
) -> BuscaPublica:
    b = await _get_da_org(db, org_id=admin.org_id, busca_id=busca_id)

    if body.nome is not None:    b.nome = body.nome.strip()
    if body.entrada is not None: b.entrada = body.entrada.strip()
    if body.agente_id is not None:
        if body.agente_id == 0:
            b.agente_id = None
        else:
            ag = await db.get(Agente, body.agente_id)
            if ag is None or ag.org_id != admin.org_id:
                raise HTTPException(status_code=400, detail="Agente inválido")
            b.agente_id = body.agente_id
    if body.max_paginas is not None:  b.max_paginas = body.max_paginas
    if body.max_produtos is not None: b.max_produtos = body.max_produtos
    if body.intervalo_minutos is not None:
        b.intervalo_minutos = body.intervalo_minutos
        # Re-arma proxima_exec_em: imediato se acabou de ligar agendamento
        b.proxima_exec_em = datetime.now(tz=timezone.utc) if body.intervalo_minutos else None
    if body.ativo is not None: b.ativo = body.ativo

    await db.commit()
    await db.refresh(b)
    return BuscaPublica.model_validate(b)


@router.delete("/{busca_id}", response_model=Mensagem)
async def deletar(
    busca_id: int,
    admin: Usuario = Depends(usuario_admin),
    db: AsyncSession = Depends(get_db_async),
) -> Mensagem:
    b = await _get_da_org(db, org_id=admin.org_id, busca_id=busca_id)
    await db.delete(b)
    await db.commit()
    return Mensagem(mensagem="Busca removida")


@router.post("/{busca_id}/rodar", response_model=dict)
async def rodar(
    busca_id: int,
    user: Usuario = Depends(usuario_atual),
    db: AsyncSession = Depends(get_db_async),
) -> dict:
    """
    Dispara execução manual: cria Tarefa(BUSCAR_MERCADO_LIVRE) e tenta
    entregar via WS. Resposta inclui tarefa_id pra rastrear.
    """
    try:
        tarefa = await busca_service.enfileirar_execucao(
            db,
            busca_id=busca_id,
            org_id=user.org_id,
            criado_por_usuario_id=user.id,
        )
    except busca_service.BuscaServiceError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None

    return {
        "ok":        True,
        "tarefa_id": tarefa.id,
        "status":    tarefa.status,
    }


# ============================================================
# Helpers
# ============================================================

async def _get_da_org(
    db: AsyncSession, *, org_id: int, busca_id: int,
) -> BuscaML:
    b = await db.get(BuscaML, busca_id)
    if b is None or b.org_id != org_id:
        raise HTTPException(status_code=404, detail="Busca não encontrada")
    return b

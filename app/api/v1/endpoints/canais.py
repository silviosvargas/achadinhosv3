"""CRUD de canais de postagem (WhatsApp via agente, Telegram via bot)."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import usuario_admin, usuario_atual
from app.db import get_db_async
from app.models import Canal, Usuario
from app.schemas.canal_grupo import (
    AtualizarCanalRequest,
    CanalPublico,
    CriarCanalRequest,
)
from app.schemas.comum import Mensagem

router = APIRouter(prefix="/canais", tags=["canais"])


@router.post("", response_model=CanalPublico, status_code=status.HTTP_201_CREATED)
async def criar(
    body: CriarCanalRequest,
    user: Usuario = Depends(usuario_admin),
    db: AsyncSession = Depends(get_db_async),
) -> CanalPublico:
    canal = Canal(
        org_id=user.org_id,
        usuario_id=body.usuario_id,
        tipo=body.tipo.value,
        nome=body.nome,
        config=body.config,
        ativo=True,
    )
    db.add(canal)
    await db.commit()
    await db.refresh(canal)
    return CanalPublico.model_validate(canal)


@router.get("", response_model=list[CanalPublico])
async def listar(
    user: Usuario = Depends(usuario_atual),
    db: AsyncSession = Depends(get_db_async),
) -> list[CanalPublico]:
    result = await db.execute(
        select(Canal).where(Canal.org_id == user.org_id).order_by(Canal.criado_em.desc())
    )
    return [CanalPublico.model_validate(c) for c in result.scalars().all()]


@router.get("/{canal_id}", response_model=CanalPublico)
async def detalhe(
    canal_id: int,
    user: Usuario = Depends(usuario_atual),
    db: AsyncSession = Depends(get_db_async),
) -> CanalPublico:
    canal = await _get_canal_da_org(db, org_id=user.org_id, canal_id=canal_id)
    return CanalPublico.model_validate(canal)


@router.patch("/{canal_id}", response_model=CanalPublico)
async def atualizar(
    canal_id: int,
    body: AtualizarCanalRequest,
    user: Usuario = Depends(usuario_admin),
    db: AsyncSession = Depends(get_db_async),
) -> CanalPublico:
    canal = await _get_canal_da_org(db, org_id=user.org_id, canal_id=canal_id)

    if body.nome is not None:
        canal.nome = body.nome
    if body.ativo is not None:
        canal.ativo = body.ativo
    if body.config is not None:
        canal.config = body.config

    await db.commit()
    await db.refresh(canal)
    return CanalPublico.model_validate(canal)


@router.delete("/{canal_id}", response_model=Mensagem)
async def deletar(
    canal_id: int,
    user: Usuario = Depends(usuario_admin),
    db: AsyncSession = Depends(get_db_async),
) -> Mensagem:
    canal = await _get_canal_da_org(db, org_id=user.org_id, canal_id=canal_id)
    await db.delete(canal)
    await db.commit()
    return Mensagem(mensagem="Canal removido")


# ── helper ───────────────────────────────────────────

async def _get_canal_da_org(
    db: AsyncSession, *, org_id: int, canal_id: int,
) -> Canal:
    canal = await db.get(Canal, canal_id)
    if canal is None or canal.org_id != org_id:
        raise HTTPException(status_code=404, detail="Canal não encontrado")
    return canal

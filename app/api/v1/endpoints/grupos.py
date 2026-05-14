"""CRUD de grupos de destino (WhatsApp/Telegram)."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import usuario_admin, usuario_atual
from app.db import get_db_async
from app.models import Canal, Grupo, GrupoNicho, Usuario
from app.schemas.canal_grupo import (
    AtualizarGrupoRequest,
    CriarGrupoRequest,
    GrupoPublico,
)
from app.schemas.comum import Mensagem
from app.services import limites

router = APIRouter(prefix="/grupos", tags=["grupos"])


@router.post("", response_model=GrupoPublico, status_code=status.HTTP_201_CREATED)
async def criar(
    body: CriarGrupoRequest,
    user: Usuario = Depends(usuario_admin),
    db: AsyncSession = Depends(get_db_async),
) -> GrupoPublico:
    # Verifica limite do plano antes de criar
    pode, msg = await limites.pode_criar_grupo(db, org_id=user.org_id)
    if not pode:
        raise HTTPException(status_code=402, detail=msg)

    # Valida canal pertence à mesma org
    canal = await db.get(Canal, body.canal_id)
    if canal is None or canal.org_id != user.org_id:
        raise HTTPException(status_code=400, detail="Canal inválido")

    grupo = Grupo(
        org_id=user.org_id,
        canal_id=body.canal_id,
        proprietario_id=body.proprietario_id,
        nome=body.nome,
        identificador=body.identificador,
        ativo=True,
    )
    db.add(grupo)
    await db.flush()

    # Vincula nichos
    for nicho_id in body.nichos_ids:
        db.add(GrupoNicho(grupo_id=grupo.id, nicho_id=nicho_id))

    await db.commit()
    await db.refresh(grupo)

    return await _grupo_publico(db, grupo)


@router.get("", response_model=list[GrupoPublico])
async def listar(
    user: Usuario = Depends(usuario_atual),
    db: AsyncSession = Depends(get_db_async),
) -> list[GrupoPublico]:
    result = await db.execute(
        select(Grupo).where(Grupo.org_id == user.org_id).order_by(Grupo.criado_em.desc())
    )
    grupos = list(result.scalars().all())
    return [await _grupo_publico(db, g) for g in grupos]


@router.get("/{grupo_id}", response_model=GrupoPublico)
async def detalhe(
    grupo_id: int,
    user: Usuario = Depends(usuario_atual),
    db: AsyncSession = Depends(get_db_async),
) -> GrupoPublico:
    grupo = await _get_grupo_da_org(db, org_id=user.org_id, grupo_id=grupo_id)
    return await _grupo_publico(db, grupo)


@router.patch("/{grupo_id}", response_model=GrupoPublico)
async def atualizar(
    grupo_id: int,
    body: AtualizarGrupoRequest,
    user: Usuario = Depends(usuario_admin),
    db: AsyncSession = Depends(get_db_async),
) -> GrupoPublico:
    grupo = await _get_grupo_da_org(db, org_id=user.org_id, grupo_id=grupo_id)

    if body.nome is not None:
        grupo.nome = body.nome
    if body.identificador is not None:
        grupo.identificador = body.identificador
    if body.ativo is not None:
        grupo.ativo = body.ativo

    if body.nichos_ids is not None:
        # Remove vínculos antigos e recria (mais simples que diff)
        await db.execute(delete(GrupoNicho).where(GrupoNicho.grupo_id == grupo.id))
        for nicho_id in body.nichos_ids:
            db.add(GrupoNicho(grupo_id=grupo.id, nicho_id=nicho_id))

    await db.commit()
    await db.refresh(grupo)
    return await _grupo_publico(db, grupo)


@router.delete("/{grupo_id}", response_model=Mensagem)
async def deletar(
    grupo_id: int,
    user: Usuario = Depends(usuario_admin),
    db: AsyncSession = Depends(get_db_async),
) -> Mensagem:
    grupo = await _get_grupo_da_org(db, org_id=user.org_id, grupo_id=grupo_id)
    await db.delete(grupo)
    await db.commit()
    return Mensagem(mensagem="Grupo removido")


# ── helpers ──────────────────────────────────────────

async def _get_grupo_da_org(
    db: AsyncSession, *, org_id: int, grupo_id: int,
) -> Grupo:
    grupo = await db.get(Grupo, grupo_id)
    if grupo is None or grupo.org_id != org_id:
        raise HTTPException(status_code=404, detail="Grupo não encontrado")
    return grupo


async def _grupo_publico(db: AsyncSession, grupo: Grupo) -> GrupoPublico:
    """Carrega nichos vinculados e monta o schema público."""
    result = await db.execute(
        select(GrupoNicho.nicho_id).where(GrupoNicho.grupo_id == grupo.id)
    )
    nichos_ids = [row[0] for row in result.all()]

    return GrupoPublico(
        id=grupo.id,
        org_id=grupo.org_id,
        canal_id=grupo.canal_id,
        proprietario_id=grupo.proprietario_id,
        nome=grupo.nome,
        identificador=grupo.identificador,
        ativo=grupo.ativo,
        precisa_atencao_admin=grupo.precisa_atencao_admin,
        nichos_ids=nichos_ids,
        criado_em=grupo.criado_em,
    )

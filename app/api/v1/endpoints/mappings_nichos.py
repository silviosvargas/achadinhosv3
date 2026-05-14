"""
CRUD do mapping `categoria_ml → nicho_id`.

Usado pelo `busca_service.ingerir_produtos`: quando um produto chega com
`categoria` do ML, o servidor consulta esta tabela pra atribuir nicho
automaticamente. Sem mapping cadastrado, produto entra sem nicho.

GET    /mappings-nichos                lista mappings da org
POST   /mappings-nichos                cria mapping
DELETE /mappings-nichos/{id}           remove mapping
GET    /mappings-nichos/categorias-vistas
       Lista categorias_ml que apareceram em produtos da org mas ainda não
       têm mapping — UX: admin clica e cria mapping pra o que está em uso.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import usuario_admin, usuario_atual
from app.db import get_db_async
from app.models import Nicho, NichoCategoriaML, Produto, Usuario
from app.schemas.busca import (
    CriarMappingCategoriaRequest,
    MappingCategoriaPublico,
)
from app.schemas.comum import Mensagem

router = APIRouter(prefix="/mappings-nichos", tags=["mappings-nichos"])


@router.get("", response_model=list[MappingCategoriaPublico])
async def listar(
    user: Usuario = Depends(usuario_atual),
    db: AsyncSession = Depends(get_db_async),
) -> list[MappingCategoriaPublico]:
    result = await db.execute(
        select(NichoCategoriaML)
        .where(NichoCategoriaML.org_id == user.org_id)
        .order_by(NichoCategoriaML.categoria_ml)
    )
    return [MappingCategoriaPublico.model_validate(m) for m in result.scalars().all()]


@router.post("", response_model=MappingCategoriaPublico,
             status_code=status.HTTP_201_CREATED)
async def criar(
    body: CriarMappingCategoriaRequest,
    admin: Usuario = Depends(usuario_admin),
    db: AsyncSession = Depends(get_db_async),
) -> MappingCategoriaPublico:
    # Valida que o nicho existe
    nicho = await db.get(Nicho, body.nicho_id)
    if nicho is None:
        raise HTTPException(status_code=400, detail="Nicho não encontrado")

    novo = NichoCategoriaML(
        org_id=admin.org_id,
        categoria_ml=body.categoria_ml.strip(),
        nicho_id=body.nicho_id,
        criado_em=datetime.now(tz=timezone.utc),
    )
    db.add(novo)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Já existe mapping pra essa categoria nesta organização",
        ) from None
    await db.refresh(novo)
    return MappingCategoriaPublico.model_validate(novo)


@router.delete("/{mapping_id}", response_model=Mensagem)
async def deletar(
    mapping_id: int,
    admin: Usuario = Depends(usuario_admin),
    db: AsyncSession = Depends(get_db_async),
) -> Mensagem:
    m = await db.get(NichoCategoriaML, mapping_id)
    if m is None or m.org_id != admin.org_id:
        raise HTTPException(status_code=404, detail="Mapping não encontrado")
    await db.delete(m)
    await db.commit()
    return Mensagem(mensagem="Mapping removido")


@router.get("/categorias-vistas", response_model=list[str])
async def categorias_vistas_pendentes(
    user: Usuario = Depends(usuario_atual),
    db: AsyncSession = Depends(get_db_async),
) -> list[str]:
    """
    Categorias que apareceram em produtos da org e ainda não têm mapping.

    Útil pra admin descobrir o que precisa cadastrar (sem precisar adivinhar
    string exata do ML).
    """
    # Categorias já mapeadas (pra excluir)
    mapeadas_rows = await db.execute(
        select(NichoCategoriaML.categoria_ml).where(
            NichoCategoriaML.org_id == user.org_id
        )
    )
    mapeadas = {r[0].lower() for r in mapeadas_rows.all()}

    # Categorias distintas em produtos
    rows = await db.execute(
        select(Produto.categoria).where(
            Produto.org_id == user.org_id,
            Produto.categoria.is_not(None),
        ).distinct()
    )
    todas = {r[0] for r in rows.all() if r[0]}
    pendentes = sorted(c for c in todas if c.lower() not in mapeadas)
    return pendentes

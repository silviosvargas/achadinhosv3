"""
CRUD de templates de mensagem.

GET    /templates             lista (com filtros)
GET    /templates/{id}        detalhe
POST   /templates             cria
PATCH  /templates/{id}        atualiza
DELETE /templates/{id}        remove
POST   /templates/preview     renderiza um texto-template com dados fake
                              (útil pra ver como vai ficar antes de salvar)
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import usuario_admin, usuario_atual
from app.db import get_db_async
from app.models import TemplateMensagem, Usuario
from app.schemas.comum import Mensagem
from app.schemas.produto import (
    AtualizarTemplateRequest,
    CriarTemplateRequest,
    TemplatePublico,
)
from app.services import templates_service

router = APIRouter(prefix="/templates", tags=["templates"])


@router.get("", response_model=list[TemplatePublico])
async def listar(
    nicho_id: int | None = None,
    user: Usuario = Depends(usuario_atual),
    db: AsyncSession = Depends(get_db_async),
) -> list[TemplatePublico]:
    base = select(TemplateMensagem).where(TemplateMensagem.org_id == user.org_id)
    if nicho_id is not None:
        base = base.where(TemplateMensagem.nicho_id == nicho_id)
    result = await db.execute(base.order_by(TemplateMensagem.ordem, TemplateMensagem.id))
    return [TemplatePublico.model_validate(t) for t in result.scalars().all()]


@router.get("/{template_id}", response_model=TemplatePublico)
async def detalhe(
    template_id: int,
    user: Usuario = Depends(usuario_atual),
    db: AsyncSession = Depends(get_db_async),
) -> TemplatePublico:
    t = await _get_da_org(db, org_id=user.org_id, template_id=template_id)
    return TemplatePublico.model_validate(t)


@router.post("", response_model=TemplatePublico, status_code=status.HTTP_201_CREATED)
async def criar(
    body: CriarTemplateRequest,
    admin: Usuario = Depends(usuario_admin),
    db: AsyncSession = Depends(get_db_async),
) -> TemplatePublico:
    novo = TemplateMensagem(
        org_id=admin.org_id,
        nicho_id=body.nicho_id,
        nome=body.nome,
        texto=body.texto,
        ativo=body.ativo,
        ordem=body.ordem,
    )
    db.add(novo)
    await db.commit()
    await db.refresh(novo)
    return TemplatePublico.model_validate(novo)


@router.patch("/{template_id}", response_model=TemplatePublico)
async def atualizar(
    template_id: int,
    body: AtualizarTemplateRequest,
    admin: Usuario = Depends(usuario_admin),
    db: AsyncSession = Depends(get_db_async),
) -> TemplatePublico:
    t = await _get_da_org(db, org_id=admin.org_id, template_id=template_id)

    if body.nome is not None:     t.nome = body.nome
    if body.texto is not None:    t.texto = body.texto
    if body.nicho_id is not None: t.nicho_id = body.nicho_id
    if body.ativo is not None:    t.ativo = body.ativo
    if body.ordem is not None:    t.ordem = body.ordem

    await db.commit()
    await db.refresh(t)
    return TemplatePublico.model_validate(t)


@router.delete("/{template_id}", response_model=Mensagem)
async def deletar(
    template_id: int,
    admin: Usuario = Depends(usuario_admin),
    db: AsyncSession = Depends(get_db_async),
) -> Mensagem:
    t = await _get_da_org(db, org_id=admin.org_id, template_id=template_id)
    await db.delete(t)
    await db.commit()
    return Mensagem(mensagem="Template removido")


@router.post("/preview")
async def preview(
    body: dict,
    user: Usuario = Depends(usuario_atual),
) -> dict:
    """
    Renderiza um texto-template com produto FAKE pra preview.

    Body: {"texto": "..."}

    Retorna o texto renderizado.
    """
    texto = body.get("texto", "")
    if not texto:
        raise HTTPException(status_code=400, detail="Falta 'texto' no body")

    # Cria objeto fake (não vai pro banco)
    from app.models import Produto
    produto_fake = Produto(
        org_id=user.org_id,
        plataforma="ml",
        item_id="MLBFAKE123",
        nome="Smartphone X Pro 256GB",
        categoria="Eletrônicos",
        preco=1899.00,
        preco_orig=2499.00,
        desconto=24.0,
        url_afiliado="https://exemplo.com/afiliado/123",
        foto_url=None,
    )
    return {"renderizado": templates_service.renderizar(texto, produto_fake)}


# ── helpers ──────────────────────────────────────────

async def _get_da_org(
    db: AsyncSession, *, org_id: int, template_id: int,
) -> TemplateMensagem:
    t = await db.get(TemplateMensagem, template_id)
    if t is None or t.org_id != org_id:
        raise HTTPException(status_code=404, detail="Template não encontrado")
    return t

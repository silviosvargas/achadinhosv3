"""
CRUD de usuários (escopado por org) + gerenciamento de afiliados.

POST   /usuarios                          admin cria usuário na própria org
GET    /usuarios                          lista usuários da org
GET    /usuarios/{id}                     detalhe
PATCH  /usuarios/{id}                     atualiza nome/email/papel/ativo
POST   /usuarios/me/senha                 usuário troca a própria senha
DELETE /usuarios/{id}                     desativa (soft delete)

Afiliados (Fase 13):
GET    /usuarios/{id}/afiliados           lista marketplaces cadastrados
POST   /usuarios/{id}/afiliados           cadastra/atualiza um marketplace
DELETE /usuarios/{id}/afiliados/{plat}    remove um marketplace
"""
from pydantic import BaseModel, Field

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import usuario_admin, usuario_atual
from app.core import marketplaces
from app.core.security import hash_senha, verificar_senha
from app.db import get_db_async
from app.models import Usuario
from app.schemas.comum import Mensagem
from app.schemas.usuario import (
    AtualizarUsuarioRequest,
    CriarUsuarioRequest,
    TrocarSenhaRequest,
    UsuarioPublico,
)
from app.services import afiliado_service, limites


class AfiliadoUpsertRequest(BaseModel):
    plataforma: str = Field(min_length=1, max_length=20)
    tag:        str = Field(min_length=1, max_length=200)


class AfiliadoPublico(BaseModel):
    plataforma: str
    nome:       str   # display name do marketplace
    icone:      str
    tag:        str

router = APIRouter(prefix="/usuarios", tags=["usuarios"])


@router.post("", response_model=UsuarioPublico, status_code=status.HTTP_201_CREATED)
async def criar(
    body: CriarUsuarioRequest,
    admin: Usuario = Depends(usuario_admin),
    db: AsyncSession = Depends(get_db_async),
) -> UsuarioPublico:
    """Cria novo usuário na MESMA org do admin que está chamando."""
    # Verifica limite do plano antes de criar
    pode, msg = await limites.pode_criar_usuario(db, org_id=admin.org_id)
    if not pode:
        raise HTTPException(status_code=402, detail=msg)

    novo = Usuario(
        org_id=admin.org_id,
        login=body.login,
        senha_hash=hash_senha(body.senha),
        papel=body.papel,
        nome_exibicao=body.nome_exibicao or body.login,
        email=body.email,
        ativo=True,
        onboarding_completo=False,
    )
    db.add(novo)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Já existe usuário com esse login nesta organização",
        ) from None
    await db.refresh(novo)
    return UsuarioPublico.model_validate(novo)


@router.get("", response_model=list[UsuarioPublico])
async def listar(
    user: Usuario = Depends(usuario_atual),
    db: AsyncSession = Depends(get_db_async),
) -> list[UsuarioPublico]:
    result = await db.execute(
        select(Usuario)
        .where(Usuario.org_id == user.org_id)
        .order_by(Usuario.criado_em.desc())
    )
    return [UsuarioPublico.model_validate(u) for u in result.scalars().all()]


@router.get("/{usuario_id}", response_model=UsuarioPublico)
async def detalhe(
    usuario_id: int,
    user: Usuario = Depends(usuario_atual),
    db: AsyncSession = Depends(get_db_async),
) -> UsuarioPublico:
    target = await _get_da_org(db, org_id=user.org_id, usuario_id=usuario_id)
    return UsuarioPublico.model_validate(target)


@router.patch("/{usuario_id}", response_model=UsuarioPublico)
async def atualizar(
    usuario_id: int,
    body: AtualizarUsuarioRequest,
    admin: Usuario = Depends(usuario_admin),
    db: AsyncSession = Depends(get_db_async),
) -> UsuarioPublico:
    target = await _get_da_org(db, org_id=admin.org_id, usuario_id=usuario_id)

    if body.nome_exibicao is not None:
        target.nome_exibicao = body.nome_exibicao
    if body.email is not None:
        target.email = body.email
    if body.papel is not None:
        target.papel = body.papel
    if body.ativo is not None:
        target.ativo = body.ativo

    await db.commit()
    await db.refresh(target)
    return UsuarioPublico.model_validate(target)


@router.post("/me/senha", response_model=Mensagem)
async def trocar_senha(
    body: TrocarSenhaRequest,
    user: Usuario = Depends(usuario_atual),
    db: AsyncSession = Depends(get_db_async),
) -> Mensagem:
    """Usuário troca a própria senha."""
    if not verificar_senha(body.senha_atual, user.senha_hash):
        raise HTTPException(status_code=400, detail="Senha atual incorreta")

    user.senha_hash = hash_senha(body.senha_nova)
    await db.commit()
    return Mensagem(mensagem="Senha trocada com sucesso")


# Endpoint legacy PATCH /credenciais (Fase 4b.1) foi removido na Fase 13.
# Substituído por GET/POST/DELETE /usuarios/{id}/afiliados (1 row por marketplace).
# Login/senha do ML eram pra auto-login do agente — feature abandonada
# (ML tem 2FA, viola TOS).


@router.delete("/{usuario_id}", response_model=Mensagem)
async def desativar(
    usuario_id: int,
    admin: Usuario = Depends(usuario_admin),
    db: AsyncSession = Depends(get_db_async),
) -> Mensagem:
    """Desativa usuário (soft delete). Não pode desativar a si mesmo."""
    if usuario_id == admin.id:
        raise HTTPException(
            status_code=400,
            detail="Você não pode desativar a si mesmo",
        )
    target = await _get_da_org(db, org_id=admin.org_id, usuario_id=usuario_id)
    target.ativo = False
    await db.commit()
    return Mensagem(mensagem="Usuário desativado")


# ─────────────────────────────────────────────────────
# Afiliados (Fase 13 — multi-marketplace)
# ─────────────────────────────────────────────────────

def _autorizar_mexer_em(target: Usuario, user: Usuario) -> None:
    """Levanta 403 se `user` não pode editar afiliados de `target`."""
    if not user.eh_admin and target.id != user.id:
        raise HTTPException(
            status_code=403,
            detail="Só admin da org ou o próprio dono pode mexer nestes afiliados",
        )


def _gate_plano_cadastrar(user: Usuario) -> None:
    """Plano free não cadastra afiliado próprio (Fase 9.9 — usa do admin)."""
    plano = user.organizacao.plano if user.organizacao else None
    if plano is None or not getattr(plano, "pode_cadastrar_afiliado", False):
        raise HTTPException(
            status_code=403,
            detail="Seu plano não permite cadastrar afiliado próprio. "
                   "No plano free, suas postagens usam o afiliado do administrador. "
                   "Faça upgrade pra cadastrar suas próprias tags.",
        )


@router.get("/{usuario_id}/afiliados", response_model=list[AfiliadoPublico])
async def listar_afiliados(
    usuario_id: int,
    user: Usuario = Depends(usuario_atual),
    db: AsyncSession = Depends(get_db_async),
) -> list[AfiliadoPublico]:
    """Lista tags de afiliado cadastradas pelo user, com display name."""
    target = await _get_da_org(db, org_id=user.org_id, usuario_id=usuario_id)
    _autorizar_mexer_em(target, user)

    rows = await afiliado_service.listar_por_usuario(db, usuario_id=target.id)
    out: list[AfiliadoPublico] = []
    for r in rows:
        mkt = marketplaces.por_slug(r.plataforma)
        out.append(AfiliadoPublico(
            plataforma=r.plataforma,
            nome=mkt.nome if mkt else r.plataforma,
            icone=mkt.icone if mkt else "🏷️",
            tag=r.tag,
        ))
    return out


@router.post("/{usuario_id}/afiliados", response_model=AfiliadoPublico,
             status_code=status.HTTP_201_CREATED)
async def cadastrar_afiliado(
    usuario_id: int,
    body: AfiliadoUpsertRequest,
    user: Usuario = Depends(usuario_atual),
    db: AsyncSession = Depends(get_db_async),
) -> AfiliadoPublico:
    """Cria ou atualiza tag de afiliado pra um marketplace (upsert)."""
    target = await _get_da_org(db, org_id=user.org_id, usuario_id=usuario_id)
    _autorizar_mexer_em(target, user)
    _gate_plano_cadastrar(user)

    mkt = marketplaces.por_slug(body.plataforma)
    if mkt is None:
        raise HTTPException(
            status_code=400,
            detail=f"Marketplace '{body.plataforma}' não suportado. "
                   f"Suportados: {sorted(marketplaces.slugs_validos())}",
        )

    try:
        row = await afiliado_service.upsert(
            db, usuario_id=target.id, plataforma=mkt.slug, tag=body.tag,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None

    return AfiliadoPublico(
        plataforma=row.plataforma, nome=mkt.nome, icone=mkt.icone, tag=row.tag,
    )


@router.delete("/{usuario_id}/afiliados/{plataforma}", response_model=Mensagem)
async def remover_afiliado(
    usuario_id: int,
    plataforma: str,
    user: Usuario = Depends(usuario_atual),
    db: AsyncSession = Depends(get_db_async),
) -> Mensagem:
    """Remove cadastro de afiliado de uma plataforma específica."""
    target = await _get_da_org(db, org_id=user.org_id, usuario_id=usuario_id)
    _autorizar_mexer_em(target, user)
    _gate_plano_cadastrar(user)

    removeu = await afiliado_service.remover(
        db, usuario_id=target.id, plataforma=plataforma.lower().strip(),
    )
    if not removeu:
        raise HTTPException(status_code=404, detail="Afiliado não encontrado")
    return Mensagem(mensagem="Afiliado removido")


# ── helper ───────────────────────────────────────────

async def _get_da_org(
    db: AsyncSession, *, org_id: int, usuario_id: int,
) -> Usuario:
    target = await db.get(Usuario, usuario_id)
    if target is None or target.org_id != org_id:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    return target

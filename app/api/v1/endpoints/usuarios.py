"""
CRUD de usuários (escopado por org).

POST   /usuarios               admin cria usuário na própria org
GET    /usuarios               lista usuários da org
GET    /usuarios/{id}          detalhe
PATCH  /usuarios/{id}          atualiza nome/email/papel/ativo
POST   /usuarios/me/senha      usuário troca a própria senha
DELETE /usuarios/{id}          desativa (soft delete)
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import usuario_admin, usuario_atual
from app.core.security import hash_senha, verificar_senha
from app.db import get_db_async
from app.models import Usuario
from app.schemas.comum import Mensagem
from app.schemas.usuario import (
    AtualizarUsuarioRequest,
    CredenciaisMLRequest,
    CriarUsuarioRequest,
    TrocarSenhaRequest,
    UsuarioPublico,
)
from app.services import limites

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


@router.patch("/{usuario_id}/credenciais", response_model=UsuarioPublico)
async def atualizar_credenciais(
    usuario_id: int,
    body: CredenciaisMLRequest,
    user: Usuario = Depends(usuario_atual),
    db: AsyncSession = Depends(get_db_async),
) -> UsuarioPublico:
    """
    Atualiza credenciais de plataforma (Fase 4b.1).

    Quem pode mexer:
    - Admin da org: pode mexer em qualquer usuário da org.
    - Usuário comum / afiliado: só nas próprias credenciais.

    Comportamento da senha:
    - `senha_ml=None`  → não mexe na senha existente
    - `senha_ml=""`    → apaga (limpa coluna cifrada)
    - `senha_ml="abc"` → cifra e armazena
    """
    target = await _get_da_org(db, org_id=user.org_id, usuario_id=usuario_id)

    # Permissão: admin OU dono das credenciais
    if not user.eh_admin and target.id != user.id:
        raise HTTPException(
            status_code=403,
            detail="Só admin ou o próprio dono pode mudar essas credenciais",
        )

    if body.usuario_ml is not None:
        target.usuario_ml = body.usuario_ml.strip() or None

    if body.senha_ml is not None:
        # None = não mexe (já tratado); "" = apaga; outro = cifra
        target.set_senha_ml(body.senha_ml if body.senha_ml != "" else None)

    await db.commit()
    await db.refresh(target)
    return UsuarioPublico.model_validate(target)


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


# ── helper ───────────────────────────────────────────

async def _get_da_org(
    db: AsyncSession, *, org_id: int, usuario_id: int,
) -> Usuario:
    target = await db.get(Usuario, usuario_id)
    if target is None or target.org_id != org_id:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    return target

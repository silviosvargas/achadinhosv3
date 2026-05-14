"""
Autenticação: login, refresh, logout, dados do usuário atual.

POST /api/v1/auth/login    — recebe credenciais, devolve tokens
POST /api/v1/auth/refresh  — troca refresh por novo access
GET  /api/v1/auth/me       — dados do usuário logado
"""
from datetime import datetime, timezone

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import usuario_atual
from app.core.config import settings
from app.core.security import (
    TOKEN_REFRESH,
    criar_access_token,
    criar_refresh_token,
    decodificar_token,
    verificar_senha,
)
from app.db import get_db_async
from app.models import Organizacao, Usuario
from app.schemas.auth import (
    LoginRequest,
    RefreshRequest,
    SignupRequest,
    SignupResponse,
    TokenResponse,
    UsuarioPublico,
)
from app.services import signup_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    db: AsyncSession = Depends(get_db_async),
) -> TokenResponse:
    """Autentica usuário e devolve par de tokens."""
    user = await _achar_usuario(db, login=body.login, org_slug=body.org_slug)

    if user is None or not verificar_senha(body.senha, user.senha_hash):
        # Mensagem genérica — não revela se é login que não existe ou senha errada
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuário ou senha inválidos",
        )
    if not user.ativo:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Conta desativada",
        )

    # Atualiza ultimo_login
    user.ultimo_login = datetime.now(tz=timezone.utc)
    await db.commit()
    await db.refresh(user)

    return _gerar_resposta_token(user)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    body: RefreshRequest,
    db: AsyncSession = Depends(get_db_async),
) -> TokenResponse:
    """Troca refresh token por novo par de tokens."""
    try:
        payload = decodificar_token(body.refresh_token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token expirado — faça login novamente",
        ) from None
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token inválido",
        ) from None

    if payload.get("tipo") != TOKEN_REFRESH:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token enviado não é de refresh",
        )

    user = await db.get(Usuario, payload.get("uid"))
    if user is None or not user.ativo:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuário não encontrado ou inativo",
        )

    return _gerar_resposta_token(user)


@router.get("/me", response_model=UsuarioPublico)
async def me(user: Usuario = Depends(usuario_atual)) -> Usuario:
    """Retorna o próprio usuário logado."""
    return user


@router.post("/signup", response_model=SignupResponse,
             status_code=status.HTTP_201_CREATED)
async def signup(
    body: SignupRequest,
    db: AsyncSession = Depends(get_db_async),
) -> SignupResponse:
    """
    Cadastro público (Fase 5). Cria org + admin + retorna JWT pra autologin.

    Sem verificação de email nesta fase — email é opcional, sem confirmação.
    Quem chama é responsável por já fazer rate-limit em prod.
    """
    from sqlalchemy.exc import IntegrityError
    try:
        org, admin = await signup_service.criar_org_e_admin(
            db,
            org_nome=body.org_nome,
            login=body.login,
            senha=body.senha,
            email=body.email,
            nome_exibicao=body.nome_exibicao,
        )
    except signup_service.SignupError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Esse nome ou login já está em uso. Tente outro.",
        ) from None

    access = criar_access_token(usuario_id=admin.id, org_id=org.id, papel=admin.papel)
    refresh = criar_refresh_token(usuario_id=admin.id, org_id=org.id)
    return SignupResponse(
        access_token=access,
        refresh_token=refresh,
        expires_in=settings.jwt_access_token_expire_minutes * 60,
        org_slug=org.slug,
        usuario=UsuarioPublico.model_validate(admin),
    )


# ============================================================
# Helpers internos
# ============================================================

async def _achar_usuario(
    db: AsyncSession, *, login: str, org_slug: str | None,
) -> Usuario | None:
    """
    Acha usuário por login. Se org_slug fornecido, restringe à org.
    Senão, só funciona se houver UM usuário com aquele login (caso single-tenant).
    """
    stmt = select(Usuario).where(Usuario.login == login, Usuario.ativo.is_(True))

    if org_slug:
        stmt = stmt.join(Organizacao).where(Organizacao.slug == org_slug)

    result = await db.execute(stmt)
    rows = result.scalars().all()

    if len(rows) == 1:
        return rows[0]
    if len(rows) > 1:
        # Múltiplos logins iguais em orgs diferentes — exige org_slug
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Login existe em mais de uma organização — informe org_slug",
        )
    return None


def _gerar_resposta_token(user: Usuario) -> TokenResponse:
    """Monta TokenResponse a partir do usuário."""
    access  = criar_access_token(usuario_id=user.id, org_id=user.org_id, papel=user.papel)
    refresh = criar_refresh_token(usuario_id=user.id, org_id=user.org_id)
    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        expires_in=settings.jwt_access_token_expire_minutes * 60,
        usuario=UsuarioPublico.model_validate(user),
    )

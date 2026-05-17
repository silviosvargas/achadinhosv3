"""
Dependencies (injeção de dependência) compartilhadas pelos endpoints.

Padrão FastAPI: funções que extraem dados da request (usuário logado, org,
permissões) e podem ser plugadas em qualquer endpoint via `Depends(...)`.
"""
import jwt
from fastapi import Cookie, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import TOKEN_ACESSO, TOKEN_AGENTE, decodificar_token
from app.db import get_db_async
from app.models import Agente, Usuario

# Esquema Bearer pra Authorization: Bearer <token>
_bearer = HTTPBearer(auto_error=False)

# Nome do cookie de sessão do dashboard — bate com app/web/routes.py COOKIE_NAME.
# Mantido como constante local pra não criar import circular web→api.
_COOKIE_SESSAO = "achadinhos_session"


async def usuario_atual(
    cred: HTTPAuthorizationCredentials | None = Depends(_bearer),
    achadinhos_session: str | None = Cookie(default=None),
    db: AsyncSession = Depends(get_db_async),
) -> Usuario:
    """
    Extrai o usuário do JWT.

    Aceita o token em **2 lugares**:
    1. Header `Authorization: Bearer <jwt>` — usado pelo agente, pelo
       SDK/CLI, e por chamadas API direta.
    2. Cookie HttpOnly `achadinhos_session` — usado pelo JS do dashboard
       (não acessa cookie HttpOnly diretamente, então não dá pra pôr no
       header; o browser anexa o cookie automaticamente no fetch).

    Retorna 401 se token ausente, inválido, expirado ou usuário inativo.
    """
    token: str | None = None
    if cred is not None:
        token = cred.credentials
    elif achadinhos_session:
        token = achadinhos_session

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Não autenticado",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = decodificar_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expirado",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None

    if payload.get("tipo") != TOKEN_ACESSO:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Tipo de token incorreto pra este endpoint",
        )

    uid = payload.get("uid")
    if not uid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token sem identificação",
        )

    user = await db.get(Usuario, uid)
    if user is None or not user.ativo:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuário não encontrado ou inativo",
        )

    return user


async def usuario_admin(user: Usuario = Depends(usuario_atual)) -> Usuario:
    """Exige papel admin (ou super)."""
    if not user.eh_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acesso restrito a administradores",
        )
    return user


def requer_plano(flag: str):
    """Factory de dependency que exige que o plano da org do user tenha
    a flag dada. Uso:

        @router.post("/...", )
        async def algo(user: Usuario = Depends(requer_plano("pode_cadastrar_afiliado"))):
            ...

    Se a flag não estiver true no plano, retorna 403 com mensagem clara.
    Fase 9.9 (signup free restrito) — flags em `planos`:
    pode_cadastrar_afiliado, pode_criar_buscas, pode_criar_produto_proprio.
    """
    async def _checker(user: Usuario = Depends(usuario_atual)) -> Usuario:
        org = user.organizacao
        if org is None or org.plano is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Org sem plano configurado",
            )
        if not getattr(org.plano, flag, False):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Seu plano não permite essa ação ({flag}). "
                       f"Faça upgrade pra um plano que libere.",
            )
        return user
    return _checker


async def agente_atual(
    cred: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db_async),
) -> Agente:
    """
    Autentica request com token tipo `agente` (longa duração).
    Usado pelo endpoint /produtos/ingest e qualquer outro callback do agente.
    """
    if cred is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Não autenticado",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = decodificar_token(cred.credentials)
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expirado",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None

    if payload.get("tipo") != TOKEN_AGENTE:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Tipo de token incorreto pra este endpoint",
        )

    aid = payload.get("agente")
    if not aid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token sem identificação do agente",
        )

    agente = await db.get(Agente, aid)
    if agente is None or not agente.ativo:
        # Defesa em camadas: se REST descobriu agente zumbi, fecha o WS
        # se houver. Sem isso, dispatcher continuava entregando tarefas
        # via WS pro agente que aceitava mas falhava no POST /ingest
        # (mesmo bug, loop infinito). Best-effort — não bloqueia 401.
        try:
            from app.services.dispatcher import _invalidar_agente_zumbi
            await _invalidar_agente_zumbi(
                aid, "agente_atual_rest_401",
            )
        except Exception:
            pass
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Agente não encontrado ou desativado",
        )

    return agente

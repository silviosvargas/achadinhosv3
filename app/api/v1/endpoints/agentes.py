"""
CRUD de agentes.

POST /agentes              cria agente + devolve token UMA vez (admin only)
GET  /agentes              lista agentes da org
GET  /agentes/{id}         detalhe de um agente
PATCH /agentes/{id}        atualiza nome/ativo
DELETE /agentes/{id}       desativa (soft delete via ativo=False)
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import agente_atual, usuario_admin, usuario_atual
from app.db import get_db_async
from app.models import Agente, Usuario
from app.schemas.agente import (
    AgenteCriadoResponse,
    AgentePublico,
    AtualizarAgenteRequest,
    AutoRegistroRequest,
    AutoRegistroResponse,
    CriarAgenteRequest,
)
from app.schemas.comum import Mensagem
from app.services import agente_service

router = APIRouter(prefix="/agentes", tags=["agentes"])


@router.post("", response_model=AgenteCriadoResponse, status_code=status.HTTP_201_CREATED)
async def criar(
    body: CriarAgenteRequest,
    user: Usuario = Depends(usuario_admin),
    db: AsyncSession = Depends(get_db_async),
) -> AgenteCriadoResponse:
    """Admin cria um agente. Resposta inclui o token (salvar — não aparece de novo)."""
    try:
        agente, token = await agente_service.criar_agente(
            db,
            org_id=user.org_id,
            usuario_id=body.usuario_id,
            nome=body.nome,
        )
    except agente_service.AgenteServiceError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None

    return AgenteCriadoResponse(
        agente=AgentePublico.model_validate(agente),
        token=token,
    )


# Cache de 60s pra metadata da última release do agente (versão + URL).
# Evita bater no GitHub API a cada poll do dashboard. Era 5min, reduzido
# pra 60s pra detecção mais rápida de releases novas (cenário: user acabou
# de publicar release e atualizou o agente — quer ver o badge sumir já).
_VERSAO_CACHE: dict[str, str | float | None] = {
    "versao": None, "url_download": None, "ate": 0.0,
}
_VERSAO_TTL_S = 60


@router.get("/versao-atual")
async def versao_agente_atual(
    _: Usuario = Depends(usuario_atual),
    nocache: int = 0,
) -> dict:
    """Retorna metadata da última release publicada do agente desktop.

    Usado pelo dashboard pra detectar se o agente instalado do user está
    desatualizado. Compara `versao` retornada aqui com a versão que o agente
    local reporta em `/ping` (campo `versao`).

    Cache local de 60s — evita bater na API do GitHub a cada poll. Use
    `?nocache=1` pra forçar consulta fresh (debug).

    Retorna `{versao: null, url_download: null}` se não houver release ou
    se a consulta ao GitHub falhar — caller deve tratar como "ignora".
    """
    import time
    import httpx

    agora = time.time()
    if (
        not nocache
        and _VERSAO_CACHE["versao"]
        and isinstance(_VERSAO_CACHE["ate"], float)
        and agora < _VERSAO_CACHE["ate"]
    ):
        return {
            "versao":       _VERSAO_CACHE["versao"],
            "url_download": _VERSAO_CACHE["url_download"],
        }

    try:
        async with httpx.AsyncClient(timeout=5.0) as cli:
            r = await cli.get(
                "https://api.github.com/repos/silviosvargas/achadinhosv3/releases/latest",
                headers={"Accept": "application/vnd.github+json"},
            )
        if r.status_code == 200:
            data = r.json()
            # Tag vem como "agente-vX.Y.Z" — extrai só "X.Y.Z"
            tag = data.get("tag_name", "") or ""
            versao = tag.split("agente-v", 1)[-1] if "agente-v" in tag else tag
            versao = (versao or "").lstrip("v").strip()

            url_dl = None
            for asset in data.get("assets", []) or []:
                nome = asset.get("name", "").lower()
                if nome.endswith(".exe") and "achadinhosagent" in nome:
                    url_dl = asset.get("browser_download_url")
                    break

            if versao and url_dl:
                _VERSAO_CACHE["versao"] = versao
                _VERSAO_CACHE["url_download"] = url_dl
                _VERSAO_CACHE["ate"] = agora + _VERSAO_TTL_S
                return {"versao": versao, "url_download": url_dl}
    except (httpx.HTTPError, ValueError, KeyError):
        pass

    return {"versao": None, "url_download": None}


@router.get("/download")
async def download_instalador(_: Usuario = Depends(usuario_atual)) -> dict:
    """
    Placeholder do download do instalador `.exe` do agente (Fase 9.5).

    DEVE ficar declarado ANTES de `GET /{agente_id}` senão FastAPI tenta
    parsear "download" como agente_id e retorna 422. Por enquanto retorna
    503 com instruções pra modo dev. A Fase 9.5 vai gerar um installer
    Windows nativo (Inno Setup + PyInstaller via GitHub Actions) e este
    endpoint vai redirecionar pra última release do GitHub.
    """
    raise HTTPException(
        status_code=503,
        detail={
            "erro": "installer_em_construcao",
            "msg": "Installer .exe ainda não disponível — entrega na Fase 9.5.",
            "alternativa": "Por enquanto rode em modo dev: ver /agentes/baixar",
        },
    )


@router.get("/status")
async def status_agentes(
    user: Usuario = Depends(usuario_atual),
    db: AsyncSession = Depends(get_db_async),
) -> dict:
    """
    Status de online/offline dos agentes da org do user (Fase 9.8).

    Crítico pro cenário "controle remoto via celular": dashboard mostra
    indicador 'N agentes online' e bloqueia ações se nenhum PC do user
    está vivo.

    Cruza a lista de agentes da org (DB) com o registry de WebSockets
    ativos no processo (memória). Retorna por agente: id, nome, ativo,
    online (bool).

    Polling client-side OK (15-30s) — o endpoint é leve, sem fan-out.
    """
    from app.services.agente_registry import registry as _registry

    agentes = await agente_service.listar_agentes_da_org(db, org_id=user.org_id)
    items = [
        {
            "id": a.id,
            "nome": a.nome,
            "ativo": a.ativo,
            "online": _registry.esta_online(a.id),
        }
        for a in agentes
    ]
    online = sum(1 for it in items if it["online"])
    return {
        "total": len(items),
        "total_online": online,
        "agentes": items,
    }


@router.get("", response_model=list[AgentePublico])
async def listar(
    user: Usuario = Depends(usuario_atual),
    db: AsyncSession = Depends(get_db_async),
) -> list[AgentePublico]:
    """Lista todos agentes da org do usuário logado."""
    agentes = await agente_service.listar_agentes_da_org(db, org_id=user.org_id)
    return [AgentePublico.model_validate(a) for a in agentes]


@router.get("/{agente_id}", response_model=AgentePublico)
async def detalhe(
    agente_id: int,
    user: Usuario = Depends(usuario_atual),
    db: AsyncSession = Depends(get_db_async),
) -> AgentePublico:
    agente = await agente_service.get_agente_da_org(
        db, org_id=user.org_id, agente_id=agente_id,
    )
    if agente is None:
        raise HTTPException(status_code=404, detail="Agente não encontrado")
    return AgentePublico.model_validate(agente)


@router.patch("/{agente_id}", response_model=AgentePublico)
async def atualizar(
    agente_id: int,
    body: AtualizarAgenteRequest,
    user: Usuario = Depends(usuario_admin),
    db: AsyncSession = Depends(get_db_async),
) -> AgentePublico:
    agente = await agente_service.get_agente_da_org(
        db, org_id=user.org_id, agente_id=agente_id,
    )
    if agente is None:
        raise HTTPException(status_code=404, detail="Agente não encontrado")

    if body.nome is not None:
        agente.nome = body.nome
    if body.ativo is not None:
        agente.ativo = body.ativo

    await db.commit()
    await db.refresh(agente)
    return AgentePublico.model_validate(agente)


@router.post("/registrar-self", response_model=AutoRegistroResponse,
             status_code=status.HTTP_201_CREATED)
async def auto_registrar(
    body: AutoRegistroRequest,
    user: Usuario = Depends(usuario_atual),
    db: AsyncSession = Depends(get_db_async),
) -> AutoRegistroResponse:
    """
    Auto-registro do agente (Fase 6).

    O app do agente, no primeiro start, pede email/senha do usuário,
    chama `POST /auth/login` pra obter JWT de acesso, e depois chama ESTE
    endpoint pra criar um agente associado A ELE MESMO (sem precisar
    admin gerar token manualmente).

    Retorna o token JWT do agente (1 ano) + URLs pra config.
    """
    from fastapi import Request as _Req  # só pra anotar
    try:
        agente, token = await agente_service.criar_agente(
            db,
            org_id=user.org_id,
            usuario_id=user.id,
            nome=body.nome.strip() or "PC",
        )
    except agente_service.AgenteServiceError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None

    # Persistir sistema_op se foi enviado
    if body.sistema_op:
        agente.sistema_op = body.sistema_op[:50]
        await db.commit()
        await db.refresh(agente)

    # URLs públicas derivadas de PUBLIC_BASE_URL no .env:
    # - dev:  http://localhost:8000  → ws://localhost:8000/api/v1/ws/agente
    # - prod: https://achadinhos.maisseguidores.ia.br → wss://.../api/v1/ws/agente
    from app.core.config import settings
    return AutoRegistroResponse(
        agente=AgentePublico.model_validate(agente),
        token=token,
        ws_url=settings.public_ws_url,
        api_url=settings.public_base_url,
    )


# Endpoint legacy GET /me/credenciais (Fase 4b.1) foi removido na Fase 13.
# Devolvia login/senha plain do ML pro agente fazer auto-login (Selenium).
# Feature abandonada porque ML tem 2FA real e tentar automatizar viola TOS.
# User passou a usar `agent.login_ml` manual (1x, sessão persiste no perfil
# Chrome). Tags de afiliado vivem em `usuarios_afiliados`.


@router.delete("/{agente_id}", response_model=Mensagem)
async def desativar(
    agente_id: int,
    user: Usuario = Depends(usuario_admin),
    db: AsyncSession = Depends(get_db_async),
) -> Mensagem:
    """Desativa o agente (soft delete). Conexão WS ativa é fechada na próxima validação."""
    agente = await agente_service.get_agente_da_org(
        db, org_id=user.org_id, agente_id=agente_id,
    )
    if agente is None:
        raise HTTPException(status_code=404, detail="Agente não encontrado")
    agente.ativo = False
    await db.commit()
    return Mensagem(mensagem="Agente desativado")

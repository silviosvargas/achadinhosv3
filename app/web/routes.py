"""
Rotas que renderizam HTML (Jinja2).

Diferente das /api/v1/* que retornam JSON, essas rotas servem páginas
pra navegador. Auth aqui é via cookie HTTP-only (não JWT no header).

Autenticação: ao fazer login, o servidor seta um cookie 'session_token'
contendo o JWT de acesso. Páginas protegidas validam esse cookie.
Logout limpa o cookie.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import jwt
from fastapi import (
    APIRouter,
    Cookie,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import (
    TOKEN_ACESSO,
    criar_access_token,
    decodificar_token,
    hash_senha,
    verificar_senha,
)
from app.db import get_db_async
from app.models import (
    Agente,
    BuscaML,
    Canal,
    Grupo,
    Nicho,
    NichoCategoriaML,
    Organizacao,
    Plano,
    Produto,
    ProdutoNicho,
    Tarefa,
    TemplateMensagem,
    Usuario,
)
from app.services import (
    agente_service,
    busca_service,
    dispatcher,
    limites,
    lote_service,
    signup_service,
    templates_service,
)

router = APIRouter(tags=["web"])

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

COOKIE_NAME = "achadinhos_session"


# ============================================================
# PWA — manifest + service worker (Fase 7)
# ============================================================

@router.get("/manifest.webmanifest", include_in_schema=False)
async def manifest_pwa() -> dict:
    """Web App Manifest — permite o site ser instalado como app no mobile."""
    return {
        "name":             "Achadinhos",
        "short_name":       "Achadinhos",
        "description":      "Automação de afiliados — buscas e postagens",
        "lang":             "pt-BR",
        "start_url":        "/dashboard",
        "scope":            "/",
        "display":          "standalone",
        "orientation":      "portrait",
        "background_color": "#f7f8fa",
        "theme_color":      "#16a34a",
        "icons": [
            {
                "src":   "/static/icons/icon-192.png",
                "sizes": "192x192",
                "type":  "image/png",
                "purpose": "any maskable",
            },
            {
                "src":   "/static/icons/icon-512.png",
                "sizes": "512x512",
                "type":  "image/png",
                "purpose": "any maskable",
            },
        ],
    }


# Service Worker inline — precisa ser servido do scope raiz, não de /static.
# Cache simples de assets estáticos; HTML sempre vai pra rede (network-first).
SW_JS = r"""
const CACHE = 'achadinhos-v1';
const ASSETS = [
  '/static/style.css',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(ASSETS)).catch(() => {}));
  self.skipWaiting();
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then((names) =>
      Promise.all(names.filter((n) => n !== CACHE).map((n) => caches.delete(n)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  // HTML/API: sempre rede (mantém dado atualizado).
  if (e.request.mode === 'navigate' ||
      url.pathname.startsWith('/api/') ||
      url.pathname === '/manifest.webmanifest') {
    return;  // deixa o browser tratar (network)
  }
  // Estáticos: cache-first com fallback de rede.
  e.respondWith(
    caches.match(e.request).then((cached) =>
      cached || fetch(e.request).then((resp) => {
        if (resp.ok && url.origin === self.location.origin) {
          const clone = resp.clone();
          caches.open(CACHE).then((c) => c.put(e.request, clone)).catch(() => {});
        }
        return resp;
      }).catch(() => cached)
    )
  );
});
"""


@router.get("/service-worker.js", include_in_schema=False)
async def service_worker():
    """Service Worker — ESCOPO RAIZ (não pode ser /static/)."""
    from fastapi.responses import Response
    return Response(
        content=SW_JS,
        media_type="application/javascript",
        # Browsers honram esse header pra ampliar o scope além do path do arquivo
        headers={"Service-Worker-Allowed": "/"},
    )


# ============================================================
# Helpers de auth
# ============================================================

async def usuario_da_sessao(
    achadinhos_session: str | None = Cookie(default=None),
    db: AsyncSession = Depends(get_db_async),
) -> Usuario | None:
    """Lê cookie de sessão e devolve usuário (ou None se inválido/ausente)."""
    if not achadinhos_session:
        return None
    try:
        payload = decodificar_token(achadinhos_session)
    except jwt.PyJWTError:
        return None

    if payload.get("tipo") != TOKEN_ACESSO:
        return None

    user = await db.get(Usuario, payload.get("uid"))
    if user is None or not user.ativo:
        return None
    return user


async def exigir_login(
    user: Usuario | None = Depends(usuario_da_sessao),
) -> Usuario:
    """Versão que redireciona pra /login se não autenticado."""
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
            headers={"Location": "/login"},
        )
    return user


async def exigir_admin(
    user: Usuario = Depends(exigir_login),
) -> Usuario:
    if not user.eh_admin:
        raise HTTPException(status_code=403, detail="Apenas administradores")
    return user


# ============================================================
# Login / Logout
# ============================================================

@router.get("/", response_class=HTMLResponse)
async def raiz_web(
    request: Request,
    user: Usuario | None = Depends(usuario_da_sessao),
):
    if user is None:
        return RedirectResponse(url="/login", status_code=302)
    return RedirectResponse(url="/dashboard", status_code=302)


@router.get("/login", response_class=HTMLResponse)
async def pagina_login(request: Request):
    return templates.TemplateResponse(
        request, "login.html", {"erro": None}
    )


@router.get("/signup", response_class=HTMLResponse)
async def pagina_signup(request: Request):
    """Cadastro público (Fase 5)."""
    return templates.TemplateResponse(
        request, "signup.html", {"user": None, "erro": None},
    )


@router.post("/signup", response_class=HTMLResponse)
async def fazer_signup(
    request: Request,
    org_nome:      str = Form(...),
    login:         str = Form(...),
    senha:         str = Form(...),
    email:         str = Form(default=""),
    nome_exibicao: str = Form(default=""),
    db: AsyncSession = Depends(get_db_async),
):
    """
    Cria org + admin + faz autologin (cookie de sessão) + redireciona pro onboarding.
    """
    try:
        org, admin = await signup_service.criar_org_e_admin(
            db,
            org_nome=org_nome,
            login=login,
            senha=senha,
            email=email or None,
            nome_exibicao=nome_exibicao or None,
        )
    except signup_service.SignupError as e:
        return templates.TemplateResponse(
            request, "signup.html",
            {"user": None, "erro": str(e)}, status_code=400,
        )
    except IntegrityError:
        await db.rollback()
        return templates.TemplateResponse(
            request, "signup.html",
            {"user": None, "erro": "Esse nome ou login já está em uso. Tente outro."},
            status_code=409,
        )

    # Autologin via cookie de sessão
    token = criar_access_token(usuario_id=admin.id, org_id=org.id, papel=admin.papel)
    resp = RedirectResponse(url="/onboarding", status_code=302)
    resp.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=settings.jwt_access_token_expire_minutes * 60,
        httponly=True,
        samesite="lax",
        secure=settings.is_production,
    )
    return resp


@router.post("/login", response_class=HTMLResponse)
async def fazer_login(
    request: Request,
    login:    str = Form(...),
    senha:    str = Form(...),
    org_slug: str = Form(...),
    db: AsyncSession = Depends(get_db_async),
):
    result = await db.execute(
        select(Usuario)
        .join(Organizacao)
        .where(
            Usuario.login == login,
            Organizacao.slug == org_slug,
            Usuario.ativo.is_(True),
        )
    )
    user = result.scalar_one_or_none()

    if user is None or not verificar_senha(senha, user.senha_hash):
        return templates.TemplateResponse(
            request, "login.html",
            {"erro": "Login, organização ou senha inválidos."},
            status_code=401,
        )

    user.ultimo_login = datetime.now(tz=timezone.utc)
    await db.commit()

    token = criar_access_token(usuario_id=user.id, org_id=user.org_id, papel=user.papel)
    resp = RedirectResponse(url="/dashboard", status_code=302)
    resp.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=settings.jwt_access_token_expire_minutes * 60,
        httponly=True,
        samesite="lax",
        secure=settings.is_production,
    )
    return resp


@router.get("/logout")
async def logout():
    resp = RedirectResponse(url="/login", status_code=302)
    resp.delete_cookie(COOKIE_NAME)
    return resp


# ============================================================
# Dashboard
# ============================================================

@router.get("/agentes/baixar", response_class=HTMLResponse)
async def pagina_baixar_agente(
    request: Request,
    user: Usuario = Depends(exigir_login),
):
    """Instruções de instalação do agente local (Fase 6)."""
    return templates.TemplateResponse(
        request, "agente_baixar.html", {"user": user},
    )


@router.get("/onboarding", response_class=HTMLResponse)
async def pagina_onboarding(
    request: Request,
    user: Usuario = Depends(exigir_login),
    db: AsyncSession = Depends(get_db_async),
):
    """
    Checklist de 4 passos: credenciais ML, agente, canal, grupo.
    Marca onboarding_completo=True quando todos OK (idempotente).
    """
    org = await db.get(Organizacao, user.org_id)

    # Avalia cada passo
    tem_cred = bool(user.usuario_ml and user.senha_ml_cifrada)
    total_agentes = await db.scalar(
        select(func.count()).select_from(Agente).where(Agente.org_id == user.org_id)
    ) or 0
    total_canais = await db.scalar(
        select(func.count()).select_from(Canal).where(
            Canal.org_id == user.org_id, Canal.ativo.is_(True),
        )
    ) or 0
    total_grupos = await db.scalar(
        select(func.count()).select_from(Grupo).where(
            Grupo.org_id == user.org_id, Grupo.ativo.is_(True),
        )
    ) or 0

    # Fase 9.9: passo de credenciais só aparece pra plano que permite.
    # No plano free, esse passo é skipado (postagem usa afiliado do admin).
    pode_credenciais = bool(org and org.plano and org.plano.pode_cadastrar_afiliado)

    passos: dict[str, dict] = {
        "agente":      {"ok": total_agentes > 0, "total": total_agentes},
        "canal":       {"ok": total_canais > 0,  "total": total_canais},
        "grupo":       {"ok": total_grupos > 0,  "total": total_grupos},
    }
    if pode_credenciais:
        passos = {"credenciais": {"ok": tem_cred}, **passos}
    completo = all(p["ok"] for p in passos.values())

    # Persiste flag (idempotente)
    if completo and not user.onboarding_completo:
        user.onboarding_completo = True
        await db.commit()

    return templates.TemplateResponse(
        request, "onboarding.html",
        {"user": user, "org": org, "passos": passos, "completo": completo},
    )


@router.get("/planos", response_class=HTMLResponse)
async def pagina_planos(
    request: Request,
    user: Usuario = Depends(exigir_login),
    db: AsyncSession = Depends(get_db_async),
):
    """
    Tabela comparativa dos planos (Fase 11 parcial).

    Mostra free / pro / business lado a lado, com limites e flags. O plano
    atual do user vem destacado. Botão "Fazer upgrade" é placeholder
    (billing real fica pra fase futura).
    """
    org = await db.get(Organizacao, user.org_id)
    result = await db.execute(
        select(Plano).where(Plano.ativo.is_(True)).order_by(Plano.preco_mensal_brl)
    )
    planos = result.scalars().all()
    return templates.TemplateResponse(
        request, "planos.html",
        {"user": user, "org": org, "planos": planos,
         "plano_atual_id": org.plano_id if org else None},
    )


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    user: Usuario = Depends(exigir_login),
    db: AsyncSession = Depends(get_db_async),
):
    org = await db.get(Organizacao, user.org_id)

    counts = {
        "agentes":       await db.scalar(
            select(func.count()).select_from(Agente).where(Agente.org_id == user.org_id)
        ) or 0,
        "agentes_online": await db.scalar(
            select(func.count()).select_from(Agente).where(
                Agente.org_id == user.org_id, Agente.online.is_(True),
            )
        ) or 0,
        "canais":         await db.scalar(
            select(func.count()).select_from(Canal).where(Canal.org_id == user.org_id)
        ) or 0,
        "grupos":         await db.scalar(
            select(func.count()).select_from(Grupo).where(Grupo.org_id == user.org_id)
        ) or 0,
        "tarefas_pendentes": await db.scalar(
            select(func.count()).select_from(Tarefa).where(
                Tarefa.org_id == user.org_id,
                Tarefa.status == "pendente",
            )
        ) or 0,
    }

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {"user": user, "org": org, "counts": counts},
    )


# ============================================================
# Agentes (lista + criar)
# ============================================================

@router.get("/agentes", response_class=HTMLResponse)
async def lista_agentes(
    request: Request,
    user: Usuario = Depends(exigir_login),
    db: AsyncSession = Depends(get_db_async),
    token_recem: str | None = None,
):
    """Lista agentes. Se vier ?token_recem=..., exibe banner com o token criado."""
    result = await db.execute(
        select(Agente).where(Agente.org_id == user.org_id).order_by(Agente.criado_em.desc())
    )
    agentes = list(result.scalars().all())

    # Lista de usuários da org (pro dropdown do form)
    usuarios = list((await db.execute(
        select(Usuario).where(Usuario.org_id == user.org_id, Usuario.ativo.is_(True))
        .order_by(Usuario.login)
    )).scalars().all())

    return templates.TemplateResponse(
        request, "agentes.html",
        {
            "user": user,
            "agentes": agentes,
            "usuarios": usuarios,
            "token_recem": token_recem,
            "pode_criar": user.eh_admin,
        },
    )


@router.post("/agentes/novo", response_class=HTMLResponse)
async def criar_agente_form(
    request: Request,
    nome:       str = Form(...),
    usuario_id: int = Form(...),
    admin: Usuario = Depends(exigir_admin),
    db: AsyncSession = Depends(get_db_async),
):
    """Form de criar agente. Redireciona com ?token_recem=... pra mostrar uma vez."""
    try:
        agente, token = await agente_service.criar_agente(
            db,
            org_id=admin.org_id,
            usuario_id=usuario_id,
            nome=nome,
        )
    except agente_service.AgenteServiceError as e:
        # Recarrega lista mostrando erro
        return templates.TemplateResponse(
            request, "agentes.html",
            {"user": admin, "erro": str(e), "agentes": [], "usuarios": [], "pode_criar": True},
            status_code=400,
        )
    # Redireciona com token na URL pra exibir uma vez (o token não fica salvo)
    return RedirectResponse(
        url=f"/agentes?token_recem={token}",
        status_code=302,
    )


# ============================================================
# Canais
# ============================================================

@router.get("/canais", response_class=HTMLResponse)
async def lista_canais(
    request: Request,
    user: Usuario = Depends(exigir_login),
    db: AsyncSession = Depends(get_db_async),
):
    result = await db.execute(
        select(Canal).where(Canal.org_id == user.org_id).order_by(Canal.criado_em.desc())
    )
    canais = list(result.scalars().all())

    # Pra dropdown do form: agentes e usuários
    agentes = list((await db.execute(
        select(Agente).where(Agente.org_id == user.org_id, Agente.ativo.is_(True))
    )).scalars().all())

    return templates.TemplateResponse(
        request, "canais.html",
        {
            "user": user,
            "canais": canais,
            "agentes": agentes,
            "pode_criar": user.eh_admin,
        },
    )


@router.post("/canais/novo", response_class=HTMLResponse)
async def criar_canal_form(
    request: Request,
    tipo:       str = Form(...),
    nome:       str = Form(...),
    agente_id:  int | None = Form(default=None),
    bot_token:  str | None = Form(default=None),
    admin: Usuario = Depends(exigir_admin),
    db: AsyncSession = Depends(get_db_async),
):
    """Cria canal. Tipo determina o config:
        whatsapp_agente → precisa agente_id
        telegram_bot    → precisa bot_token (validação async via Celery)
    """
    config: dict = {}
    if tipo == "whatsapp_agente":
        if not agente_id:
            raise HTTPException(status_code=400, detail="agente_id obrigatório")
        agente = await db.get(Agente, agente_id)
        if agente is None or agente.org_id != admin.org_id:
            raise HTTPException(status_code=400, detail="Agente inválido")
        config = {"agente_id": agente_id}
    elif tipo == "telegram_bot":
        if not bot_token or ":" not in bot_token:
            raise HTTPException(status_code=400,
                                detail="bot_token inválido (formato esperado: 123456:ABC...)")
        config = {"bot_token": bot_token.strip()}
    else:
        raise HTTPException(status_code=400, detail="Tipo inválido")

    canal = Canal(
        org_id=admin.org_id,
        tipo=tipo,
        nome=nome,
        config=config,
        ativo=True,
    )
    db.add(canal)
    await db.commit()
    await db.refresh(canal)

    # Dispara validação assíncrona pro Telegram
    if tipo == "telegram_bot":
        try:
            # Import tardio evita ciclo
            from app.workers.celery_app import celery_app
            celery_app.send_task("validar_canal_telegram", args=[canal.id])
        except Exception as e:
            # Validação é opcional — não bloqueia criação
            log_msg = f"validacao Telegram não disparada: {e}"
            print(log_msg)  # vai pro stdout do uvicorn

    return RedirectResponse(url="/canais", status_code=302)


# ============================================================
# Grupos
# ============================================================

@router.get("/grupos", response_class=HTMLResponse)
async def lista_grupos(
    request: Request,
    user: Usuario = Depends(exigir_login),
    db: AsyncSession = Depends(get_db_async),
):
    result = await db.execute(
        select(Grupo).where(Grupo.org_id == user.org_id).order_by(Grupo.criado_em.desc())
    )
    grupos = list(result.scalars().all())

    # Canais pra dropdown
    canais = list((await db.execute(
        select(Canal).where(Canal.org_id == user.org_id, Canal.ativo.is_(True))
    )).scalars().all())

    # Mapa canal_id → canal pra mostrar nome do canal nas linhas
    canais_map = {c.id: c for c in canais}

    return templates.TemplateResponse(
        request, "grupos.html",
        {
            "user": user,
            "grupos": grupos,
            "canais": canais,
            "canais_map": canais_map,
            "pode_criar": user.eh_admin,
        },
    )


@router.post("/grupos/novo", response_class=HTMLResponse)
async def criar_grupo_form(
    request: Request,
    canal_id:      int = Form(...),
    nome:          str = Form(...),
    identificador: str = Form(...),
    admin: Usuario = Depends(exigir_admin),
    db: AsyncSession = Depends(get_db_async),
):
    # Verifica limite do plano
    pode, msg = await limites.pode_criar_grupo(db, org_id=admin.org_id)
    if not pode:
        from urllib.parse import quote
        return RedirectResponse(url=f"/grupos?erro={quote(msg)}", status_code=302)

    canal = await db.get(Canal, canal_id)
    if canal is None or canal.org_id != admin.org_id:
        raise HTTPException(status_code=400, detail="Canal inválido")

    grupo = Grupo(
        org_id=admin.org_id,
        canal_id=canal_id,
        nome=nome,
        identificador=identificador,
        ativo=True,
    )
    db.add(grupo)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Já existe grupo com esse identificador neste canal",
        ) from None
    return RedirectResponse(url="/grupos", status_code=302)


# ============================================================
# Tarefas (lista + detalhe + postar manual + cancelar)
# ============================================================

@router.get("/tarefas", response_class=HTMLResponse)
async def lista_tarefas(
    request: Request,
    user: Usuario = Depends(exigir_login),
    db: AsyncSession = Depends(get_db_async),
    status_filtro: str | None = None,
):
    base = select(Tarefa).where(Tarefa.org_id == user.org_id)
    if status_filtro:
        base = base.where(Tarefa.status == status_filtro)

    result = await db.execute(base.order_by(Tarefa.criado_em.desc()).limit(100))
    tarefas = list(result.scalars().all())

    # Pra form de postagem manual
    grupos = list((await db.execute(
        select(Grupo).where(Grupo.org_id == user.org_id, Grupo.ativo.is_(True))
        .order_by(Grupo.nome)
    )).scalars().all())

    return templates.TemplateResponse(
        request, "tarefas.html",
        {
            "user": user,
            "tarefas": tarefas,
            "grupos": grupos,
            "status_filtro": status_filtro,
        },
    )


@router.get("/tarefas/{tarefa_id}", response_class=HTMLResponse)
async def detalhe_tarefa(
    tarefa_id: int,
    request: Request,
    user: Usuario = Depends(exigir_login),
    db: AsyncSession = Depends(get_db_async),
):
    tarefa = await db.get(Tarefa, tarefa_id)
    if tarefa is None or tarefa.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada")

    return templates.TemplateResponse(
        request, "tarefa_detalhe.html",
        {"user": user, "tarefa": tarefa},
    )


@router.post("/tarefas/postar", response_class=HTMLResponse)
async def postar_form(
    request: Request,
    grupo_id:   int = Form(...),
    texto:      str = Form(...),
    imagem_url: str | None = Form(default=None),
    user: Usuario = Depends(exigir_login),
    db: AsyncSession = Depends(get_db_async),
):
    """Form de enfileirar postagem manual."""
    try:
        await dispatcher.enfileirar_postagem(
            db,
            org_id=user.org_id,
            grupo_id=grupo_id,
            texto=texto,
            imagem_url=imagem_url or None,
            criado_por_usuario_id=user.id,
        )
    except dispatcher.DispatcherError as e:
        # Volta pra lista mostrando erro
        return RedirectResponse(
            url=f"/tarefas?erro={e}",
            status_code=302,
        )
    return RedirectResponse(url="/tarefas", status_code=302)


@router.post("/tarefas/{tarefa_id}/cancelar", response_class=HTMLResponse)
async def cancelar_tarefa(
    tarefa_id: int,
    user: Usuario = Depends(exigir_login),
    db: AsyncSession = Depends(get_db_async),
):
    tarefa = await db.get(Tarefa, tarefa_id)
    if tarefa is None or tarefa.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada")
    if tarefa.status in ("pendente", "processando"):
        tarefa.status = "cancelada"
        await db.commit()
    return RedirectResponse(url="/tarefas", status_code=302)


# ============================================================
# Usuários
# ============================================================

@router.get("/usuarios", response_class=HTMLResponse)
async def lista_usuarios(
    request: Request,
    user: Usuario = Depends(exigir_login),
    db: AsyncSession = Depends(get_db_async),
):
    result = await db.execute(
        select(Usuario).where(Usuario.org_id == user.org_id)
        .order_by(Usuario.criado_em.desc())
    )
    usuarios = list(result.scalars().all())

    return templates.TemplateResponse(
        request, "usuarios.html",
        {
            "user": user,
            "usuarios": usuarios,
            "pode_criar": user.eh_admin,
        },
    )


@router.get("/usuarios/{usuario_id}/credenciais", response_class=HTMLResponse)
async def form_credenciais(
    usuario_id: int,
    request: Request,
    user: Usuario = Depends(exigir_login),
    db: AsyncSession = Depends(get_db_async),
    mensagem: str | None = None,
    erro: str | None = None,
):
    """Form pra cadastrar credenciais de plataforma (Fase 4b.1)."""
    target = await db.get(Usuario, usuario_id)
    if target is None or target.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    # Só admin ou o próprio dono
    if not user.eh_admin and target.id != user.id:
        raise HTTPException(status_code=403,
            detail="Só admin ou o próprio dono pode editar essas credenciais")
    return templates.TemplateResponse(
        request, "usuario_credenciais.html",
        {"user": user, "target": target, "mensagem": mensagem, "erro": erro},
    )


@router.post("/usuarios/{usuario_id}/credenciais", response_class=HTMLResponse)
async def salvar_credenciais(
    usuario_id: int,
    request: Request,
    usuario_ml: str = Form(default=""),
    senha_ml: str = Form(default=""),
    apagar_senha_ml: str = Form(default=""),
    user: Usuario = Depends(exigir_login),
    db: AsyncSession = Depends(get_db_async),
):
    target = await db.get(Usuario, usuario_id)
    if target is None or target.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    if not user.eh_admin and target.id != user.id:
        raise HTTPException(status_code=403, detail="Acesso negado")

    target.usuario_ml = usuario_ml.strip() or None

    # Senha: 3 caminhos
    # 1. Checkbox "apagar" marcado → limpa
    # 2. Campo senha preenchido → cifra e substitui
    # 3. Senha vazia e sem apagar → mantém atual (não mexe)
    if apagar_senha_ml:
        target.set_senha_ml(None)
    elif senha_ml.strip():
        target.set_senha_ml(senha_ml)

    await db.commit()
    return RedirectResponse(
        url=f"/usuarios/{usuario_id}/credenciais?mensagem=Credenciais+salvas",
        status_code=302,
    )


@router.post("/usuarios/novo", response_class=HTMLResponse)
async def criar_usuario_form(
    request: Request,
    login:         str = Form(...),
    senha:         str = Form(...),
    papel:         str = Form(...),
    nome_exibicao: str = Form(""),
    email:         str = Form(""),
    admin: Usuario = Depends(exigir_admin),
    db: AsyncSession = Depends(get_db_async),
):
    # Verifica limite do plano
    pode, msg = await limites.pode_criar_usuario(db, org_id=admin.org_id)
    if not pode:
        from urllib.parse import quote
        return RedirectResponse(
            url=f"/usuarios?erro={quote(msg)}",
            status_code=302,
        )

    novo = Usuario(
        org_id=admin.org_id,
        login=login,
        senha_hash=hash_senha(senha),
        papel=papel,
        nome_exibicao=nome_exibicao or login,
        email=email or None,
        ativo=True,
        onboarding_completo=False,
    )
    db.add(novo)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        return RedirectResponse(
            url="/usuarios?erro=Login+ja+existe+nesta+organizacao",
            status_code=302,
        )
    return RedirectResponse(url="/usuarios", status_code=302)


# ============================================================
# Produtos
# ============================================================

@router.get("/produtos", response_class=HTMLResponse)
async def lista_produtos(
    request: Request,
    user: Usuario = Depends(exigir_login),
    db: AsyncSession = Depends(get_db_async),
    nicho_id: int | None = None,
    plataforma: str | None = None,
    bloqueado: int | None = None,   # 1/0/None
    busca: str | None = None,
):
    base = select(Produto).where(Produto.org_id == user.org_id)

    if plataforma:
        base = base.where(Produto.plataforma == plataforma)
    if bloqueado == 1:
        base = base.where(Produto.bloqueado.is_(True))
    elif bloqueado == 0:
        base = base.where(Produto.bloqueado.is_(False))
    if busca:
        base = base.where(Produto.nome.ilike(f"%{busca}%"))
    if nicho_id is not None:
        subq = select(ProdutoNicho.produto_id).where(ProdutoNicho.nicho_id == nicho_id)
        base = base.where(Produto.id.in_(subq))

    produtos = list((await db.execute(
        base.order_by(Produto.atualizado_em.desc()).limit(100)
    )).scalars().all())

    # Nichos pra filtros do form
    nichos = list((await db.execute(
        select(Nicho).where(Nicho.ativo.is_(True)).order_by(Nicho.ordem, Nicho.label)
    )).scalars().all())
    nichos_map = {n.id: n for n in nichos}

    # Carrega nichos vinculados em batch
    nichos_por_prod: dict[int, list[Nicho]] = {}
    if produtos:
        ids = [p.id for p in produtos]
        rows = (await db.execute(
            select(ProdutoNicho.produto_id, ProdutoNicho.nicho_id)
            .where(ProdutoNicho.produto_id.in_(ids))
        )).all()
        for pid, nid in rows:
            n = nichos_map.get(nid)
            if n:
                nichos_por_prod.setdefault(pid, []).append(n)

    return templates.TemplateResponse(
        request, "produtos.html",
        {
            "user": user,
            "produtos": produtos,
            "nichos": nichos,
            "nichos_por_prod": nichos_por_prod,
            "filtro_nicho_id": nicho_id,
            "filtro_plataforma": plataforma,
            "filtro_bloqueado": bloqueado,
            "filtro_busca": busca or "",
            "pode_criar": user.eh_admin,
        },
    )


@router.get("/produtos/novo", response_class=HTMLResponse)
async def novo_produto_form(
    request: Request,
    admin: Usuario = Depends(exigir_admin),
    db: AsyncSession = Depends(get_db_async),
):
    nichos = list((await db.execute(
        select(Nicho).where(Nicho.ativo.is_(True)).order_by(Nicho.ordem, Nicho.label)
    )).scalars().all())
    return templates.TemplateResponse(
        request, "produto_form.html",
        {"user": admin, "nichos": nichos, "produto": None, "erro": None},
    )


@router.post("/produtos/novo", response_class=HTMLResponse)
async def criar_produto_form(
    request: Request,
    plataforma: str = Form(...),
    item_id:    str = Form(...),
    nome:       str = Form(...),
    preco:      str = Form(...),
    preco_orig: str = Form(""),
    desconto:   str = Form(""),
    comissao:   str = Form(""),
    categoria:  str = Form(""),
    url_canonica: str = Form(""),
    url_afiliado: str = Form(""),
    foto_url:   str = Form(""),
    nichos_ids: list[int] = Form(default=[]),
    admin: Usuario = Depends(exigir_admin),
    db: AsyncSession = Depends(get_db_async),
):
    def _f(v: str) -> float | None:
        v = v.replace(",", ".").strip()
        if not v:
            return None
        try:
            return float(v)
        except ValueError:
            return None

    preco_val = _f(preco)
    if preco_val is None or preco_val < 0:
        nichos = list((await db.execute(select(Nicho).where(Nicho.ativo.is_(True)))).scalars().all())
        return templates.TemplateResponse(
            request, "produto_form.html",
            {"user": admin, "nichos": nichos, "produto": None,
             "erro": "Preço inválido — use formato: 89.90"},
            status_code=400,
        )

    novo = Produto(
        org_id=admin.org_id,
        plataforma=plataforma.strip().lower(),
        item_id=item_id.strip(),
        nome=nome.strip(),
        categoria=categoria.strip() or None,
        preco=preco_val,
        preco_orig=_f(preco_orig),
        desconto=_f(desconto),
        comissao=_f(comissao),
        url_canonica=url_canonica.strip() or None,
        url_afiliado=url_afiliado.strip() or None,
        foto_url=foto_url.strip() or None,
        fonte="manual",
    )
    db.add(novo)
    try:
        await db.commit()
        await db.refresh(novo)
    except IntegrityError:
        await db.rollback()
        nichos = list((await db.execute(select(Nicho).where(Nicho.ativo.is_(True)))).scalars().all())
        return templates.TemplateResponse(
            request, "produto_form.html",
            {"user": admin, "nichos": nichos, "produto": None,
             "erro": "Já existe produto com essa (plataforma, item_id)"},
            status_code=409,
        )

    for nid in nichos_ids:
        db.add(ProdutoNicho(produto_id=novo.id, nicho_id=nid))
    await db.commit()

    return RedirectResponse(url="/produtos", status_code=302)


@router.post("/produtos/{produto_id}/bloquear", response_class=HTMLResponse)
async def bloquear_produto(
    produto_id: int,
    admin: Usuario = Depends(exigir_admin),
    db: AsyncSession = Depends(get_db_async),
):
    p = await db.get(Produto, produto_id)
    if p is None or p.org_id != admin.org_id:
        raise HTTPException(status_code=404, detail="Produto não encontrado")
    p.bloqueado = not p.bloqueado
    await db.commit()
    return RedirectResponse(url="/produtos", status_code=302)


@router.get("/produtos/import-csv", response_class=HTMLResponse)
async def pagina_import_csv(
    request: Request,
    admin: Usuario = Depends(exigir_admin),
):
    return templates.TemplateResponse(
        request, "produtos_import.html",
        {"user": admin, "resultado": None, "erro": None},
    )


@router.post("/produtos/import-csv", response_class=HTMLResponse)
async def upload_csv(
    request: Request,
    arquivo: UploadFile = File(...),
    admin: Usuario = Depends(exigir_admin),
    db: AsyncSession = Depends(get_db_async),
):
    """Form-based upload de CSV. Reaproveita a lógica do endpoint REST."""
    from app.api.v1.endpoints.produtos import importar_csv as endpoint_importar
    try:
        resultado = await endpoint_importar(arquivo=arquivo, admin=admin, db=db)
    except HTTPException as e:
        return templates.TemplateResponse(
            request, "produtos_import.html",
            {"user": admin, "resultado": None, "erro": e.detail},
            status_code=e.status_code,
        )
    return templates.TemplateResponse(
        request, "produtos_import.html",
        {"user": admin, "resultado": resultado, "erro": None},
    )


# ============================================================
# Templates de mensagem
# ============================================================

@router.get("/templates", response_class=HTMLResponse)
async def lista_templates(
    request: Request,
    user: Usuario = Depends(exigir_login),
    db: AsyncSession = Depends(get_db_async),
):
    tpls = list((await db.execute(
        select(TemplateMensagem)
        .where(TemplateMensagem.org_id == user.org_id)
        .order_by(TemplateMensagem.nicho_id.is_(None), TemplateMensagem.ordem, TemplateMensagem.id)
    )).scalars().all())

    nichos = list((await db.execute(
        select(Nicho).where(Nicho.ativo.is_(True)).order_by(Nicho.ordem, Nicho.label)
    )).scalars().all())
    nichos_map = {n.id: n for n in nichos}

    return templates.TemplateResponse(
        request, "templates.html",
        {
            "user": user,
            "templates_lista": tpls,
            "nichos": nichos,
            "nichos_map": nichos_map,
            "pode_criar": user.eh_admin,
        },
    )


@router.get("/templates/novo", response_class=HTMLResponse)
async def novo_template_form(
    request: Request,
    admin: Usuario = Depends(exigir_admin),
    db: AsyncSession = Depends(get_db_async),
    nicho_id: int | None = None,
):
    nichos = list((await db.execute(
        select(Nicho).where(Nicho.ativo.is_(True)).order_by(Nicho.ordem, Nicho.label)
    )).scalars().all())
    return templates.TemplateResponse(
        request, "template_form.html",
        {"user": admin, "nichos": nichos, "template": None,
         "nicho_id_pre": nicho_id, "erro": None},
    )


@router.post("/templates/novo", response_class=HTMLResponse)
async def criar_template_form(
    request: Request,
    nome:     str = Form(...),
    texto:    str = Form(...),
    nicho_id: str = Form(""),    # vazio = template padrão
    ordem:    int = Form(0),
    admin: Usuario = Depends(exigir_admin),
    db: AsyncSession = Depends(get_db_async),
):
    nicho_int: int | None = None
    if nicho_id.strip():
        try:
            nicho_int = int(nicho_id)
        except ValueError:
            nicho_int = None

    novo = TemplateMensagem(
        org_id=admin.org_id,
        nicho_id=nicho_int,
        nome=nome.strip(),
        texto=texto,
        ativo=True,
        ordem=ordem,
    )
    db.add(novo)
    await db.commit()
    return RedirectResponse(url="/templates", status_code=302)


@router.post("/templates/{template_id}/excluir", response_class=HTMLResponse)
async def excluir_template_form(
    template_id: int,
    admin: Usuario = Depends(exigir_admin),
    db: AsyncSession = Depends(get_db_async),
):
    t = await db.get(TemplateMensagem, template_id)
    if t and t.org_id == admin.org_id:
        await db.delete(t)
        await db.commit()
    return RedirectResponse(url="/templates", status_code=302)


# ============================================================
# Lote (botão "rodar lote agora")
# ============================================================

@router.get("/lote", response_class=HTMLResponse)
async def pagina_lote(
    request: Request,
    user: Usuario = Depends(exigir_login),
    db: AsyncSession = Depends(get_db_async),
):
    """Página com botão 'rodar lote'."""
    # Conta o que tem disponível
    from sqlalchemy import func as sqlfunc
    counts = {
        "produtos": await db.scalar(
            select(sqlfunc.count()).select_from(Produto)
            .where(Produto.org_id == user.org_id, Produto.bloqueado.is_(False))
        ) or 0,
        "templates": await db.scalar(
            select(sqlfunc.count()).select_from(TemplateMensagem)
            .where(TemplateMensagem.org_id == user.org_id, TemplateMensagem.ativo.is_(True))
        ) or 0,
        "grupos": await db.scalar(
            select(sqlfunc.count()).select_from(Grupo)
            .where(Grupo.org_id == user.org_id, Grupo.ativo.is_(True))
        ) or 0,
    }

    return templates.TemplateResponse(
        request, "lote.html",
        {"user": user, "counts": counts, "resultado": None},
    )


@router.post("/lote/rodar", response_class=HTMLResponse)
async def rodar_lote_form(
    request: Request,
    max_produtos: int = Form(default=10),
    canal_tipo: str = Form(default=""),
    user: Usuario = Depends(exigir_login),
    db: AsyncSession = Depends(get_db_async),
):
    resultado = await lote_service.rodar_lote(
        db,
        org_id=user.org_id,
        max_produtos=max(1, min(50, max_produtos)),
        canal_tipo=canal_tipo.strip() or None,
        criado_por_usuario_id=user.id,
        usuario=user,
    )
    from sqlalchemy import func as sqlfunc
    counts = {
        "produtos": await db.scalar(
            select(sqlfunc.count()).select_from(Produto)
            .where(Produto.org_id == user.org_id, Produto.bloqueado.is_(False))
        ) or 0,
        "templates": await db.scalar(
            select(sqlfunc.count()).select_from(TemplateMensagem)
            .where(TemplateMensagem.org_id == user.org_id, TemplateMensagem.ativo.is_(True))
        ) or 0,
        "grupos": await db.scalar(
            select(sqlfunc.count()).select_from(Grupo)
            .where(Grupo.org_id == user.org_id, Grupo.ativo.is_(True))
        ) or 0,
    }
    return templates.TemplateResponse(
        request, "lote.html",
        {"user": user, "counts": counts, "resultado": resultado},
    )


# ============================================================
# Buscas ML
# ============================================================

@router.get("/buscas", response_class=HTMLResponse)
async def lista_buscas(
    request: Request,
    user: Usuario = Depends(exigir_login),
    db: AsyncSession = Depends(get_db_async),
    mensagem: str | None = None,
    erro: str | None = None,
):
    buscas = list((await db.execute(
        select(BuscaML).where(BuscaML.org_id == user.org_id)
        .order_by(BuscaML.criado_em.desc())
    )).scalars().all())
    return templates.TemplateResponse(
        request, "buscas.html",
        {
            "user": user,
            "buscas": buscas,
            "pode_admin": user.eh_admin,
            "mensagem": mensagem,
            "erro": erro,
        },
    )


@router.get("/buscas/nova", response_class=HTMLResponse)
async def nova_busca_form(
    request: Request,
    user: Usuario = Depends(exigir_login),
    db: AsyncSession = Depends(get_db_async),
):
    agentes = list((await db.execute(
        select(Agente).where(Agente.org_id == user.org_id).order_by(Agente.id)
    )).scalars().all())
    return templates.TemplateResponse(
        request, "busca_form.html",
        {"user": user, "agentes": agentes, "erro": None},
    )


@router.post("/buscas/nova", response_class=HTMLResponse)
async def criar_busca_form(
    request: Request,
    nome:    str = Form(...),
    entrada: str = Form(...),
    max_paginas:  int = Form(default=3),
    max_produtos: int = Form(default=50),
    agente_id: str = Form(default=""),
    intervalo_minutos: str = Form(default=""),
    ativo: str = Form(default=""),
    user: Usuario = Depends(exigir_login),
    db: AsyncSession = Depends(get_db_async),
):
    # Parse agente_id
    aid: int | None = None
    if agente_id.strip():
        try:
            aid = int(agente_id)
            ag = await db.get(Agente, aid)
            if ag is None or ag.org_id != user.org_id:
                aid = None
        except ValueError:
            aid = None

    # Parse intervalo
    intervalo: int | None = None
    if intervalo_minutos.strip():
        try:
            intervalo = max(15, int(intervalo_minutos))
        except ValueError:
            intervalo = None

    agora = datetime.now(tz=timezone.utc)
    nova = BuscaML(
        org_id=user.org_id,
        criado_por_usuario_id=user.id,
        agente_id=aid,
        nome=nome.strip()[:150],
        entrada=entrada.strip()[:2000],
        max_paginas=max(1, min(20, max_paginas)),
        max_produtos=max(1, min(500, max_produtos)),
        intervalo_minutos=intervalo,
        ativo=bool(ativo),
        proxima_exec_em=agora if intervalo else None,
    )
    db.add(nova)
    await db.commit()
    return RedirectResponse(url="/buscas?mensagem=Busca+criada", status_code=302)


@router.post("/buscas/{busca_id}/rodar", response_class=HTMLResponse)
async def rodar_busca_form(
    busca_id: int,
    user: Usuario = Depends(exigir_login),
    db: AsyncSession = Depends(get_db_async),
):
    try:
        tarefa = await busca_service.enfileirar_execucao(
            db, busca_id=busca_id, org_id=user.org_id,
            criado_por_usuario_id=user.id,
        )
        return RedirectResponse(
            url=f"/buscas?mensagem=Busca+enfileirada+(tarefa+%23{tarefa.id})",
            status_code=302,
        )
    except busca_service.BuscaServiceError as e:
        return RedirectResponse(url=f"/buscas?erro={e}", status_code=302)


@router.post("/buscas/{busca_id}/excluir", response_class=HTMLResponse)
async def excluir_busca_form(
    busca_id: int,
    admin: Usuario = Depends(exigir_admin),
    db: AsyncSession = Depends(get_db_async),
):
    b = await db.get(BuscaML, busca_id)
    if b and b.org_id == admin.org_id:
        await db.delete(b)
        await db.commit()
    return RedirectResponse(url="/buscas?mensagem=Busca+removida", status_code=302)


# ============================================================
# Mappings categoria_ml → nicho_id
# ============================================================

@router.get("/mappings-nichos", response_class=HTMLResponse)
async def lista_mappings(
    request: Request,
    user: Usuario = Depends(exigir_login),
    db: AsyncSession = Depends(get_db_async),
    mensagem: str | None = None,
    erro: str | None = None,
):
    """Lista mappings + form + categorias 'vistas mas não mapeadas'."""
    # Mappings da org
    mappings = list((await db.execute(
        select(NichoCategoriaML).where(NichoCategoriaML.org_id == user.org_id)
        .order_by(NichoCategoriaML.categoria_ml)
    )).scalars().all())

    # Nichos pra dropdown + lookup pra exibir nome
    nichos = list((await db.execute(
        select(Nicho).where(Nicho.ativo.is_(True)).order_by(Nicho.ordem, Nicho.label)
    )).scalars().all())
    nichos_map = {n.id: n for n in nichos}

    # Categorias vistas em produtos da org, ainda não mapeadas
    mapeadas = {m.categoria_ml.lower() for m in mappings}
    cat_rows = await db.execute(
        select(Produto.categoria).where(
            Produto.org_id == user.org_id,
            Produto.categoria.is_not(None),
        ).distinct()
    )
    todas_cats = {r[0] for r in cat_rows.all() if r[0]}
    pendentes = sorted(c for c in todas_cats if c.lower() not in mapeadas)

    return templates.TemplateResponse(
        request, "mappings_nichos.html",
        {
            "user": user,
            "mappings": mappings,
            "nichos": nichos,
            "nichos_map": nichos_map,
            "pendentes": pendentes,
            "pode_admin": user.eh_admin,
            "mensagem": mensagem,
            "erro": erro,
        },
    )


@router.post("/mappings-nichos/novo", response_class=HTMLResponse)
async def criar_mapping_form(
    request: Request,
    categoria_ml: str = Form(...),
    nicho_id:     int = Form(...),
    admin: Usuario = Depends(exigir_admin),
    db: AsyncSession = Depends(get_db_async),
):
    cat = categoria_ml.strip()
    if not cat:
        return RedirectResponse(url="/mappings-nichos?erro=Categoria+vazia", status_code=302)

    nicho = await db.get(Nicho, nicho_id)
    if nicho is None:
        return RedirectResponse(url="/mappings-nichos?erro=Nicho+invalido", status_code=302)

    novo = NichoCategoriaML(
        org_id=admin.org_id,
        categoria_ml=cat,
        nicho_id=nicho_id,
        criado_em=datetime.now(tz=timezone.utc),
    )
    db.add(novo)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        return RedirectResponse(
            url=f"/mappings-nichos?erro=Ja+existe+mapping+pra+'{cat[:30]}'",
            status_code=302,
        )
    return RedirectResponse(url="/mappings-nichos?mensagem=Mapping+criado", status_code=302)


@router.post("/mappings-nichos/{mapping_id}/excluir", response_class=HTMLResponse)
async def excluir_mapping_form(
    mapping_id: int,
    admin: Usuario = Depends(exigir_admin),
    db: AsyncSession = Depends(get_db_async),
):
    m = await db.get(NichoCategoriaML, mapping_id)
    if m and m.org_id == admin.org_id:
        await db.delete(m)
        await db.commit()
    return RedirectResponse(url="/mappings-nichos?mensagem=Mapping+removido", status_code=302)

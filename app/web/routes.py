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
    StatusTarefa,
    Tarefa,
    TemplateMensagem,
    TipoTarefa,
    Usuario,
)
from app.services import (
    agente_service,
    busca_service,
    dispatcher,
    limites,
    lote_service,
    papel_service,
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


async def exigir_admin_central(
    user: Usuario = Depends(exigir_login),
) -> Usuario:
    """Só admin da org central (settings.admin_org_id) — regras
    arquiteturais (17/05/2026): só ele gerencia catálogo + fila."""
    if not user.eh_admin_central:
        raise HTTPException(
            status_code=403,
            detail="Apenas o admin central pode acessar esta página.",
        )
    return user


async def exigir_super(
    user: Usuario = Depends(exigir_login),
) -> Usuario:
    """Só super admin estrela. Reservado pra ações sensíveis tipo
    promover outro admin a super."""
    if not user.eh_super:
        raise HTTPException(
            status_code=403,
            detail="Apenas um super admin pode executar essa ação.",
        )
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


@router.get("/r/{slug}")
async def redirect_curto(
    slug: str,
    db: AsyncSession = Depends(get_db_async),
):
    """Encurtador próprio (Fase 14): GET /r/{slug} → 302 pro destino real.

    Rota PÚBLICA (sem auth). Cliques vão acontecer fora do app
    (WhatsApp/Telegram), então não dá pra exigir login. Slug não revela
    info sensível.

    Conta o click pra métricas, mas não bloqueia o redirect se a contagem
    falhar (resiliência).
    """
    from app.services import redirect_service

    red = await redirect_service.resolver(db, slug=slug)
    if red is None:
        raise HTTPException(status_code=404, detail="Link não encontrado")

    # Conta click (fire-and-forget — se falhar, ainda redireciona)
    await redirect_service.registrar_click(db, redirect_id=red.id)
    return RedirectResponse(url=red.url_destino, status_code=302)


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


# Cache da URL do installer (evita bater no GitHub a cada click).
# TTL curto (60s) pra propagar releases recém-publicadas sem espera longa.
# Bypass via `?nocache=1` quando precisa forçar refresh (ex: usuário acabou
# de publicar uma versão e quer baixar imediatamente).
_INSTALADOR_CACHE: dict[str, str | float] = {"url": "", "ate": 0.0}
_INSTALADOR_TTL_S = 60


@router.get("/agentes/instalador")
async def baixar_instalador(
    request: Request,
    user: Usuario = Depends(exigir_login),
    nocache: str | None = None,
):
    """
    Redireciona pra última release do agente no GitHub.

    Procura em `silviosvargas/achadinhosv3/releases/latest` pelo asset
    `AchadinhosAgent-Setup-*.exe` produzido pelo workflow
    `.github/workflows/release-agente.yml`. Se acha → 302 pra ele
    (download começa direto no browser). Se não acha → renderiza
    `agente_instalador_em_breve.html` com mensagem amigável.

    `?nocache=1` força bypass do cache de 60s — útil logo após publicar
    uma nova release.
    """
    import time

    import httpx
    from fastapi.responses import RedirectResponse

    agora = time.time()
    if nocache != "1":
        cached_url = _INSTALADOR_CACHE["url"]
        cached_ate = _INSTALADOR_CACHE["ate"]
        if isinstance(cached_url, str) and cached_url and isinstance(cached_ate, float) and agora < cached_ate:
            return RedirectResponse(url=cached_url, status_code=302)

    try:
        async with httpx.AsyncClient(timeout=5.0) as cli:
            r = await cli.get(
                "https://api.github.com/repos/silviosvargas/achadinhosv3/releases/latest",
                headers={"Accept": "application/vnd.github+json"},
            )
        if r.status_code == 200:
            data = r.json()
            for asset in data.get("assets", []) or []:
                nome = asset.get("name", "")
                if nome.lower().endswith(".exe") and "achadinhosagent" in nome.lower():
                    url = asset.get("browser_download_url")
                    if url:
                        _INSTALADOR_CACHE["url"] = url
                        _INSTALADOR_CACHE["ate"] = agora + _INSTALADOR_TTL_S
                        return RedirectResponse(url=url, status_code=302)
    except (httpx.HTTPError, ValueError, KeyError):
        pass

    # Fallback: nenhuma release publicada ainda → página de espera.
    return templates.TemplateResponse(
        request, "agente_instalador_em_breve.html", {"user": user},
    )


@router.get("/onboarding", response_class=HTMLResponse)
async def pagina_onboarding(
    request: Request,
    user: Usuario = Depends(exigir_login),
    db: AsyncSession = Depends(get_db_async),
):
    """
    Checklist de 4 passos: afiliados, agente, canal, grupo.
    Marca onboarding_completo=True quando todos OK (idempotente).
    """
    from app.models import UsuarioAfiliado
    org = await db.get(Organizacao, user.org_id)

    # Avalia cada passo
    tem_afiliado = (await db.scalar(
        select(func.count()).select_from(UsuarioAfiliado)
        .where(UsuarioAfiliado.usuario_id == user.id)
    ) or 0) > 0
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

    # Regra refinada Fase D: admin central + afiliado têm passo "afiliados".
    # Usuário comum não vê — postagens usam afiliado do admin.
    pode_credenciais = user.eh_admin_central or user.eh_afiliado

    passos: dict[str, dict] = {
        "agente":      {"ok": total_agentes > 0, "total": total_agentes},
        "canal":       {"ok": total_canais > 0,  "total": total_canais},
        "grupo":       {"ok": total_grupos > 0,  "total": total_grupos},
    }
    if pode_credenciais:
        passos = {"afiliados": {"ok": tem_afiliado}, **passos}
    completo = all(p["ok"] for p in passos.values())

    # Persiste flag (idempotente)
    if completo and not user.onboarding_completo:
        user.onboarding_completo = True
        await db.commit()

    return templates.TemplateResponse(
        request, "onboarding.html",
        {"user": user, "org": org, "passos": passos, "completo": completo},
    )


@router.get("/conta", response_class=HTMLResponse)
async def pagina_conta(
    request: Request,
    user: Usuario = Depends(exigir_login),
):
    """Página 'Minha conta': mostra dados básicos + form trocar senha."""
    return templates.TemplateResponse(
        request, "conta.html",
        {"user": user, "mensagem": None, "erro": None},
    )


@router.post("/conta/senha", response_class=HTMLResponse)
async def trocar_senha_form(
    request: Request,
    senha_atual: str = Form(...),
    senha_nova: str = Form(...),
    senha_nova2: str = Form(...),
    user: Usuario = Depends(exigir_login),
    db: AsyncSession = Depends(get_db_async),
):
    """Trocar a própria senha via form HTML. Valida: atual correta,
    nova >= 8 chars, confirmação bate."""
    def _render(erro: str | None = None, mensagem: str | None = None, status: int = 200):
        return templates.TemplateResponse(
            request, "conta.html",
            {"user": user, "erro": erro, "mensagem": mensagem},
            status_code=status,
        )

    if not verificar_senha(senha_atual, user.senha_hash):
        return _render(erro="Senha atual incorreta.", status=400)
    if len(senha_nova) < 8:
        return _render(erro="A nova senha precisa ter pelo menos 8 caracteres.", status=400)
    if senha_nova != senha_nova2:
        return _render(erro="As duas senhas novas não batem.", status=400)
    if senha_nova == senha_atual:
        return _render(erro="A nova senha precisa ser diferente da atual.", status=400)

    user.senha_hash = hash_senha(senha_nova)
    await db.commit()
    return _render(mensagem="Senha trocada com sucesso. Use ela no próximo login.")


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
    from app.services import curadoria_service

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

    # Fase 18 — preview do TOP por nota
    # Assinatura mudou no commit c02eca6 pra retornar (produtos, fonte, total)
    top_preview, _fonte, _total = await curadoria_service.listar_top_com_fallback(
        db, org_id=user.org_id, limite=6, nota_minima=30.0,
    )

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {"user": user, "org": org, "counts": counts, "top_preview": top_preview},
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
    from app.services import capabilities_service

    result = await db.execute(
        select(Agente).where(Agente.org_id == user.org_id).order_by(Agente.criado_em.desc())
    )
    agentes = list(result.scalars().all())

    # Lista de usuários da org (pro dropdown do form)
    usuarios = list((await db.execute(
        select(Usuario).where(Usuario.org_id == user.org_id, Usuario.ativo.is_(True))
        .order_by(Usuario.login)
    )).scalars().all())

    # Fase D (17/05/2026): capabilities por agente — UI mostra badges
    # ("🟢 WhatsApp", "🟢 ML", "🔒 Shopee" etc) baseado no tipo do user dono.
    capabilities_por_agente: dict[int, list[str]] = {}
    for a in agentes:
        capabilities_por_agente[a.id] = await capabilities_service.capabilities_do_agente(
            db, agente_id=a.id,
        )

    return templates.TemplateResponse(
        request, "agentes.html",
        {
            "user": user,
            "agentes": agentes,
            "usuarios": usuarios,
            "token_recem": token_recem,
            "pode_criar": user.eh_admin,
            "capabilities_por_agente": capabilities_por_agente,
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
    page: int = 1,
):
    """Lista canais. User comum vê SÓ os próprios (Fase 17/05/2026 noite —
    privacidade entre users da mesma org). Admin central vê tudo.
    Paginação 50/página."""
    PER_PAGE = 50

    base = select(Canal).where(Canal.org_id == user.org_id)
    if not user.eh_admin_central:
        base = base.where(Canal.usuario_id == user.id)

    total = await db.scalar(select(func.count()).select_from(base.subquery())) or 0
    total_paginas = max(1, -(-total // PER_PAGE))
    page = max(1, min(page, total_paginas))

    result = await db.execute(
        base.order_by(Canal.criado_em.desc())
        .limit(PER_PAGE).offset((page - 1) * PER_PAGE)
    )
    canais = list(result.scalars().all())

    # Pra dropdown do form: agentes e usuários
    agentes = list((await db.execute(
        select(Agente).where(Agente.org_id == user.org_id, Agente.ativo.is_(True))
    )).scalars().all())

    # Mapa de donos (canal.usuario_id) pra mostrar "criado por X" nos alheios
    donos_map: dict[int, Usuario] = {}
    uids = {c.usuario_id for c in canais if c.usuario_id}
    if uids:
        rows = (await db.execute(
            select(Usuario).where(Usuario.id.in_(uids))
        )).scalars().all()
        donos_map = {u.id: u for u in rows}

    return templates.TemplateResponse(
        request, "canais.html",
        {
            "user": user,
            "canais": canais,
            "agentes": agentes,
            "donos_map": donos_map,
            "user_id": user.id,
            "eh_admin_central": user.eh_admin_central,
            "pode_criar": True,
            "mensagem": request.query_params.get("mensagem"),
            "erro":     request.query_params.get("erro"),
            "page_atual": page,
            "total_paginas": total_paginas,
            "total_count":   total,
        },
    )


def _pode_editar_canal(user: Usuario, canal: Canal) -> bool:
    """Permissão pra editar/excluir canal: dono OU admin central."""
    return user.eh_admin_central or canal.usuario_id == user.id


@router.post("/canais/novo", response_class=HTMLResponse)
async def criar_canal_form(
    request: Request,
    tipo:       str = Form(...),
    nome:       str = Form(...),
    agente_id:  int | None = Form(default=None),
    bot_token:  str | None = Form(default=None),
    user: Usuario = Depends(exigir_login),
    db: AsyncSession = Depends(get_db_async),
):
    """Qualquer user logado cria canal. `usuario_id` registra o dono.

    Tipo determina o config:
        whatsapp_agente → precisa agente_id
        telegram_bot    → precisa bot_token (validação async via Celery)
    """
    from urllib.parse import quote

    config: dict = {}
    if tipo == "whatsapp_agente":
        if not agente_id:
            return RedirectResponse(
                url=f"/canais?erro={quote('Selecione um agente pra canal WhatsApp')}",
                status_code=302,
            )
        agente = await db.get(Agente, agente_id)
        if agente is None or agente.org_id != user.org_id:
            return RedirectResponse(
                url=f"/canais?erro={quote('Agente inválido')}", status_code=302,
            )
        config = {"agente_id": agente_id}
    elif tipo == "telegram_bot":
        if not bot_token or ":" not in bot_token:
            return RedirectResponse(
                url=f"/canais?erro={quote('Bot token inválido (esperado: 123456:ABC...)')}",
                status_code=302,
            )
        config = {"bot_token": bot_token.strip()}
    else:
        return RedirectResponse(
            url=f"/canais?erro={quote('Tipo inválido')}", status_code=302,
        )

    canal = Canal(
        org_id=user.org_id,
        usuario_id=user.id,
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
            from app.workers.celery_app import celery_app
            celery_app.send_task("validar_canal_telegram", args=[canal.id])
        except Exception as e:
            log_msg = f"validacao Telegram não disparada: {e}"
            print(log_msg)

    return RedirectResponse(url="/canais?mensagem=Canal+criado", status_code=302)


@router.get("/canais/{canal_id}/editar", response_class=HTMLResponse)
async def editar_canal_form_get(
    request: Request,
    canal_id: int,
    user: Usuario = Depends(exigir_login),
    db: AsyncSession = Depends(get_db_async),
):
    """Form de edição. Dono ou admin central."""
    canal = await db.get(Canal, canal_id)
    if canal is None or canal.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="Canal não encontrado")
    if not _pode_editar_canal(user, canal):
        raise HTTPException(
            status_code=403,
            detail="Apenas o dono do canal pode editar.",
        )

    agentes = list((await db.execute(
        select(Agente).where(Agente.org_id == user.org_id, Agente.ativo.is_(True))
    )).scalars().all())

    return templates.TemplateResponse(
        request, "canal_form.html",
        {"user": user, "canal": canal, "agentes": agentes, "erro": None},
    )


@router.post("/canais/{canal_id}/editar", response_class=HTMLResponse)
async def editar_canal_form_post(
    canal_id: int,
    nome:      str = Form(...),
    agente_id: int | None = Form(default=None),
    bot_token: str | None = Form(default=None),
    ativo:     str | None = Form(default=None),
    user: Usuario = Depends(exigir_login),
    db: AsyncSession = Depends(get_db_async),
):
    """Salva edição do canal. Dono ou admin central.

    NÃO permite trocar o `tipo` (whatsapp ↔ telegram) — isso muda o schema
    do config e quebra postagens em andamento. Pra trocar tipo, criar novo.
    """
    from urllib.parse import quote

    canal = await db.get(Canal, canal_id)
    if canal is None or canal.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="Canal não encontrado")
    if not _pode_editar_canal(user, canal):
        raise HTTPException(status_code=403, detail="Apenas o dono do canal edita")

    canal.nome = nome.strip()
    canal.ativo = (ativo == "1")

    # Reconfigura conforme tipo (mantém tipo original)
    if canal.tipo == "whatsapp_agente":
        if not agente_id:
            return RedirectResponse(
                url=f"/canais?erro={quote('Selecione um agente')}", status_code=302,
            )
        agente = await db.get(Agente, agente_id)
        if agente is None or agente.org_id != user.org_id:
            return RedirectResponse(
                url=f"/canais?erro={quote('Agente inválido')}", status_code=302,
            )
        canal.config = {**(canal.config or {}), "agente_id": agente_id}
    elif canal.tipo == "telegram_bot":
        if bot_token and ":" in bot_token:
            canal.config = {**(canal.config or {}), "bot_token": bot_token.strip()}
            # Re-valida via Celery se mudou token
            try:
                from app.workers.celery_app import celery_app
                celery_app.send_task("validar_canal_telegram", args=[canal.id])
            except Exception:
                pass
        # Se não veio bot_token, mantém o atual sem mexer

    await db.commit()
    return RedirectResponse(url="/canais?mensagem=Canal+atualizado", status_code=302)


@router.post("/canais/{canal_id}/excluir", response_class=HTMLResponse)
async def excluir_canal_form(
    canal_id: int,
    user: Usuario = Depends(exigir_login),
    db: AsyncSession = Depends(get_db_async),
):
    """Exclui canal. Dono ou admin central.

    Grupos referenciando esse canal vão pra estado órfão (canal_id inválido).
    A exclusão NÃO cascateia em grupos por segurança (postagens históricas).
    Mostra warning se há grupos vinculados.
    """
    from urllib.parse import quote

    canal = await db.get(Canal, canal_id)
    if canal is None or canal.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="Canal não encontrado")
    if not _pode_editar_canal(user, canal):
        raise HTTPException(status_code=403, detail="Apenas o dono do canal exclui")

    # Verifica se tem grupos vinculados
    n_grupos = await db.scalar(
        select(func.count()).select_from(Grupo).where(Grupo.canal_id == canal_id)
    ) or 0
    if n_grupos > 0:
        return RedirectResponse(
            url=f"/canais?erro={quote(f'Canal tem {n_grupos} grupo(s) vinculados. Apague-os antes de excluir o canal.')}",
            status_code=302,
        )

    nome_curto = (canal.nome or "")[:40]
    await db.delete(canal)
    await db.commit()
    return RedirectResponse(
        url=f"/canais?mensagem={quote('Canal excluído: ' + nome_curto)}",
        status_code=302,
    )


# ============================================================
# Grupos
# ============================================================

@router.get("/grupos", response_class=HTMLResponse)
async def lista_grupos(
    request: Request,
    user: Usuario = Depends(exigir_login),
    db: AsyncSession = Depends(get_db_async),
    page: int = 1,
):
    """Lista grupos. User comum vê SÓ os próprios (Fase 17/05/2026 noite
    — privacidade entre users da mesma org). Admin central vê tudo.
    Paginação 50/página."""
    PER_PAGE = 50

    base = select(Grupo).where(Grupo.org_id == user.org_id)
    if not user.eh_admin_central:
        base = base.where(Grupo.proprietario_id == user.id)

    total = await db.scalar(select(func.count()).select_from(base.subquery())) or 0
    total_paginas = max(1, -(-total // PER_PAGE))
    page = max(1, min(page, total_paginas))

    result = await db.execute(
        base.order_by(Grupo.criado_em.desc())
        .limit(PER_PAGE).offset((page - 1) * PER_PAGE)
    )
    grupos = list(result.scalars().all())

    # Canais pra dropdown
    canais = list((await db.execute(
        select(Canal).where(Canal.org_id == user.org_id, Canal.ativo.is_(True))
    )).scalars().all())

    # Mapa canal_id → canal pra mostrar nome do canal nas linhas
    canais_map = {c.id: c for c in canais}

    # Mapa de proprietários (pra mostrar "criado por X" nos grupos alheios)
    proprietarios_map: dict[int, Usuario] = {}
    pids = {g.proprietario_id for g in grupos if g.proprietario_id}
    if pids:
        rows = (await db.execute(
            select(Usuario).where(Usuario.id.in_(pids))
        )).scalars().all()
        proprietarios_map = {u.id: u for u in rows}

    return templates.TemplateResponse(
        request, "grupos.html",
        {
            "user": user,
            "grupos": grupos,
            "canais": canais,
            "canais_map": canais_map,
            "proprietarios_map": proprietarios_map,
            "pode_criar": True,
            "user_id":    user.id,
            "eh_admin_central": user.eh_admin_central,
            "mensagem":   request.query_params.get("mensagem"),
            "erro":       request.query_params.get("erro"),
            "page_atual":    page,
            "total_paginas": total_paginas,
            "total_count":   total,
        },
    )


def _pode_editar_grupo(user: Usuario, grupo: Grupo) -> bool:
    """Permissão pra editar/excluir grupo: dono OU admin central."""
    return user.eh_admin_central or grupo.proprietario_id == user.id


@router.post("/grupos/novo", response_class=HTMLResponse)
async def criar_grupo_form(
    request: Request,
    canal_id:      int = Form(...),
    nome:          str = Form(...),
    identificador: str = Form(...),
    user: Usuario = Depends(exigir_login),
    db: AsyncSession = Depends(get_db_async),
):
    """Qualquer user logado cria grupo. `proprietario_id` registra o dono."""
    from urllib.parse import quote

    # Verifica limite do plano
    pode, msg = await limites.pode_criar_grupo(db, org_id=user.org_id)
    if not pode:
        return RedirectResponse(url=f"/grupos?erro={quote(msg)}", status_code=302)

    canal = await db.get(Canal, canal_id)
    if canal is None or canal.org_id != user.org_id:
        raise HTTPException(status_code=400, detail="Canal inválido")

    grupo = Grupo(
        org_id=user.org_id,
        canal_id=canal_id,
        nome=nome,
        identificador=identificador,
        proprietario_id=user.id,
        ativo=True,
    )
    db.add(grupo)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        return RedirectResponse(
            url=f"/grupos?erro={quote('Já existe grupo com esse identificador neste canal')}",
            status_code=302,
        )
    return RedirectResponse(url="/grupos?mensagem=Grupo+criado", status_code=302)


@router.get("/grupos/{grupo_id}/editar", response_class=HTMLResponse)
async def editar_grupo_form_get(
    request: Request,
    grupo_id: int,
    user: Usuario = Depends(exigir_login),
    db: AsyncSession = Depends(get_db_async),
):
    """Form de edição. Dono ou admin central."""
    grupo = await db.get(Grupo, grupo_id)
    if grupo is None or grupo.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="Grupo não encontrado")
    if not _pode_editar_grupo(user, grupo):
        raise HTTPException(
            status_code=403,
            detail="Apenas o dono do grupo pode editar.",
        )

    canais = list((await db.execute(
        select(Canal).where(Canal.org_id == user.org_id, Canal.ativo.is_(True))
    )).scalars().all())

    return templates.TemplateResponse(
        request, "grupo_form.html",
        {"user": user, "grupo": grupo, "canais": canais, "erro": None},
    )


@router.post("/grupos/{grupo_id}/editar", response_class=HTMLResponse)
async def editar_grupo_form_post(
    grupo_id: int,
    canal_id:      int = Form(...),
    nome:          str = Form(...),
    identificador: str = Form(...),
    ativo:         str | None = Form(default=None),
    user: Usuario = Depends(exigir_login),
    db: AsyncSession = Depends(get_db_async),
):
    """Salva edição. Dono ou admin central."""
    from urllib.parse import quote

    grupo = await db.get(Grupo, grupo_id)
    if grupo is None or grupo.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="Grupo não encontrado")
    if not _pode_editar_grupo(user, grupo):
        raise HTTPException(status_code=403, detail="Apenas o dono do grupo edita")

    canal = await db.get(Canal, canal_id)
    if canal is None or canal.org_id != user.org_id:
        raise HTTPException(status_code=400, detail="Canal inválido")

    grupo.canal_id      = canal_id
    grupo.nome          = nome.strip()
    grupo.identificador = identificador.strip()
    grupo.ativo         = (ativo == "1")
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        return RedirectResponse(
            url=f"/grupos?erro={quote('Já existe grupo com esse identificador neste canal')}",
            status_code=302,
        )
    return RedirectResponse(url="/grupos?mensagem=Grupo+atualizado", status_code=302)


@router.post("/grupos/{grupo_id}/excluir", response_class=HTMLResponse)
async def excluir_grupo_form(
    grupo_id: int,
    user: Usuario = Depends(exigir_login),
    db: AsyncSession = Depends(get_db_async),
):
    """Exclui grupo. Dono ou admin central. CASCADE limpa GrupoNicho e
    referências em Postagem (que tem ondelete=SET NULL ou CASCADE conforme schema)."""
    from urllib.parse import quote

    grupo = await db.get(Grupo, grupo_id)
    if grupo is None or grupo.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="Grupo não encontrado")
    if not _pode_editar_grupo(user, grupo):
        raise HTTPException(status_code=403, detail="Apenas o dono do grupo exclui")

    nome_curto = (grupo.nome or "")[:40]
    await db.delete(grupo)
    await db.commit()
    return RedirectResponse(
        url=f"/grupos?mensagem={quote('Grupo excluído: ' + nome_curto)}",
        status_code=302,
    )


# ============================================================
# Tarefas (lista + detalhe + postar manual + cancelar)
# ============================================================

@router.get("/tarefas", response_class=HTMLResponse)
async def lista_tarefas(
    request: Request,
    user: Usuario = Depends(exigir_login),
    db: AsyncSession = Depends(get_db_async),
    status_filtro: str | None = None,
    page: int = 1,
):
    PER_PAGE = 50

    base = select(Tarefa).where(Tarefa.org_id == user.org_id)
    if status_filtro:
        base = base.where(Tarefa.status == status_filtro)

    total = await db.scalar(select(func.count()).select_from(base.subquery())) or 0
    total_paginas = max(1, -(-total // PER_PAGE))
    page = max(1, min(page, total_paginas))

    result = await db.execute(
        base.order_by(Tarefa.criado_em.desc())
        .limit(PER_PAGE).offset((page - 1) * PER_PAGE)
    )
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
            "page_atual":    page,
            "total_paginas": total_paginas,
            "total_count":   total,
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
    papel:  str | None = None,    # admin | afiliado | usuario | "" / None = todos
    busca:  str | None = None,    # match parcial em login / nome_exibicao / email
    desde:  str | None = None,    # YYYY-MM-DD — criado_em >= desde
    ate:    str | None = None,    # YYYY-MM-DD — criado_em <= ate
    page:   int = 1,              # paginação 1-indexed
):
    """Lista usuários.

    Admin central (17/05/2026): vê TODOS do sistema com filtros por
    papel / nome / data de cadastro. Outros admins: só da própria org.
    Paginação: 50 por página (?page=N).
    """
    from datetime import datetime as _dt, timezone as _tz

    PER_PAGE = 50

    base = select(Usuario)
    if not user.eh_admin_central:
        base = base.where(Usuario.org_id == user.org_id)

    # Filtros (admin central só)
    if user.eh_admin_central:
        if papel and papel in ("admin", "super", "afiliado", "usuario"):
            if papel == "admin":
                # Inclui super como admin pra simplificar
                base = base.where(Usuario.papel.in_(["admin", "super"]))
            else:
                base = base.where(Usuario.papel == papel)

        if busca:
            busca_str = f"%{busca.strip()}%"
            base = base.where(
                (Usuario.login.ilike(busca_str))
                | (Usuario.nome_exibicao.ilike(busca_str))
                | (Usuario.email.ilike(busca_str))
            )

        if desde:
            try:
                d = _dt.strptime(desde, "%Y-%m-%d").replace(tzinfo=_tz.utc)
                base = base.where(Usuario.criado_em >= d)
            except ValueError:
                pass

        if ate:
            try:
                d = _dt.strptime(ate, "%Y-%m-%d").replace(
                    hour=23, minute=59, second=59, tzinfo=_tz.utc,
                )
                base = base.where(Usuario.criado_em <= d)
            except ValueError:
                pass

    # Total pra paginação
    total = await db.scalar(
        select(func.count()).select_from(base.subquery())
    ) or 0
    total_paginas = max(1, -(-total // PER_PAGE))  # ceil division
    page = max(1, min(page, total_paginas))

    offset = (page - 1) * PER_PAGE
    result = await db.execute(
        base.order_by(Usuario.criado_em.desc()).limit(PER_PAGE).offset(offset)
    )
    usuarios = list(result.scalars().all())

    # Pra admin central: carrega orgs dos usuários (mostrar nome da org)
    orgs_map: dict[int, Organizacao] = {}
    if user.eh_admin_central and usuarios:
        org_ids = {u.org_id for u in usuarios}
        rows = (await db.execute(
            select(Organizacao).where(Organizacao.id.in_(org_ids))
        )).scalars().all()
        orgs_map = {o.id: o for o in rows}

    # Enriquece cada user com flags de ações permitidas pelo actor logado.
    # Calcular aqui (server-side, pura) evita chamadas Python no Jinja.
    usuarios_view = []
    for u in usuarios:
        promover_pra = papel_service.proximo_papel_acima(u.papel)
        rebaixar_pra = papel_service.proximo_papel_abaixo(u.papel)
        pode_promover = (
            promover_pra is not None
            and papel_service.pode_mudar_papel(user, u, promover_pra)[0]
        )
        pode_rebaixar = (
            rebaixar_pra is not None
            and papel_service.pode_mudar_papel(user, u, rebaixar_pra)[0]
        )
        usuarios_view.append({
            "u": u,
            "pode_editar":    papel_service.pode_editar_dados(user, u)[0],
            "pode_promover":  pode_promover,
            "promover_pra":   promover_pra if pode_promover else None,
            "pode_rebaixar":  pode_rebaixar,
            "rebaixar_pra":   rebaixar_pra if pode_rebaixar else None,
            "pode_desativar": (
                u.ativo and papel_service.pode_desativar(user, u)[0]
            ),
            "pode_reativar": (
                (not u.ativo) and papel_service.pode_editar_dados(user, u)[0]
            ),
            "pode_excluir":   papel_service.pode_excluir(user, u)[0],
        })

    return templates.TemplateResponse(
        request, "usuarios.html",
        {
            "user": user,
            "usuarios": usuarios,           # mantém pra compat / itens simples
            "usuarios_view": usuarios_view, # enriquecido com flags de ações
            "pode_criar": user.eh_admin,
            "eh_admin_central": user.eh_admin_central,
            "orgs_map": orgs_map,
            # Echo dos filtros pro form lembrar
            "filtro_papel": papel or "",
            "filtro_busca": busca or "",
            "filtro_desde": desde or "",
            "filtro_ate":   ate or "",
            "filtros_ativos": bool(
                user.eh_admin_central and any([papel, busca, desde, ate])
            ),
            # Paginação
            "page_atual":    page,
            "total_paginas": total_paginas,
            "total_count":   total,
            "per_page":      PER_PAGE,
        },
    )


@router.get("/usuarios/{usuario_id}/credenciais")
async def credenciais_legacy_redirect(usuario_id: int):
    """Redireciona URL antiga pra nova de afiliados (Fase 13).
    Mantemos 302 pra não quebrar bookmarks/links antigos."""
    return RedirectResponse(url=f"/usuarios/{usuario_id}/afiliados", status_code=302)


@router.get("/usuarios/{usuario_id}/afiliados", response_class=HTMLResponse)
async def form_afiliados(
    usuario_id: int,
    request: Request,
    user: Usuario = Depends(exigir_login),
    db: AsyncSession = Depends(get_db_async),
    mensagem: str | None = None,
    erro: str | None = None,
):
    """Página pra gerenciar tags de afiliado por marketplace (Fase 13).

    Mostra os afiliados cadastrados + dropdown "+ Adicionar" com os
    marketplaces ainda não usados pelo user.
    """
    from app.core import marketplaces
    from app.models import UsuarioAfiliado
    from app.services import afiliado_service

    target = await db.get(Usuario, usuario_id)
    if target is None or target.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    if not user.eh_admin and target.id != user.id:
        raise HTTPException(
            status_code=403,
            detail="Só admin ou o próprio dono pode editar estes afiliados",
        )

    # Regra refinada Fase D: admin central + afiliado podem cadastrar tag
    # própria. Usuário comum não.
    pode_cadastrar = user.eh_admin_central or user.eh_afiliado

    cadastrados = await afiliado_service.listar_por_usuario(db, usuario_id=target.id)
    slugs_ja_usados = {c.plataforma for c in cadastrados}
    disponiveis = [m for m in marketplaces.MARKETPLACES if m.slug not in slugs_ja_usados]

    # Enriquece cadastrados com display name + ícone do marketplace
    cadastrados_view = [
        {
            "plataforma": c.plataforma,
            "tag": c.tag,
            "nome": (marketplaces.por_slug(c.plataforma).nome
                     if marketplaces.por_slug(c.plataforma) else c.plataforma),
            "icone": (marketplaces.por_slug(c.plataforma).icone
                      if marketplaces.por_slug(c.plataforma) else "🏷️"),
            "placeholder": (marketplaces.por_slug(c.plataforma).placeholder_tag
                            if marketplaces.por_slug(c.plataforma) else ""),
        }
        for c in cadastrados
    ]

    return templates.TemplateResponse(
        request, "usuario_afiliados.html",
        {
            "user": user, "target": target,
            "cadastrados": cadastrados_view,
            "disponiveis": disponiveis,
            "pode_cadastrar": pode_cadastrar,
            "mensagem": mensagem, "erro": erro,
        },
    )


@router.post("/usuarios/{usuario_id}/afiliados/adicionar")
async def adicionar_afiliado_form(
    usuario_id: int,
    plataforma: str = Form(...),
    tag: str = Form(...),
    user: Usuario = Depends(exigir_login),
    db: AsyncSession = Depends(get_db_async),
):
    """Adiciona ou atualiza tag de afiliado via form HTML."""
    from app.core import marketplaces
    from app.services import afiliado_service

    target = await db.get(Usuario, usuario_id)
    if target is None or target.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    if not user.eh_admin and target.id != user.id:
        raise HTTPException(status_code=403, detail="Acesso negado")

    # Regra refinada Fase D: admin central + afiliado podem cadastrar.
    if not (user.eh_admin_central or user.eh_afiliado):
        return RedirectResponse(
            url=f"/usuarios/{usuario_id}/afiliados?erro=Apenas+admin+ou+afiliado+cadastra+tag",
            status_code=302,
        )

    mkt = marketplaces.por_slug(plataforma)
    if mkt is None:
        return RedirectResponse(
            url=f"/usuarios/{usuario_id}/afiliados?erro=Marketplace+invalido",
            status_code=302,
        )

    tag = (tag or "").strip()
    if not tag:
        return RedirectResponse(
            url=f"/usuarios/{usuario_id}/afiliados?erro=Tag+vazia",
            status_code=302,
        )

    await afiliado_service.upsert(
        db, usuario_id=target.id, plataforma=mkt.slug, tag=tag,
    )
    return RedirectResponse(
        url=f"/usuarios/{usuario_id}/afiliados?mensagem={mkt.nome}+salvo",
        status_code=302,
    )


@router.post("/usuarios/{usuario_id}/afiliados/{plataforma}/remover")
async def remover_afiliado_form(
    usuario_id: int,
    plataforma: str,
    user: Usuario = Depends(exigir_login),
    db: AsyncSession = Depends(get_db_async),
):
    """Remove tag de afiliado via form HTML (botão 🗑)."""
    from app.services import afiliado_service

    target = await db.get(Usuario, usuario_id)
    if target is None or target.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    if not user.eh_admin and target.id != user.id:
        raise HTTPException(status_code=403, detail="Acesso negado")

    await afiliado_service.remover(
        db, usuario_id=target.id, plataforma=plataforma.lower().strip(),
    )
    return RedirectResponse(
        url=f"/usuarios/{usuario_id}/afiliados?mensagem=Removido",
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
    from urllib.parse import quote

    # Não permite criar 'super' aqui — super só via promoção.
    if papel not in ("usuario", "afiliado", "admin"):
        return RedirectResponse(
            url=f"/usuarios?erro={quote('Papel inválido')}",
            status_code=302,
        )

    # Verifica limite do plano
    pode, msg = await limites.pode_criar_usuario(db, org_id=admin.org_id)
    if not pode:
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


# ── Edição, promoção, exclusão ──────────────────────────────

async def _carregar_target(
    db: AsyncSession, actor: Usuario, usuario_id: int,
) -> Usuario:
    """Carrega target respeitando visibilidade (admin central vê tudo)."""
    target = await db.get(Usuario, usuario_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    if not actor.eh_admin_central and target.org_id != actor.org_id:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    return target


def _redirect_usuarios(mensagem: str | None = None, erro: str | None = None):
    from urllib.parse import quote
    qs = []
    if mensagem:
        qs.append(f"mensagem={quote(mensagem)}")
    if erro:
        qs.append(f"erro={quote(erro)}")
    url = "/usuarios"
    if qs:
        url += "?" + "&".join(qs)
    return RedirectResponse(url=url, status_code=302)


@router.get("/usuarios/{usuario_id}/editar", response_class=HTMLResponse)
async def editar_usuario_form(
    usuario_id: int,
    request: Request,
    user: Usuario = Depends(exigir_admin),
    db: AsyncSession = Depends(get_db_async),
    mensagem: str | None = None,
    erro: str | None = None,
):
    target = await _carregar_target(db, user, usuario_id)

    # Quais papéis o `user` pode atribuir ao `target`? Pré-calcula no servidor
    # pra UI mostrar só as opções válidas.
    papeis_disponiveis = [
        p for p in papel_service.HIERARQUIA_PAPEIS
        if p == target.papel  # mantém papel atual (no-op)
        or papel_service.pode_mudar_papel(user, target, p)[0]
    ]

    org = await db.get(Organizacao, target.org_id)

    return templates.TemplateResponse(
        request, "usuario_form_editar.html",
        {
            "user": user,
            "target": target,
            "org": org,
            "papeis_disponiveis": papeis_disponiveis,
            "pode_editar_dados": papel_service.pode_editar_dados(user, target)[0],
            "pode_excluir": papel_service.pode_excluir(user, target)[0],
            "mensagem": mensagem,
            "erro": erro,
        },
    )


@router.post("/usuarios/{usuario_id}/editar", response_class=HTMLResponse)
async def editar_usuario_submit(
    usuario_id: int,
    nome_exibicao: str = Form(""),
    email:         str = Form(""),
    papel:         str = Form(...),
    ativo:         str = Form(""),   # "on" ou "" (checkbox)
    user: Usuario = Depends(exigir_admin),
    db: AsyncSession = Depends(get_db_async),
):
    target = await _carregar_target(db, user, usuario_id)

    # Edição de dados (nome/email/ativo)
    pode_dados, motivo_dados = papel_service.pode_editar_dados(user, target)
    if not pode_dados:
        return _redirect_usuarios(erro=motivo_dados)

    target.nome_exibicao = (nome_exibicao.strip() or target.login)
    target.email = email.strip() or None

    novo_ativo = (ativo == "on")
    if novo_ativo is False and target.ativo:
        ok_s, motivo_s = await papel_service.checar_salvaguardas_desativacao(
            db, target, actor=user,
        )
        if not ok_s:
            return _redirect_usuarios(erro=motivo_s)
    target.ativo = novo_ativo

    # Mudança de papel (só se mudou)
    if papel and papel != target.papel:
        ok, motivo = papel_service.pode_mudar_papel(user, target, papel)
        if not ok:
            return _redirect_usuarios(erro=motivo)
        # Se for rebaixamento de admin/super, valida salvaguarda
        if papel_service.proximo_papel_acima(target.papel) != papel:
            ok_s, motivo_s = await papel_service.checar_salvaguardas_rebaixamento(
                db, target, papel, actor=user,
            )
            if not ok_s:
                return _redirect_usuarios(erro=motivo_s)
        target.papel = papel

    await db.commit()
    return _redirect_usuarios(mensagem=f"Usuário {target.login} atualizado")


@router.post("/usuarios/{usuario_id}/promover")
async def promover_usuario(
    usuario_id: int,
    user: Usuario = Depends(exigir_admin),
    db: AsyncSession = Depends(get_db_async),
):
    """Sobe 1 degrau na hierarquia (usuario→afiliado→admin→super)."""
    target = await _carregar_target(db, user, usuario_id)

    novo = papel_service.proximo_papel_acima(target.papel)
    if novo is None:
        return _redirect_usuarios(erro="Usuário já está no topo da hierarquia")

    ok, motivo = papel_service.pode_mudar_papel(user, target, novo)
    if not ok:
        return _redirect_usuarios(erro=motivo)

    target.papel = novo
    await db.commit()
    return _redirect_usuarios(mensagem=f"{target.login} promovido pra {novo}")


@router.post("/usuarios/{usuario_id}/rebaixar")
async def rebaixar_usuario(
    usuario_id: int,
    user: Usuario = Depends(exigir_admin),
    db: AsyncSession = Depends(get_db_async),
):
    """Desce 1 degrau na hierarquia."""
    target = await _carregar_target(db, user, usuario_id)

    novo = papel_service.proximo_papel_abaixo(target.papel)
    if novo is None:
        return _redirect_usuarios(erro="Usuário já está na base da hierarquia")

    ok, motivo = papel_service.pode_mudar_papel(user, target, novo)
    if not ok:
        return _redirect_usuarios(erro=motivo)

    ok_s, motivo_s = await papel_service.checar_salvaguardas_rebaixamento(
        db, target, novo, actor=user,
    )
    if not ok_s:
        return _redirect_usuarios(erro=motivo_s)

    target.papel = novo
    await db.commit()
    return _redirect_usuarios(mensagem=f"{target.login} rebaixado pra {novo}")


@router.post("/usuarios/{usuario_id}/desativar")
async def desativar_usuario(
    usuario_id: int,
    user: Usuario = Depends(exigir_admin),
    db: AsyncSession = Depends(get_db_async),
):
    """Soft delete — `ativo=False`. Reversível via /reativar."""
    target = await _carregar_target(db, user, usuario_id)

    ok, motivo = papel_service.pode_desativar(user, target)
    if not ok:
        return _redirect_usuarios(erro=motivo)

    if not target.ativo:
        return _redirect_usuarios(mensagem=f"{target.login} já estava desativado")

    ok_s, motivo_s = await papel_service.checar_salvaguardas_desativacao(
        db, target, actor=user,
    )
    if not ok_s:
        return _redirect_usuarios(erro=motivo_s)

    target.ativo = False
    await db.commit()
    return _redirect_usuarios(mensagem=f"{target.login} desativado")


@router.post("/usuarios/{usuario_id}/reativar")
async def reativar_usuario(
    usuario_id: int,
    user: Usuario = Depends(exigir_admin),
    db: AsyncSession = Depends(get_db_async),
):
    target = await _carregar_target(db, user, usuario_id)

    ok, motivo = papel_service.pode_editar_dados(user, target)
    if not ok:
        return _redirect_usuarios(erro=motivo)

    if target.ativo:
        return _redirect_usuarios(mensagem=f"{target.login} já estava ativo")

    target.ativo = True
    await db.commit()
    return _redirect_usuarios(mensagem=f"{target.login} reativado")


@router.post("/usuarios/{usuario_id}/excluir")
async def excluir_usuario(
    usuario_id: int,
    confirmacao_login: str = Form(...),
    user: Usuario = Depends(exigir_admin),
    db: AsyncSession = Depends(get_db_async),
):
    """Hard delete — APAGA permanentemente. Confirm tripla no JS + token
    de defesa em profundidade aqui: requer o login literal do target."""
    target = await _carregar_target(db, user, usuario_id)

    if confirmacao_login.strip() != target.login:
        return _redirect_usuarios(
            erro=f"Token de confirmação incorreto (esperado: {target.login!r})",
        )

    ok, motivo = papel_service.pode_excluir(user, target)
    if not ok:
        return _redirect_usuarios(erro=motivo)

    ok_s, motivo_s = await papel_service.checar_salvaguardas_exclusao(
        db, target, actor=user,
    )
    if not ok_s:
        return _redirect_usuarios(erro=motivo_s)

    login_apagado = target.login
    await db.delete(target)
    await db.commit()
    return _redirect_usuarios(
        mensagem=f"Usuário {login_apagado!r} apagado permanentemente",
    )


# ============================================================
# Produtos
# ============================================================

def _query_int(valor: str | None) -> int | None:
    """Converte query param string → int. Vazio/inválido → None.

    Necessário pq FastAPI com `int | None` declarado direto explode 422
    quando o form envia `?campo=` (string vazia). Pega como string e
    parseia manualmente.
    """
    if valor is None:
        return None
    v = valor.strip()
    if not v:
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


@router.get("/produtos", response_class=HTMLResponse)
async def lista_produtos(
    request: Request,
    user: Usuario = Depends(exigir_login),
    db: AsyncSession = Depends(get_db_async),
    nicho_id: str | None = None,        # parseado via _query_int (aceita "" do form)
    plataforma: str | None = None,
    bloqueado: str | None = None,       # "1" / "0" / "" / None
    afiliado: str | None = None,        # "meli" (tem meli.la) / "fallback" / "" / None
    busca: str | None = None,
    limite: str | None = None,          # default 100, max 500
    # Filtros novos por faixa de comissão e preço
    comissao_min: str | None = None,    # %, ex: "10"
    comissao_max: str | None = None,
    preco_min:    str | None = None,    # R$, ex: "50"
    preco_max:    str | None = None,
    extras:       str | None = None,    # "1" → só com comissao_extra; "" / None → todos
    page: int = 1,
):
    nicho_id_int = _query_int(nicho_id)
    bloqueado_int = _query_int(bloqueado)
    # `limite` = tamanho da página. Default 50, max 500.
    limite_int = max(10, min(500, _query_int(limite) or 50))

    def _query_float(v: str | None) -> float | None:
        """Parse float aceitando vírgula brasileira; vazio → None."""
        if v is None:
            return None
        s = str(v).replace(",", ".").strip()
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            return None

    comissao_min_f = _query_float(comissao_min)
    comissao_max_f = _query_float(comissao_max)
    preco_min_f    = _query_float(preco_min)
    preco_max_f    = _query_float(preco_max)

    base = select(Produto).where(Produto.org_id == user.org_id)

    if plataforma:
        base = base.where(Produto.plataforma == plataforma)
    if bloqueado_int == 1:
        base = base.where(Produto.bloqueado.is_(True))
    elif bloqueado_int == 0:
        base = base.where(Produto.bloqueado.is_(False))
    if busca:
        base = base.where(Produto.nome.ilike(f"%{busca}%"))
    if nicho_id_int is not None:
        subq = select(ProdutoNicho.produto_id).where(ProdutoNicho.nicho_id == nicho_id_int)
        base = base.where(Produto.id.in_(subq))
    # Filtro "afiliado" — produtos COM meli.la salvo vs COM fallback `?matt_word=`
    if afiliado == "meli":
        base = base.where(Produto.url_afiliado.ilike("%meli.la/%"))
    elif afiliado == "fallback":
        base = base.where(
            Produto.url_afiliado.is_not(None),
            ~Produto.url_afiliado.ilike("%meli.la/%"),
        )

    # Faixa de comissão (NULL conta como excluído quando min é definido)
    if comissao_min_f is not None:
        base = base.where(Produto.comissao.is_not(None), Produto.comissao >= comissao_min_f)
    if comissao_max_f is not None:
        base = base.where(Produto.comissao.is_not(None), Produto.comissao <= comissao_max_f)

    # Faixa de preço
    if preco_min_f is not None:
        base = base.where(Produto.preco >= preco_min_f)
    if preco_max_f is not None:
        base = base.where(Produto.preco <= preco_max_f)

    # Só com bônus GANHOS EXTRAS (capturado da barra preta ML)
    if extras == "1":
        base = base.where(
            Produto.comissao_extra.is_not(None), Produto.comissao_extra > 0,
        )

    # Contagem total (sem limit) pra UI mostrar "X de N" + paginação
    from sqlalchemy import func as sa_func, select as sa_select
    total_count = (await db.execute(
        sa_select(sa_func.count()).select_from(base.subquery())
    )).scalar_one()
    total_paginas = max(1, -(-total_count // limite_int))
    page = max(1, min(page, total_paginas))

    produtos = list((await db.execute(
        base.order_by(Produto.atualizado_em.desc())
        .limit(limite_int).offset((page - 1) * limite_int)
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

    # Fase B (17/05/2026): set de IDs de produtos JÁ personalizados pelo user.
    # Usado no template pra mostrar ⭐ vs ⭐✓ em cada card.
    personalizados_ids: set[int] = set()
    if produtos:
        from app.models import UsuarioProdutoPersonalizado as UPP
        rows = (await db.execute(
            select(UPP.produto_id).where(
                UPP.usuario_id == user.id,
                UPP.produto_id.in_(ids),
            )
        )).all()
        personalizados_ids = {pid for (pid,) in rows}

    # Detecta se ALGUM filtro está ativo (pra UI mostrar "limpar filtros")
    filtros_ativos = any([
        busca, plataforma,
        nicho_id_int is not None,
        bloqueado_int is not None,
        afiliado,
        comissao_min_f is not None, comissao_max_f is not None,
        preco_min_f is not None,    preco_max_f is not None,
        extras == "1",
    ])

    return templates.TemplateResponse(
        request, "produtos.html",
        {
            "user": user,
            "produtos": produtos,
            "nichos": nichos,
            "nichos_por_prod": nichos_por_prod,
            "filtro_nicho_id": nicho_id_int,
            "filtro_plataforma": plataforma or "",
            "filtro_bloqueado": bloqueado_int,
            "filtro_afiliado": afiliado or "",
            "filtro_busca": busca or "",
            "filtro_limite": limite_int,
            "filtro_comissao_min": comissao_min_f if comissao_min_f is not None else "",
            "filtro_comissao_max": comissao_max_f if comissao_max_f is not None else "",
            "filtro_preco_min":    preco_min_f    if preco_min_f    is not None else "",
            "filtro_preco_max":    preco_max_f    if preco_max_f    is not None else "",
            "filtro_extras":       "1" if extras == "1" else "",
            "filtros_ativos": filtros_ativos,
            "total_count": int(total_count or 0),
            "mostrados": len(produtos),
            "page_atual":    page,
            "total_paginas": total_paginas,
            "per_page":      limite_int,
            # Só admin central edita/exclui/bloqueia produtos no catálogo.
            "pode_criar": user.eh_admin_central,
            # Fase B: todos podem personalizar (favoritar).
            "personalizados_ids": personalizados_ids,
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


@router.get("/produtos/{produto_id}/editar", response_class=HTMLResponse)
async def editar_produto_form(
    request: Request,
    produto_id: int,
    admin: Usuario = Depends(exigir_admin),
    db: AsyncSession = Depends(get_db_async),
):
    """Form de edição com campos pré-preenchidos."""
    p = await db.get(Produto, produto_id)
    if p is None or p.org_id != admin.org_id:
        raise HTTPException(status_code=404, detail="Produto não encontrado")

    nichos = list((await db.execute(
        select(Nicho).where(Nicho.ativo.is_(True)).order_by(Nicho.ordem, Nicho.label)
    )).scalars().all())

    nichos_atuais = {nid for (nid,) in (await db.execute(
        select(ProdutoNicho.nicho_id).where(ProdutoNicho.produto_id == produto_id)
    )).all()}

    return templates.TemplateResponse(
        request, "produto_form.html",
        {"user": admin, "nichos": nichos, "produto": p,
         "nichos_atuais": nichos_atuais, "erro": None},
    )


@router.post("/produtos/{produto_id}/editar", response_class=HTMLResponse)
async def salvar_edicao_produto(
    request: Request,
    produto_id: int,
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
    """Salva edição. Plataforma + item_id NÃO mudam (identidade do produto)."""
    p = await db.get(Produto, produto_id)
    if p is None or p.org_id != admin.org_id:
        raise HTTPException(status_code=404, detail="Produto não encontrado")

    def _f(v: str) -> float | None:
        v = (v or "").replace(",", ".").strip()
        if not v:
            return None
        try:
            return float(v)
        except ValueError:
            return None

    preco_val = _f(preco)
    if preco_val is None or preco_val < 0:
        nichos = list((await db.execute(
            select(Nicho).where(Nicho.ativo.is_(True))
        )).scalars().all())
        nichos_atuais = {nid for (nid,) in (await db.execute(
            select(ProdutoNicho.nicho_id).where(ProdutoNicho.produto_id == produto_id)
        )).all()}
        return templates.TemplateResponse(
            request, "produto_form.html",
            {"user": admin, "nichos": nichos, "produto": p,
             "nichos_atuais": nichos_atuais,
             "erro": "Preço inválido — use formato: 89.90"},
            status_code=400,
        )

    from datetime import datetime, timezone
    from app.services.scoring import calcular_nota

    p.nome         = nome.strip()
    p.preco        = preco_val
    p.preco_orig   = _f(preco_orig)
    p.desconto     = _f(desconto)

    # ── Fase 18.5: comissão editada manualmente é MARCADA ─────────────
    # Quando admin edita comissão pela UI, marca `comissao_fonte=manual`
    # (topo da hierarquia). Buscas/revalidações automáticas NÃO sobrescrevem
    # — `_upsert_produto` respeita a hierarquia (v3.4.4+).
    nova_comissao = _f(comissao)
    if nova_comissao is not None and nova_comissao != p.comissao:
        p.comissao               = nova_comissao
        p.comissao_fonte         = "manual"
        p.comissao_atualizada_em = datetime.now(tz=timezone.utc)
    elif nova_comissao is None and p.comissao is not None:
        # Admin limpou o campo de propósito — também conta como manual
        p.comissao               = None
        p.comissao_fonte         = "manual"
        p.comissao_atualizada_em = datetime.now(tz=timezone.utc)

    p.categoria    = categoria.strip() or None
    p.url_canonica = url_canonica.strip() or None
    p.url_afiliado = url_afiliado.strip() or None
    p.foto_url     = foto_url.strip() or None

    # Sincroniza nichos: remove os antigos, adiciona os novos
    await db.execute(
        ProdutoNicho.__table__.delete().where(ProdutoNicho.produto_id == produto_id)
    )
    for nid in nichos_ids:
        db.add(ProdutoNicho(produto_id=produto_id, nicho_id=nid))

    # Recalcula nota com valores atualizados (Fase 18)
    info_nota = calcular_nota({
        "plataforma":     p.plataforma,
        "preco":          p.preco,
        "preco_orig":     p.preco_orig,
        "desconto":       p.desconto,
        "comissao":       p.comissao,
        "total_vendidos": p.total_vendidos,
        "is_bestseller":  p.is_bestseller,
        "is_em_alta":     p.is_em_alta,
    })
    p.nota              = info_nota["nota"]
    p.comissao_validada = info_nota["comissao_validada"]

    await db.commit()
    return RedirectResponse(url="/produtos", status_code=302)


@router.post("/produtos/{produto_id}/excluir", response_class=HTMLResponse)
async def excluir_produto(
    produto_id: int,
    admin: Usuario = Depends(exigir_admin),
    db: AsyncSession = Depends(get_db_async),
):
    """Apaga 1 produto. CASCADE remove produto_nichos + redirects associados."""
    p = await db.get(Produto, produto_id)
    if p is None or p.org_id != admin.org_id:
        raise HTTPException(status_code=404, detail="Produto não encontrado")
    await db.delete(p)
    await db.commit()
    return RedirectResponse(url="/produtos", status_code=302)


# ============================================================
# Personalizar (favoritar) produto — Fase B (17/05/2026)
# Cliente comum não cria produto próprio; ele FAVORITA produtos do
# catálogo central pra ter acesso rápido em /produtos/personalizados.
# ============================================================

@router.post("/produtos/{produto_id}/personalizar", response_class=HTMLResponse)
async def personalizar_produto(
    request: Request,
    produto_id: int,
    user: Usuario = Depends(exigir_login),
    db: AsyncSession = Depends(get_db_async),
):
    """Marca produto como 'personalizado/favorito' do usuário atual.

    Idempotente — se já marcado, não dá erro (UPSERT effectivo).
    Redireciona pra `next` do form, ou /produtos se não tiver.
    """
    from app.models import UsuarioProdutoPersonalizado as UPP
    from sqlalchemy import select as sa_select

    # Confirma que o produto existe e é visível pro user
    produto = await db.get(Produto, produto_id)
    if produto is None:
        raise HTTPException(status_code=404, detail="Produto não encontrado")

    # Tem que estar entre os visíveis (catálogo central, ou própria org)
    from app.api.v1.endpoints.produtos import _org_ids_visiveis
    if produto.org_id not in _org_ids_visiveis(user):
        raise HTTPException(status_code=403, detail="Produto fora do seu catálogo")

    # Idempotente — verifica antes de inserir
    ja_existe = await db.scalar(
        sa_select(UPP.id).where(
            UPP.usuario_id == user.id,
            UPP.produto_id == produto_id,
        )
    )
    if not ja_existe:
        db.add(UPP(usuario_id=user.id, produto_id=produto_id))
        await db.commit()

    # Redireciona pra de onde veio (form `next`) ou /produtos
    form_data = await request.form()
    proximo = form_data.get("next") or "/produtos"
    return RedirectResponse(url=str(proximo), status_code=302)


@router.post("/produtos/{produto_id}/despersonalizar", response_class=HTMLResponse)
async def despersonalizar_produto(
    request: Request,
    produto_id: int,
    user: Usuario = Depends(exigir_login),
    db: AsyncSession = Depends(get_db_async),
):
    """Remove marcação 'personalizado' do produto pro usuário atual.
    Não apaga o produto do catálogo (que é do admin)."""
    from app.models import UsuarioProdutoPersonalizado as UPP
    from sqlalchemy import delete as sa_delete

    await db.execute(
        sa_delete(UPP).where(
            UPP.usuario_id == user.id,
            UPP.produto_id == produto_id,
        )
    )
    await db.commit()
    form_data = await request.form()
    proximo = form_data.get("next") or "/produtos/personalizados"
    return RedirectResponse(url=str(proximo), status_code=302)


@router.post("/produtos/regenerar-meli-la", response_class=HTMLResponse)
async def regenerar_meli_la(
    admin: Usuario = Depends(exigir_admin),
    db: AsyncSession = Depends(get_db_async),
):
    """
    Re-enfileira tarefa GERAR_LINK pros produtos ML da org que ainda não têm
    `meli.la` salvo. Útil pra catalogar produtos antigos depois do fix do bug
    de campo `link_produto` → `url_canonica` (pré-v3.18.x).

    Pega o primeiro agente online da org pra processar.
    """
    from app.models import Agente
    from app.services import busca_service
    from app.services.agente_registry import registry
    from sqlalchemy import update as sa_update

    # 1. Antes de tudo: LIMPA fragment/query das URLs já no DB.
    # Produtos legados (pré-v3.0.7) foram salvos com `#polycard_client=...`
    # do scraping ML, que poluía a URL e quebrava match no aplicar_mapping.
    # Update em batch, antes de coletar a lista de pendentes.
    produtos_da_org = list((await db.execute(
        select(Produto.id, Produto.url_canonica).where(
            Produto.org_id == admin.org_id,
            Produto.plataforma == "ml",
            Produto.url_canonica.is_not(None),
        )
    )).all())
    limpos = 0
    for pid, url in produtos_da_org:
        url_limpa = busca_service._limpar_url_canonica(url)
        if url_limpa and url_limpa != url:
            await db.execute(
                sa_update(Produto).where(Produto.id == pid).values(url_canonica=url_limpa)
            )
            limpos += 1
    if limpos:
        await db.commit()

    # 2. Recarrega depois da limpeza
    rows = (await db.execute(
        select(Produto.url_canonica, Produto.url_afiliado).where(
            Produto.org_id == admin.org_id,
            Produto.plataforma == "ml",
            Produto.url_canonica.is_not(None),
        )
    )).all()
    # Pendente = url_afiliado NÃO contém meli.la
    urls_pendentes = [
        url_c for url_c, url_a in rows
        if url_c and (not url_a or "meli.la/" not in (url_a or ""))
    ]

    if not urls_pendentes:
        return RedirectResponse(
            url="/produtos?mensagem=Nenhum+produto+pendente+(todos+já+têm+meli.la)",
            status_code=302,
        )

    # 3. Pega agente online da org
    agentes_org = list((await db.execute(
        select(Agente).where(Agente.org_id == admin.org_id, Agente.ativo.is_(True))
    )).scalars().all())
    agente = next((a for a in agentes_org if registry.esta_online(a.id)), None)
    if agente is None:
        return RedirectResponse(
            url="/produtos?mensagem=Nenhum+agente+online+(abra+o+AchadinhosAgent+e+tente+de+novo)",
            status_code=302,
        )

    await busca_service._enfileirar_geracao_links_ml(
        db, org_id=admin.org_id, agente_id=agente.id,
        usuario_id=admin.id, urls=urls_pendentes,
    )
    msg = f"{len(urls_pendentes)}+URLs+enfileiradas+pro+agente"
    if limpos:
        msg += f"+(limpei+{limpos}+URLs+com+fragment)"
    return RedirectResponse(
        url=f"/produtos?mensagem={msg}",
        status_code=302,
    )


@router.post("/produtos/excluir-todos", response_class=HTMLResponse)
async def excluir_todos_produtos(
    confirmacao: str = Form(...),
    admin: Usuario = Depends(exigir_admin),
    db: AsyncSession = Depends(get_db_async),
):
    """
    Apaga TODOS os produtos da org. Confirmação tripla:
    - JS no front pede 2× confirm() + 1× prompt() (texto "APAGAR TUDO")
    - Servidor exige o mesmo token "APAGAR TUDO" no campo `confirmacao`
    """
    if confirmacao != "APAGAR TUDO":
        raise HTTPException(
            status_code=400,
            detail="Token de confirmação inválido — operação cancelada.",
        )

    # CASCADE remove produto_nichos + redirects automaticamente
    result = await db.execute(
        Produto.__table__.delete().where(Produto.org_id == admin.org_id)
    )
    await db.commit()
    apagados = result.rowcount or 0
    return RedirectResponse(
        url=f"/produtos?mensagem=Apagados+{apagados}+produtos",
        status_code=302,
    )


# ============================================================
# Busca rápida (admin central): URL → adiciona direto; termo → preview
# ============================================================

def _classificar_busca_rapida(entrada: str) -> str:
    """Retorna 'url' ou 'termo'. URL exige marketplace conhecido."""
    e = (entrada or "").strip().lower()
    if e.startswith(("http://", "https://")):
        if any(d in e for d in (
            "mercadolivre.com", "shopee.com", "amazon.com.br",
        )):
            return "url"
        # URL desconhecida (social, etc) — trata como termo livre.
        # Usuário pode digitar uma palavra-chave se preferir.
        return "termo"
    return "termo"


@router.post("/produtos/buscar-rapida", response_class=HTMLResponse)
async def buscar_rapida_iniciar(
    entrada: str = Form(...),
    admin: Usuario = Depends(exigir_admin_central),
    db: AsyncSession = Depends(get_db_async),
):
    """Cria Tarefa BUSCAR_MERCADO_LIVRE e redireciona pra página de
    espera. URL → 1 produto, modo direto. Termo → 10 produtos, modo
    preview (mostra checkboxes pra usuário escolher)."""
    from urllib.parse import quote_plus
    from app.services.agente_registry import registry

    entrada = (entrada or "").strip()
    if not entrada:
        return RedirectResponse(
            url="/produtos?erro=" + quote_plus("Digite uma palavra-chave ou link"),
            status_code=302,
        )
    if len(entrada) > 500:
        return RedirectResponse(
            url="/produtos?erro=" + quote_plus("Entrada muito longa"),
            status_code=302,
        )

    tipo_dec = _classificar_busca_rapida(entrada)
    if tipo_dec == "url":
        tipo_busca   = "por_url"
        tipo_entrada = "url"
        max_produtos = 1
    else:
        tipo_busca   = "termo_livre"
        tipo_entrada = "termo"
        max_produtos = 10

    # Acha 1º agente da org central que está online
    agentes = list((await db.execute(
        select(Agente).where(
            Agente.org_id == admin.org_id,
            Agente.ativo.is_(True),
        )
    )).scalars().all())
    agente = next((a for a in agentes if registry.esta_online(a.id)), None)
    if agente is None:
        return RedirectResponse(
            url="/produtos?erro=" + quote_plus(
                "Seu agente não está online. Abra o agente no PC e tente novamente."
            ),
            status_code=302,
        )

    # Cria tarefa + entrega — tudo em try/except pra capturar erro REAL.
    # Sem isso o user vê só "Internal Server Error" sem dica do problema.
    try:
        tarefa = Tarefa(
            org_id=admin.org_id,
            tipo=TipoTarefa.BUSCAR_MERCADO_LIVRE,
            status=StatusTarefa.PENDENTE,
            agente_id=agente.id,
            payload={
                "tipo_entrada":  tipo_entrada,
                "entrada":       entrada,
                "max_paginas":   1,
                "max_produtos":  max_produtos,
                "tipo_busca":    tipo_busca,
                "marketplaces":  ["ml"],
                # Marker pra UI saber que essa tarefa foi disparada da busca
                # rápida (e pra modo de exibição na página de polling).
                "_busca_rapida_modo": "imediato" if tipo_dec == "url" else "preview",
            },
            criado_por_usuario_id=admin.id,
        )
        db.add(tarefa)
        await db.flush()
        await db.commit()
        await db.refresh(tarefa)
    except Exception as e:
        log.exception("buscar_rapida.criar_tarefa_falhou",
                      erro=str(e), entrada=entrada[:80])
        return RedirectResponse(
            url="/produtos?erro=" + quote_plus(
                f"Erro ao criar tarefa: {type(e).__name__}: {str(e)[:120]}"
            ),
            status_code=302,
        )

    try:
        await dispatcher._tentar_entrega(db, tarefa)
    except Exception as e:
        # Tarefa já está no DB (PENDENTE). Continua o fluxo e deixa o
        # `reentregar_pendentes` cuidar quando o agente reconectar.
        log.exception("buscar_rapida.entrega_falhou",
                      tarefa_id=tarefa.id, erro=str(e))

    log.info("buscar_rapida.iniciada",
             tarefa_id=tarefa.id, tipo=tipo_dec,
             entrada=entrada[:80], admin_id=admin.id)
    return RedirectResponse(
        url=f"/produtos/buscar-rapida/{tarefa.id}", status_code=302,
    )


@router.get("/produtos/buscar-rapida/{tarefa_id}",
            response_class=HTMLResponse)
async def buscar_rapida_status(
    tarefa_id: int,
    request: Request,
    admin: Usuario = Depends(exigir_admin_central),
    db: AsyncSession = Depends(get_db_async),
):
    """Página de espera + preview. Auto-refresh enquanto PENDENTE/PROCESSANDO.
    Quando CONCLUIDA:
    - modo `imediato` (URL): mostra produto + auto-redirect /produtos em 3s
    - modo `preview` (termo): mostra grid com checkboxes
    """
    t = await db.get(Tarefa, tarefa_id)
    if t is None or t.org_id != admin.org_id:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada")

    payload = t.payload or {}
    modo = payload.get("_busca_rapida_modo", "preview")
    entrada = payload.get("entrada") or ""
    status_str = t.status.value if hasattr(t.status, "value") else str(t.status)

    # Se ainda rodando: só mostra status, o JS faz auto-refresh
    produtos_view = []
    if status_str == "concluida":
        # Pega produtos criados na janela da tarefa (margem de 2min após
        # conclusão pra cobrir delay de commit/timezone). Inclui ATUALIZADOS
        # também (mesmo se o produto já existia, ele "veio dessa busca").
        from datetime import timedelta
        ini = t.iniciado_em or t.criado_em
        fim = (t.concluido_em or t.atualizado_em or t.criado_em) + timedelta(minutes=2)
        produtos_rows = list((await db.execute(
            select(Produto)
            .where(
                Produto.org_id == admin.org_id,
                Produto.descoberto_em >= ini,
                Produto.descoberto_em <= fim,
            )
            .order_by(Produto.descoberto_em.desc())
        )).scalars().all())
        produtos_view = produtos_rows

    return templates.TemplateResponse(
        request, "produtos_buscar_rapida.html",
        {
            "user":          admin,
            "tarefa":        t,
            "status":        status_str,
            "modo":          modo,
            "entrada":       entrada,
            "produtos":      produtos_view,
        },
    )


@router.post("/produtos/buscar-rapida/{tarefa_id}/confirmar",
             response_class=HTMLResponse)
async def buscar_rapida_confirmar(
    tarefa_id: int,
    request: Request,
    admin: Usuario = Depends(exigir_admin_central),
    db: AsyncSession = Depends(get_db_async),
):
    """Recebe checkbox[manter] da UI. Apaga os produtos NÃO selecionados
    (que vieram nessa busca). Retorna pra /produtos com contagem."""
    from urllib.parse import quote_plus
    from datetime import timedelta

    t = await db.get(Tarefa, tarefa_id)
    if t is None or t.org_id != admin.org_id:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada")

    form = await request.form()
    manter_ids_raw = form.getlist("manter")
    manter_ids = set()
    for v in manter_ids_raw:
        try:
            manter_ids.add(int(v))
        except (ValueError, TypeError):
            continue

    # Carrega todos os produtos da janela dessa tarefa
    ini = t.iniciado_em or t.criado_em
    fim = (t.concluido_em or t.atualizado_em or t.criado_em) + timedelta(minutes=2)
    candidatos = list((await db.execute(
        select(Produto).where(
            Produto.org_id == admin.org_id,
            Produto.descoberto_em >= ini,
            Produto.descoberto_em <= fim,
        )
    )).scalars().all())

    mantidos = 0
    apagados = 0
    for p in candidatos:
        if p.id in manter_ids:
            mantidos += 1
        else:
            await db.delete(p)
            apagados += 1
    await db.commit()

    log.info("buscar_rapida.confirmada",
             tarefa_id=tarefa_id, mantidos=mantidos, apagados=apagados,
             admin_id=admin.id)
    msg = f"{mantidos} produto(s) adicionado(s)"
    if apagados:
        msg += f" · {apagados} descartado(s)"
    return RedirectResponse(
        url="/produtos?mensagem=" + quote_plus(msg),
        status_code=302,
    )


# ============================================================
# Produtos Personalizados (Fase 17)
# ============================================================

@router.get("/produtos/personalizados", response_class=HTMLResponse)
async def lista_personalizados(
    request: Request,
    user: Usuario = Depends(exigir_login),
    db: AsyncSession = Depends(get_db_async),
):
    """Lista personalizados visíveis pro user + solicitações pendentes
    + form pra solicitar novo (vai pra fila admin — Fase C)."""
    from app.core.config import settings
    from app.services import personalizado_service, solicitacao_service

    produtos = await personalizado_service.listar_personalizados_visiveis(db, user=user)

    # Solicitações do user (qualquer status) — pra mostrar "em processamento"
    # acima dos produtos já resolvidos.
    solicitacoes = await solicitacao_service.listar_do_usuario(
        db, usuario_id=user.id, limite=20,
    )

    # Carrega criadores pra mostrar "criado por X" pro admin central
    criadores_map: dict[int, Usuario] = {}
    if user.eh_admin_central and produtos:
        ids_criadores = {p.criado_por_usuario_id for p in produtos if p.criado_por_usuario_id}
        if ids_criadores:
            rows = (await db.execute(
                select(Usuario).where(Usuario.id.in_(ids_criadores))
            )).scalars().all()
            criadores_map = {u.id: u for u in rows}

    return templates.TemplateResponse(
        request, "produtos_personalizados.html",
        {
            "user":          user,
            "produtos":      produtos,
            "solicitacoes":  solicitacoes,
            "criadores_map": criadores_map,
            "ia_disponivel": bool(settings.anthropic_api_key),
            "mensagem":      request.query_params.get("mensagem"),
            "erro":          request.query_params.get("erro"),
        },
    )


@router.post("/produtos/personalizados/buscar", response_class=HTMLResponse)
async def personalizado_buscar(
    entrada:  str = Form(...),
    usar_ia:  str | None = Form(default=None),  # mantido pra compat — agora ignorado aqui
    user:     Usuario = Depends(exigir_login),
    db:       AsyncSession = Depends(get_db_async),
):
    """
    Fase C (17/05/2026): cadastra solicitação na FILA admin (não dispara
    busca direto). Cliente vê: "✅ Solicitado — disponível em até 2h".
    Admin processa em `/admin/fila-personalizados` ou Celery beat hourly.

    Mudança vs Fase 17: agente do cliente NÃO é mais chamado pra Selenium
    ML — só admin central tem agente pra isso (Regra 2 do user).
    """
    from urllib.parse import quote_plus

    from app.services import solicitacao_service

    try:
        s = await solicitacao_service.criar_solicitacao(
            db, usuario=user, entrada=entrada,
        )
    except solicitacao_service.SolicitacaoError as e:
        return RedirectResponse(
            url=f"/produtos/personalizados?erro={quote_plus(str(e))}",
            status_code=302,
        )

    msg = (
        f"✅ Solicitação #{s.id} criada! Seu produto fica disponível em "
        "até 2h — você é notificado quando aparecer no catálogo."
    )
    return RedirectResponse(
        url=f"/produtos/personalizados?mensagem={quote_plus(msg)}",
        status_code=302,
    )


@router.post("/produtos/personalizados/{produto_id}/excluir", response_class=HTMLResponse)
async def personalizado_excluir(
    produto_id: int,
    user: Usuario = Depends(exigir_login),
    db:   AsyncSession = Depends(get_db_async),
):
    """Remove produto da minha seleção.

    v17/05/2026 (Fase B): cliente NÃO apaga produto do catálogo central
    (que pertence ao admin). Em vez disso, REMOVE a marcação UPP
    (despersonaliza). Se for produto que o próprio user criou, apaga
    de verdade.
    """
    from app.core.config import settings as _settings
    from app.models import UsuarioProdutoPersonalizado as UPP
    from sqlalchemy import delete as sa_delete

    p = await db.get(Produto, produto_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Produto não encontrado")

    # Admin central: deleta o produto direto
    if user.eh_admin_central:
        await db.delete(p)
        await db.commit()
        return RedirectResponse(url="/produtos/personalizados", status_code=302)

    # Eu CRIEI o produto → posso deletar (apaga do catálogo)
    if p.criado_por_usuario_id == user.id and p.org_id == user.org_id:
        await db.delete(p)
        await db.commit()
        return RedirectResponse(url="/produtos/personalizados", status_code=302)

    # Caso padrão: produto do catálogo central que eu favoritei →
    # remove só da minha seleção (UPP), produto permanece no catálogo
    await db.execute(
        sa_delete(UPP).where(
            UPP.usuario_id == user.id, UPP.produto_id == produto_id,
        )
    )
    await db.commit()
    return RedirectResponse(url="/produtos/personalizados", status_code=302)


@router.post("/produtos/personalizados/limpar-todos", response_class=HTMLResponse)
async def personalizado_limpar_todos(
    user: Usuario = Depends(exigir_login),
    db:   AsyncSession = Depends(get_db_async),
):
    """Apaga TODOS os personalizados visíveis pro user (apenas os próprios)."""
    # Sempre filtra por criador = user (admin não apaga em massa de outros)
    result = await db.execute(
        Produto.__table__.delete().where(
            Produto.org_id == user.org_id,
            Produto.criado_por_usuario_id == user.id,
        )
    )
    await db.commit()
    n = result.rowcount or 0
    return RedirectResponse(
        url=f"/produtos/personalizados?mensagem=Apagados+{n}+produtos+seus",
        status_code=302,
    )


@router.post("/produtos/personalizados/{produto_id}/postar", response_class=HTMLResponse)
async def personalizado_postar(
    produto_id: int,
    user: Usuario = Depends(exigir_login),
    db:   AsyncSession = Depends(get_db_async),
):
    """
    Posta 1 produto personalizado imediatamente (função dedicada — não
    passa pelo lote_service.rodar_lote que filtra entre todos os elegíveis).

    v17/05/2026 (Fase B): aceita postar qualquer produto que o user vê
    em /produtos/personalizados — favoritado OU criado por ele OU criado
    via solicitação que admin processou. Permissão é "está na minha
    seleção", não "eu criei".
    """
    from urllib.parse import quote_plus
    from app.core.config import settings as _settings
    from app.models import UsuarioProdutoPersonalizado as UPP
    from app.services import lote_service
    from sqlalchemy import select as sa_select

    p = await db.get(Produto, produto_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Produto não encontrado")

    # Produto deve estar na minha org OU na org admin central (catálogo)
    if p.org_id != user.org_id and p.org_id != _settings.admin_org_id:
        raise HTTPException(status_code=404, detail="Produto fora do seu catálogo")

    # Permissão de postar: 4 caminhos
    #  a) admin_central                                  → tudo
    #  b) eu criei                                       → meu produto
    #  c) eu favoritei (UPP)                             → minha seleção
    #  d) é produto público da minha própria org         → tudo do meu catálogo
    pode_postar = (
        user.eh_admin_central
        or p.criado_por_usuario_id == user.id
        or p.org_id == user.org_id
    )
    if not pode_postar:
        # Última chance: ele favoritou via UPP
        favoritado = await db.scalar(
            sa_select(UPP.id).where(
                UPP.usuario_id == user.id, UPP.produto_id == produto_id,
            )
        )
        pode_postar = favoritado is not None

    if not pode_postar:
        raise HTTPException(status_code=403, detail="Sem permissão pra postar este produto")

    try:
        resultado = await lote_service.postar_produto_imediato(
            db,
            produto_id=produto_id,
            org_id=user.org_id,    # grupos vão ser da MINHA org
            criado_por_usuario_id=user.id,
            # Gate de privacidade: user comum só posta nos próprios grupos.
            # Admin central passa None (vê todos da org).
            proprietario_grupo_id=(None if user.eh_admin_central else user.id),
        )
    except Exception as e:
        log.exception("personalizado.postar_crashou", erro=str(e))
        msg = f"erro={quote_plus('Erro ao postar: ' + str(e)[:120])}"
        return RedirectResponse(
            url=f"/produtos/personalizados?{msg}", status_code=302,
        )

    if resultado.get("ok"):
        msg = (
            f"mensagem={quote_plus('Postagem enfileirada pro grupo ' + resultado.get('grupo_nome', '?') + ' (tarefa #' + str(resultado.get('tarefa_id')) + ')')}"
        )
    else:
        msg = f"erro={quote_plus(resultado.get('erro', 'erro_desconhecido'))}"

    return RedirectResponse(
        url=f"/produtos/personalizados?{msg}", status_code=302,
    )


@router.post("/produtos/personalizados/postar-todos", response_class=HTMLResponse)
async def personalizado_postar_todos(
    user: Usuario = Depends(exigir_login),
    db:   AsyncSession = Depends(get_db_async),
):
    """Roda lote pegando TODOS os personalizados visíveis (max 50)."""
    from app.services import lote_service, personalizado_service

    produtos = await personalizado_service.listar_personalizados_visiveis(db, user=user)
    if not produtos:
        return RedirectResponse(
            url="/produtos/personalizados?erro=Nenhum+produto+pra+postar",
            status_code=302,
        )
    try:
        resultado = await lote_service.rodar_lote(
            db, org_id=user.org_id,
            max_produtos=min(50, len(produtos)),
            usuario=user,
            criado_por_usuario_id=user.id,
        )
        n = resultado.get("tarefas_criadas", 0)
        msg = f"mensagem={n}+postagens+enfileiradas"
    except Exception as e:
        log.exception("personalizado.postar_todos_falhou", erro=str(e))
        msg = f"erro=Erro%3A+{str(e)[:80]}"
    return RedirectResponse(url=f"/produtos/personalizados?{msg}", status_code=302)


# ============================================================
# Curadoria — TOP por nota (Fase 18)
# ============================================================

@router.get("/curadoria/top", response_class=HTMLResponse)
async def pagina_curadoria_top(
    request: Request,
    nota_min: float = 30.0,
    limite:   int   = 50,
    page:     int   = 1,
    user: Usuario = Depends(exigir_login),
    db:   AsyncSession = Depends(get_db_async),
):
    """Página completa do TOP por nota.

    Lê `produtos.nota` direto — sem snapshot, sem beat task. Always live.
    A query já filtra produtos postados nos últimos 7 dias
    (`JANELA_DEDUP_DIAS`). Paginação 50/página (`limite` = tamanho).
    """
    from app.services import curadoria_service

    # Clamp do limite (10–100)
    limite = max(10, min(100, limite))

    # Total primeiro pra clamp do page
    total = await curadoria_service.contar_top(
        db, org_id=user.org_id, nota_minima=nota_min,
    )
    # Fallback admin se própria org tá vazia
    if total == 0 and user.org_id != settings.admin_org_id:
        total = await curadoria_service.contar_top(
            db, org_id=settings.admin_org_id, nota_minima=nota_min,
        )

    total_paginas = max(1, -(-total // limite)) if total > 0 else 1
    page = max(1, min(page, total_paginas))
    offset = (page - 1) * limite

    produtos, fonte, _ = await curadoria_service.listar_top_com_fallback(
        db, org_id=user.org_id, limite=limite, offset=offset, nota_minima=nota_min,
    )

    return templates.TemplateResponse(
        request, "curadoria_top.html",
        {
            "user":     user,
            "produtos": produtos,
            "fonte":    fonte,
            "nota_min": nota_min,
            "limite":   limite,
            "mensagem": request.query_params.get("mensagem"),
            "erro":     request.query_params.get("erro"),
            "page_atual":    page,
            "total_paginas": total_paginas,
            "total_count":   total,
        },
    )


@router.post("/curadoria/top/{produto_id}/postar", response_class=HTMLResponse)
async def curadoria_postar_um(
    produto_id: int,
    user: Usuario = Depends(exigir_login),
    db:   AsyncSession = Depends(get_db_async),
):
    """Posta 1 produto do TOP imediatamente. Reusa serviço da Fase 17."""
    from urllib.parse import quote_plus

    try:
        resultado = await lote_service.postar_produto_imediato(
            db,
            produto_id=produto_id,
            org_id=user.org_id,
            criado_por_usuario_id=user.id,
            # Gate de privacidade: user comum só posta nos próprios grupos
            proprietario_grupo_id=(None if user.eh_admin_central else user.id),
        )
    except Exception as e:
        msg = f"erro={quote_plus('Erro ao postar: ' + str(e)[:120])}"
        return RedirectResponse(url=f"/curadoria/top?{msg}", status_code=302)

    if resultado.get("ok"):
        msg = (
            "mensagem="
            + quote_plus(
                "Postagem enfileirada pro grupo "
                + resultado.get("grupo_nome", "?")
                + " (tarefa #" + str(resultado.get("tarefa_id")) + ")"
            )
        )
    else:
        msg = f"erro={quote_plus(resultado.get('erro', 'erro_desconhecido'))}"
    return RedirectResponse(url=f"/curadoria/top?{msg}", status_code=302)


@router.post("/curadoria/top/{produto_id}/excluir", response_class=HTMLResponse)
async def curadoria_excluir_produto(
    produto_id: int,
    admin: Usuario = Depends(exigir_admin),
    db:    AsyncSession = Depends(get_db_async),
):
    """Admin: exclui produto direto do TOP. Reusa CASCADE do DB pra
    apagar produto_nichos + redirects (definido nos models).
    """
    from urllib.parse import quote_plus

    produto = await db.get(Produto, produto_id)
    if produto is None or produto.org_id != admin.org_id:
        msg = "erro=" + quote_plus("Produto não encontrado nesta organização")
        return RedirectResponse(url=f"/curadoria/top?{msg}", status_code=302)

    nome_curto = (produto.nome or "")[:60]
    try:
        await db.delete(produto)
        await db.commit()
        msg = "mensagem=" + quote_plus(f"Produto excluído: {nome_curto}")
    except Exception as e:
        await db.rollback()
        msg = "erro=" + quote_plus(f"Falha ao excluir: {str(e)[:120]}")
    return RedirectResponse(url=f"/curadoria/top?{msg}", status_code=302)


@router.post("/curadoria/recalcular-notas", response_class=HTMLResponse)
async def curadoria_recalcular_notas_form(
    admin: Usuario = Depends(exigir_admin),
    db:    AsyncSession = Depends(get_db_async),
):
    """Admin: re-aplica fórmula de nota em todos produtos da org."""
    from urllib.parse import quote_plus
    from app.services import curadoria_service

    try:
        resultado = await curadoria_service.recalcular_notas_da_org(
            db, org_id=admin.org_id,
        )
        await db.commit()
        msg = (
            "mensagem="
            + quote_plus(
                f"Recalculou {resultado['atualizados']}/{resultado['total']} produtos"
            )
        )
    except Exception as e:
        msg = f"erro={quote_plus('Falha recalculando: ' + str(e)[:120])}"
    return RedirectResponse(url=f"/curadoria/top?{msg}", status_code=302)


@router.post("/curadoria/revalidar-comissoes", response_class=HTMLResponse)
async def curadoria_revalidar_comissoes_form(
    admin: Usuario = Depends(exigir_admin),
    db:    AsyncSession = Depends(get_db_async),
):
    """Admin: dispara tarefa pro agente abrir cada produto ML e capturar
    comissão REAL da barra preta de afiliados (Fase 18.3, v3.4.1).

    NÃO recalcula só com dados do DB — pede pro agente abrir URLs e capturar.
    Resultado vem assíncrono via callback WS → `aplicar_mapping_comissoes_barra`.
    """
    from urllib.parse import quote_plus
    from app.services import curadoria_service

    try:
        resultado = await curadoria_service.disparar_revalidacao_comissoes_via_agente(
            db, org_id=admin.org_id, limite=100,
        )
        if resultado.get("ok"):
            msg = "mensagem=" + quote_plus(resultado["mensagem"])
        else:
            msg = "erro=" + quote_plus(resultado.get("erro", "erro_desconhecido"))
    except Exception as e:
        msg = "erro=" + quote_plus("Falha disparando revalidacao: " + str(e)[:120])
    return RedirectResponse(url=f"/curadoria/top?{msg}", status_code=302)


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
    page: int = 1,
):
    """Lista templates personalizadas. Mostra TODAS da org — user pode
    USAR (postar com) qualquer template, inclusive as do admin. Só EDITA
    e EXCLUI as próprias (gate em `_pode_editar_template`).
    Paginação 50/página."""
    PER_PAGE = 50

    base = select(TemplateMensagem).where(TemplateMensagem.org_id == user.org_id)

    total = await db.scalar(select(func.count()).select_from(base.subquery())) or 0
    total_paginas = max(1, -(-total // PER_PAGE))
    page = max(1, min(page, total_paginas))

    tpls = list((await db.execute(
        base.order_by(TemplateMensagem.nicho_id.is_(None),
                      TemplateMensagem.ordem, TemplateMensagem.id)
        .limit(PER_PAGE).offset((page - 1) * PER_PAGE)
    )).scalars().all())

    nichos = list((await db.execute(
        select(Nicho).where(Nicho.ativo.is_(True)).order_by(Nicho.ordem, Nicho.label)
    )).scalars().all())
    nichos_map = {n.id: n for n in nichos}

    # Mapa de criadores pra mostrar "criado por X" nos alheios
    criadores_map: dict[int, Usuario] = {}
    cids = {t.criado_por_usuario_id for t in tpls if t.criado_por_usuario_id}
    if cids:
        rows = (await db.execute(
            select(Usuario).where(Usuario.id.in_(cids))
        )).scalars().all()
        criadores_map = {u.id: u for u in rows}

    return templates.TemplateResponse(
        request, "templates.html",
        {
            "user": user,
            "templates_lista": tpls,
            "nichos": nichos,
            "nichos_map": nichos_map,
            "criadores_map": criadores_map,
            "user_id": user.id,
            "eh_admin_central": user.eh_admin_central,
            "pode_criar": True,
            "mensagem": request.query_params.get("mensagem"),
            "erro":     request.query_params.get("erro"),
            "page_atual":    page,
            "total_paginas": total_paginas,
            "total_count":   total,
        },
    )


def _pode_editar_template(user: Usuario, tpl: TemplateMensagem) -> bool:
    """Permissão pra editar/excluir template: dono OU admin central."""
    return user.eh_admin_central or tpl.criado_por_usuario_id == user.id


@router.get("/templates/novo", response_class=HTMLResponse)
async def novo_template_form(
    request: Request,
    user: Usuario = Depends(exigir_login),
    db: AsyncSession = Depends(get_db_async),
    nicho_id: int | None = None,
):
    nichos = list((await db.execute(
        select(Nicho).where(Nicho.ativo.is_(True)).order_by(Nicho.ordem, Nicho.label)
    )).scalars().all())
    return templates.TemplateResponse(
        request, "template_form.html",
        {"user": user, "nichos": nichos, "template": None,
         "nicho_id_pre": nicho_id, "erro": None},
    )


@router.post("/templates/novo", response_class=HTMLResponse)
async def criar_template_form(
    request: Request,
    nome:     str = Form(...),
    texto:    str = Form(...),
    nicho_id: str = Form(""),    # vazio = template padrão
    ordem:    int = Form(0),
    user: Usuario = Depends(exigir_login),
    db: AsyncSession = Depends(get_db_async),
):
    """Qualquer user logado cria template. `criado_por_usuario_id` = dono."""
    nicho_int: int | None = None
    if nicho_id.strip():
        try:
            nicho_int = int(nicho_id)
        except ValueError:
            nicho_int = None

    novo = TemplateMensagem(
        org_id=user.org_id,
        nicho_id=nicho_int,
        nome=nome.strip(),
        texto=texto,
        ativo=True,
        ordem=ordem,
        criado_por_usuario_id=user.id,
    )
    db.add(novo)
    await db.commit()
    return RedirectResponse(
        url="/templates?mensagem=Template+criada", status_code=302,
    )


@router.get("/templates/{template_id}/editar", response_class=HTMLResponse)
async def editar_template_form_get(
    request: Request,
    template_id: int,
    user: Usuario = Depends(exigir_login),
    db: AsyncSession = Depends(get_db_async),
):
    """Form de edição. Dono ou admin central."""
    t = await db.get(TemplateMensagem, template_id)
    if t is None or t.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="Template não encontrada")
    if not _pode_editar_template(user, t):
        raise HTTPException(
            status_code=403,
            detail="Apenas o dono da template pode editar.",
        )
    nichos = list((await db.execute(
        select(Nicho).where(Nicho.ativo.is_(True)).order_by(Nicho.ordem, Nicho.label)
    )).scalars().all())
    return templates.TemplateResponse(
        request, "template_form.html",
        {"user": user, "nichos": nichos, "template": t,
         "nicho_id_pre": None, "erro": None},
    )


@router.post("/templates/{template_id}/editar", response_class=HTMLResponse)
async def editar_template_form_post(
    template_id: int,
    nome:     str = Form(...),
    texto:    str = Form(...),
    nicho_id: str = Form(""),
    ordem:    int = Form(0),
    ativo:    str | None = Form(default=None),
    user: Usuario = Depends(exigir_login),
    db: AsyncSession = Depends(get_db_async),
):
    """Salva edição da template. Dono ou admin central."""
    t = await db.get(TemplateMensagem, template_id)
    if t is None or t.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="Template não encontrada")
    if not _pode_editar_template(user, t):
        raise HTTPException(status_code=403, detail="Apenas o dono edita")

    nicho_int: int | None = None
    if nicho_id.strip():
        try:
            nicho_int = int(nicho_id)
        except ValueError:
            nicho_int = None

    t.nome     = nome.strip()
    t.texto    = texto
    t.nicho_id = nicho_int
    t.ordem    = ordem
    t.ativo    = (ativo == "1")
    await db.commit()
    return RedirectResponse(
        url="/templates?mensagem=Template+atualizada", status_code=302,
    )


@router.post("/templates/{template_id}/excluir", response_class=HTMLResponse)
async def excluir_template_form(
    template_id: int,
    user: Usuario = Depends(exigir_login),
    db: AsyncSession = Depends(get_db_async),
):
    """Exclui template. Dono ou admin central."""
    t = await db.get(TemplateMensagem, template_id)
    if t is None or t.org_id != user.org_id:
        return RedirectResponse(url="/templates", status_code=302)
    if not _pode_editar_template(user, t):
        raise HTTPException(status_code=403, detail="Apenas o dono exclui")
    await db.delete(t)
    await db.commit()
    return RedirectResponse(
        url="/templates?mensagem=Template+exclu%C3%ADda", status_code=302,
    )


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
    from app.core.buscas_padrao import listar_ativas as _listar_padrao

    buscas = list((await db.execute(
        select(BuscaML).where(BuscaML.org_id == user.org_id)
        .order_by(BuscaML.criado_em.desc())
    )).scalars().all())
    return templates.TemplateResponse(
        request, "buscas.html",
        {
            "user": user,
            "buscas": buscas,
            "buscas_padrao": _listar_padrao(),     # Fase 19 — seção topo
            "pode_admin": user.eh_admin,
            "mensagem": mensagem or request.query_params.get("mensagem"),
            "erro": erro or request.query_params.get("erro"),
        },
    )


@router.post("/buscas/padrao/{slug}/rodar", response_class=HTMLResponse)
async def buscas_padrao_rodar_form(
    slug: str,
    admin: Usuario = Depends(exigir_admin),
    db:    AsyncSession = Depends(get_db_async),
):
    """Fase 19: dispara uma busca padrão (admin-only).

    Cria Tarefa(BUSCAR_MERCADO_LIVRE) com payload da busca padrão, agente
    abre cada categoria, captura comissão REAL via barra preta, salva.
    """
    from urllib.parse import quote_plus
    from app.services import buscas_padrao_service

    try:
        resultado = await buscas_padrao_service.disparar(
            db, slug=slug, org_id=admin.org_id,
            criado_por_usuario_id=admin.id,
        )
        if resultado.get("ok"):
            msg = "mensagem=" + quote_plus(resultado["mensagem"])
        else:
            msg = "erro=" + quote_plus(resultado.get("erro", "erro_desconhecido"))
    except buscas_padrao_service.BuscaPadraoServiceError as e:
        msg = "erro=" + quote_plus(str(e))
    except Exception as e:
        msg = "erro=" + quote_plus(f"Falha: {str(e)[:120]}")

    return RedirectResponse(url=f"/buscas?{msg}", status_code=302)


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


_TIPOS_BUSCA_VALIDOS = {
    "termo_livre", "por_url", "mais_vendidos", "melhor_comissao", "em_alta",
}
_MARKETPLACES_SUPORTADOS = {"ml", "shopee", "amazon"}  # Fase 16.6: + Amazon via scraping + SiteStripe


@router.post("/buscas/nova", response_class=HTMLResponse)
async def criar_busca_form(
    request: Request,
    nome:         str = Form(...),
    tipo:         str = Form(default="termo_livre"),
    marketplaces: list[str] = Form(default=[]),
    termo:        str = Form(default=""),
    url_produto:  str = Form(default=""),
    max_paginas:  int = Form(default=3),
    max_produtos: int = Form(default=50),
    agente_id:    str = Form(default=""),
    intervalo_minutos: str = Form(default=""),
    ativo:        str = Form(default=""),
    user: Usuario = Depends(exigir_login),
    db: AsyncSession = Depends(get_db_async),
):
    """Cria busca (Fase 16: multi-marketplace + tipos de busca).

    Tipos suportados:
    - termo_livre   → varre páginas de resultado por palavra-chave
    - por_url       → 1 produto específico (busca personalizada)
    - mais_vendidos → URLs hardcoded de "best sellers" por categoria
    - melhor_comissao, em_alta → roadmap (cria a busca mas devolve aviso)
    """
    import json
    from app.web.routes import _render_busca_form_erro  # forward ref

    # Validação de tipo
    tipo = (tipo or "").strip().lower()
    if tipo not in _TIPOS_BUSCA_VALIDOS:
        return await _render_busca_form_erro(
            request, db, user, f"Tipo '{tipo}' inválido.",
        )

    # Marketplaces: filtra suportados (Fase 16.1 só ML está funcional)
    marketplaces_pedidos = [m for m in marketplaces if m]
    marketplaces_validos = [m for m in marketplaces_pedidos if m in _MARKETPLACES_SUPORTADOS]
    if not marketplaces_validos:
        return await _render_busca_form_erro(
            request, db, user,
            "Selecione pelo menos um marketplace suportado. "
            "Por enquanto apenas Mercado Livre tem scraper ativo; outros "
            "marketplaces ficam na próxima fase.",
        )

    # Por enquanto entrada compatível com schema antigo: termo OU URL.
    # Pra tipo "termo_livre" usa o termo; pra "por_url" usa a URL; pros
    # automáticos (mais_vendidos/melhor_comissao/em_alta), entrada vira o
    # próprio nome do tipo (scraper decide o que fazer).
    if tipo == "termo_livre":
        entrada = (termo or "").strip()
        if not entrada:
            return await _render_busca_form_erro(
                request, db, user, "Termo de busca obrigatório.",
            )
    elif tipo == "por_url":
        entrada = (url_produto or "").strip()
        if not entrada or not entrada.startswith(("http://", "https://")):
            return await _render_busca_form_erro(
                request, db, user, "URL do produto inválida (precisa começar com http(s)://).",
            )
    else:
        # Automáticos não precisam de entrada do user
        entrada = f"[{tipo}]"

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
        entrada=entrada[:2000],
        tipo=tipo,
        marketplaces=json.dumps(marketplaces_validos),
        max_paginas=max(1, min(20, max_paginas)) if tipo != "por_url" else 1,
        max_produtos=max(1, min(500, max_produtos)),
        intervalo_minutos=intervalo,
        ativo=bool(ativo),
        proxima_exec_em=agora if intervalo else None,
    )
    db.add(nova)
    await db.commit()
    return RedirectResponse(url="/buscas?mensagem=Busca+criada", status_code=302)


async def _render_busca_form_erro(
    request: Request, db: AsyncSession, user: Usuario, msg: str,
):
    """Re-renderiza /buscas/nova com mensagem de erro."""
    agentes = list((await db.execute(
        select(Agente).where(Agente.org_id == user.org_id).order_by(Agente.id)
    )).scalars().all())
    return templates.TemplateResponse(
        request, "busca_form.html",
        {"user": user, "agentes": agentes, "erro": msg},
        status_code=400,
    )


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


# ============================================================
# Fase C (17/05/2026): Fila de solicitações personalizadas — admin central
# ============================================================

@router.get("/admin/fila-personalizados", response_class=HTMLResponse)
async def fila_personalizados(
    request: Request,
    admin: Usuario = Depends(exigir_admin_central),
    db: AsyncSession = Depends(get_db_async),
):
    """Admin vê fila de solicitações personalizadas dos clientes.

    Lista pendentes + recentes (últimas 50 em qualquer status). Admin
    pode processar (cria Tarefa pro próprio agente) ou rejeitar.
    """
    from app.models import SolicitacaoPersonalizada
    from sqlalchemy import select as sa_select

    # Pendentes primeiro, depois recentes
    pendentes = list((await db.execute(
        sa_select(SolicitacaoPersonalizada)
        .where(SolicitacaoPersonalizada.status == "pendente")
        .order_by(SolicitacaoPersonalizada.criado_em.asc())
    )).scalars().all())

    recentes = list((await db.execute(
        sa_select(SolicitacaoPersonalizada)
        .where(SolicitacaoPersonalizada.status != "pendente")
        .order_by(SolicitacaoPersonalizada.criado_em.desc())
        .limit(50)
    )).scalars().all())

    # Carrega usuários referenciados
    user_ids = {s.usuario_id for s in pendentes + recentes}
    usuarios_map: dict[int, Usuario] = {}
    if user_ids:
        rows = (await db.execute(
            sa_select(Usuario).where(Usuario.id.in_(user_ids))
        )).scalars().all()
        usuarios_map = {u.id: u for u in rows}

    return templates.TemplateResponse(
        request, "admin_fila_personalizados.html",
        {
            "user":      admin,
            "pendentes": pendentes,
            "recentes":  recentes,
            "usuarios":  usuarios_map,
            "mensagem":  request.query_params.get("mensagem"),
            "erro":      request.query_params.get("erro"),
        },
    )


@router.get("/admin/logs", response_class=HTMLResponse)
async def admin_logs(
    request: Request,
    admin: Usuario = Depends(exigir_admin_central),
):
    """Console de logs em tempo real (admin central / super only).

    UI estilo terminal: viewport com fundo escuro mostrando logs INFO+
    via SSE (`/api/v1/_diag/logs/stream`). Dropdown lateral lista jobs
    antigos (via `/api/v1/_diag/logs/jobs`) pra carregar histórico de
    uma tarefa específica. Útil pra diagnosticar bugs sem SSH.
    """
    return templates.TemplateResponse(
        request, "admin_logs.html",
        {"user": admin},
    )



@router.post("/admin/fila-personalizados/{solicitacao_id}/processar",
             response_class=HTMLResponse)
async def fila_processar(
    solicitacao_id: int,
    admin: Usuario = Depends(exigir_admin_central),
    db: AsyncSession = Depends(get_db_async),
):
    """Admin processa solicitação — cria Tarefa pro próprio agente."""
    from urllib.parse import quote_plus
    from app.services import solicitacao_service

    resultado = await solicitacao_service.processar_solicitacao(
        db, solicitacao_id=solicitacao_id, admin=admin,
    )
    if resultado["ok"]:
        msg = f"mensagem={quote_plus(resultado['mensagem'])}"
    else:
        msg = f"erro={quote_plus(resultado.get('erro', 'erro_desconhecido'))}"
    return RedirectResponse(
        url=f"/admin/fila-personalizados?{msg}", status_code=302,
    )


@router.post("/admin/fila-personalizados/{solicitacao_id}/rejeitar",
             response_class=HTMLResponse)
async def fila_rejeitar(
    solicitacao_id: int,
    motivo: str = Form(default="Rejeitada pelo admin"),
    admin: Usuario = Depends(exigir_admin_central),
    db: AsyncSession = Depends(get_db_async),
):
    """Admin rejeita manualmente — não envia pra agente."""
    from urllib.parse import quote_plus
    from app.services import solicitacao_service

    ok = await solicitacao_service.rejeitar_solicitacao(
        db, solicitacao_id=solicitacao_id, motivo=motivo,
    )
    msg = (
        f"mensagem={quote_plus('Rejeitada')}"
        if ok else f"erro={quote_plus('Solicitação não está pendente')}"
    )
    return RedirectResponse(
        url=f"/admin/fila-personalizados?{msg}", status_code=302,
    )


@router.post("/admin/fila-personalizados/processar-tudo",
             response_class=HTMLResponse)
async def fila_processar_tudo(
    admin: Usuario = Depends(exigir_admin_central),
    db: AsyncSession = Depends(get_db_async),
):
    """Admin processa TODAS pendentes em sequência."""
    from urllib.parse import quote_plus
    from app.services import solicitacao_service

    pendentes = await solicitacao_service.listar_pendentes(db, limite=50)
    enfileiradas = 0
    falhas = 0
    for s in pendentes:
        r = await solicitacao_service.processar_solicitacao(
            db, solicitacao_id=s.id, admin=admin,
        )
        if r["ok"]:
            enfileiradas += 1
        else:
            falhas += 1

    msg = f"Processadas: {enfileiradas} enfileiradas, {falhas} falhas"
    return RedirectResponse(
        url=f"/admin/fila-personalizados?mensagem={quote_plus(msg)}",
        status_code=302,
    )


@router.post("/admin/fila-personalizados/{solicitacao_id}/excluir",
             response_class=HTMLResponse)
async def fila_excluir(
    solicitacao_id: int,
    admin: Usuario = Depends(exigir_admin_central),
    db: AsyncSession = Depends(get_db_async),
):
    """Apaga a solicitação. NÃO toca na tarefa vinculada (se houver) —
    ela continua no histórico. Idempotente."""
    from urllib.parse import quote_plus
    from app.services import solicitacao_service

    ok = await solicitacao_service.excluir_solicitacao(
        db, solicitacao_id=solicitacao_id,
    )
    msg = (
        f"mensagem={quote_plus(f'Solicitação #{solicitacao_id} excluída')}"
        if ok else
        f"erro={quote_plus('Solicitação não encontrada')}"
    )
    return RedirectResponse(
        url=f"/admin/fila-personalizados?{msg}", status_code=302,
    )


@router.post("/admin/fila-personalizados/{solicitacao_id}/reprocessar",
             response_class=HTMLResponse)
async def fila_reprocessar(
    solicitacao_id: int,
    admin: Usuario = Depends(exigir_admin_central),
    db: AsyncSession = Depends(get_db_async),
):
    """Volta a solicitação pra PENDENTE e dispara processamento. Útil
    pra falhou/processando travado/rejeitada por engano."""
    from urllib.parse import quote_plus
    from app.services import solicitacao_service

    resultado = await solicitacao_service.reprocessar_solicitacao(
        db, solicitacao_id=solicitacao_id, admin=admin,
    )
    if resultado["ok"]:
        msg = f"mensagem={quote_plus('Reprocessando: ' + resultado['mensagem'])}"
    else:
        msg = f"erro={quote_plus(resultado.get('erro', 'erro_desconhecido'))}"
    return RedirectResponse(
        url=f"/admin/fila-personalizados?{msg}", status_code=302,
    )


@router.post("/admin/fila-personalizados/limpar-falhas",
             response_class=HTMLResponse)
async def fila_limpar_falhas(
    admin: Usuario = Depends(exigir_admin_central),
    db: AsyncSession = Depends(get_db_async),
):
    """Apaga TODAS solicitações com status falhou OU rejeitada — limpeza
    em massa pra desafogar a tabela após série de erros."""
    from urllib.parse import quote_plus
    from app.services import solicitacao_service

    total = await solicitacao_service.limpar_por_status(
        db, statuses=["falhou", "rejeitada"],
    )
    msg = f"mensagem={quote_plus(f'{total} solicitações falhas/rejeitadas apagadas')}"
    return RedirectResponse(
        url=f"/admin/fila-personalizados?{msg}", status_code=302,
    )


@router.post("/admin/fila-personalizados/limpar-todas",
             response_class=HTMLResponse)
async def fila_limpar_todas(
    confirmacao: str = Form(""),
    admin: Usuario = Depends(exigir_admin_central),
    db: AsyncSession = Depends(get_db_async),
):
    """Apaga TODAS solicitações (qualquer status, inclusive concluídas).
    Defesa em profundidade: além do JS de confirm tripla, exige token
    literal `APAGAR TUDO` no body (igual padrão de produtos/excluir-todos)."""
    from urllib.parse import quote_plus
    from app.services import solicitacao_service

    if confirmacao.strip() != "APAGAR TUDO":
        return RedirectResponse(
            url=(
                "/admin/fila-personalizados?erro="
                + quote_plus("Token de confirmação incorreto (esperado 'APAGAR TUDO')")
            ),
            status_code=302,
        )

    total = await solicitacao_service.limpar_todas(db)
    msg = f"mensagem={quote_plus(f'{total} solicitações apagadas (TUDO)')}"
    return RedirectResponse(
        url=f"/admin/fila-personalizados?{msg}", status_code=302,
    )

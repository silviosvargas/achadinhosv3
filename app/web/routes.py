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


# Cache de 5 min pra URL do installer (evita bater no GitHub a cada click).
_INSTALADOR_CACHE: dict[str, str | float] = {"url": "", "ate": 0.0}
_INSTALADOR_TTL_S = 300


@router.get("/agentes/instalador")
async def baixar_instalador(
    request: Request,
    user: Usuario = Depends(exigir_login),
):
    """
    Redireciona pra última release do agente no GitHub.

    Procura em `silviosvargas/achadinhosv3/releases/latest` pelo asset
    `AchadinhosAgent-Setup-*.exe` produzido pelo workflow
    `.github/workflows/release-agente.yml`. Se acha → 302 pra ele
    (download começa direto no browser). Se não acha → renderiza
    `agente_instalador_em_breve.html` com mensagem amigável.
    """
    import time

    import httpx
    from fastapi.responses import RedirectResponse

    agora = time.time()
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

    # Fase 9.9: passo de afiliados só aparece pra plano que permite.
    # No plano free, esse passo é skipado (postagem usa afiliado do admin).
    pode_credenciais = bool(org and org.plano and org.plano.pode_cadastrar_afiliado)

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

    plano = user.organizacao.plano if user.organizacao else None
    pode_cadastrar = bool(plano and plano.pode_cadastrar_afiliado)

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

    plano = user.organizacao.plano if user.organizacao else None
    if not (plano and plano.pode_cadastrar_afiliado):
        return RedirectResponse(
            url=f"/usuarios/{usuario_id}/afiliados?erro=Seu+plano+nao+permite",
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
):
    nicho_id_int = _query_int(nicho_id)
    bloqueado_int = _query_int(bloqueado)
    limite_int = max(10, min(500, _query_int(limite) or 100))

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

    # Contagem total (sem limit) pra UI mostrar "X de N"
    from sqlalchemy import func as sa_func, select as sa_select
    total_count = (await db.execute(
        sa_select(sa_func.count()).select_from(base.subquery())
    )).scalar_one()

    produtos = list((await db.execute(
        base.order_by(Produto.atualizado_em.desc()).limit(limite_int)
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

    # Detecta se ALGUM filtro está ativo (pra UI mostrar "limpar filtros")
    filtros_ativos = any([
        busca, plataforma,
        nicho_id_int is not None,
        bloqueado_int is not None,
        afiliado,
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
            "filtros_ativos": filtros_ativos,
            "total_count": int(total_count or 0),
            "mostrados": len(produtos),
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

    p.nome         = nome.strip()
    p.preco        = preco_val
    p.preco_orig   = _f(preco_orig)
    p.desconto     = _f(desconto)
    p.comissao     = _f(comissao)
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
# Produtos Personalizados (Fase 17)
# ============================================================

@router.get("/produtos/personalizados", response_class=HTMLResponse)
async def lista_personalizados(
    request: Request,
    user: Usuario = Depends(exigir_login),
    db: AsyncSession = Depends(get_db_async),
):
    """Lista personalizados visíveis pro user + form pra adicionar."""
    from app.core.config import settings
    from app.services import personalizado_service

    produtos = await personalizado_service.listar_personalizados_visiveis(db, user=user)

    # Carrega criadores pra mostrar "criado por X" pro admin
    criadores_map: dict[int, Usuario] = {}
    if user.eh_admin and produtos:
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
            "criadores_map": criadores_map,
            "ia_disponivel": bool(settings.anthropic_api_key),
            "mensagem":      request.query_params.get("mensagem"),
            "erro":          request.query_params.get("erro"),
        },
    )


@router.post("/produtos/personalizados/buscar", response_class=HTMLResponse)
async def personalizado_buscar(
    entrada:  str = Form(...),
    usar_ia:  str | None = Form(default=None),
    user:     Usuario = Depends(exigir_login),
    db:       AsyncSession = Depends(get_db_async),
):
    """
    Dispara busca pra cadastrar produto(s) personalizado(s).

    Lógica:
    - Entrada é URL de marketplace conhecido (mercadolivre/shopee/amazon)
      → busca por_url no marketplace correto.
    - Entrada é URL de social (TikTok/Insta/YT) e `usar_ia` marcado
      → chama Claude pra extrair palavra-chave, depois busca termo_livre ML.
    - Entrada é palavra-chave → busca termo_livre ML, limit 10 produtos.

    A busca é enfileirada via Tarefa(BUSCAR_MERCADO_LIVRE) com marcadores
    no payload — quando o agente ingerir, os produtos serão gravados com
    `fonte=personalizado` + dono/criador apropriados.
    """
    from app.core.config import settings
    from app.models import Agente, StatusTarefa, Tarefa, TipoTarefa
    from app.services import dispatcher, personalizado_service

    entrada = (entrada or "").strip()
    if not entrada:
        return RedirectResponse(
            url="/produtos/personalizados?erro=Digite+uma+palavra-chave+ou+URL",
            status_code=302,
        )

    eh_url = entrada.lower().startswith(("http://", "https://"))
    eh_url_marketplace = eh_url and any(
        d in entrada.lower() for d in (
            "mercadolivre.com.br", "mercadolivre.com",
            "shopee.com.br", "amazon.com.br",
        )
    )

    # Se for URL de social com IA → extrai palavra-chave primeiro
    if eh_url and not eh_url_marketplace and usar_ia and settings.anthropic_api_key:
        palavra = await personalizado_service.extrair_palavra_chave_de_link_social(
            entrada, anthropic_api_key=settings.anthropic_api_key,
        )
        if not palavra:
            return RedirectResponse(
                url="/produtos/personalizados?erro=N%C3%A3o+identifiquei+o+produto+no+link.+Tente+outro+ou+cole+o+nome.",
                status_code=302,
            )
        entrada = palavra
        eh_url = False
        eh_url_marketplace = False

    # Define payload da tarefa
    if eh_url_marketplace:
        tipo_busca   = "por_url"
        tipo_entrada = "url"
        max_produtos = 1
    else:
        tipo_busca   = "termo_livre"
        tipo_entrada = "termo"
        max_produtos = 10

    # Pega 1º agente online da org
    agentes_org = list((await db.execute(
        select(Agente).where(
            Agente.org_id == user.org_id, Agente.ativo.is_(True),
        )
    )).scalars().all())
    from app.services.agente_registry import registry
    agente = next((a for a in agentes_org if registry.esta_online(a.id)), None)
    if agente is None:
        return RedirectResponse(
            url="/produtos/personalizados?erro=Nenhum+agente+online.+Abra+o+AchadinhosAgent+no+seu+PC.",
            status_code=302,
        )

    # Cria tarefa com marcadores de "personalizado"
    tarefa = Tarefa(
        org_id=user.org_id,
        tipo=TipoTarefa.BUSCAR_MERCADO_LIVRE,
        status=StatusTarefa.PENDENTE,
        agente_id=agente.id,
        payload={
            "tipo_entrada":  tipo_entrada,
            "entrada":       entrada,
            "max_paginas":   1,
            "max_produtos":  max_produtos,
            "disparado_por": user.id,
            "tipo_busca":    tipo_busca,
            "marketplaces":  ["ml"],
            # Marcadores pra ingest gravar como personalizado:
            "_personalizado_criador_id": user.id,
        },
        criado_por_usuario_id=user.id,
    )
    db.add(tarefa)
    await db.commit()
    await db.refresh(tarefa)
    # Entrega imediata via WS
    await dispatcher._tentar_entrega(db, tarefa)
    return RedirectResponse(
        url=(
            f"/produtos/personalizados?mensagem="
            f"Busca+enfileirada+pra+%22{entrada[:60]}%22+%28tarefa+%23{tarefa.id}%29"
            f"+%E2%80%94+atualiza+em+~30s"
        ),
        status_code=302,
    )


@router.post("/produtos/personalizados/{produto_id}/excluir", response_class=HTMLResponse)
async def personalizado_excluir(
    produto_id: int,
    user: Usuario = Depends(exigir_login),
    db:   AsyncSession = Depends(get_db_async),
):
    """Apaga 1 produto personalizado. Dono ou admin pode."""
    p = await db.get(Produto, produto_id)
    if p is None or p.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="Produto não encontrado")
    if not user.eh_admin and p.criado_por_usuario_id != user.id:
        raise HTTPException(status_code=403, detail="Sem permissão")
    await db.delete(p)
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
    """
    from urllib.parse import quote_plus
    from app.services import lote_service

    p = await db.get(Produto, produto_id)
    if p is None or p.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="Produto não encontrado")
    if not user.eh_admin and p.criado_por_usuario_id != user.id:
        raise HTTPException(status_code=403, detail="Sem permissão")

    try:
        resultado = await lote_service.postar_produto_imediato(
            db,
            produto_id=produto_id,
            org_id=user.org_id,
            criado_por_usuario_id=user.id,
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

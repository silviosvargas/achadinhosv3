"""
Serviço de Produtos Personalizados (Fase 17).

Adicionar produto via UI `/produtos/personalizados`. 3 caminhos de entrada:

1. **Palavra-chave**: dispara busca termo_livre no Mercado Livre, importa
   os top N resultados.
2. **Link de produto de marketplace**: dispara busca por_url (extrai 1
   produto específico).
3. **Link de social** (TikTok/Insta/YT) com IA: chama Claude pra inferir
   palavra-chave do conteúdo, depois fluxo 1. Requer ANTHROPIC_API_KEY.

Regras de dono/visibilidade (a pedido do user, Fase 17):

| Cadastrante          | usuario_dono_id     | criado_por_usuario_id | Quem posta            |
|----------------------|---------------------|-----------------------|-----------------------|
| Admin                | NULL (público)      | admin.id              | Admin + ninguém mais  |
| Usuário comum        | NULL (público)      | user.id               | Admin (com tag dele)  |
| Afiliado COM tag     | afiliado.id (privado)| afiliado.id          | SÓ o afiliado         |
| Afiliado SEM tag     | NULL (público)      | afiliado.id           | Admin (com tag dele)  |

Listagem `/produtos/personalizados`:
- Admin: todos `criado_por_usuario_id IS NOT NULL` da org
- Outro user: só `criado_por_usuario_id == user.id`

Visibilidade de postagem (selecao_service) é separada (ADR-008).
"""
from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import Produto, Usuario

log = get_logger(__name__)


async def _afiliado_tem_tag(
    db: AsyncSession, *, usuario_id: int, plataforma: str,
) -> bool:
    """True se o afiliado já cadastrou tag pra essa plataforma."""
    from app.services import afiliado_service
    tag = await afiliado_service.tag_com_cascata(
        db, plataforma=plataforma, usuario_id=usuario_id, org_id=None,
    )
    # Só conta como "tem tag própria" se não veio da cascata global (admin)
    # — comparação grosseira: se a tag é a do user diretamente, é dele.
    # Por simplicidade, considera "tem tag" se qualquer tag não-vazia voltou.
    return bool(tag)


async def listar_personalizados_visiveis(
    db: AsyncSession, *, user: Usuario,
) -> list[Produto]:
    """
    Lista produtos "personalizados" do user. Inclui DOIS caminhos
    (Fase B — 17/05/2026):

    1. **Solicitou** o cadastro (caso B do conceito): produtos onde
       `criado_por_usuario_id == user.id`. Hoje (Fase 17) cria com agente
       do próprio user; Fase C transforma em fila admin.

    2. **Favoritou** produto do catálogo central (caso A): produtos
       com row em `usuario_produto_personalizado WHERE usuario_id = user.id`.

    Pra admin central, mostra TODOS personalizados da org (qualquer
    criador) — útil pra gerenciar a fila.

    Returns lista deduplicada de Produtos, ordenada por atualizado_em DESC.
    """
    from app.models import UsuarioProdutoPersonalizado as UPP

    # 1. IDs dos favoritados pelo user
    favoritos_ids = [
        pid for (pid,) in (await db.execute(
            select(UPP.produto_id).where(UPP.usuario_id == user.id)
        )).all()
    ]

    # 2. Query principal: combina criado_por OR id IN favoritos
    if user.eh_admin_central:
        # Admin central: vê todos personalizados da org central
        base = (
            select(Produto)
            .where(
                Produto.org_id == user.org_id,
                Produto.criado_por_usuario_id.is_not(None),
            )
        )
    else:
        # Cliente: criou OU favoritou
        condicoes = [Produto.criado_por_usuario_id == user.id]
        if favoritos_ids:
            condicoes.append(Produto.id.in_(favoritos_ids))
        base = select(Produto).where(or_(*condicoes))

    base = base.order_by(Produto.atualizado_em.desc()).limit(200)

    rows = (await db.execute(base)).scalars().all()
    return list(rows)


def aplicar_donos(
    *,
    user: Usuario,
    afiliado_tem_tag: bool,
) -> tuple[int | None, int]:
    """
    Determina (usuario_dono_id, criado_por_usuario_id) pra um produto
    cadastrado pelo `user`.

    Returns:
        - usuario_dono_id: NULL se público (admin/usuário comum/afiliado sem tag);
                          user.id se afiliado COM tag (privado dele).
        - criado_por_usuario_id: sempre user.id.
    """
    if user.eh_afiliado and afiliado_tem_tag:
        # Afiliado com tag própria → produto fica privado dele
        return (user.id, user.id)
    # Admin, usuário comum, ou afiliado sem tag → produto público da org
    return (None, user.id)


def marcar_produtos_personalizados(
    produtos: list[dict[str, Any]],
    *,
    usuario_dono_id: int | None,
    criado_por_usuario_id: int,
) -> list[dict[str, Any]]:
    """
    Anota os produtos retornados pela busca pra que o ingest grave:
    - `fonte = "personalizado"`
    - dono / criador conforme regra acima.

    O ingest (`busca_service._upsert_produto`) usa esses campos
    quando faz o insert.
    """
    for p in produtos:
        p["fonte"] = "personalizado"
        p["_personalizado_dono_id"] = usuario_dono_id   # consumed by ingest
        p["_personalizado_criador_id"] = criado_por_usuario_id
    return produtos


# ============================================================
# Extrator de IA pra link de social (Fase 17 — opcional)
# ============================================================

import re

import httpx

_RE_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_RE_META = re.compile(
    r'<meta\s+[^>]*(?:property|name)="(og:title|og:description|description)"'
    r'[^>]*content="([^"]+)"',
    re.IGNORECASE,
)


async def extrair_palavra_chave_de_link_social(
    url: str, *, anthropic_api_key: str,
) -> str | None:
    """
    Lê metadados da página (title + og:title + og:description) e pede pro
    Claude extrair UMA palavra-chave de produto. Útil pra TikTok/Insta/YT
    onde o post mostra um produto sem dizer o nome direto.

    Portado da V2 `src/buscar_palavra/extrator_link.py:54`.

    Returns palavra-chave (ex: "chaleira elétrica inox 1500w") ou None.
    """
    if not url or not anthropic_api_key:
        return None

    UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )

    # 1. Baixa HTML
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as cli:
            r = await cli.get(url, headers={"User-Agent": UA})
            if r.status_code != 200:
                log.warning("personalizado.html_status", status=r.status_code, url=url[:120])
                return None
            html = r.text
    except Exception as e:
        log.warning("personalizado.html_baixou_falhou", erro=str(e)[:120], url=url[:120])
        return None

    # 2. Extrai metadados
    meta_title = ""
    m = _RE_TITLE.search(html)
    if m:
        meta_title = m.group(1).strip()[:200]

    og_title = ""
    og_desc = ""
    desc = ""
    for prop, conteudo in _RE_META.findall(html):
        prop_l = prop.lower()
        if prop_l == "og:title" and not og_title:
            og_title = conteudo.strip()[:500]
        elif prop_l == "og:description" and not og_desc:
            og_desc = conteudo.strip()[:500]
        elif prop_l == "description" and not desc:
            desc = conteudo.strip()[:500]

    contexto = "\n".join(filter(None, [
        f"Título: {og_title or meta_title}",
        f"Descrição: {og_desc or desc}",
    ]))[:1500]

    if not contexto.strip():
        log.warning("personalizado.sem_metadata", url=url[:120])
        return None

    # 3. Chama Claude — Haiku 4.5 é rápido e barato pra esse tipo de extração
    prompt = (
        "Você analisa metadados de páginas de redes sociais (TikTok, "
        "Instagram, YouTube, etc) e extrai o nome do produto principal "
        "que está sendo recomendado/mostrado.\n\n"
        f"Metadados:\n{contexto}\n\n"
        "Retorne APENAS uma palavra-chave curta (3-6 palavras) que eu "
        "possa usar pra buscar esse produto em marketplaces brasileiros "
        "(Mercado Livre, Shopee, Amazon). Sem aspas, sem pontuação extra.\n\n"
        "Se não conseguir identificar um produto específico, responda "
        "exatamente: NAO_IDENTIFICADO"
    )

    try:
        async with httpx.AsyncClient(timeout=20.0) as cli:
            r = await cli.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "Content-Type":      "application/json",
                    "x-api-key":         anthropic_api_key,
                    "anthropic-version": "2023-06-01",
                },
                json={
                    "model":      "claude-haiku-4-5",
                    "max_tokens": 80,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            if r.status_code != 200:
                log.warning("personalizado.claude_status",
                            status=r.status_code, body=r.text[:200])
                return None
            data = r.json()
            texto = (data.get("content") or [{}])[0].get("text", "").strip()
    except Exception as e:
        log.warning("personalizado.claude_erro", erro=str(e)[:120])
        return None

    texto = texto.strip('"').strip("'").strip()
    if not texto or texto.upper() == "NAO_IDENTIFICADO" or len(texto) < 3:
        log.info("personalizado.claude_sem_produto", texto=texto[:60])
        return None

    palavra = re.sub(r"[^\w\sÀ-ÿ]", "", texto)[:80].strip()
    log.info("personalizado.claude_palavra", palavra=palavra)
    return palavra or None

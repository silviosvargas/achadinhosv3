"""
Signup público (Fase 5) — criação de nova org + admin inicial.

Fluxo:
1. Slug derivado do nome da org (slugify simples).
2. Se slug já existe: adiciona sufixo numérico até achar livre (`empresa-2`).
3. Cria Organizacao com plano free (id=1).
4. Cria Usuario admin com a senha hashed.
5. Retorna (org, admin) — caller emite o JWT.

Idempotente NÃO é — cada chamada cria entidades novas. Validação de
duplicata (mesmo login dentro do mesmo slug) é responsabilidade do caller
ou da constraint do banco.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.security import hash_senha
from app.models import Organizacao, Usuario

log = get_logger(__name__)


# Plano default para novas orgs (seed da migration 0001)
PLANO_FREE_ID = 1


class SignupError(Exception):
    """Erro de regra de negócio no signup (ex: login inválido)."""


def slugify(texto: str) -> str:
    """Slug ASCII: NFKD pra decompor acentos, descarta não-ASCII, troca o resto por traços.

    Ex: 'Loja do João Tester' → 'loja-do-joao-tester'.
    """
    import unicodedata
    # Decompõe acentos: 'ã' → 'a' + COMBINING TILDE; depois filtra os combining
    nfkd = unicodedata.normalize("NFKD", texto.strip().lower())
    base = "".join(c for c in nfkd if not unicodedata.combining(c))

    out: list[str] = []
    ultimo_traco = False
    for c in base:
        if c.isascii() and c.isalnum():
            out.append(c)
            ultimo_traco = False
        elif not ultimo_traco:
            out.append("-")
            ultimo_traco = True
    s = "".join(out).strip("-")
    return s or "org"


async def _slug_unico(db: AsyncSession, base: str) -> str:
    """Adiciona sufixo numérico se slug já existe (`base`, `base-2`, `base-3`...)."""
    slug = base
    n = 1
    while True:
        existe = await db.scalar(
            select(Organizacao.id).where(Organizacao.slug == slug)
        )
        if not existe:
            return slug
        n += 1
        slug = f"{base}-{n}"
        if n > 999:
            # Limite sanity — n unica vai estourar muito antes
            raise SignupError("Não consegui gerar slug único")


async def criar_org_e_admin(
    db: AsyncSession,
    *,
    org_nome: str,
    login: str,
    senha: str,
    email: str | None = None,
    nome_exibicao: str | None = None,
) -> tuple[Organizacao, Usuario]:
    """
    Cria Organizacao + Usuario admin em uma transação.

    Retorna a tupla (org, admin). Caller emite JWT e seta cookie/responde.
    """
    if not org_nome.strip():
        raise SignupError("Nome da organização é obrigatório")

    slug = await _slug_unico(db, slugify(org_nome))

    org = Organizacao(
        slug=slug,
        nome=org_nome.strip(),
        plano_id=PLANO_FREE_ID,
        ativo=True,
    )
    db.add(org)
    await db.flush()  # gera org.id sem commit

    admin = Usuario(
        org_id=org.id,
        login=login,
        senha_hash=hash_senha(senha),
        papel="admin",
        nome_exibicao=(nome_exibicao or login).strip(),
        email=email.strip() if email else None,
        ativo=True,
        onboarding_completo=False,
        ultimo_login=datetime.now(tz=timezone.utc),
    )
    db.add(admin)
    await db.commit()
    await db.refresh(org)
    await db.refresh(admin)

    log.info("signup.criado",
             org_id=org.id, slug=org.slug, admin_id=admin.id, login=admin.login)
    return org, admin

"""
Script de bootstrap: cria a primeira organização e o admin inicial.

Roda 1x na primeira instalação. Idempotente — chamar de novo não duplica.

Uso (dentro do container):
    docker compose exec api python -m scripts.criar_admin

Lê dados de:
    .env: ADMIN_LOGIN, ADMIN_PASSWORD, ADMIN_EMAIL, ADMIN_ORG_NOME
"""
import sys
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.config import settings
from app.core.logging import configurar_logging, get_logger
from app.core.security import hash_senha
from app.db import sessao_sync
from app.models import Organizacao, Usuario


configurar_logging()
log = get_logger(__name__)


def main() -> int:
    log.info("bootstrap.iniciando",
             admin_login=settings.admin_login,
             org_nome=settings.admin_org_nome)

    with sessao_sync() as db:
        # Slug a partir do nome da org
        slug = _slugify(settings.admin_org_nome)

        org = db.scalar(select(Organizacao).where(Organizacao.slug == slug))
        if not org:
            org = Organizacao(
                slug=slug,
                nome=settings.admin_org_nome,
                plano_id=3,             # Business — admin inicial sem limite
                ativo=True,
            )
            db.add(org)
            db.flush()                  # gera org.id sem commit
            log.info("organizacao.criada", id=org.id, slug=org.slug)
        else:
            log.info("organizacao.ja_existia", id=org.id, slug=org.slug)

        # Usuário admin
        admin = db.scalar(
            select(Usuario).where(
                Usuario.org_id == org.id,
                Usuario.login == settings.admin_login,
            )
        )
        if not admin:
            admin = Usuario(
                org_id=org.id,
                login=settings.admin_login,
                senha_hash=hash_senha(settings.admin_password),
                papel="admin",
                nome_exibicao=settings.admin_login.capitalize(),
                email=settings.admin_email,
                ativo=True,
                onboarding_completo=True,
                ultimo_login=datetime.now(tz=timezone.utc),
            )
            db.add(admin)
            db.flush()
            log.warning(
                "admin.criado",
                login=admin.login,
                org=org.slug,
                aviso="TROQUE A SENHA NO PRIMEIRO LOGIN",
            )
        else:
            log.info("admin.ja_existia", id=admin.id, login=admin.login)

    log.info("bootstrap.concluido")
    return 0


def _slugify(texto: str) -> str:
    """Slug minimalista: lowercase + traços. Sem acentos sofisticados."""
    base = texto.strip().lower()
    out = []
    ultimo_traço = False
    for c in base:
        if c.isalnum():
            out.append(c)
            ultimo_traço = False
        elif not ultimo_traço:
            out.append("-")
            ultimo_traço = True
    return "".join(out).strip("-") or "default"


if __name__ == "__main__":
    sys.exit(main())

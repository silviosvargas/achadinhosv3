"""
Script de dev: cria um agente "Dev Local" pra testar fluxo Fase 4b ponta-a-ponta.

Idempotente — se já existir agente com mesmo nome na org admin, reaproveita
mas gera token novo (toda execução imprime um token válido pra colocar no .env
do agente local).

Uso:
    docker compose exec api python -m scripts.criar_agente_dev

Saída:
    Imprime o JWT do agente no stdout (copia pro .env do achadinhos-agent).
"""
import hashlib
import sys
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.config import settings
from app.core.logging import configurar_logging, get_logger
from app.core.security import criar_token_agente
from app.db import sessao_sync
from app.models import Agente, Organizacao, Usuario


configurar_logging()
log = get_logger(__name__)


def main() -> int:
    nome_agente = "Dev Local"

    with sessao_sync() as db:
        # 1. Localiza org admin + admin user
        admin = db.scalar(
            select(Usuario).where(Usuario.login == settings.admin_login)
        )
        if admin is None:
            print(
                "ERRO: usuario admin nao encontrado. "
                "Rode primeiro: docker compose exec api python -m scripts.criar_admin",
                file=sys.stderr,
            )
            return 1

        org = db.get(Organizacao, admin.org_id)
        log.info("contexto", admin_id=admin.id, org_id=org.id, org_slug=org.slug)

        # 2. Reaproveita agente existente ou cria novo
        agente = db.scalar(
            select(Agente).where(
                Agente.org_id == org.id,
                Agente.usuario_id == admin.id,
                Agente.nome == nome_agente,
            )
        )
        if agente is None:
            agente = Agente(
                org_id=org.id,
                usuario_id=admin.id,
                nome=nome_agente,
                token_hash="",  # preenchido abaixo
                ativo=True,
                online=False,
            )
            db.add(agente)
            db.flush()
            log.info("agente.criado", id=agente.id, nome=agente.nome)
        else:
            agente.ativo = True   # garante reativado se tinha sido desativado
            log.info("agente.reaproveitado", id=agente.id, nome=agente.nome)

        # 3. Gera token novo (substitui o anterior)
        token = criar_token_agente(
            usuario_id=admin.id,
            org_id=org.id,
            agente_id=agente.id,
        )
        agente.token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        db.commit()

    # 4. Imprime o token pra clipboard/manual copy
    sep = "=" * 70
    print(sep)
    print(f"AGENTE id={agente.id} '{nome_agente}' org={org.slug}")
    print(sep)
    print("TOKEN JWT (cola no .env do achadinhos-agent como ACHADINHOS_TOKEN):")
    print()
    print(token)
    print()
    print(sep)
    print("URL WS (em dev, com Docker rodando na porta 8000):")
    print("  ws://localhost:8000/api/v1/ws/agente")
    print(sep)

    return 0


if __name__ == "__main__":
    sys.exit(main())

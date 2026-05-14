"""
Serviço de agentes.

Encapsula:
- Criação com token JWT de 1 ano (entregue UMA vez ao admin)
- Validação multi-tenant (não criar agente em outra org)
- Marcação online/offline via WebSocket connect/disconnect
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import criar_token_agente
from app.models import Agente, Usuario


class AgenteServiceError(Exception):
    """Erro de regra de negócio do serviço de agente."""


async def criar_agente(
    db: AsyncSession,
    *,
    org_id: int,
    usuario_id: int,
    nome: str,
) -> tuple[Agente, str]:
    """
    Cria um agente vinculado ao usuário e devolve (agente, token_em_texto).

    O token é JWT de 1 ano. Guardamos só o hash (SHA-256) no banco,
    seguindo o mesmo princípio das senhas: se o banco vazar, ninguém
    consegue se passar pelo agente sem o segredo do JWT.

    Note: a verificação real do token na conexão WS é feita pela
    assinatura HMAC do JWT (security.decodificar_token), não pelo hash.
    O hash serve só pra revogação ("token X ainda é válido?").
    """
    # 1. Valida que o usuário existe e pertence à org
    user = await db.get(Usuario, usuario_id)
    if user is None:
        raise AgenteServiceError("Usuário não encontrado")
    if user.org_id != org_id:
        raise AgenteServiceError("Usuário pertence a outra organização")

    # 2. Cria registro vazio pra obter ID (token precisa do agente_id)
    agente = Agente(
        org_id=org_id,
        usuario_id=usuario_id,
        nome=nome,
        token_hash="",   # preenchido logo abaixo
        ativo=True,
        online=False,
    )
    db.add(agente)
    await db.flush()    # gera agente.id sem commit

    # 3. Gera token JWT com agente_id, hash e atualiza
    token = criar_token_agente(
        usuario_id=usuario_id,
        org_id=org_id,
        agente_id=agente.id,
    )
    agente.token_hash = _hash_token(token)
    await db.commit()
    await db.refresh(agente)

    return agente, token


async def listar_agentes_da_org(
    db: AsyncSession, *, org_id: int,
) -> list[Agente]:
    """Lista todos os agentes da organização."""
    result = await db.execute(
        select(Agente).where(Agente.org_id == org_id).order_by(Agente.criado_em.desc())
    )
    return list(result.scalars().all())


async def get_agente_da_org(
    db: AsyncSession, *, org_id: int, agente_id: int,
) -> Agente | None:
    """Busca agente garantindo que pertença à org (defesa contra IDOR)."""
    result = await db.execute(
        select(Agente).where(
            Agente.id == agente_id,
            Agente.org_id == org_id,
        )
    )
    return result.scalar_one_or_none()


async def marcar_online(
    db: AsyncSession, *, agente_id: int, ip: str | None = None,
) -> None:
    """Chamado pelo WS quando um agente conecta."""
    agente = await db.get(Agente, agente_id)
    if agente is None:
        return
    agente.online = True
    agente.ultimo_ping = datetime.now(tz=timezone.utc)
    if ip:
        agente.ultimo_ip = ip
    await db.commit()


async def marcar_offline(
    db: AsyncSession, *, agente_id: int,
) -> None:
    """Chamado pelo WS quando um agente desconecta ou para de pingar."""
    agente = await db.get(Agente, agente_id)
    if agente is None:
        return
    agente.online = False
    await db.commit()


async def atualizar_metricas(
    db: AsyncSession, *, agente_id: int, metricas: dict,
) -> None:
    """Recebido via mensagem 'metricas' do agente.

    Extrai campos especiais (versao_app, sistema_op) pra colunas dedicadas;
    o resto vai pro JSON `metricas_atuais`.
    """
    agente = await db.get(Agente, agente_id)
    if agente is None:
        return

    # Campos com coluna dedicada — atualiza só se vierem no payload
    if "versao_app" in metricas:
        agente.versao_app = str(metricas["versao_app"])[:30]
    if "sistema_op" in metricas:
        agente.sistema_op = str(metricas["sistema_op"])[:50]

    # Resto vai pro JSON (RAM, CPU, chrome_aberto, etc)
    extras = {k: v for k, v in metricas.items()
              if k not in ("versao_app", "sistema_op")}
    agente.metricas_atuais = extras
    agente.ultimo_ping = datetime.now(tz=timezone.utc)
    await db.commit()


def _hash_token(token: str) -> str:
    """SHA-256 do token. Não é segurança forte (token é JWT já), só rastreio."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

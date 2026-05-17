"""Service da fila de solicitações personalizadas (Fase C — 17/05/2026).

Fluxo:
1. Cliente cadastra solicitação em `/produtos/personalizados/buscar`:
   `criar_solicitacao` → row em `solicitacoes_personalizadas` (status=pendente)
2. Admin processa (manual ou via Celery beat hourly):
   `processar_solicitacao` → cria Tarefa(BUSCAR_MERCADO_LIVRE) pro agente
   do admin central com `solicitacao_id` no payload → status=processando
3. Agente executa, ingere produtos com `criado_por_usuario_id` do solicitante
4. Hook em `dispatcher.marcar_concluida` chama `marcar_concluida_via_hook`:
   conta produtos criados, atualiza status pra concluida.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.models import (
    Agente,
    SolicitacaoPersonalizada,
    StatusSolicitacao,
    StatusTarefa,
    Tarefa,
    TipoSolicitacao,
    TipoTarefa,
    Usuario,
)

log = get_logger(__name__)


class SolicitacaoError(Exception):
    """Erro de domínio do service."""


def _detectar_tipo(entrada: str) -> TipoSolicitacao:
    """Detecta tipo de solicitação pela entrada."""
    entrada_low = entrada.strip().lower()
    eh_url = entrada_low.startswith(("http://", "https://"))
    if not eh_url:
        return TipoSolicitacao.PALAVRA_CHAVE
    eh_marketplace = any(
        d in entrada_low for d in (
            "mercadolivre.com.br", "mercadolivre.com",
            "shopee.com.br", "amazon.com.br",
        )
    )
    return TipoSolicitacao.URL if eh_marketplace else TipoSolicitacao.SOCIAL


async def criar_solicitacao(
    db: AsyncSession,
    *,
    usuario: Usuario,
    entrada: str,
    tipo_forcado: TipoSolicitacao | None = None,
) -> SolicitacaoPersonalizada:
    """Cria solicitação na fila. NÃO dispara processamento.

    Cliente vê: "✅ Solicitado — fica disponível em até 2h".

    Args:
        usuario: quem solicitou (vai virar `criado_por_usuario_id` no produto)
        entrada: palavra-chave OU URL marketplace OU URL social
        tipo_forcado: pula auto-detect (útil quando IA já resolveu social→palavra)
    """
    entrada = (entrada or "").strip()
    if not entrada:
        raise SolicitacaoError("Entrada vazia")
    if len(entrada) > 500:
        raise SolicitacaoError("Entrada muito longa (máx 500 chars)")

    tipo = tipo_forcado or _detectar_tipo(entrada)

    s = SolicitacaoPersonalizada(
        usuario_id=usuario.id,
        org_id_solicitante=usuario.org_id,
        tipo=tipo.value,
        entrada=entrada,
        status=StatusSolicitacao.PENDENTE.value,
    )
    db.add(s)
    await db.commit()
    await db.refresh(s)

    log.info("solicitacao.criada",
             solicitacao_id=s.id, usuario_id=usuario.id,
             tipo=tipo.value, entrada=entrada[:80])
    return s


async def listar_pendentes(
    db: AsyncSession, *, limite: int = 100,
) -> list[SolicitacaoPersonalizada]:
    """Solicitações na fila (status=pendente), mais antigas primeiro."""
    rows = (await db.execute(
        select(SolicitacaoPersonalizada)
        .where(SolicitacaoPersonalizada.status == StatusSolicitacao.PENDENTE.value)
        .order_by(SolicitacaoPersonalizada.criado_em.asc())
        .limit(limite)
    )).scalars().all()
    return list(rows)


async def listar_do_usuario(
    db: AsyncSession, *, usuario_id: int, limite: int = 50,
) -> list[SolicitacaoPersonalizada]:
    """Solicitações do user (qualquer status), recentes primeiro."""
    rows = (await db.execute(
        select(SolicitacaoPersonalizada)
        .where(SolicitacaoPersonalizada.usuario_id == usuario_id)
        .order_by(SolicitacaoPersonalizada.criado_em.desc())
        .limit(limite)
    )).scalars().all()
    return list(rows)


async def processar_solicitacao(
    db: AsyncSession,
    *,
    solicitacao_id: int,
    admin: Usuario | None = None,
) -> dict:
    """Cria Tarefa(BUSCAR_MERCADO_LIVRE) pro agente do admin central
    processar. Atualiza status pra `processando`.

    Args:
        admin: opcional — quando chamado pela rotina automática, pode ser
               None. O agente é resolvido pelo `admin_org_id` em qualquer caso.

    Returns:
        {"ok": True, "tarefa_id": N, "mensagem": "..."}
        ou {"ok": False, "erro": "..."}
    """
    from app.services import dispatcher
    from app.services.agente_registry import registry

    s = await db.get(SolicitacaoPersonalizada, solicitacao_id)
    if s is None:
        return {"ok": False, "erro": "Solicitação não encontrada"}
    if s.status != StatusSolicitacao.PENDENTE.value:
        return {"ok": False, "erro": f"Já está em status {s.status}"}

    # Define parâmetros da busca pelo tipo
    if s.tipo == TipoSolicitacao.URL.value:
        tipo_busca   = "por_url"
        tipo_entrada = "url"
        max_produtos = 1
        entrada      = s.entrada
    elif s.tipo == TipoSolicitacao.SOCIAL.value:
        # SOCIAL exige resolução prévia via IA pra virar palavra-chave.
        # Em chamadas via beat, a entrada original ainda é URL.
        # Tenta resolver agora; se falhar, marca como falhou.
        if not settings.anthropic_api_key:
            await _marcar_falhou(
                db, s,
                "Tipo 'social' exige ANTHROPIC_API_KEY configurada no servidor.",
            )
            return {"ok": False, "erro": "ANTHROPIC_API_KEY não configurada"}
        from app.services import personalizado_service
        palavra = await personalizado_service.extrair_palavra_chave_de_link_social(
            s.entrada, anthropic_api_key=settings.anthropic_api_key,
        )
        if not palavra:
            await _marcar_falhou(
                db, s, "IA não conseguiu identificar produto no link social.",
            )
            return {"ok": False, "erro": "IA não identificou produto"}
        tipo_busca   = "termo_livre"
        tipo_entrada = "termo"
        max_produtos = 10
        entrada      = palavra
    else:
        tipo_busca   = "termo_livre"
        tipo_entrada = "termo"
        max_produtos = 10
        entrada      = s.entrada

    # Pega 1º agente online da org central
    agentes = list((await db.execute(
        select(Agente).where(
            Agente.org_id == settings.admin_org_id,
            Agente.ativo.is_(True),
        )
    )).scalars().all())
    agente = next((a for a in agentes if registry.esta_online(a.id)), None)
    if agente is None:
        return {
            "ok":   False,
            "erro": "Nenhum agente do admin central online. Tente novamente quando o agente do admin estiver rodando.",
        }

    # Cria tarefa marcando solicitacao_id pro hook pós-conclusão.
    # `_personalizado_criador_id` faz o `_upsert_produto` salvar produto
    # com `criado_por_usuario_id` do solicitante — assim ele vê em
    # `/produtos/personalizados`.
    tarefa = Tarefa(
        org_id=settings.admin_org_id,
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
            # Marcadores pra ingest gravar produto como personalizado do solicitante:
            "_personalizado_criador_id": s.usuario_id,
            # Hook pós-conclusão usa pra atualizar status da fila:
            "solicitacao_id": s.id,
        },
        criado_por_usuario_id=(admin.id if admin else None),
    )
    db.add(tarefa)
    await db.flush()

    # Atualiza solicitação
    s.status = StatusSolicitacao.PROCESSANDO.value
    s.tarefa_id = tarefa.id
    s.processado_em = datetime.now(tz=timezone.utc)
    await db.commit()
    await db.refresh(tarefa)

    # Entrega via WS
    await dispatcher._tentar_entrega(db, tarefa)

    log.info("solicitacao.processando",
             solicitacao_id=s.id, tarefa_id=tarefa.id,
             agente_id=agente.id)
    return {
        "ok":        True,
        "tarefa_id": tarefa.id,
        "mensagem":  f"Solicitação #{s.id} enfileirada (tarefa #{tarefa.id}).",
    }


async def rejeitar_solicitacao(
    db: AsyncSession, *, solicitacao_id: int, motivo: str | None = None,
) -> bool:
    """Admin rejeita manualmente — não vai pro agente."""
    s = await db.get(SolicitacaoPersonalizada, solicitacao_id)
    if s is None or s.status != StatusSolicitacao.PENDENTE.value:
        return False
    s.status = StatusSolicitacao.REJEITADA.value
    s.mensagem_erro = (motivo or "Rejeitada pelo admin.")[:500]
    s.concluido_em = datetime.now(tz=timezone.utc)
    await db.commit()
    log.info("solicitacao.rejeitada", solicitacao_id=solicitacao_id)
    return True


async def marcar_concluida_via_hook(
    db: AsyncSession,
    *,
    solicitacao_id: int,
    produtos_criados: int,
    ok: bool,
    mensagem_erro: str | None = None,
) -> None:
    """Hook chamado por `dispatcher.marcar_concluida/falhou` quando uma
    tarefa com `payload.solicitacao_id` termina.

    Atualiza a SolicitacaoPersonalizada pra `concluida` ou `falhou`.
    """
    s = await db.get(SolicitacaoPersonalizada, solicitacao_id)
    if s is None:
        log.warning("solicitacao.hook_inexistente", solicitacao_id=solicitacao_id)
        return
    if s.status not in (
        StatusSolicitacao.PROCESSANDO.value,
        StatusSolicitacao.PENDENTE.value,
    ):
        # já tratada anteriormente (ex: rejeitada)
        return

    if ok:
        s.status = StatusSolicitacao.CONCLUIDA.value
        s.produtos_criados = produtos_criados
        s.concluido_em = datetime.now(tz=timezone.utc)
        log.info("solicitacao.concluida",
                 solicitacao_id=solicitacao_id,
                 produtos_criados=produtos_criados)
    else:
        s.status = StatusSolicitacao.FALHOU.value
        s.mensagem_erro = (mensagem_erro or "Agente falhou ao processar.")[:500]
        s.concluido_em = datetime.now(tz=timezone.utc)
        log.warning("solicitacao.falhou",
                    solicitacao_id=solicitacao_id, erro=mensagem_erro)
    await db.commit()


async def _marcar_falhou(
    db: AsyncSession,
    s: SolicitacaoPersonalizada,
    erro: str,
) -> None:
    """Marca interna — usado quando o próprio processar() detecta erro
    (ex: IA não identificou produto)."""
    s.status = StatusSolicitacao.FALHOU.value
    s.mensagem_erro = erro[:500]
    s.concluido_em = datetime.now(tz=timezone.utc)
    await db.commit()

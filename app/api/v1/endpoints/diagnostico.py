"""
Endpoints de diagnóstico — admin-only.

Pra investigar bugs sem precisar de SSH no Railway. Retorna JSON
estruturado com últimas tarefas + resultado + motivos de ignorados
+ count produtos + últimas inserções.

USO:
  GET /api/v1/_diag/busca?org_id=N        — diagnóstico de buscas
  GET /api/v1/_diag/busca?org_id=N&tarefa_id=X  — detalhe de UMA tarefa

`org_id` opcional. Default = org do user logado. Admin central pode
passar qualquer org.
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import usuario_admin
from app.db import get_db_async
from app.models import Agente, LogEntry, Produto, StatusTarefa, Tarefa, Usuario
from app.services.agente_registry import registry

router = APIRouter(prefix="/_diag", tags=["diagnóstico"])


def _resolver_org_id(user: Usuario, org_id_param: int | None) -> int:
    """Admin central pode investigar qualquer org. Outros, só a própria."""
    if org_id_param is None:
        return user.org_id
    if not user.eh_admin_central and org_id_param != user.org_id:
        raise HTTPException(
            status_code=403,
            detail="Você só pode diagnosticar a própria org",
        )
    return org_id_param


def _tarefa_resumo(t: Tarefa) -> dict[str, Any]:
    """Resumo de uma tarefa pra diagnóstico — inclui resultado COMPLETO
    (incl. lista `detalhes` com motivos de ignorados) + erro + payload
    resumido."""
    payload = t.payload or {}
    return {
        "id":                  t.id,
        "tipo":                t.tipo.value if hasattr(t.tipo, "value") else str(t.tipo),
        "status":              t.status.value if hasattr(t.status, "value") else str(t.status),
        "tentativas":          t.tentativas,
        "criado_em":           t.criado_em.isoformat() if t.criado_em else None,
        "iniciado_em":         t.iniciado_em.isoformat() if t.iniciado_em else None,
        "concluido_em":        t.concluido_em.isoformat() if t.concluido_em else None,
        "duracao_seg":         t.duracao_seg,
        "progresso_pct":       t.progresso_pct,
        "progresso_mensagem":  t.progresso_mensagem,
        "erro":                t.erro,
        "resultado":           t.resultado,  # FULL — inclui detalhes dos ignorados
        "payload_resumo": {
            "tipo_busca":      payload.get("tipo_busca"),
            "termo":           payload.get("termo"),
            "marketplaces":    payload.get("marketplaces"),
            "max_produtos":    payload.get("max_produtos"),
            "categorias_alvo": payload.get("categorias_alvo"),
            "personalizado":   bool(payload.get("_personalizado_criador_id")),
        },
        "agente_id":           t.agente_id,
        "criado_por_usuario_id": t.criado_por_usuario_id,
    }


@router.get("/busca")
async def diagnostico_busca(
    org_id: int | None = None,
    tarefa_id: int | None = None,
    limite_tarefas: int = 20,
    limite_produtos: int = 10,
    janela_horas: int = 24,
    user: Usuario = Depends(usuario_admin),
    db: AsyncSession = Depends(get_db_async),
) -> dict[str, Any]:
    """Snapshot do estado do pipeline de buscas pra investigar bugs.

    Retorna:
      - org: id + nome
      - me: user logado (papel + admin_central + super)
      - tarefas_recentes: últimas N tarefas (resultado FULL inclui detalhes
        dos ignorados — `resultado.detalhes` = lista de motivos por item_id)
      - tarefas_falhou_recentes: separa as que FALHOU pro topo
      - produtos: total na org + total nas últimas Xh + últimos N criados
      - agentes_online: quem está conectado no WS agora (do registry)
    """
    org_alvo = _resolver_org_id(user, org_id)
    agora = datetime.now(tz=timezone.utc)
    desde = agora - timedelta(hours=janela_horas)

    # Org info
    from app.models import Organizacao
    org = await db.get(Organizacao, org_alvo)

    # Se passou tarefa_id, foca só nessa
    if tarefa_id is not None:
        t = await db.get(Tarefa, tarefa_id)
        if t is None or t.org_id != org_alvo:
            raise HTTPException(status_code=404, detail="Tarefa não encontrada")
        return {
            "org_id": org_alvo,
            "org_nome": org.nome if org else None,
            "tarefa": _tarefa_resumo(t),
        }

    # Últimas N tarefas (qualquer status)
    rows = list((await db.execute(
        select(Tarefa)
        .where(Tarefa.org_id == org_alvo)
        .order_by(desc(Tarefa.criado_em))
        .limit(limite_tarefas)
    )).scalars().all())
    tarefas_recentes = [_tarefa_resumo(t) for t in rows]

    # Tarefas que falharam nas últimas 24h (em separado pra destacar)
    falhou_rows = list((await db.execute(
        select(Tarefa)
        .where(
            Tarefa.org_id == org_alvo,
            Tarefa.status == StatusTarefa.FALHOU,
            Tarefa.criado_em >= desde,
        )
        .order_by(desc(Tarefa.criado_em))
        .limit(20)
    )).scalars().all())
    tarefas_falhou_recentes = [_tarefa_resumo(t) for t in falhou_rows]

    # Tarefas CONCLUIDA mas com ignorados > 0 (sintoma típico do bug atual!)
    sem_efeito_rows = list((await db.execute(
        select(Tarefa)
        .where(
            Tarefa.org_id == org_alvo,
            Tarefa.status == StatusTarefa.CONCLUIDA,
            Tarefa.criado_em >= desde,
        )
        .order_by(desc(Tarefa.criado_em))
        .limit(30)
    )).scalars().all())
    tarefas_concluidas_sem_criar = []
    for t in sem_efeito_rows:
        res = t.resultado or {}
        if res.get("recebidos", 0) > 0 and res.get("criados", 0) == 0:
            tarefas_concluidas_sem_criar.append(_tarefa_resumo(t))

    # Count de produtos
    total_produtos = await db.scalar(
        select(func.count()).select_from(Produto).where(Produto.org_id == org_alvo)
    ) or 0
    total_produtos_recentes = await db.scalar(
        select(func.count()).select_from(Produto).where(
            Produto.org_id == org_alvo,
            Produto.descoberto_em >= desde,
        )
    ) or 0

    # Últimos N produtos criados
    produtos_rows = list((await db.execute(
        select(Produto)
        .where(Produto.org_id == org_alvo)
        .order_by(desc(Produto.descoberto_em))
        .limit(limite_produtos)
    )).scalars().all())
    produtos_recentes = [
        {
            "id":              p.id,
            "plataforma":      p.plataforma,
            "item_id":         p.item_id,
            "nome":            (p.nome or "")[:80],
            "preco":           p.preco,
            "comissao":        p.comissao,
            "comissao_fonte":  p.comissao_fonte,
            "url_afiliado":    (p.url_afiliado or "")[:100],
            "usuario_dono_id": p.usuario_dono_id,
            "criado_por_usuario_id": p.criado_por_usuario_id,
            "descoberto_em":   p.descoberto_em.isoformat() if p.descoberto_em else None,
        }
        for p in produtos_rows
    ]

    # Agentes online da org: pega IDs do registry (memória) e cruza com
    # a tabela `agentes` pra confirmar que pertencem à org alvo.
    todos_ids_online = set(registry._conexoes.keys())  # noqa: SLF001
    if todos_ids_online:
        agentes_org_rows = (await db.execute(
            select(Agente.id, Agente.nome, Agente.usuario_id).where(
                Agente.org_id == org_alvo,
                Agente.id.in_(todos_ids_online),
            )
        )).all()
        agentes_online = [
            {"id": r.id, "nome": r.nome, "usuario_id": r.usuario_id}
            for r in agentes_org_rows
        ]
    else:
        agentes_online = []

    # Todos os agentes da org (online ou não) — útil pra ver se o agente
    # registrado existe mas não está conectado.
    agentes_org_todos = list((await db.execute(
        select(Agente).where(Agente.org_id == org_alvo)
    )).scalars().all())
    agentes_org_view = [
        {
            "id":           a.id,
            "nome":         a.nome,
            "usuario_id":   a.usuario_id,
            "ativo":        a.ativo,
            "online_db":    a.online,
            "ultimo_ping":  a.ultimo_ping.isoformat() if a.ultimo_ping else None,
            "versao_app":   a.versao_app,
            "online_ws":    a.id in todos_ids_online,
        }
        for a in agentes_org_todos
    ]

    return {
        "agora":             agora.isoformat(),
        "janela_horas":      janela_horas,
        "org_id":            org_alvo,
        "org_nome":          org.nome if org else None,
        "me": {
            "id":               user.id,
            "login":            user.login,
            "papel":            user.papel,
            "org_id":           user.org_id,
            "eh_admin":         user.eh_admin,
            "eh_admin_central": user.eh_admin_central,
            "eh_super":         user.eh_super,
        },
        "produtos": {
            "total_na_org":     int(total_produtos),
            f"criados_em_{janela_horas}h": int(total_produtos_recentes),
            "ultimos":          produtos_recentes,
        },
        "agentes_online": {
            "lista": agentes_online,
            "total": len(agentes_online),
        },
        "agentes_da_org": agentes_org_view,
        # Os 3 blocos críticos pra diagnosticar o bug
        # "agente busca mas produtos não aparecem":
        "tarefas_concluidas_sem_criar_produto": tarefas_concluidas_sem_criar,
        "tarefas_falhou_recentes":              tarefas_falhou_recentes,
        "tarefas_recentes":                     tarefas_recentes,
    }


# ═════════════════════════════════════════════════════════════════════
#  LOGS PERSISTENTES (alimentados pelo processor structlog + worker
#  em `app/core/log_buffer.py`)
# ═════════════════════════════════════════════════════════════════════

def _entry_dict(e: LogEntry) -> dict[str, Any]:
    return {
        "id":         e.id,
        "ts":         e.ts.isoformat() if e.ts else None,
        "nivel":      e.nivel,
        "evento":     e.evento,
        "mensagem":   e.mensagem,
        "contexto":   e.contexto or {},
        "source":     e.source,
        "tarefa_id":  e.tarefa_id,
        "org_id":     e.org_id,
        "agente_id":  e.agente_id,
    }


@router.get("/logs/jobs")
async def logs_jobs_recentes(
    org_id: int | None = None,
    janela_horas: int = Query(72, ge=1, le=720),
    limite: int = Query(50, ge=1, le=200),
    user: Usuario = Depends(usuario_admin),
    db: AsyncSession = Depends(get_db_async),
) -> dict[str, Any]:
    """Lista de "jobs" recentes — agrupado por tarefa_id pra UI estilo
    Railway: dropdown 'Logs de jobs antigos'. Cada entry traz primeiro/último
    timestamp, count de linhas e tipo da tarefa.
    """
    org_alvo = _resolver_org_id(user, org_id)
    desde = datetime.now(tz=timezone.utc) - timedelta(hours=janela_horas)

    rows = list((await db.execute(
        select(
            LogEntry.tarefa_id,
            func.count().label("linhas"),
            func.min(LogEntry.ts).label("primeiro"),
            func.max(LogEntry.ts).label("ultimo"),
            func.max(LogEntry.nivel).label("nivel_max"),
        )
        .where(
            LogEntry.tarefa_id.is_not(None),
            LogEntry.ts >= desde,
        )
        .group_by(LogEntry.tarefa_id)
        .order_by(desc(func.max(LogEntry.ts)))
        .limit(limite * 3)  # margem pra filtrar por org depois
    )).all())

    # Pega info das tarefas referenciadas pra mostrar tipo
    tarefa_ids = [r.tarefa_id for r in rows if r.tarefa_id]
    tarefas_info: dict[int, Tarefa] = {}
    if tarefa_ids:
        t_rows = (await db.execute(
            select(Tarefa).where(Tarefa.id.in_(tarefa_ids))
        )).scalars().all()
        for t in t_rows:
            tarefas_info[t.id] = t

    out: list[dict[str, Any]] = []
    for r in rows:
        t = tarefas_info.get(r.tarefa_id)
        if t is None:
            continue
        # Filtra por org (admin central vê tudo, demais só a própria)
        if not user.eh_admin_central and t.org_id != user.org_id:
            continue
        if org_id is not None and t.org_id != org_alvo:
            continue
        out.append({
            "tarefa_id": r.tarefa_id,
            "tipo":      t.tipo.value if hasattr(t.tipo, "value") else str(t.tipo),
            "status":    t.status.value if hasattr(t.status, "value") else str(t.status),
            "org_id":    t.org_id,
            "linhas":    r.linhas,
            "primeiro":  r.primeiro.isoformat() if r.primeiro else None,
            "ultimo":    r.ultimo.isoformat() if r.ultimo else None,
            "nivel_max": r.nivel_max,
            "criado_em": t.criado_em.isoformat() if t.criado_em else None,
        })
        if len(out) >= limite:
            break

    return {"jobs": out, "total": len(out), "janela_horas": janela_horas}


@router.get("/logs")
async def listar_logs(
    org_id:    int | None = None,
    tarefa_id: int | None = None,
    nivel:     str | None = Query(None, pattern="^(INFO|WARNING|ERROR|CRITICAL)$"),
    janela_horas: int = Query(24, ge=1, le=720),
    limite: int = Query(500, ge=1, le=5000),
    user: Usuario = Depends(usuario_admin),
    db: AsyncSession = Depends(get_db_async),
) -> dict[str, Any]:
    """Histórico de logs (ordem ts ASC pra leitura como terminal)."""
    org_alvo = _resolver_org_id(user, org_id)
    desde = datetime.now(tz=timezone.utc) - timedelta(hours=janela_horas)

    base = select(LogEntry).where(LogEntry.ts >= desde)

    if tarefa_id is not None:
        # Pra mostrar logs de UMA tarefa, ignora filtro de org (admin pode
        # pedir tarefa de outra org se sabe o id). Mas valida visibilidade:
        t = await db.get(Tarefa, tarefa_id)
        if t is None:
            raise HTTPException(status_code=404, detail="Tarefa não encontrada")
        if not user.eh_admin_central and t.org_id != user.org_id:
            raise HTTPException(status_code=403, detail="Sem acesso a esta tarefa")
        base = base.where(LogEntry.tarefa_id == tarefa_id)
    else:
        # Sem tarefa específica: filtra por org (admin central vê tudo se
        # não passar org_id; passou org_id → respeitado).
        if user.eh_admin_central:
            if org_id is not None:
                base = base.where(LogEntry.org_id == org_alvo)
            # Senão: vê tudo (org_id pode ser NULL pra logs do sistema)
        else:
            base = base.where(LogEntry.org_id == user.org_id)

    if nivel is not None:
        # Inclui o nível pedido + acima (ERROR ⊃ CRITICAL).
        ordem = ["INFO", "WARNING", "ERROR", "CRITICAL"]
        try:
            i = ordem.index(nivel)
            base = base.where(LogEntry.nivel.in_(ordem[i:]))
        except ValueError:
            pass

    # Ordem ASC pra UI rolar de cima pra baixo igual terminal.
    rows = list((await db.execute(
        base.order_by(LogEntry.ts.desc()).limit(limite)
    )).scalars().all())
    rows.reverse()  # devolve ASC pro client (já limitado)

    return {
        "logs":          [_entry_dict(e) for e in rows],
        "total":         len(rows),
        "janela_horas":  janela_horas,
        "tarefa_id":     tarefa_id,
        "org_id":        org_alvo if org_id is not None else None,
        "nivel_minimo":  nivel,
    }


async def _gerar_eventos_sse(request: Request, canais: list[str]):
    """Generator async pra StreamingResponse — yield "data: <json>\\n\\n"
    pra cada mensagem publicada no Redis."""
    from app.core.redis import get_redis

    r = get_redis()
    pubsub = r.pubsub()
    try:
        await pubsub.subscribe(*canais)
        # Heartbeat inicial pro client saber que conectou
        yield ": connected\n\n"

        while True:
            # Se client fechou a aba, sai
            if await request.is_disconnected():
                break

            # Timeout curto pra checar disconnected periodicamente
            msg = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=5.0,
            )
            if msg is None:
                # Heartbeat (comentário SSE) pra manter conexão viva
                yield ": ping\n\n"
                continue

            # data = string (decode_responses=True no Redis)
            data = msg.get("data")
            if not data:
                continue
            # Já está em JSON do publisher. Envia como single line.
            yield f"data: {data}\n\n"
    finally:
        try:
            await pubsub.unsubscribe()
            await pubsub.aclose()
        except Exception:
            pass


@router.get("/logs/stream")
async def stream_logs(
    request: Request,
    org_id: int | None = None,
    user: Usuario = Depends(usuario_admin),
):
    """Server-Sent Events: stream de logs em tempo real via Redis pub/sub.

    Frontend conecta com `new EventSource('/api/v1/_diag/logs/stream')`.
    Cada event tem payload JSON serializado igual a `_entry_dict`.

    Filtragem por org: admin central pode passar `?org_id=N` ou ver TUDO
    (canal "logs:all"). Demais: força no próprio org_id.
    """
    if user.eh_admin_central:
        if org_id is not None:
            canais = [f"logs:org:{org_id}"]
        else:
            canais = ["logs:all"]
    else:
        canais = [f"logs:org:{user.org_id}"]

    return StreamingResponse(
        _gerar_eventos_sse(request, canais),
        media_type="text/event-stream",
        headers={
            # SSE precisa desses headers pra funcionar atrás de proxy
            "Cache-Control":   "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection":      "keep-alive",
        },
    )

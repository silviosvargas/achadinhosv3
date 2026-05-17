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
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import usuario_admin
from app.db import get_db_async
from app.models import Agente, Produto, StatusTarefa, Tarefa, Usuario
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

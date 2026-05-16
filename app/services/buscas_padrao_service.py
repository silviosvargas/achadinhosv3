"""
Service de buscas PADRÃO (Fase 19).

Buscas padrão são fixadas no código (`app/core/buscas_padrao.py`) — elas
NÃO usam a tabela `buscas_ml` (que é pras buscas customizadas dos users).

Quando admin clica "Rodar agora" numa busca padrão, este service cria
uma Tarefa(BUSCAR_MERCADO_LIVRE) com payload especial que o agente
reconhece pelo `tipo_busca=padrao_mais_vendidos_completo` (ou outro slug).

Resultado vem pelo ingest normal — `busca_service._upsert_produto` salva
produtos com `comissao_fonte=ml_barra_afiliados` (porque o agente capturou
a comissão real na barra preta).
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.buscas_padrao import buscar_por_slug
from app.core.logging import get_logger
from app.models import Agente, StatusTarefa, Tarefa, TipoTarefa
from app.services import dispatcher
from app.services.agente_registry import registry

log = get_logger(__name__)


class BuscaPadraoServiceError(Exception):
    """Erro de domínio (busca não existe, sem agente online, etc)."""


async def disparar(
    db: AsyncSession,
    *,
    slug: str,
    org_id: int,
    criado_por_usuario_id: int | None = None,
) -> dict:
    """Cria Tarefa(BUSCAR_MERCADO_LIVRE) com payload da busca padrão e
    tenta entregar via WS pro agente online da org.

    Returns:
        {"ok": True, "tarefa_id": N, "mensagem": "..."}
        ou {"ok": False, "erro": "..."}
    """
    busca = buscar_por_slug(slug)
    if busca is None:
        raise BuscaPadraoServiceError(f"Busca padrão '{slug}' não existe")
    if not busca.get("ativa"):
        raise BuscaPadraoServiceError(f"Busca padrão '{slug}' está inativa")

    # 1º agente online da org
    agentes = list((await db.execute(
        select(Agente).where(
            Agente.org_id == org_id, Agente.ativo.is_(True),
        )
    )).scalars().all())
    agente = next((a for a in agentes if registry.esta_online(a.id)), None)
    if agente is None:
        return {
            "ok":   False,
            "erro": "Nenhum agente online — abra o AchadinhosAgent no PC primeiro.",
        }

    # Payload da tarefa — o agente lê `tipo_busca` em `executar_busca` e roteia
    # pra `_varrer_padrao_mais_vendidos_completo` (Fase 19).
    payload = {
        "tipo_busca":   busca["tipo_busca"],
        "marketplaces": busca["marketplaces"],
        "max_produtos": busca["max_produtos"],
        "candidatos_por_categoria": busca.get("candidatos_por_categoria", 20),
        "slug_padrao":  slug,
        "disparado_por": criado_por_usuario_id,
    }
    # Busca padrão com alvo de N produtos (filtra/stop-early). Quando definido,
    # agente lê esse campo e para ao juntar N produtos válidos.
    if "alvo_total" in busca:
        payload["alvo_total"] = busca["alvo_total"]
    # Mínimo de produtos válidos POR CATEGORIA (busca extras v3.8.5+).
    # Agente itera candidatos da categoria até atingir esse mínimo (ou esgotar
    # candidatos da categoria) e passa pra próxima categoria.
    if "min_por_categoria" in busca:
        payload["min_por_categoria"] = busca["min_por_categoria"]

    tarefa = Tarefa(
        org_id=org_id,
        tipo=TipoTarefa.BUSCAR_MERCADO_LIVRE,
        status=StatusTarefa.PENDENTE,
        agente_id=agente.id,
        payload=payload,
        criado_por_usuario_id=criado_por_usuario_id,
    )
    db.add(tarefa)
    await db.commit()
    await db.refresh(tarefa)

    await dispatcher._tentar_entrega(db, tarefa)

    log.info("buscas_padrao.disparada",
             slug=slug, org_id=org_id, tarefa_id=tarefa.id,
             agente_id=agente.id)

    # Mensagem amigável — busca pode especificar texto próprio em `mensagem_run`.
    # Senão usa fallback genérico baseado em `candidatos_por_categoria` quando
    # presente (típico ML), ou só nome + tarefa_id pra outras.
    if "mensagem_run" in busca:
        msg = busca["mensagem_run"].format(
            nome=busca["nome"], tarefa_id=tarefa.id,
        )
    elif "candidatos_por_categoria" in busca:
        msg = (
            f"'{busca['nome']}' enfileirada (tarefa #{tarefa.id}). "
            f"Agente vai abrir cada categoria, capturar comissão real de "
            f"~{busca['candidatos_por_categoria']} candidatos, e ingestar os "
            f"melhores. Demora ~8min."
        )
    else:
        msg = (
            f"'{busca['nome']}' enfileirada (tarefa #{tarefa.id}). "
            f"Acompanhe o progresso no dashboard."
        )

    return {
        "ok":        True,
        "tarefa_id": tarefa.id,
        "mensagem":  msg,
    }

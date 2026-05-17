"""
Service de buscas Mercado Livre — ingest e roteamento.

Fluxo:
1. Admin (ou Celery beat) chama `enfileirar_execucao(busca_id)` que cria uma
   Tarefa(BUSCAR_MERCADO_LIVRE) e tenta entregar via WS pro agente. Atualiza
   `proxima_exec_em` da busca.
2. Agente roda Selenium, extrai produtos, e chama
   `POST /api/v1/produtos/ingest` com o lote.
3. Endpoint chama `ingerir_produtos(...)` aqui, que:
   - Resolve tag de afiliado e dono baseado em quem disparou a busca.
   - Faz upsert por (org, [dono], plataforma, item_id).
   - Aplica mapping `categoria_ml → nicho_id` automaticamente.
   - Marca tarefa como concluida.

Regras de tag / visibilidade (ADR-008):
- Busca disparada por admin/usuario_comum → produtos públicos (dono=NULL),
  tag do admin da org.
- Busca disparada por afiliado → produtos privados (dono=afiliado),
  tag dele próprio (`Usuario.afiliado_ml`).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse, urlunparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import (
    BuscaML,
    NichoCategoriaML,
    Produto,
    ProdutoNicho,
    StatusTarefa,
    Tarefa,
    TipoTarefa,
    Usuario,
)
from app.services import linkbuilder
from app.services.agente_registry import registry
from app.services.scoring import calcular_nota

log = get_logger(__name__)


def _limpar_url_canonica(url: str | None) -> str | None:
    """Tira fragment + query (lixo de scraping: `#polycard_client=...`,
    `?tracking_id=...`). URL canônica ML é estável só com path + host.

    Necessário pra:
    1) Estabilizar match com `meli.la` mapping no `aplicar_mapping`.
    2) Não estourar limite de 2000 chars do `url_canonica` no DB.
    3) Compartilhar URL pro user sem expor tracking interno.
    """
    if not url:
        return url
    parts = urlparse(url)
    return urlunparse(parts._replace(query="", fragment=""))


class BuscaServiceError(Exception):
    """Erro de negócio em operação de busca."""


# ============================================================
# Detecção termo vs URL
# ============================================================

def detectar_tipo_entrada(entrada: str) -> str:
    """'url' se começa com http(s)://, senão 'termo'."""
    entrada_low = entrada.strip().lower()
    if entrada_low.startswith(("http://", "https://")):
        return "url"
    return "termo"


# ============================================================
# Enfileirar execução de busca
# ============================================================

async def enfileirar_execucao(
    db: AsyncSession,
    *,
    busca_id: int,
    org_id: int,
    criado_por_usuario_id: int | None = None,
) -> Tarefa:
    """
    Cria Tarefa(BUSCAR_MERCADO_LIVRE) e entrega via WS se agente online.
    Senão fica pendente. Atualiza estado da busca (próxima exec, contagem).
    """
    busca = await db.get(BuscaML, busca_id)
    if busca is None or busca.org_id != org_id:
        raise BuscaServiceError("Busca não encontrada nesta organização")
    if not busca.ativo:
        raise BuscaServiceError("Busca está inativa")

    # Quem dispara: o usuário que clicou (criado_por_usuario_id) OU
    # o dono original da busca (busca.criado_por_usuario_id) se for agendada.
    disparado_por = criado_por_usuario_id or busca.criado_por_usuario_id

    tipo_entrada = detectar_tipo_entrada(busca.entrada)

    # Fase 16: parseia marketplaces (JSON string no DB) pra lista no payload
    import json as _json
    try:
        marketplaces_list = _json.loads(busca.marketplaces or '["ml"]')
        if not isinstance(marketplaces_list, list):
            marketplaces_list = ["ml"]
    except (_json.JSONDecodeError, TypeError):
        marketplaces_list = ["ml"]

    tarefa = Tarefa(
        org_id=org_id,
        tipo=TipoTarefa.BUSCAR_MERCADO_LIVRE,
        status=StatusTarefa.PENDENTE,
        agente_id=busca.agente_id,
        payload={
            "busca_id":      busca.id,
            "tipo_entrada":  tipo_entrada,
            "entrada":       busca.entrada,
            "max_paginas":   busca.max_paginas,
            "max_produtos":  busca.max_produtos,
            "disparado_por": disparado_por,
            # Fase 16: agente decide URLs/strategy baseado no tipo_busca.
            # CHAVE INTENCIONAL "tipo_busca" (não "tipo") — `dispatcher._tentar_entrega`
            # faz `**tarefa.payload` na hora de montar a mensagem WS, e a chave
            # de topo do WS já é "tipo" (= comando como "iniciar_busca_ml").
            # Se chamássemos isso de "tipo" aqui, o spread sobrescreveria o
            # comando WS — agente receberia tipo="mais_vendidos" e cairia em
            # `ws.tipo_sem_handler`.
            "tipo_busca":   getattr(busca, "tipo", "termo_livre"),
            "marketplaces": marketplaces_list,
        },
        criado_por_usuario_id=criado_por_usuario_id,
    )
    db.add(tarefa)
    await db.commit()
    await db.refresh(tarefa)

    # Atualiza estado da busca
    agora = datetime.now(tz=timezone.utc)
    busca.ultima_exec_em   = agora
    busca.ultima_tarefa_id = tarefa.id
    busca.execucoes       += 1
    if busca.intervalo_minutos:
        busca.proxima_exec_em = agora + timedelta(minutes=busca.intervalo_minutos)
    await db.commit()

    # Tenta entregar via WS se agente específico online
    if busca.agente_id and registry.esta_online(busca.agente_id):
        await _entregar_para_agente(db, tarefa, agente_id=busca.agente_id)
    else:
        # Sem agente específico: pega qualquer agente da org online
        # (Fase futura: round-robin entre agentes). Por enquanto: deixa pendente
        # e o agente puxa via reentregar_pendentes quando reconectar.
        log.info("busca.aguarda_agente", busca_id=busca.id, tarefa_id=tarefa.id)

    return tarefa


async def _entregar_para_agente(
    db: AsyncSession, tarefa: Tarefa, *, agente_id: int,
) -> None:
    """Envia comando `iniciar_busca_ml` via WS."""
    # Spread PRIMEIRO; tipo/tarefa_id sobrescrevem por último. Defesa contra
    # tarefa legada com "tipo" no payload (mesmo motivo do dispatcher).
    payload = {
        **(tarefa.payload or {}),
        "tipo":         "iniciar_busca_ml",
        "tarefa_id":    tarefa.id,
    }
    enviado = await registry.enviar_para(agente_id, payload)
    if enviado:
        tarefa.status      = StatusTarefa.PROCESSANDO
        tarefa.iniciado_em = datetime.now(tz=timezone.utc)
        tarefa.tentativas += 1
        await db.commit()
        log.info("busca.entregue", tarefa_id=tarefa.id, agente_id=agente_id)


# ============================================================
# Ingest — recebe produtos do agente e popula catálogo
# ============================================================

async def ingerir_produtos(
    db: AsyncSession,
    *,
    org_id: int,
    agente_id: int,
    produtos_recebidos: list[dict[str, Any]],
    busca_id: int | None = None,
    tarefa_id: int | None = None,
) -> dict[str, Any]:
    """
    Recebe lote de produtos extraídos pelo agente. Faz upsert respeitando:
    - tag de afiliado de quem disparou a busca
    - visibilidade pública vs privada
    - mapping categoria_ml → nicho_id (auto-classificação)

    Retorna estatísticas pra resposta ao agente.
    """
    stats = {
        "recebidos":   len(produtos_recebidos),
        "criados":     0,
        "atualizados": 0,
        "ignorados":   0,
        "com_nicho":   0,
        "detalhes":    [],
    }

    if not produtos_recebidos:
        return stats

    # 1. Descobre quem disparou pra resolver tag + dono
    disparador, dono_id = await _resolver_disparador(
        db, org_id=org_id, tarefa_id=tarefa_id, busca_id=busca_id,
    )
    # Cascata de fallback pra tag de afiliado por plataforma (Fase 13).
    # Coleta tags pra TODAS as plataformas que aparecem no lote — usadas
    # pra validar que `url_afiliado` do agente contém a tag do admin
    # (senão é link de OUTRA pessoa e a comissão vai pra ela, não pra gente).
    from app.services import afiliado_service

    plataformas_no_lote = {
        (i.get("plataforma") or "ml").lower() for i in produtos_recebidos
    }
    tags_por_plataforma: dict[str, str | None] = {}
    for plat in plataformas_no_lote:
        tags_por_plataforma[plat] = await afiliado_service.tag_com_cascata(
            db,
            plataforma=plat,
            usuario_id=disparador.id if disparador else None,
            org_id=org_id,
        )
    tag_ml = tags_por_plataforma.get("ml")   # retrocompat: ainda passamos `tag_ml`

    # 2. Carrega mapping categoria → nicho da org (1 query)
    mapping_rows = (await db.execute(
        select(NichoCategoriaML.categoria_ml, NichoCategoriaML.nicho_id)
        .where(NichoCategoriaML.org_id == org_id)
    )).all()
    mapping: dict[str, int] = {c.lower(): n for c, n in mapping_rows}

    # 2.1. Detecta se é busca PERSONALIZADA (Fase 17). Marcador vem no
    # payload da tarefa quando o user dispara via /produtos/personalizados.
    # Resultado: products viram `fonte=personalizado` + dono apropriado.
    personalizado_dono_id: int | None = None
    personalizado_criador_id: int | None = None
    eh_busca_personalizada = False
    if tarefa_id:
        tarefa = await db.get(Tarefa, tarefa_id)
        if tarefa and tarefa.payload and tarefa.payload.get("_personalizado_criador_id"):
            criador_id = int(tarefa.payload["_personalizado_criador_id"])
            criador = await db.get(Usuario, criador_id)
            if criador:
                eh_busca_personalizada = True
                personalizado_criador_id = criador_id
                # Regra de dono (Fase 17):
                # - Afiliado COM tag pra ML → produto privado dele (`dono_id=user.id`)
                # - Senão (admin/usuário/afiliado sem tag) → público (`dono_id=NULL`)
                # → admin posta com tag dele
                from app.services import afiliado_service
                tag_user = await afiliado_service.tag_com_cascata(
                    db, plataforma="ml", usuario_id=criador.id, org_id=org_id,
                )
                # `tag_com_cascata` faz fallback pra admin. Pra saber se o user
                # tem tag PRÓPRIA (não da cascata), checa direto na tabela:
                from app.models import UsuarioAfiliado
                tem_tag_propria = (await db.execute(
                    select(UsuarioAfiliado).where(
                        UsuarioAfiliado.usuario_id == criador.id,
                        UsuarioAfiliado.plataforma == "ml",
                    ).limit(1)
                )).scalar_one_or_none() is not None
                if criador.eh_afiliado and tem_tag_propria:
                    personalizado_dono_id = criador.id  # privado
                else:
                    personalizado_dono_id = None        # público

    # 3. Upsert em loop
    for item in produtos_recebidos:
        if eh_busca_personalizada:
            item["fonte"] = "personalizado"
            item["_personalizado_dono_id"] = personalizado_dono_id
            item["_personalizado_criador_id"] = personalizado_criador_id
        try:
            criou, com_nicho = await _upsert_produto(
                db,
                org_id=org_id,
                dono_id=dono_id,
                tag_ml=tag_ml,
                tags_por_plataforma=tags_por_plataforma,
                item=item,
                mapping_categoria=mapping,
            )
            if criou:
                stats["criados"] += 1
            else:
                stats["atualizados"] += 1
            if com_nicho:
                stats["com_nicho"] += 1
        except Exception as e:
            stats["ignorados"] += 1
            stats["detalhes"].append(
                f"item_id={item.get('item_id', '?')}: {type(e).__name__}: {str(e)[:120]}"
            )

    await db.commit()

    # 4. Marca tarefa como concluída
    if tarefa_id:
        tarefa = await db.get(Tarefa, tarefa_id)
        if tarefa and tarefa.org_id == org_id:
            tarefa.status = StatusTarefa.CONCLUIDA
            tarefa.concluido_em = datetime.now(tz=timezone.utc)
            tarefa.resultado = {
                "recebidos":   stats["recebidos"],
                "criados":     stats["criados"],
                "atualizados": stats["atualizados"],
                "ignorados":   stats["ignorados"],
                "com_nicho":   stats["com_nicho"],
                # Detalhes dos ignorados (motivo + item_id) — sem isso a UI
                # mostra só "CONCLUIDA com N ignorados" e o user não tem
                # como diagnosticar o bug. Truncado em 50 entries pra não
                # estourar a coluna JSON do Postgres em buscas gigantes.
                "detalhes": stats["detalhes"][:50],
            }
            await db.commit()

    log.info(
        "busca.ingest.concluido",
        org_id=org_id, agente_id=agente_id, busca_id=busca_id,
        tarefa_id=tarefa_id, **{k: v for k, v in stats.items() if k != "detalhes"},
    )

    # Agente v3.0.9+: gera meli.la INLINE durante a busca (mesmo driver Chrome,
    # igual V2). Servidor não enfileira mais tarefa GERAR_LINK separada — isso
    # criava conflito de driver e race conditions na re-entrega WS.
    #
    # Fallback: se algum produto vier do agente AINDA sem meli.la (sessão ML
    # expirou no linkbuilder, painel mudou layout), o endpoint
    # /produtos/regenerar-meli-la ainda existe pra retry manual via UI.
    return stats


async def _coletar_urls_sem_meli_la(
    db: AsyncSession, *, org_id: int, urls_ingeridas: list[str],
) -> list[str]:
    """Filtra: só URLs cujo `url_afiliado` no DB AINDA não é `meli.la/...`."""
    if not urls_ingeridas:
        return []
    rows = (await db.execute(
        select(Produto.url_canonica, Produto.url_afiliado).where(
            Produto.org_id == org_id,
            Produto.plataforma == "ml",
            Produto.url_canonica.in_(urls_ingeridas),
        )
    )).all()
    pendentes: list[str] = []
    for url_c, url_a in rows:
        if not url_a or "meli.la/" not in (url_a or ""):
            pendentes.append(url_c)
    return pendentes


async def _enfileirar_geracao_links_ml(
    db: AsyncSession,
    *,
    org_id: int,
    agente_id: int,
    usuario_id: int | None,
    urls: list[str],
) -> None:
    """Cria tarefa `GERAR_LINK` e despacha pro agente via WS."""
    from app.services.dispatcher import _tentar_entrega

    tarefa = Tarefa(
        org_id=org_id,
        tipo=TipoTarefa.GERAR_LINK,
        agente_id=agente_id,
        criado_por_usuario_id=usuario_id,
        status=StatusTarefa.PENDENTE,
        payload={"urls": urls},
    )
    db.add(tarefa)
    await db.commit()
    await db.refresh(tarefa)
    await _tentar_entrega(db, tarefa)
    log.info("linkbuilder.tarefa_criada",
             tarefa_id=tarefa.id, agente_id=agente_id, urls=len(urls))


async def _resolver_disparador(
    db: AsyncSession, *, org_id: int, tarefa_id: int | None, busca_id: int | None,
) -> tuple[Usuario | None, int | None]:
    """
    Devolve (usuario_disparador, dono_id_pra_produtos).

    - Se afiliado disparou: dono_id = afiliado.id (produto privado).
    - Senão (admin/usuario comum/sem disparador): dono_id = None (produto público).
    """
    user: Usuario | None = None

    if tarefa_id:
        tarefa = await db.get(Tarefa, tarefa_id)
        if tarefa and tarefa.criado_por_usuario_id:
            user = await db.get(Usuario, tarefa.criado_por_usuario_id)
    if user is None and busca_id:
        busca = await db.get(BuscaML, busca_id)
        if busca and busca.criado_por_usuario_id:
            user = await db.get(Usuario, busca.criado_por_usuario_id)

    if user is None:
        return None, None

    dono_id = user.id if user.eh_afiliado else None
    return user, dono_id


def _url_afiliado_contem_tag(url: str | None, tag: str | None) -> bool:
    """
    True se a URL de afiliado contém a tag esperada (substring case-insensitive).

    Usado pra validar que o `url_afiliado` enviado pelo agente realmente
    bate com o ID de afiliado configurado no admin — senão a comissão vai
    pra OUTRA pessoa (o user que estava logado no Chrome do agente quando
    o linkbuilder rodou).

    Sem tag configurada (None/vazia) → retorna False (cai pro fallback).
    Sem URL → False.
    """
    if not url or not tag:
        return False
    tag_norm = tag.strip().lower()
    if not tag_norm:
        return False
    return tag_norm in url.lower()


async def _upsert_produto(
    db: AsyncSession,
    *,
    org_id: int,
    dono_id: int | None,
    tag_ml: str | None,
    tags_por_plataforma: dict[str, str | None] | None = None,
    item: dict[str, Any],
    mapping_categoria: dict[str, int],
) -> tuple[bool, bool]:
    """
    Insere ou atualiza um produto. Retorna (criou_novo, recebeu_nicho).
    """
    plataforma = (item.get("plataforma") or "ml").lower()
    item_id    = str(item.get("item_id") or "").strip()
    if not item_id:
        raise BuscaServiceError("item_id ausente")

    # Busca existente respeitando dono (público vs privado)
    cond_dono = (
        (Produto.usuario_dono_id.is_(None))
        if dono_id is None
        else (Produto.usuario_dono_id == dono_id)
    )
    existente = await db.scalar(
        select(Produto).where(
            Produto.org_id == org_id,
            cond_dono,
            Produto.plataforma == plataforma,
            Produto.item_id == item_id,
        )
    )

    # Limpa fragment/query — scraping ML traz `#polycard_client=...&tracking_id=...`
    # que polui URL canônica e quebra match com meli.la mapping (Fase 15+).
    url_canonica = _limpar_url_canonica(item.get("url_canonica"))

    # Decide url_afiliado:
    # 1. Agente mandou `url_afiliado` JÁ COM TAG (caso normal):
    #    - ML: linkbuilder inline gera `meli.la/XXX` antes do ingest
    #    - Shopee: API afiliados retorna `long_link` (s.shopee.com.br/...)
    # 2. VALIDA que `url_afiliado` realmente contém a tag do admin
    #    (senão a comissão vai pra OUTRA pessoa — o user que estava
    #    logado no Chrome do agente). Sem tag válida → fallback.
    # 3. Não veio nada / igual à canônica / tag não bate → fallback
    #    `?matt_word=...&utm_source=...` do linkbuilder do servidor.
    tag_esperada = (tags_por_plataforma or {}).get(plataforma)
    url_afiliado_agente = (item.get("url_afiliado") or "").strip() or None

    aceita_url_agente = (
        url_afiliado_agente is not None
        and url_afiliado_agente != url_canonica
        and (
            # Shorteners oficiais — confiamos sem validar tag porque a
            # redirect interna do ML/Shopee aplica a tag implícita.
            "meli.la/" in url_afiliado_agente
            or "s.shopee.com.br/" in url_afiliado_agente
            or "shp.ee/" in url_afiliado_agente
            or "amzn.to/" in url_afiliado_agente
            # URL de marketplace com tag visível na query — valida substring
            or _url_afiliado_contem_tag(url_afiliado_agente, tag_esperada)
        )
    )

    if aceita_url_agente:
        url_afiliado = url_afiliado_agente
        log.debug("ingest.url_afiliado_do_agente",
                  plataforma=plataforma, item_id=item_id,
                  url_afiliado=url_afiliado_agente[:120])
    else:
        url_afiliado = linkbuilder.gerar_url_afiliado(
            plataforma=plataforma, url_canonica=url_canonica, tag=tag_esperada,
        )
        if url_afiliado_agente:
            motivo = "tag_nao_bate" if url_afiliado_agente != url_canonica else "igual_a_canonica"
        else:
            motivo = "agente_nao_enviou"
        log.info("ingest.url_afiliado_fallback",
                 plataforma=plataforma, item_id=item_id,
                 motivo=motivo, tag_esperada=tag_esperada,
                 url_afiliado_agente=(url_afiliado_agente or "")[:120])

    # Personalizado (Fase 17): sobrescreve dono/criador se vier marcado.
    # Item marcado por `personalizado_service.marcar_produtos_personalizados`.
    eh_personalizado = item.get("fonte") == "personalizado"
    if eh_personalizado:
        dono_id_efetivo = item.get("_personalizado_dono_id")
        criador_id      = item.get("_personalizado_criador_id")
        fonte           = "personalizado"
    else:
        dono_id_efetivo = dono_id
        criador_id      = None
        fonte           = "busca_ml"

    # ── Fase 18: dados de curadoria + validação de comissão ─────────
    # Cada scraper agora envia (quando disponível):
    #   comissao        — % real (ML painel, Shopee API) ou estimativa
    #   comissao_fonte  — "ml_painel" | "shopee_api" | "amazon_tabela" | "estimativa"
    #   total_vendidos  — número absoluto (ML, Shopee) ou proxy (Amazon rank)
    #   is_bestseller   — True se veio de busca tipo mais_vendidos/bestsellers
    #   is_em_alta      — True se veio de /ofertas ML ou API Shopee ofertas
    comissao_pct   = item.get("comissao")
    comissao_extra = item.get("comissao_extra")  # None = sem bônus GANHOS EXTRAS
    comissao_fonte = (item.get("comissao_fonte") or "").strip() or _fonte_default_por_plataforma(plataforma)
    total_vendidos = item.get("total_vendidos")
    is_bestseller  = bool(item.get("is_bestseller"))
    is_em_alta     = bool(item.get("is_em_alta"))

    # ── Fase 18.2/18.3: cascata de fontes pra comissão ML ────────────
    # Hierarquia de confiabilidade (alta → baixa):
    #   1. ml_barra_afiliados — agente capturou barra preta do ML afiliados
    #                            (valor REAL com promoção EXTRAS, fonte de verdade)
    #   2. ml_painel          — agente capturou tabela do painel linkbuilder
    #                            (frágil — DOM muda, nem todo produto exibe)
    #   3. categoria_ml_v2    — servidor estimou pela tabela de ~50 categorias
    #                            (refinamento baseado no path de categoria)
    #   4. estimativa         — categoria pai hardcoded no agente (otimista)
    #
    # Se a comissão veio de fontes 1 ou 2, NÃO sobrescreve com tabela.
    # Senão, tenta refinar pelo categoria_ml_v2.
    FONTES_OFICIAIS_ML = {"ml_barra_afiliados", "ml_painel"}
    if plataforma == "ml" and comissao_fonte not in FONTES_OFICIAIS_ML:
        from app.core.comissoes_ml_categorias import estimar_comissao_ml_categoria
        estimativa_refinada = estimar_comissao_ml_categoria(item.get("categoria"))
        if estimativa_refinada is not None and estimativa_refinada != comissao_pct:
            log.info(
                "ingest.comissao_refinada_por_categoria",
                item_id=item_id,
                categoria=item.get("categoria"),
                antes=comissao_pct,
                depois=estimativa_refinada,
            )
            comissao_pct   = estimativa_refinada
            comissao_fonte = "categoria_ml_v2"  # estimativa refinada pelo path

    # Calcula nota + valida comissão (função pura)
    info_nota = calcular_nota({
        "plataforma":     plataforma,
        "preco":          item.get("preco"),
        "preco_orig":     item.get("preco_orig"),
        "desconto":       item.get("desconto"),
        "comissao":       comissao_pct,
        "total_vendidos": total_vendidos,
        "is_bestseller":  is_bestseller,
        "is_em_alta":     is_em_alta,
    })
    nota              = info_nota["nota"]
    comissao_validada = info_nota["comissao_validada"]

    agora = datetime.now(tz=timezone.utc)

    if existente is None:
        produto = Produto(
            org_id=org_id,
            usuario_dono_id=dono_id_efetivo,
            criado_por_usuario_id=criador_id,
            plataforma=plataforma,
            item_id=item_id,
            nome=item.get("nome", "")[:500],
            categoria=item.get("categoria"),
            preco=float(item.get("preco") or 0),
            preco_orig=item.get("preco_orig"),
            desconto=item.get("desconto"),
            comissao=comissao_pct,
            comissao_extra=comissao_extra,
            frete_gratis=bool(item.get("frete_gratis")),
            url_canonica=url_canonica,
            url_afiliado=url_afiliado,
            foto_url=item.get("foto_url"),
            fonte=fonte,
            descoberto_em=agora,
            # Fase 18
            nota=nota,
            is_bestseller=is_bestseller,
            is_em_alta=is_em_alta,
            total_vendidos=int(total_vendidos) if total_vendidos else 0,
            comissao_fonte=comissao_fonte,
            comissao_validada=comissao_validada,
            preco_atualizado_em=agora,
            comissao_atualizada_em=agora if comissao_pct else None,
            vendidos_atualizado_em=agora if total_vendidos else None,
        )
        db.add(produto)
        await db.flush()
        criou = True
    else:
        produto = existente
        produto.nome = item.get("nome", produto.nome)[:500]
        if item.get("categoria"):
            produto.categoria = item["categoria"]
        # Preço — só carimba `preco_atualizado_em` se mudou de fato
        novo_preco = float(item.get("preco") or produto.preco)
        if novo_preco != produto.preco:
            produto.preco = novo_preco
            produto.preco_atualizado_em = agora
        if item.get("preco_orig") is not None:
            produto.preco_orig = item["preco_orig"]
        if item.get("desconto") is not None:
            produto.desconto = item["desconto"]
        # ── Comissão: HIERARQUIA DE CONFIANÇA (Fase 18.4) ────────────
        # NÃO sobrescreve dado real (ml_barra_afiliados/ml_painel) com
        # estimativa antiga. Cenário do bug v3.4.4:
        # - Busca 1: agente capturou 26% via barra → DB tem ml_barra_afiliados=26%
        # - Busca 2: agente falhou captura → vem com estimativa → servidor
        #   refina pra categoria_ml_v2=12% → SOBRESCRITO o dado bom com ruim
        # Fix: só atualiza se nova fonte é >= confiável que a atual.
        if comissao_pct is not None and comissao_pct > 0:
            confianca_nova   = _confianca_fonte(comissao_fonte)
            confianca_atual  = _confianca_fonte(produto.comissao_fonte)
            if confianca_nova >= confianca_atual:
                if comissao_pct != produto.comissao:
                    produto.comissao = comissao_pct
                    produto.comissao_atualizada_em = agora
                if comissao_fonte and comissao_fonte != produto.comissao_fonte:
                    produto.comissao_fonte = comissao_fonte
                # Recalcula validada só quando atualiza
                produto.comissao_validada = comissao_validada
                # comissao_extra acompanha o update da comissão: se a nova
                # captura tem fonte ≥ a atual, sobrescreve (inclusive pra None
                # = produto perdeu o bônus EXTRAS desde a última varredura).
                produto.comissao_extra = comissao_extra
            else:
                # Fonte nova menos confiável que a atual — NÃO sobrescreve.
                # Mantém comissao + comissao_fonte + comissao_atualizada_em.
                log.info(
                    "ingest.comissao_nao_sobrescrita_fonte_menor",
                    item_id=item_id,
                    fonte_atual=produto.comissao_fonte,
                    fonte_nova=comissao_fonte,
                    pct_atual=produto.comissao,
                    pct_nova=comissao_pct,
                )
        # Total vendidos — só atualiza se veio e mudou
        if total_vendidos is not None and total_vendidos > 0:
            tv = int(total_vendidos)
            if tv != produto.total_vendidos:
                produto.total_vendidos = tv
                produto.vendidos_atualizado_em = agora
        # Flags is_bestseller / is_em_alta: marca True se veio True
        # (não desmarca — produto pode estar em múltiplas listas)
        if is_bestseller:
            produto.is_bestseller = True
        if is_em_alta:
            produto.is_em_alta = True
        produto.frete_gratis = bool(item.get("frete_gratis", produto.frete_gratis))
        if url_canonica:
            produto.url_canonica = url_canonica
            # Regra de atualização do url_afiliado:
            # - Se o agente mandou link VÁLIDO (passou na validação acima):
            #   sobrescreve o que tinha no DB.
            # - Senão, recalcula o fallback com a tag atual do admin
            #   (em caso de o admin ter trocado de afiliado no meio).
            produto.url_afiliado = url_afiliado
        if item.get("foto_url"):
            produto.foto_url = item["foto_url"]
        # Recalcula nota com valores ATUALIZADOS do produto (após updates acima)
        info_nota_atualizada = calcular_nota({
            "plataforma":     produto.plataforma,
            "preco":          produto.preco,
            "preco_orig":     produto.preco_orig,
            "desconto":       produto.desconto,
            "comissao":       produto.comissao,
            "total_vendidos": produto.total_vendidos,
            "is_bestseller":  produto.is_bestseller,
            "is_em_alta":     produto.is_em_alta,
        })
        produto.nota              = info_nota_atualizada["nota"]
        produto.comissao_validada = info_nota_atualizada["comissao_validada"]
        criou = False

    # Auto-classificação por categoria
    recebeu_nicho = False
    categoria = (produto.categoria or "").lower().strip()
    if categoria and not await _tem_algum_nicho(db, produto_id=produto.id):
        nicho_id = mapping_categoria.get(categoria)
        # Fallback: match por prefixo (categoria do ML é hierárquica
        # "Eletrônicos > Áudio > Fones" — tenta níveis intermediários)
        if nicho_id is None:
            for cat_chave, nid in mapping_categoria.items():
                if categoria.startswith(cat_chave) or cat_chave in categoria:
                    nicho_id = nid
                    break
        if nicho_id is not None:
            db.add(ProdutoNicho(produto_id=produto.id, nicho_id=nicho_id))
            recebeu_nicho = True

    return criou, recebeu_nicho


# ── Helper Fase 18 ───────────────────────────────────────────
def _fonte_default_por_plataforma(plataforma: str) -> str:
    """Fonte default quando o scraper não enviou explicitamente.

    - amazon → 'amazon_tabela' (todo Amazon vem com comissão da tabela oficial)
    - shopee → 'shopee_api' (todo Shopee vem da API)
    - ml     → 'estimativa' (estimativa do card; sobrescrita pra 'ml_painel'
               quando o linkbuilder inline captura % real)
    - outros → 'estimativa'
    """
    plat = (plataforma or "").lower()
    if plat == "amazon":
        return "amazon_tabela"
    if plat == "shopee":
        return "shopee_api"
    return "estimativa"


# Hierarquia de confiança da fonte de comissão (alta → baixa).
# Index 0 = mais confiável. Usado em `_upsert_produto` pra NÃO sobrescrever
# dado capturado real (ml_barra_afiliados) com estimativa antiga (categoria/etc).
#
# ⚠ LIÇÃO v3.4.4: bug em prod onde produtos rebuscados perdiam a comissão
# real porque o servidor refinava pra `categoria_ml_v2` quando captura
# falhava na nova busca, e sobrescrevia o `ml_barra_afiliados` antigo.
# Documentado em CLAUDE.md armadilha "Hierarquia de comissao_fonte".
#
# v3.4.5: `manual` adicionado no topo — quando admin edita comissão pela UI
# (PATCH /produtos/{id}), marca como manual. Imutável por busca automática.
_HIERARQUIA_FONTE_COMISSAO = [
    "manual",               # admin editou pela UI editar produto — MÁXIMA confiança
    "ml_barra_afiliados",   # capturado da barra preta ML (Fase 18.3) — fonte de verdade
    "ml_painel",            # capturado do painel linkbuilder ML (Fase 18.0)
    "shopee_api",           # Shopee API direta (Fase 18.0)
    "amazon_tabela",        # tabela oficial Amazon BR por categoria
    "categoria_ml_v2",      # estimativa do servidor pela tabela de ~50 categorias
    "estimativa",           # categoria pai hardcoded no agente (otimista)
]


def _confianca_fonte(fonte: str | None) -> int:
    """Retorna confiança da fonte (maior = mais confiável). Default 0 (mínima)."""
    if not fonte:
        return 0
    try:
        # Inverte: index 0 = mais confiável → retorna len - index
        return len(_HIERARQUIA_FONTE_COMISSAO) - _HIERARQUIA_FONTE_COMISSAO.index(fonte)
    except ValueError:
        return 0


async def _tem_algum_nicho(db: AsyncSession, *, produto_id: int) -> bool:
    row = await db.scalar(
        select(ProdutoNicho.id).where(ProdutoNicho.produto_id == produto_id).limit(1)
    )
    return row is not None

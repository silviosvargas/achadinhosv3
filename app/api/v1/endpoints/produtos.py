"""
CRUD de produtos + import CSV.

GET    /produtos              lista (com filtros)
GET    /produtos/{id}         detalhe
POST   /produtos              cria manualmente
PATCH  /produtos/{id}         atualiza (preço, nichos, bloquear, etc)
DELETE /produtos/{id}         remove
POST   /produtos/import-csv   sobe planilha CSV pra criar/atualizar em lote
"""
import csv
import io

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import delete, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import agente_atual, usuario_admin, usuario_atual
from app.core.config import settings
from app.db import get_db_async
from app.models import Agente, Produto, ProdutoNicho, Usuario


def _org_ids_visiveis(user: Usuario) -> list[int]:
    """IDs de orgs cujos produtos esse user pode VER (não editar).

    Regra arquitetural (17/05/2026): TODO cliente non-admin-central
    consome APENAS do catálogo da org central. Própria org só relevante
    se for a própria org central.

    Pra escrita (POST/PATCH/DELETE), só admin central pode.
    """
    if user.eh_admin_central:
        return [user.org_id]
    # Cliente comum: vê só catálogo da org admin central
    return [settings.admin_org_id]
from app.schemas.comum import Mensagem, Pagina
from app.schemas.produto import (
    AtualizarProdutoRequest,
    CriarProdutoRequest,
    IngestLoteRequest,
    ProdutoPublico,
    ResultadoIngest,
)
from app.services import busca_service

router = APIRouter(prefix="/produtos", tags=["produtos"])


@router.get("", response_model=Pagina[ProdutoPublico])
async def listar(
    pagina: int = Query(default=1, ge=1),
    por_pagina: int = Query(default=50, ge=1, le=200),
    plataforma: str | None = None,
    nicho_id:   int | None = None,
    bloqueado:  bool | None = None,
    busca:      str | None = Query(default=None, max_length=200),
    user: Usuario = Depends(usuario_atual),
    db: AsyncSession = Depends(get_db_async),
) -> Pagina[ProdutoPublico]:
    # Fase 11: plano free vê catálogo do admin além do próprio. Outros planos
    # só veem da própria org.
    org_ids = _org_ids_visiveis(user)
    base = select(Produto).where(
        Produto.org_id.in_(org_ids),
        # Esconde produtos em modo PREVIEW (busca rápida pendente confirmação)
        or_(Produto.fonte.is_(None), Produto.fonte.notlike("preview:%")),
    )

    # Visibilidade (ADR-008). Produtos privados de afiliado (`usuario_dono_id`
    # NOT NULL) só aparecem pro dono ou pro admin da org. Mesmo regra que antes.
    if user.eh_afiliado:
        base = base.where(or_(
            Produto.usuario_dono_id.is_(None),
            Produto.usuario_dono_id == user.id,
        ))
    else:
        base = base.where(Produto.usuario_dono_id.is_(None))

    if plataforma:
        base = base.where(Produto.plataforma == plataforma)
    if bloqueado is not None:
        base = base.where(Produto.bloqueado.is_(bloqueado))
    if busca:
        base = base.where(Produto.nome.ilike(f"%{busca}%"))
    if nicho_id is not None:
        # Subquery com produtos que têm o nicho
        subq = select(ProdutoNicho.produto_id).where(ProdutoNicho.nicho_id == nicho_id)
        base = base.where(Produto.id.in_(subq))

    # Total (count)
    from sqlalchemy import func
    total = await db.scalar(
        select(func.count()).select_from(base.subquery())
    ) or 0

    offset = (pagina - 1) * por_pagina
    result = await db.execute(
        base.order_by(Produto.atualizado_em.desc())
            .limit(por_pagina).offset(offset)
    )
    produtos = list(result.scalars().all())

    # Carrega nichos em batch (1 query) pra evitar N+1
    items: list[ProdutoPublico] = []
    if produtos:
        ids = [p.id for p in produtos]
        rows = (await db.execute(
            select(ProdutoNicho.produto_id, ProdutoNicho.nicho_id)
            .where(ProdutoNicho.produto_id.in_(ids))
        )).all()
        nichos_map: dict[int, list[int]] = {}
        for pid, nid in rows:
            nichos_map.setdefault(pid, []).append(nid)

        for p in produtos:
            pub = ProdutoPublico.model_validate(p)
            pub.nichos_ids = nichos_map.get(p.id, [])
            items.append(pub)

    return Pagina[ProdutoPublico](
        items=items, total=total, pagina=pagina, por_pagina=por_pagina,
    )


@router.get("/{produto_id}", response_model=ProdutoPublico)
async def detalhe(
    produto_id: int,
    user: Usuario = Depends(usuario_atual),
    db: AsyncSession = Depends(get_db_async),
) -> ProdutoPublico:
    # Fase 11: plano free também pode ver detalhes de produto do admin
    org_ids = _org_ids_visiveis(user)
    p = await _get_da_org(
        db, org_id=user.org_id, produto_id=produto_id, user=user,
        org_ids_extras=[oid for oid in org_ids if oid != user.org_id],
    )
    pub = ProdutoPublico.model_validate(p)
    pub.nichos_ids = await _nichos_de(db, produto_id=p.id)
    return pub


@router.post("", response_model=ProdutoPublico, status_code=status.HTTP_201_CREATED)
async def criar(
    body: CriarProdutoRequest,
    admin: Usuario = Depends(usuario_admin),
    db: AsyncSession = Depends(get_db_async),
) -> ProdutoPublico:
    # Regra arquitetural (17/05/2026): só admin central cria produtos no
    # catálogo. Cliente comum consome catálogo central e usa "personalizados"
    # (Fase B do refactor) pra solicitar/favoritar.
    if not admin.eh_admin_central:
        raise HTTPException(
            status_code=403,
            detail="Apenas o admin central cadastra produtos no catálogo. "
                   "Use a página 'Personalizados' pra solicitar novos produtos.",
        )
    novo = Produto(
        org_id=admin.org_id,
        plataforma=body.plataforma,
        item_id=body.item_id,
        nome=body.nome,
        categoria=body.categoria,
        preco=body.preco,
        preco_orig=body.preco_orig,
        desconto=body.desconto,
        comissao=body.comissao,
        frete_gratis=body.frete_gratis,
        url_canonica=body.url_canonica,
        url_afiliado=body.url_afiliado,
        foto_url=body.foto_url,
        fonte=body.fonte,
    )
    db.add(novo)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Já existe produto com essa (plataforma, item_id) na sua organização",
        ) from None
    await db.refresh(novo)

    # Vincula nichos
    for nid in body.nichos_ids:
        db.add(ProdutoNicho(produto_id=novo.id, nicho_id=nid))
    await db.commit()

    pub = ProdutoPublico.model_validate(novo)
    pub.nichos_ids = list(body.nichos_ids)
    return pub


@router.patch("/{produto_id}", response_model=ProdutoPublico)
async def atualizar(
    produto_id: int,
    body: AtualizarProdutoRequest,
    admin: Usuario = Depends(usuario_admin),
    db: AsyncSession = Depends(get_db_async),
) -> ProdutoPublico:
    p = await _get_da_org(db, org_id=admin.org_id, produto_id=produto_id, user=admin)

    if body.nome is not None:         p.nome = body.nome
    if body.preco is not None:        p.preco = body.preco
    if body.preco_orig is not None:   p.preco_orig = body.preco_orig
    if body.desconto is not None:     p.desconto = body.desconto
    if body.comissao is not None:     p.comissao = body.comissao
    if body.url_afiliado is not None: p.url_afiliado = body.url_afiliado
    if body.foto_url is not None:     p.foto_url = body.foto_url
    if body.bloqueado is not None:    p.bloqueado = body.bloqueado
    if body.bloqueado_motivo is not None:
        p.bloqueado_motivo = body.bloqueado_motivo

    if body.nichos_ids is not None:
        await db.execute(delete(ProdutoNicho).where(ProdutoNicho.produto_id == p.id))
        for nid in body.nichos_ids:
            db.add(ProdutoNicho(produto_id=p.id, nicho_id=nid))

    await db.commit()
    await db.refresh(p)

    pub = ProdutoPublico.model_validate(p)
    pub.nichos_ids = await _nichos_de(db, produto_id=p.id)
    return pub


@router.delete("/{produto_id}", response_model=Mensagem)
async def deletar(
    produto_id: int,
    admin: Usuario = Depends(usuario_admin),
    db: AsyncSession = Depends(get_db_async),
) -> Mensagem:
    p = await _get_da_org(db, org_id=admin.org_id, produto_id=produto_id, user=admin)
    await db.delete(p)
    await db.commit()
    return Mensagem(mensagem="Produto removido")


# ============================================================
# Import CSV
# ============================================================

@router.post("/import-csv")
async def importar_csv(
    arquivo: UploadFile = File(..., description="CSV com colunas: plataforma,item_id,nome,preco,..."),
    admin: Usuario = Depends(usuario_admin),
    db: AsyncSession = Depends(get_db_async),
) -> dict:
    """
    Importa produtos via CSV.

    Colunas esperadas (header obrigatório):
        plataforma,item_id,nome,categoria,preco,preco_orig,desconto,
        comissao,frete_gratis,url_canonica,url_afiliado,foto_url,nichos

    `nichos` deve ser pipe-separated (ex: "moda|beleza"). Aceita slugs.

    Comportamento: upsert por (plataforma, item_id). Atualiza preço e
    re-vincula nichos se já existir.
    """
    if not arquivo.filename or not arquivo.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Envie um arquivo .csv")

    conteudo = (await arquivo.read()).decode("utf-8-sig", errors="replace")

    reader = csv.DictReader(io.StringIO(conteudo))
    if reader.fieldnames is None:
        raise HTTPException(status_code=400, detail="CSV vazio ou sem cabeçalho")

    obrigatorios = {"plataforma", "item_id", "nome", "preco"}
    faltam = obrigatorios - set(reader.fieldnames)
    if faltam:
        raise HTTPException(
            status_code=400,
            detail=f"Cabeçalho do CSV está faltando: {', '.join(faltam)}",
        )

    # Carrega mapping slug→id de nichos pra resolver na hora
    from app.models import Nicho
    nichos_db = (await db.execute(select(Nicho))).scalars().all()
    slug_para_id = {n.slug: n.id for n in nichos_db}

    criados   = 0
    atualizados = 0
    erros: list[str] = []
    linha_num = 1  # contando o cabeçalho

    for linha in reader:
        linha_num += 1
        try:
            plat = (linha.get("plataforma") or "").strip().lower()
            item_id = (linha.get("item_id") or "").strip()
            nome = (linha.get("nome") or "").strip()
            preco_str = (linha.get("preco") or "0").replace(",", ".").strip()

            if not plat or not item_id or not nome:
                erros.append(f"Linha {linha_num}: faltando campo obrigatório")
                continue

            try:
                preco = float(preco_str)
            except ValueError:
                erros.append(f"Linha {linha_num}: preço inválido '{preco_str}'")
                continue

            existente = await db.scalar(
                select(Produto).where(
                    Produto.org_id == admin.org_id,
                    Produto.plataforma == plat,
                    Produto.item_id == item_id,
                )
            )

            def _f(chave: str) -> float | None:
                v = (linha.get(chave) or "").replace(",", ".").strip()
                if not v:
                    return None
                try:
                    return float(v)
                except ValueError:
                    return None

            def _b(chave: str) -> bool:
                return (linha.get(chave) or "").strip().lower() in {"1", "true", "sim", "yes"}

            if existente is None:
                produto = Produto(
                    org_id=admin.org_id,
                    plataforma=plat,
                    item_id=item_id,
                    nome=nome,
                    categoria=(linha.get("categoria") or "").strip() or None,
                    preco=preco,
                    preco_orig=_f("preco_orig"),
                    desconto=_f("desconto"),
                    comissao=_f("comissao"),
                    frete_gratis=_b("frete_gratis"),
                    url_canonica=(linha.get("url_canonica") or "").strip() or None,
                    url_afiliado=(linha.get("url_afiliado") or "").strip() or None,
                    foto_url=(linha.get("foto_url") or "").strip() or None,
                    fonte="csv_import",
                )
                db.add(produto)
                await db.flush()
                criados += 1
            else:
                produto = existente
                produto.nome = nome
                produto.preco = preco
                if _f("preco_orig") is not None: produto.preco_orig = _f("preco_orig")
                if _f("desconto")  is not None: produto.desconto = _f("desconto")
                if _f("comissao")  is not None: produto.comissao = _f("comissao")
                cat = (linha.get("categoria") or "").strip()
                if cat: produto.categoria = cat
                url = (linha.get("url_afiliado") or "").strip()
                if url: produto.url_afiliado = url
                foto = (linha.get("foto_url") or "").strip()
                if foto: produto.foto_url = foto
                atualizados += 1

            # Nichos (pipe-separated, ex "moda|beleza")
            nichos_raw = (linha.get("nichos") or "").strip()
            if nichos_raw:
                # Limpa nichos antigos
                await db.execute(
                    delete(ProdutoNicho).where(ProdutoNicho.produto_id == produto.id)
                )
                for slug in nichos_raw.split("|"):
                    slug = slug.strip().lower()
                    nid = slug_para_id.get(slug)
                    if nid:
                        db.add(ProdutoNicho(produto_id=produto.id, nicho_id=nid))
                    else:
                        erros.append(
                            f"Linha {linha_num}: nicho '{slug}' não existe (ignorado)"
                        )
        except Exception as e:
            erros.append(f"Linha {linha_num}: erro inesperado — {type(e).__name__}: {str(e)[:100]}")

    await db.commit()

    return {
        "criados":     criados,
        "atualizados": atualizados,
        "erros":       erros[:50],   # limita pra não estourar resposta
        "total_erros": len(erros),
    }


# ============================================================
# Ingest (agente local devolve produtos extraídos via busca)
# ============================================================

@router.post("/ingest", response_model=ResultadoIngest)
async def ingest_de_agente(
    body: IngestLoteRequest,
    agente: Agente = Depends(agente_atual),
    db: AsyncSession = Depends(get_db_async),
) -> ResultadoIngest:
    """
    Recebe lote de produtos do agente após execução de busca ML.

    Autenticação: token JWT tipo "agente" no header Authorization.
    Upsert respeita visibilidade pública vs privada (ver ADR-008).
    Aplica mapping categoria_ml → nicho automaticamente se cadastrado na org.
    """
    stats = await busca_service.ingerir_produtos(
        db,
        org_id=agente.org_id,
        agente_id=agente.id,
        produtos_recebidos=[item.model_dump() for item in body.produtos],
        busca_id=body.busca_id,
        tarefa_id=body.tarefa_id,
    )
    return ResultadoIngest(**stats)


# ============================================================
# Helpers
# ============================================================

async def _get_da_org(
    db: AsyncSession, *, org_id: int, produto_id: int,
    user: Usuario | None = None,
    org_ids_extras: list[int] | None = None,
) -> Produto:
    """Busca produto restringindo por org.

    Fase 11: `org_ids_extras` permite incluir orgs adicionais como
    visíveis (ex: org admin pra plano free). Default mantém comportamento
    estrito (escrita só na própria org).
    """
    p = await db.get(Produto, produto_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Produto não encontrado")

    orgs_permitidas = {org_id, *(org_ids_extras or [])}
    if p.org_id not in orgs_permitidas:
        raise HTTPException(status_code=404, detail="Produto não encontrado")

    # Visibilidade ADR-008: privados são vistos pelo dono e por admins da
    # MESMA ORG do produto. Cross-org sempre 404 pra privados.
    if p.usuario_dono_id is not None and user is not None:
        if not (user.eh_admin and p.org_id == user.org_id) and p.usuario_dono_id != user.id:
            raise HTTPException(status_code=404, detail="Produto não encontrado")
    return p


async def _nichos_de(db: AsyncSession, *, produto_id: int) -> list[int]:
    rows = await db.execute(
        select(ProdutoNicho.nicho_id).where(ProdutoNicho.produto_id == produto_id)
    )
    return [r[0] for r in rows.all()]

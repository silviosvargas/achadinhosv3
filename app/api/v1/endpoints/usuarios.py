"""
CRUD de usuários (escopado por org) + gerenciamento de afiliados.

POST   /usuarios                          admin cria usuário na própria org
GET    /usuarios                          lista usuários da org
GET    /usuarios/{id}                     detalhe
PATCH  /usuarios/{id}                     atualiza nome/email/papel/ativo
POST   /usuarios/me/senha                 usuário troca a própria senha
DELETE /usuarios/{id}                     desativa (soft delete)

Afiliados (Fase 13):
GET    /usuarios/{id}/afiliados           lista marketplaces cadastrados
POST   /usuarios/{id}/afiliados           cadastra/atualiza um marketplace
DELETE /usuarios/{id}/afiliados/{plat}    remove um marketplace
"""
from pydantic import BaseModel, Field

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import usuario_admin, usuario_atual
from app.core import marketplaces
from app.core.security import hash_senha, verificar_senha
from app.db import get_db_async
from app.models import Usuario
from app.schemas.comum import Mensagem
from app.schemas.usuario import (
    AtualizarUsuarioRequest,
    CriarUsuarioRequest,
    TrocarSenhaRequest,
    UsuarioPublico,
)
from app.services import afiliado_service, limites, papel_service


class AfiliadoUpsertRequest(BaseModel):
    plataforma: str = Field(min_length=1, max_length=20)
    tag:        str = Field(min_length=1, max_length=200)


class AfiliadoPublico(BaseModel):
    plataforma: str
    nome:       str   # display name do marketplace
    icone:      str
    tag:        str

router = APIRouter(prefix="/usuarios", tags=["usuarios"])


@router.post("", response_model=UsuarioPublico, status_code=status.HTTP_201_CREATED)
async def criar(
    body: CriarUsuarioRequest,
    admin: Usuario = Depends(usuario_admin),
    db: AsyncSession = Depends(get_db_async),
) -> UsuarioPublico:
    """Cria novo usuário na MESMA org do admin que está chamando."""
    # Verifica limite do plano antes de criar
    pode, msg = await limites.pode_criar_usuario(db, org_id=admin.org_id)
    if not pode:
        raise HTTPException(status_code=402, detail=msg)

    novo = Usuario(
        org_id=admin.org_id,
        login=body.login,
        senha_hash=hash_senha(body.senha),
        papel=body.papel,
        nome_exibicao=body.nome_exibicao or body.login,
        email=body.email,
        ativo=True,
        onboarding_completo=False,
    )
    db.add(novo)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Já existe usuário com esse login nesta organização",
        ) from None
    await db.refresh(novo)
    return UsuarioPublico.model_validate(novo)


@router.get("", response_model=list[UsuarioPublico])
async def listar(
    user: Usuario = Depends(usuario_atual),
    db: AsyncSession = Depends(get_db_async),
) -> list[UsuarioPublico]:
    result = await db.execute(
        select(Usuario)
        .where(Usuario.org_id == user.org_id)
        .order_by(Usuario.criado_em.desc())
    )
    return [UsuarioPublico.model_validate(u) for u in result.scalars().all()]


@router.get("/{usuario_id}", response_model=UsuarioPublico)
async def detalhe(
    usuario_id: int,
    user: Usuario = Depends(usuario_atual),
    db: AsyncSession = Depends(get_db_async),
) -> UsuarioPublico:
    target = await _get_da_org(db, org_id=user.org_id, usuario_id=usuario_id)
    return UsuarioPublico.model_validate(target)


@router.patch("/{usuario_id}", response_model=UsuarioPublico)
async def atualizar(
    usuario_id: int,
    body: AtualizarUsuarioRequest,
    admin: Usuario = Depends(usuario_admin),
    db: AsyncSession = Depends(get_db_async),
) -> UsuarioPublico:
    target = await _get_target_visivel(db, actor=admin, usuario_id=usuario_id)

    # Mudança de papel passa por gate dedicado (só super promove super, etc).
    if body.papel is not None and body.papel != target.papel:
        ok, motivo = papel_service.pode_mudar_papel(admin, target, body.papel)
        if not ok:
            raise HTTPException(status_code=403, detail=motivo)
        if papel_service.proximo_papel_acima(target.papel) != body.papel:
            # Rebaixamento (não promove 1 degrau pra cima): verifica salvaguarda
            ok_s, motivo_s = await papel_service.checar_salvaguardas_rebaixamento(
                db, target, body.papel,
            )
            if not ok_s:
                raise HTTPException(status_code=409, detail=motivo_s)
        target.papel = body.papel

    # Demais campos: requer permissão de editar dados (admite self-edit).
    if (body.nome_exibicao is not None or body.email is not None
            or body.ativo is not None):
        ok, motivo = papel_service.pode_editar_dados(admin, target)
        if not ok:
            raise HTTPException(status_code=403, detail=motivo)

    if body.nome_exibicao is not None:
        target.nome_exibicao = body.nome_exibicao
    if body.email is not None:
        target.email = body.email
    if body.ativo is not None:
        # Desativar = perde acesso. Aplica salvaguardas (último admin/super).
        if body.ativo is False and target.ativo:
            ok_s, motivo_s = await papel_service.checar_salvaguardas_desativacao(
                db, target,
            )
            if not ok_s:
                raise HTTPException(status_code=409, detail=motivo_s)
        target.ativo = body.ativo

    await db.commit()
    await db.refresh(target)
    return UsuarioPublico.model_validate(target)


@router.post("/me/senha", response_model=Mensagem)
async def trocar_senha(
    body: TrocarSenhaRequest,
    user: Usuario = Depends(usuario_atual),
    db: AsyncSession = Depends(get_db_async),
) -> Mensagem:
    """Usuário troca a própria senha."""
    if not verificar_senha(body.senha_atual, user.senha_hash):
        raise HTTPException(status_code=400, detail="Senha atual incorreta")

    user.senha_hash = hash_senha(body.senha_nova)
    await db.commit()
    return Mensagem(mensagem="Senha trocada com sucesso")


# Endpoint legacy PATCH /credenciais (Fase 4b.1) foi removido na Fase 13.
# Substituído por GET/POST/DELETE /usuarios/{id}/afiliados (1 row por marketplace).
# Login/senha do ML eram pra auto-login do agente — feature abandonada
# (ML tem 2FA, viola TOS).


@router.delete("/{usuario_id}", response_model=Mensagem)
async def desativar(
    usuario_id: int,
    admin: Usuario = Depends(usuario_admin),
    db: AsyncSession = Depends(get_db_async),
) -> Mensagem:
    """Desativa usuário (soft delete). Respeita matriz de permissões
    do `papel_service` + salvaguardas (último admin/super)."""
    target = await _get_target_visivel(db, actor=admin, usuario_id=usuario_id)

    ok, motivo = papel_service.pode_desativar(admin, target)
    if not ok:
        raise HTTPException(status_code=403, detail=motivo)

    if not target.ativo:
        return Mensagem(mensagem="Usuário já estava desativado")

    ok_s, motivo_s = await papel_service.checar_salvaguardas_desativacao(db, target)
    if not ok_s:
        raise HTTPException(status_code=409, detail=motivo_s)

    target.ativo = False
    await db.commit()
    return Mensagem(mensagem="Usuário desativado")


@router.post("/{usuario_id}/reativar", response_model=Mensagem)
async def reativar(
    usuario_id: int,
    admin: Usuario = Depends(usuario_admin),
    db: AsyncSession = Depends(get_db_async),
) -> Mensagem:
    """Volta `ativo=True` em usuário previamente desativado."""
    target = await _get_target_visivel(db, actor=admin, usuario_id=usuario_id)

    ok, motivo = papel_service.pode_editar_dados(admin, target)
    if not ok:
        raise HTTPException(status_code=403, detail=motivo)

    if target.ativo:
        return Mensagem(mensagem="Usuário já estava ativo")
    target.ativo = True
    await db.commit()
    return Mensagem(mensagem="Usuário reativado")


@router.delete("/{usuario_id}/permanente", response_model=Mensagem)
async def excluir_permanente(
    usuario_id: int,
    admin: Usuario = Depends(usuario_admin),
    db: AsyncSession = Depends(get_db_async),
) -> Mensagem:
    """Hard delete — APAGA permanentemente o usuário do banco.

    CASCADE em DB cuida de: usuarios_afiliados, solicitacoes,
    produtos privados (usuario_dono_id), favoritos, agentes.
    SET NULL em: produtos.criado_por, templates, tarefas, grupos,
    canais, busca_ml.criado_por (registros preservados, dono zera).

    Use desativar (soft) na UI por default. Hard é pra limpeza
    consciente — `confirm()` tripla no JS protege contra clique acidental.
    """
    target = await _get_target_visivel(db, actor=admin, usuario_id=usuario_id)

    ok, motivo = papel_service.pode_excluir(admin, target)
    if not ok:
        raise HTTPException(status_code=403, detail=motivo)

    ok_s, motivo_s = await papel_service.checar_salvaguardas_exclusao(db, target)
    if not ok_s:
        raise HTTPException(status_code=409, detail=motivo_s)

    await db.delete(target)
    await db.commit()
    return Mensagem(mensagem=f"Usuário {target.login!r} apagado permanentemente")


# ─────────────────────────────────────────────────────
# Afiliados (Fase 13 — multi-marketplace)
# ─────────────────────────────────────────────────────

def _autorizar_mexer_em(target: Usuario, user: Usuario) -> None:
    """Levanta 403 se `user` não pode editar afiliados de `target`."""
    if not user.eh_admin and target.id != user.id:
        raise HTTPException(
            status_code=403,
            detail="Só admin da org ou o próprio dono pode mexer nestes afiliados",
        )


def _gate_plano_cadastrar(user: Usuario) -> None:
    """Regra refinada (17/05/2026 noite — Fase D):
    Admin central + Afiliado podem cadastrar tags próprias. Usuário comum
    NÃO — postagens dele usam o afiliado do admin.

    Permite o cenário "afiliado tem agente próprio com Selenium ML/Shopee
    pra fazer comissão na conta DELE".
    """
    if not (user.eh_admin_central or user.eh_afiliado):
        raise HTTPException(
            status_code=403,
            detail="Apenas admin central ou afiliados cadastram tags. "
                   "Como usuário comum, suas postagens usam o afiliado do admin.",
        )


@router.get("/{usuario_id}/afiliados", response_model=list[AfiliadoPublico])
async def listar_afiliados(
    usuario_id: int,
    user: Usuario = Depends(usuario_atual),
    db: AsyncSession = Depends(get_db_async),
) -> list[AfiliadoPublico]:
    """Lista tags de afiliado cadastradas pelo user, com display name."""
    target = await _get_da_org(db, org_id=user.org_id, usuario_id=usuario_id)
    _autorizar_mexer_em(target, user)

    rows = await afiliado_service.listar_por_usuario(db, usuario_id=target.id)
    out: list[AfiliadoPublico] = []
    for r in rows:
        mkt = marketplaces.por_slug(r.plataforma)
        out.append(AfiliadoPublico(
            plataforma=r.plataforma,
            nome=mkt.nome if mkt else r.plataforma,
            icone=mkt.icone if mkt else "🏷️",
            tag=r.tag,
        ))
    return out


@router.post("/{usuario_id}/afiliados", response_model=AfiliadoPublico,
             status_code=status.HTTP_201_CREATED)
async def cadastrar_afiliado(
    usuario_id: int,
    body: AfiliadoUpsertRequest,
    user: Usuario = Depends(usuario_atual),
    db: AsyncSession = Depends(get_db_async),
) -> AfiliadoPublico:
    """Cria ou atualiza tag de afiliado pra um marketplace (upsert)."""
    target = await _get_da_org(db, org_id=user.org_id, usuario_id=usuario_id)
    _autorizar_mexer_em(target, user)
    _gate_plano_cadastrar(user)

    mkt = marketplaces.por_slug(body.plataforma)
    if mkt is None:
        raise HTTPException(
            status_code=400,
            detail=f"Marketplace '{body.plataforma}' não suportado. "
                   f"Suportados: {sorted(marketplaces.slugs_validos())}",
        )

    try:
        row = await afiliado_service.upsert(
            db, usuario_id=target.id, plataforma=mkt.slug, tag=body.tag,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None

    return AfiliadoPublico(
        plataforma=row.plataforma, nome=mkt.nome, icone=mkt.icone, tag=row.tag,
    )


@router.delete("/{usuario_id}/afiliados/{plataforma}", response_model=Mensagem)
async def remover_afiliado(
    usuario_id: int,
    plataforma: str,
    user: Usuario = Depends(usuario_atual),
    db: AsyncSession = Depends(get_db_async),
) -> Mensagem:
    """Remove cadastro de afiliado de uma plataforma específica."""
    target = await _get_da_org(db, org_id=user.org_id, usuario_id=usuario_id)
    _autorizar_mexer_em(target, user)
    _gate_plano_cadastrar(user)

    removeu = await afiliado_service.remover(
        db, usuario_id=target.id, plataforma=plataforma.lower().strip(),
    )
    if not removeu:
        raise HTTPException(status_code=404, detail="Afiliado não encontrado")
    return Mensagem(mensagem="Afiliado removido")


# ── helpers ──────────────────────────────────────────

async def _get_da_org(
    db: AsyncSession, *, org_id: int, usuario_id: int,
) -> Usuario:
    """404 se não existir OU se for de outra org. Usado em endpoints
    estritamente escopados por org (afiliados, detalhe, etc)."""
    target = await db.get(Usuario, usuario_id)
    if target is None or target.org_id != org_id:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    return target


async def _get_target_visivel(
    db: AsyncSession, *, actor: Usuario, usuario_id: int,
) -> Usuario:
    """Carrega target respeitando a visibilidade do actor:
    - Admin central → enxerga qualquer org (404 só se não existir)
    - Demais → só a própria org

    Usado em endpoints que admitem cross-org pra admin central
    (editar/desativar/excluir users de qualquer cliente)."""
    target = await db.get(Usuario, usuario_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    if not actor.eh_admin_central and target.org_id != actor.org_id:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    return target

"""Service puro pras decisões de quem pode mudar/excluir papel de quem.

Concentrado num só lugar pra ser testável e usado tanto pelas rotas web
(Jinja) quanto pela API REST.

Matriz de permissão (17/05/2026 — pedido do user):

| Actor                          | Target                              | Pode?            |
|--------------------------------|-------------------------------------|------------------|
| super                          | qualquer (≠ self)                   | tudo, inc. super |
| admin_central (não-super)      | usuario/afiliado/admin qualquer org | até `admin`      |
| admin_central (não-super)      | super OU peer admin promovido       | NÃO              |
| admin não-central              | usuario/afiliado/admin própria org  | até `admin`      |
| admin não-central              | qualquer fora da própria org        | NÃO              |
| outros                         | qualquer                            | NÃO              |

Salvaguardas:
- actor.id == target.id → sempre NÃO (não pode se rebaixar/excluir).
- Excluir último `super` ativo do sistema → NÃO.
- Excluir último `admin/super` ativo de uma org → NÃO (org órfã).
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Usuario

# Ordem de "altura" dos papéis pra promover/rebaixar 1 degrau.
HIERARQUIA_PAPEIS: tuple[str, ...] = ("usuario", "afiliado", "admin", "super")

PAPEIS_VALIDOS: frozenset[str] = frozenset(HIERARQUIA_PAPEIS)


def proximo_papel_acima(papel_atual: str) -> str | None:
    """Próximo degrau acima na hierarquia. Topo (`super`) → None."""
    try:
        i = HIERARQUIA_PAPEIS.index(papel_atual)
    except ValueError:
        return None
    if i + 1 >= len(HIERARQUIA_PAPEIS):
        return None
    return HIERARQUIA_PAPEIS[i + 1]


def proximo_papel_abaixo(papel_atual: str) -> str | None:
    """Próximo degrau abaixo. Base (`usuario`) → None."""
    try:
        i = HIERARQUIA_PAPEIS.index(papel_atual)
    except ValueError:
        return None
    if i - 1 < 0:
        return None
    return HIERARQUIA_PAPEIS[i - 1]


def pode_mudar_papel(
    actor: Usuario, target: Usuario, novo_papel: str,
) -> tuple[bool, str]:
    """Pode `actor` setar papel de `target` pra `novo_papel`?

    Returns (ok, motivo). Motivo é mensagem amigável quando ok=False.
    """
    if novo_papel not in PAPEIS_VALIDOS:
        return False, f"Papel inválido: {novo_papel!r}"

    if actor.id == target.id:
        return False, "Você não pode mudar o próprio papel"

    # Promover pra 'super' → só outro super
    if novo_papel == "super" and not actor.eh_super:
        return False, "Apenas um super admin promove outro a super"

    # Mexer em quem JÁ é admin/super → só super
    if target.papel in ("admin", "super") and not actor.eh_super:
        return False, "Apenas um super admin altera o papel de outro admin"

    # Admin central comum (não-super): pode mexer em usuario/afiliado de qualquer org,
    # mas não pode promover ninguém pra super (já tratado acima) nem mexer em admin/super.
    if actor.eh_admin_central:
        return True, ""

    # Admin não-central: só na própria org
    if actor.eh_admin:
        if target.org_id != actor.org_id:
            return False, "Você só pode mexer em usuários da sua organização"
        return True, ""

    return False, "Você não tem permissão pra alterar papéis"


def pode_editar_dados(actor: Usuario, target: Usuario) -> tuple[bool, str]:
    """Pode `actor` editar dados (nome, email, ativo) de `target`?

    Self-edit é permitido (usuário muda próprio nome/email). Pra mudar PAPEL
    use `pode_mudar_papel` separadamente — esta função NÃO autoriza isso.
    """
    if actor.id == target.id:
        return True, ""

    if target.papel in ("admin", "super") and not actor.eh_super:
        return False, "Apenas um super admin edita outro admin"

    if actor.eh_admin_central:
        return True, ""

    if actor.eh_admin:
        if target.org_id != actor.org_id:
            return False, "Você só pode editar usuários da sua organização"
        return True, ""

    return False, "Você não tem permissão pra editar este usuário"


def pode_excluir(actor: Usuario, target: Usuario) -> tuple[bool, str]:
    """Pode `actor` apagar (hard delete) `target`?

    Mesma matriz de `pode_mudar_papel`, mas SEM a etapa de novo_papel.
    Checks adicionais de salvaguarda (último super, último admin da org)
    vivem em `_checar_salvaguardas_exclusao` que precisa de DB async.
    """
    if actor.id == target.id:
        return False, "Você não pode excluir a si mesmo"

    if target.papel in ("admin", "super") and not actor.eh_super:
        return False, "Apenas um super admin exclui outro admin"

    if actor.eh_admin_central:
        return True, ""

    if actor.eh_admin:
        if target.org_id != actor.org_id:
            return False, "Você só pode excluir usuários da sua organização"
        return True, ""

    return False, "Você não tem permissão pra excluir este usuário"


def pode_desativar(actor: Usuario, target: Usuario) -> tuple[bool, str]:
    """Pode `actor` setar `ativo=False` em `target`?

    Mesma matriz da exclusão. Mantida como função separada pra deixar
    explícito no chamador qual a intenção (soft vs hard).
    """
    return pode_excluir(actor, target)


async def checar_salvaguardas_exclusao(
    db: AsyncSession, target: Usuario,
) -> tuple[bool, str]:
    """Checks que dependem do estado do DB:

    1. Não pode excluir o ÚLTIMO super ativo do sistema (perderíamos a
       cadeia de promoção pra super).
    2. Não pode excluir o ÚLTIMO admin ativo de uma org (org ficaria
       sem ninguém capaz de gerenciá-la).
    """
    if target.papel == "super":
        total = await db.scalar(
            select(func.count())
            .select_from(Usuario)
            .where(Usuario.papel == "super", Usuario.ativo.is_(True))
        ) or 0
        if total <= 1:
            return (
                False,
                "Não dá pra excluir o último super admin do sistema",
            )

    if target.papel in ("admin", "super"):
        total_org = await db.scalar(
            select(func.count())
            .select_from(Usuario)
            .where(
                Usuario.org_id == target.org_id,
                Usuario.papel.in_(("admin", "super")),
                Usuario.ativo.is_(True),
            )
        ) or 0
        if total_org <= 1:
            return (
                False,
                "Esta organização ficaria sem admin — promova outro antes",
            )

    return True, ""


async def checar_salvaguardas_desativacao(
    db: AsyncSession, target: Usuario,
) -> tuple[bool, str]:
    """Mesmas salvaguardas da exclusão — desativar o último super/admin
    causa o mesmo problema lógico (perde acesso administrativo)."""
    return await checar_salvaguardas_exclusao(db, target)


async def checar_salvaguardas_rebaixamento(
    db: AsyncSession, target: Usuario, novo_papel: str,
) -> tuple[bool, str]:
    """Rebaixar = manter user ativo mas reduzir privilégio. Aplica as
    mesmas regras: não rebaixe o último super/admin da org pra papel
    sem privilégio (admin → afiliado é rebaixamento)."""
    rebaixando_super = (
        target.papel == "super" and novo_papel != "super"
    )
    rebaixando_admin = (
        target.papel in ("admin", "super") and novo_papel not in ("admin", "super")
    )

    if rebaixando_super:
        total = await db.scalar(
            select(func.count())
            .select_from(Usuario)
            .where(Usuario.papel == "super", Usuario.ativo.is_(True))
        ) or 0
        if total <= 1:
            return (
                False,
                "Não dá pra rebaixar o último super admin do sistema",
            )

    if rebaixando_admin:
        total_org = await db.scalar(
            select(func.count())
            .select_from(Usuario)
            .where(
                Usuario.org_id == target.org_id,
                Usuario.papel.in_(("admin", "super")),
                Usuario.ativo.is_(True),
            )
        ) or 0
        if total_org <= 1:
            return (
                False,
                "Esta organização ficaria sem admin — promova outro antes",
            )

    return True, ""

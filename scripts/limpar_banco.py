"""
DESTRUTIVO — apaga dados do banco DEIXANDO SÓ os admins.

O que **PERMANECE** (NÃO é apagado):
- Usuários com `papel in ('admin', 'super')`
- Organizações que contêm pelo menos 1 admin/super
- Planos (seed do sistema)
- Nichos (seed)
- NichoCategoriaML (mappings — seed)

O que é **APAGADO**:
- Usuários não-admin (`papel` = 'usuario' ou 'afiliado')
- Organizações que ficaram sem nenhum admin
- TODOS os grupos, canais, agentes, tarefas, postagens
- TODOS os produtos (catálogo) + nichos vinculados + redirects
- TODAS as buscas customizadas
- TODAS as templates (recriar via UI)
- TODAS as solicitações personalizadas + favoritos
- TODOS os usuários_afiliados (tags de afiliados — qualquer user)

Como rodar (Railway / container prod):
    python -m scripts.limpar_banco --confirmar APAGAR

Sem `--confirmar APAGAR`, só mostra preview do que seria apagado.

Pra rodar local em dev:
    cd D:/ACHADINHOSV3
    docker compose exec api python -m scripts.limpar_banco --confirmar APAGAR
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import sessao_async
from app.models import (
    Agente,
    BuscaML,
    Canal,
    Grupo,
    GrupoNicho,
    Organizacao,
    Postagem,
    Produto,
    ProdutoNicho,
    Redirect,
    SolicitacaoPersonalizada,
    Tarefa,
    TemplateMensagem,
    Usuario,
    UsuarioAfiliado,
    UsuarioProdutoPersonalizado,
)


async def preview(db: AsyncSession) -> dict[str, int]:
    """Conta o que existe hoje pra mostrar antes de apagar."""
    from sqlalchemy import func as f

    async def count(model) -> int:
        return await db.scalar(select(f.count()).select_from(model)) or 0

    total_users = await count(Usuario)
    admins = await db.scalar(
        select(f.count()).select_from(Usuario).where(
            Usuario.papel.in_(["admin", "super"])
        )
    ) or 0

    total_orgs = await count(Organizacao)
    # Orgs com pelo menos 1 admin (vão FICAR)
    orgs_com_admin = await db.scalar(
        select(f.count(f.distinct(Usuario.org_id))).where(
            Usuario.papel.in_(["admin", "super"])
        )
    ) or 0

    return {
        "usuarios_total":    total_users,
        "usuarios_admin":    admins,
        "usuarios_a_apagar": total_users - admins,
        "orgs_total":        total_orgs,
        "orgs_a_manter":     orgs_com_admin,
        "orgs_a_apagar":     total_orgs - orgs_com_admin,
        "grupos":            await count(Grupo),
        "canais":            await count(Canal),
        "agentes":           await count(Agente),
        "produtos":          await count(Produto),
        "templates":         await count(TemplateMensagem),
        "tarefas":           await count(Tarefa),
        "postagens":         await count(Postagem),
        "buscas_ml":         await count(BuscaML),
        "redirects":         await count(Redirect),
        "solicitacoes":      await count(SolicitacaoPersonalizada),
        "favoritos_upp":     await count(UsuarioProdutoPersonalizado),
        "usuarios_afiliados": await count(UsuarioAfiliado),
    }


async def executar(db: AsyncSession) -> None:
    """Apaga TUDO mantendo só admins + orgs com admin + seeds."""
    # ── 1. Pega IDs que vão SOBREVIVER ──
    admin_ids = list((await db.execute(
        select(Usuario.id).where(Usuario.papel.in_(["admin", "super"]))
    )).scalars().all())

    org_admins_ids = list((await db.execute(
        select(Usuario.org_id).where(Usuario.papel.in_(["admin", "super"]))
        .distinct()
    )).scalars().all())

    print(f"\n  Mantendo {len(admin_ids)} usuário(s) admin: {admin_ids}")
    print(f"  Mantendo {len(org_admins_ids)} org(s): {org_admins_ids}\n")

    # ── 2. Apaga em ordem (FKs) ──
    # Postagens → Tarefas → BuscaML → Solicitações → UPP →
    # Redirects → ProdutoNicho → Produto → GrupoNicho → Grupo →
    # TemplateMensagem → UsuarioAfiliado → Canal → Agente →
    # Usuario (non-admin) → Organizacao (sem admin)

    # CASCADE deve cobrir muita coisa, mas pra ser explícito:
    for nome, model in [
        ("Postagem",                   Postagem),
        ("Tarefa",                     Tarefa),
        ("BuscaML",                    BuscaML),
        ("SolicitacaoPersonalizada",   SolicitacaoPersonalizada),
        ("UsuarioProdutoPersonalizado", UsuarioProdutoPersonalizado),
        ("Redirect",                   Redirect),
        ("ProdutoNicho",               ProdutoNicho),
        ("Produto",                    Produto),
        ("GrupoNicho",                 GrupoNicho),
        ("Grupo",                      Grupo),
        ("TemplateMensagem",           TemplateMensagem),
        ("UsuarioAfiliado",            UsuarioAfiliado),
        ("Canal",                      Canal),
        ("Agente",                     Agente),
    ]:
        result = await db.execute(delete(model))
        print(f"  [ok] {nome:30} apagados={result.rowcount or 0}")

    # Apaga usuários NÃO admin
    if admin_ids:
        result = await db.execute(
            delete(Usuario).where(~Usuario.id.in_(admin_ids))
        )
    else:
        result = await db.execute(delete(Usuario))
    print(f"  [ok] {'Usuario (non-admin)':30} apagados={result.rowcount or 0}")

    # Apaga orgs que ficaram sem nenhum admin
    if org_admins_ids:
        result = await db.execute(
            delete(Organizacao).where(~Organizacao.id.in_(org_admins_ids))
        )
    else:
        result = await db.execute(delete(Organizacao))
    print(f"  [ok] {'Organizacao (sem admin)':30} apagadas={result.rowcount or 0}")

    await db.commit()
    print("\n✅ Banco limpo. Permanecem só admins + orgs deles + seeds (planos/nichos).")


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Limpa banco mantendo só admins.",
    )
    parser.add_argument(
        "--confirmar",
        type=str,
        default="",
        help="Passe `--confirmar APAGAR` pra executar. Sem isso, só preview.",
    )
    args = parser.parse_args()

    async with sessao_async() as db:
        contagens = await preview(db)

    print("\n" + "═" * 60)
    print("  PREVIEW — quantidades atuais no banco")
    print("═" * 60)
    print(f"  Usuários TOTAL   : {contagens['usuarios_total']}")
    print(f"    admins/super   : {contagens['usuarios_admin']}      ← FICAM")
    print(f"    a apagar       : {contagens['usuarios_a_apagar']}")
    print()
    print(f"  Organizações TOTAL : {contagens['orgs_total']}")
    print(f"    com admin (mantém): {contagens['orgs_a_manter']}")
    print(f"    a apagar           : {contagens['orgs_a_apagar']}")
    print()
    print("  Dados que serão APAGADOS:")
    for chave in [
        "grupos", "canais", "agentes", "produtos", "templates", "tarefas",
        "postagens", "buscas_ml", "redirects", "solicitacoes",
        "favoritos_upp", "usuarios_afiliados",
    ]:
        print(f"    {chave:20} : {contagens[chave]}")
    print("═" * 60)

    if args.confirmar != "APAGAR":
        print("\n  💡 Modo PREVIEW — nada apagado.")
        print("  Pra executar: python -m scripts.limpar_banco --confirmar APAGAR\n")
        return 0

    print("\n  ⚠  EXECUTANDO LIMPEZA — IRREVERSÍVEL\n")
    async with sessao_async() as db:
        await executar(db)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

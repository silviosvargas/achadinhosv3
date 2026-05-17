"""
Usuários do sistema. Equivalente à tabela `usuarios` da V2,
mas com `org_id` (multi-tenant).

Papéis:
- 'admin':     dono da org, faz tudo
- 'afiliado':  posta nos próprios grupos com agente local próprio
- 'usuario':   acesso limitado (visualiza)
- 'super':    super-admin do SaaS (cross-org, raro — só para suporte)

Tags de afiliado: vivem na tabela `usuarios_afiliados` (1 row por user × marketplace).
O campo legacy `usuarios.afiliado_ml` foi mantido pra dual-read durante a
transição (Fase 13). Outros marketplaces nunca tiveram coluna — sempre
foram via env settings + nova tabela.
"""
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.organizacao import Organizacao


class UsuarioAfiliado(Base, TimestampMixin):
    """Tag de afiliado de um user pra UM marketplace específico (Fase 13).

    Substituiu as colunas mono-marketplace (`usuarios.afiliado_*`). Permite
    adicionar marketplace novo sem migration — basta atualizar a constante
    `MARKETPLACES` em `app.core.marketplaces`.
    """
    __tablename__ = "usuarios_afiliados"
    __table_args__ = (
        UniqueConstraint("usuario_id", "plataforma", name="uq_usuarios_afiliados_user_plat"),
    )

    id:          Mapped[int] = mapped_column(Integer, primary_key=True)
    usuario_id:  Mapped[int] = mapped_column(
        ForeignKey("usuarios.id", ondelete="CASCADE"), index=True,
    )
    plataforma:  Mapped[str] = mapped_column(String(20), index=True)  # 'ml', 'shopee', ...
    tag:         Mapped[str] = mapped_column(String(200))


class Usuario(Base, TimestampMixin):
    """Usuário pertencente a uma organização."""
    __tablename__ = "usuarios"
    __table_args__ = (
        # Login é único DENTRO da org, não global. Permite "joao" em org A e B.
        UniqueConstraint("org_id", "login", name="uq_usuarios_org_login"),
    )

    id:     Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizacoes.id", ondelete="CASCADE"),
        index=True,
    )

    login:        Mapped[str] = mapped_column(String(80))
    senha_hash:   Mapped[str] = mapped_column(String(255))
    papel:        Mapped[str] = mapped_column(String(20), default="usuario")
    nome_exibicao: Mapped[str | None] = mapped_column(String(150), default=None)
    email:        Mapped[str | None] = mapped_column(String(255), default=None)
    ativo:        Mapped[bool]       = mapped_column(Boolean, default=True)

    # Legacy: mantido pra dual-read na transição. Migration 0008 backfilla
    # esse valor pra `usuarios_afiliados`. Será removido em migration futura.
    afiliado_ml:  Mapped[str | None] = mapped_column(String(100), default=None)

    # Limites individuais (sobrescrevem os do plano se preenchidos)
    limite_postagens_dia: Mapped[int | None] = mapped_column(default=None)

    # Onboarding (afiliado fez tour inicial?)
    onboarding_completo: Mapped[bool] = mapped_column(Boolean, default=False)

    # Última atividade
    ultimo_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    # ── Relacionamentos ──────────────────────────────
    # lazy="joined" pra funcionar em contexto async (sem MissingGreenlet).
    # Organizacao.plano já vem joined → cadeia user→org→plano carrega em 1 query.
    organizacao: Mapped["Organizacao"] = relationship(
        back_populates="usuarios", lazy="joined",
    )

    def __repr__(self) -> str:
        return f"<Usuario id={self.id} login={self.login!r} org={self.org_id}>"

    @property
    def eh_admin(self) -> bool:
        return self.papel in ("admin", "super")

    @property
    def eh_super(self) -> bool:
        """Super admin estrela — pode promover/rebaixar outros admins/super.
        Cadeia restrita: só outro super faz alguém virar super. Migration
        0019 promove o admin mais antigo da org central pra abrir a cadeia."""
        return self.papel == "super"

    @property
    def eh_afiliado(self) -> bool:
        return self.papel == "afiliado"

    @property
    def eh_admin_central(self) -> bool:
        """True se for admin da org central Achadinhos (settings.admin_org_id).

        Pelas regras de produto (17/05/2026):
        - Cliente comum (qualquer plano, qualquer papel) NUNCA cadastra
          afiliado próprio, busca própria ou produto próprio.
        - Só o admin central pode gerenciar catálogo, buscas, afiliados.

        Esse predicate substituiu as flags `pode_cadastrar_afiliado`,
        `pode_criar_buscas`, `pode_criar_produto_proprio` do plano —
        agora a permissão é arquitetural (qual org), não comercial
        (qual plano).
        """
        from app.core.config import settings
        return self.eh_admin and self.org_id == settings.admin_org_id


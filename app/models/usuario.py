"""
Usuários do sistema. Equivalente à tabela `usuarios` da V2,
mas com `org_id` (multi-tenant).

Papéis:
- 'admin':     dono da org, faz tudo
- 'afiliado':  posta nos próprios grupos com agente local próprio
- 'usuario':   acesso limitado (visualiza)
- 'super':    super-admin do SaaS (cross-org, raro — só para suporte)
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

    # Tags de afiliado próprias (sobrescrevem as da org se preenchidas)
    afiliado_ml:        Mapped[str | None] = mapped_column(String(100), default=None)
    afiliado_shopee:    Mapped[str | None] = mapped_column(String(100), default=None)
    afiliado_amazon:    Mapped[str | None] = mapped_column(String(100), default=None)
    afiliado_magalu:    Mapped[str | None] = mapped_column(String(100), default=None)
    afiliado_aliexpress: Mapped[str | None] = mapped_column(String(100), default=None)

    # Credenciais de login na plataforma (Fase 4b.1) — senha sempre CIFRADA.
    # Use set_senha_ml() / get_senha_ml() em vez de acessar a coluna direto.
    usuario_ml:       Mapped[str | None] = mapped_column(String(150), default=None)
    senha_ml_cifrada: Mapped[str | None] = mapped_column(String(500), default=None)

    # Limites individuais (sobrescrevem os do plano se preenchidos)
    limite_postagens_dia: Mapped[int | None] = mapped_column(default=None)

    # Onboarding (afiliado fez tour inicial?)
    onboarding_completo: Mapped[bool] = mapped_column(Boolean, default=False)

    # Última atividade
    ultimo_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    # ── Relacionamentos ──────────────────────────────
    organizacao: Mapped["Organizacao"] = relationship(back_populates="usuarios")

    def __repr__(self) -> str:
        return f"<Usuario id={self.id} login={self.login!r} org={self.org_id}>"

    @property
    def eh_admin(self) -> bool:
        return self.papel in ("admin", "super")

    @property
    def eh_afiliado(self) -> bool:
        return self.papel == "afiliado"

    # ── Credenciais ML (cifragem transparente) ──────────────
    # Import tardio evita ciclo com app.core.config no boot inicial dos
    # models.

    def set_senha_ml(self, plain: str | None) -> None:
        """Cifra e armazena senha do ML. Passar None ou '' apaga."""
        from app.core.crypto import cifrar
        self.senha_ml_cifrada = cifrar(plain) if plain else None

    def get_senha_ml(self) -> str | None:
        """Decifra senha do ML. Lança CredencialError se chave estiver errada."""
        from app.core.crypto import decifrar
        return decifrar(self.senha_ml_cifrada)

    @property
    def tem_senha_ml(self) -> bool:
        """Útil pra UI saber se mostra '****' ou form vazio."""
        return bool(self.senha_ml_cifrada)

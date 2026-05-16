"""
Catálogo de produtos — ISOLADO por org.

Decisão arquitetural: cada org tem seu próprio catálogo. Permite cobrar
por cliente e mantém privacidade entre orgs concorrentes.

Se no futuro quiser "produtos da comunidade" (visíveis pra todos),
basta permitir `org_id NULL` e ajustar queries.

Plataformas suportadas: ml, shopee, amazon, magalu, aliexpress.
"""
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class Plataforma(StrEnum):
    """Plataformas suportadas. Centraliza pra evitar typos."""
    ML         = "ml"
    SHOPEE     = "shopee"
    AMAZON     = "amazon"
    MAGALU     = "magalu"
    ALIEXPRESS = "aliexpress"


class Produto(Base, TimestampMixin):
    """
    Produto do catálogo de uma org.

    Identidade (partial unique indexes em Postgres):
    - Públicos da org (usuario_dono_id IS NULL): unique (org_id, plataforma, item_id)
    - Privados de afiliado (usuario_dono_id NOT NULL): unique (org_id,
      usuario_dono_id, plataforma, item_id)

    Permite o mesmo MLB existir como público da org E privado de cada afiliado
    com tags de afiliado diferentes. Ver ADR-008.
    """
    __tablename__ = "produtos"
    __table_args__ = (
        Index("ix_produtos_atualizado", "atualizado_em"),
        Index("ix_produtos_org_plat", "org_id", "plataforma"),
        Index(
            "uq_produtos_publico",
            "org_id", "plataforma", "item_id",
            unique=True,
            postgresql_where=text("usuario_dono_id IS NULL"),
        ),
        Index(
            "uq_produtos_privado",
            "org_id", "usuario_dono_id", "plataforma", "item_id",
            unique=True,
            postgresql_where=text("usuario_dono_id IS NOT NULL"),
        ),
    )

    id:         Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id:     Mapped[int] = mapped_column(
        Integer, ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True,
    )
    # NULL = produto público da org (admin/usuário comum vê e posta).
    # Preenchido = produto privado do afiliado (só ele vê e posta com a tag dele).
    usuario_dono_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("usuarios.id", ondelete="CASCADE"),
        default=None, index=True,
    )
    # Quem CADASTROU o produto (diferente do dono):
    # - Em busca automática: NULL (ninguém especifico)
    # - Em "Produtos Personalizados" UI: sempre preenchido. Permite o user
    #   ver SEUS personalizados mesmo quando o produto vira público
    #   (`usuario_dono_id = NULL` pra usuários comuns/admin com tag central).
    criado_por_usuario_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("usuarios.id", ondelete="SET NULL"),
        default=None, index=True,
    )
    plataforma: Mapped[str] = mapped_column(String(20), index=True)
    item_id:    Mapped[str] = mapped_column(String(100))

    nome:       Mapped[str] = mapped_column(String(500))
    categoria:  Mapped[str | None] = mapped_column(String(200), default=None)

    preco:        Mapped[float] = mapped_column(Float, default=0.0)
    preco_orig:   Mapped[float | None] = mapped_column(Float, default=None)
    desconto:     Mapped[float | None] = mapped_column(Float, default=None)
    comissao:     Mapped[float | None] = mapped_column(Float, default=None)
    frete_gratis: Mapped[bool]  = mapped_column(Boolean, default=False)

    # URL canônica (sem tag de afiliado). Tag é da org (em ConfigOrg, futuro).
    url_canonica: Mapped[str | None] = mapped_column(String(2000), default=None)
    # URL com tag de afiliado pronta (preenchido por linkbuilder na busca)
    url_afiliado: Mapped[str | None] = mapped_column(String(2000), default=None)

    foto_url:     Mapped[str | None] = mapped_column(String(2000), default=None)

    # Bloqueio rápido: admin marcou como "não postar"
    bloqueado:        Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    bloqueado_motivo: Mapped[str | None] = mapped_column(String(500), default=None)

    # Diagnóstico
    fonte:        Mapped[str | None] = mapped_column(String(50), default=None,
                                                     comment="busca_ml | csv_import | manual")
    descoberto_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None,
    )

    # Relacionamentos
    nichos: Mapped[list["ProdutoNicho"]] = relationship(
        back_populates="produto", cascade="all, delete-orphan",
    )


class ProdutoNicho(Base):
    """
    N:N entre produtos e nichos.

    Determinado por:
    - Categoria do produto (mapping categoria→nicho)
    - Análise de keywords no nome
    - Manual (admin pode marcar)
    """
    __tablename__ = "produto_nichos"
    __table_args__ = (
        UniqueConstraint("produto_id", "nicho_id", name="uq_produto_nicho"),
    )

    id:         Mapped[int] = mapped_column(Integer, primary_key=True)
    produto_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("produtos.id", ondelete="CASCADE"), index=True,
    )
    nicho_id:   Mapped[int] = mapped_column(
        Integer, ForeignKey("nichos.id", ondelete="CASCADE"), index=True,
    )

    produto: Mapped[Produto] = relationship(back_populates="nichos")


class Nicho(Base, TimestampMixin):
    """
    Nicho canônico (beleza, moda_feminina, tecnologia...).
    Lista global, não muda por org. Conteúdo seedeado da V2.
    """
    __tablename__ = "nichos"

    id:     Mapped[int]  = mapped_column(Integer, primary_key=True)
    slug:   Mapped[str]  = mapped_column(String(50), unique=True, index=True)
    label:  Mapped[str]  = mapped_column(String(150))
    icone:  Mapped[str | None] = mapped_column(String(10), default=None)
    ativo:  Mapped[bool] = mapped_column(Boolean, default=True)
    ordem:  Mapped[int]  = mapped_column(Integer, default=0)


class TemplateMensagem(Base, TimestampMixin):
    """
    Template de texto pra postagem, organizado por org + nicho.

    Quando um produto vai ser postado, o sistema escolhe um template
    do nicho do produto (com rotação) e renderiza com placeholders.

    Placeholders suportados (renderização em templates_service):
    - {nome}            nome do produto
    - {preco}           preço promocional formatado (R$ 99,90)
    - {preco_orig}      preço original (riscado)
    - {desconto}        percentual de desconto (15%)
    - {bloco_preco}     bloco "De ~R$ 159~ por R$ 89,90"
    - {plataforma}      "Mercado Livre" / "Shopee" etc
    - {url}             URL com tag de afiliado
    - {chamada}         frase aleatória ("Corre que vai esgotar!")
    - {chamada_emoji}   emoji aleatório de chamada
    """
    __tablename__ = "templates_mensagem"
    __table_args__ = (
        Index("ix_templates_org_nicho", "org_id", "nicho_id"),
    )

    id:       Mapped[int]  = mapped_column(Integer, primary_key=True)
    org_id:   Mapped[int]  = mapped_column(
        Integer, ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True,
    )
    nicho_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("nichos.id", ondelete="SET NULL"), default=None, index=True,
        comment="NULL = template padrão da org (fallback quando nicho não tem template)",
    )
    nome:     Mapped[str]  = mapped_column(String(150))
    texto:    Mapped[str]  = mapped_column(String(4096))
    ativo:    Mapped[bool] = mapped_column(Boolean, default=True)
    ordem:    Mapped[int]  = mapped_column(Integer, default=0,
                                            comment="Pra exibição ordenada na UI")

    # Diagnóstico
    vezes_usado:   Mapped[int] = mapped_column(Integer, default=0)
    ultimo_uso_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None,
    )

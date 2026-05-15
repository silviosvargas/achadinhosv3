"""
Redirect = entry do encurtador próprio (Fase 14).

Mapping (slug curto) → (URL completa do marketplace com tag de afiliado).
1 row por produto (UNIQUE produto_id). Atualiza `url_destino` quando a
tag muda; slugs antigos no WhatsApp continuam válidos.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Redirect(Base):
    __tablename__ = "redirects"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_redirects_slug"),
        UniqueConstraint("produto_id", name="uq_redirects_produto"),
    )

    id:          Mapped[int] = mapped_column(Integer, primary_key=True)
    slug:        Mapped[str] = mapped_column(String(20), index=True)
    produto_id:  Mapped[int | None] = mapped_column(
        ForeignKey("produtos.id", ondelete="CASCADE"), nullable=True,
    )
    url_destino: Mapped[str] = mapped_column(String(2000))

    total_clicks:    Mapped[int] = mapped_column(Integer, default=0)
    ultimo_click_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None,
    )

    criado_em:     Mapped[datetime] = mapped_column(DateTime(timezone=True))
    atualizado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True))

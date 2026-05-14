"""
Agentes locais (PCs Windows dos afiliados) e canais de postagem.

Modelo:
- 1 usuário pode ter 1 ou mais agentes (PC do trabalho + PC de casa).
- Cada agente registra suas plataformas conectadas (WhatsApp, eventualmente outras).
- Telegram NÃO precisa de agente — roda via Bot API direto na nuvem.
  Por isso `Canal` aceita tanto canais de agente (whatsapp) quanto canais
  cloud (telegram_bot).
"""
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.usuario import Usuario


class Agente(Base, TimestampMixin):
    """
    Um agente = 1 instância do app `AchadinhosAgent.exe` rodando num PC.

    O agente conecta no servidor via WebSocket e fica online.
    O servidor manda comandos ("posta isso") e recebe eventos ("postado", "erro").
    """
    __tablename__ = "agentes"

    id:         Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id:     Mapped[int] = mapped_column(
        ForeignKey("organizacoes.id", ondelete="CASCADE"),
        index=True,
    )
    usuario_id: Mapped[int] = mapped_column(
        ForeignKey("usuarios.id", ondelete="CASCADE"),
        index=True,
    )

    nome:           Mapped[str]   = mapped_column(String(100))   # "PC do João — escritório"
    token_hash:     Mapped[str]   = mapped_column(String(255))   # hash do JWT do agente (revogável)
    versao_app:     Mapped[str | None] = mapped_column(String(30), default=None)
    sistema_op:     Mapped[str | None] = mapped_column(String(50), default=None)  # "Windows 11"
    ativo:          Mapped[bool]  = mapped_column(Boolean, default=True)

    # Status em tempo real (atualizado a cada ping)
    online:           Mapped[bool] = mapped_column(Boolean, default=False)
    ultimo_ping:      Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    ultimo_ip:        Mapped[str | None] = mapped_column(String(50), default=None)

    # Métricas (ram_mb, chrome_ok, etc) — JSON livre pra evoluir sem migração
    metricas_atuais: Mapped[dict] = mapped_column(JSON, default=dict)

    usuario: Mapped["Usuario"] = relationship()


class Canal(Base, TimestampMixin):
    """
    Canal de postagem — abstração sobre WhatsApp, Telegram, etc.

    Tipos:
    - 'whatsapp_agente':   posta via agente local (depende de PC ligado)
    - 'telegram_bot':      posta via Bot API na nuvem (24h online)

    Cada canal tem `config` JSON com os dados próprios:
    - whatsapp_agente: { agente_id, perfil_chrome }
    - telegram_bot: { bot_token, bot_username }
    """
    __tablename__ = "canais"

    id:     Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizacoes.id", ondelete="CASCADE"),
        index=True,
    )
    usuario_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios.id", ondelete="SET NULL"),
        index=True,
        default=None,
    )

    tipo:     Mapped[str]  = mapped_column(String(30), index=True)
    nome:     Mapped[str]  = mapped_column(String(100))
    ativo:    Mapped[bool] = mapped_column(Boolean, default=True)
    config:   Mapped[dict] = mapped_column(JSON, default=dict)

    # Saúde
    ultima_postagem_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    ultima_falha_em:    Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    ultima_falha_msg:   Mapped[str | None]      = mapped_column(String(500), default=None)

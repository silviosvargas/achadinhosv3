"""
Tarefas — comandos enfileirados pra agentes locais ou workers cloud.

Por que uma tabela e não só Redis?
- Redis pode perder filas em restart sem persistência.
- A tabela dá auditoria: o que foi mandado, quando, sucesso/falha.
- Permite reenfileirar manualmente pelo dashboard ("tentar de novo").

O Redis ainda é usado, mas só pra notificação rápida ("ó agente X,
tem tarefa nova"). A fonte de verdade é a tabela.
"""
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class StatusTarefa(StrEnum):
    PENDENTE   = "pendente"        # esperando agente pegar
    PROCESSANDO = "processando"    # agente pegou, executando
    CONCLUIDA  = "concluida"       # ok
    FALHOU     = "falhou"          # erro permanente
    CANCELADA  = "cancelada"       # admin cancelou


class TipoTarefa(StrEnum):
    POSTAR_WHATSAPP      = "postar_whatsapp"
    POSTAR_TELEGRAM      = "postar_telegram"
    BUSCAR_MERCADO_LIVRE = "buscar_mercado_livre"
    BUSCAR_PRODUTOS      = "buscar_produtos"      # legado, manter pra retrocompat
    BAIXAR_IMAGEM        = "baixar_imagem"
    GERAR_LINK           = "gerar_link_afiliado"
    # Fase 18.3 (v3.4.1): re-abre URLs no agente pra capturar comissão
    # da barra preta de afiliados ML. Atualiza produtos sem rebuscar tudo.
    REVALIDAR_COMISSAO_ML = "revalidar_comissao_ml"


class Tarefa(Base, TimestampMixin):
    """Comando enfileirado. Persistente, auditável."""
    __tablename__ = "tarefas"
    __table_args__ = (
        Index("ix_tarefas_pendentes", "agente_id", "status"),
        Index("ix_tarefas_org_data", "org_id", "criado_em"),
    )

    id:     Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizacoes.id", ondelete="CASCADE"),
        index=True,
    )

    tipo:   Mapped[str] = mapped_column(String(50))                   # TipoTarefa
    status: Mapped[str] = mapped_column(String(30), default="pendente", index=True)

    # Quem deve executar:
    # - Tarefas de WhatsApp: agente_id NOT NULL (PC específico)
    # - Tarefas cloud (Telegram, busca HTTP): agente_id NULL — qualquer worker pega
    agente_id: Mapped[int | None] = mapped_column(
        ForeignKey("agentes.id", ondelete="SET NULL"),
        default=None,
        index=True,
    )

    # Payload completo do comando (URL imagem, texto, grupo, etc)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)

    # Resultado (preenchido quando termina)
    resultado: Mapped[dict | None] = mapped_column(JSON, default=None)
    erro:      Mapped[str | None] = mapped_column(String(1000), default=None)

    # Timing
    iniciado_em:   Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    concluido_em:  Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    proxima_tentativa_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        default=None,
    )

    tentativas:    Mapped[int] = mapped_column(Integer, default=0)
    max_tentativas: Mapped[int] = mapped_column(Integer, default=3)

    # Quem disparou (pra rastreio)
    criado_por_usuario_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios.id", ondelete="SET NULL"),
        default=None,
    )

    # Fase 20 — Progresso em tempo real (UI mostra barra no dashboard)
    progresso_pct: Mapped[float] = mapped_column(
        Float, default=0.0, server_default="0",
        comment="0..100. Agente reporta em checkpoints via WS tarefa_progresso.",
    )
    progresso_mensagem: Mapped[str | None] = mapped_column(
        String(200), default=None,
        comment="Texto curto pra UI: 'Categoria 3/8: Beleza', 'Capturando barra...'",
    )
    progresso_atualizado_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None,
    )
    # Fase 20.2 — Duração total (preenchido ao concluir/falhar/cancelar)
    duracao_seg: Mapped[int | None] = mapped_column(
        Integer, default=None,
        comment="(concluido_em - iniciado_em).total_seconds(). NULL enquanto rodando.",
    )

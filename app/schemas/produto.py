"""Schemas Pydantic — produto e template de mensagem."""
from datetime import datetime

from pydantic import BaseModel, Field


# ============================================================
# Produto
# ============================================================

class CriarProdutoRequest(BaseModel):
    """Cria produto manualmente (em geral importação CSV usa o mesmo formato)."""
    plataforma:   str   = Field(min_length=2, max_length=20)
    item_id:      str   = Field(min_length=1, max_length=100)
    nome:         str   = Field(min_length=1, max_length=500)
    categoria:    str | None = Field(default=None, max_length=200)
    preco:        float = Field(ge=0)
    preco_orig:   float | None = Field(default=None, ge=0)
    desconto:     float | None = Field(default=None, ge=0, le=100)
    comissao:     float | None = Field(default=None, ge=0, le=100)
    frete_gratis: bool = False
    url_canonica: str | None = Field(default=None, max_length=2000)
    url_afiliado: str | None = Field(default=None, max_length=2000)
    foto_url:     str | None = Field(default=None, max_length=2000)
    nichos_ids:   list[int] = Field(default_factory=list)
    fonte:        str | None = Field(default="manual", max_length=50)


class AtualizarProdutoRequest(BaseModel):
    nome:         str | None = Field(default=None, min_length=1, max_length=500)
    preco:        float | None = Field(default=None, ge=0)
    preco_orig:   float | None = Field(default=None, ge=0)
    desconto:     float | None = Field(default=None, ge=0, le=100)
    comissao:     float | None = Field(default=None, ge=0, le=100)
    url_afiliado: str | None = Field(default=None, max_length=2000)
    foto_url:     str | None = Field(default=None, max_length=2000)
    nichos_ids:   list[int] | None = None
    bloqueado:    bool | None = None
    bloqueado_motivo: str | None = Field(default=None, max_length=500)


class ProdutoPublico(BaseModel):
    id:              int
    org_id:          int
    usuario_dono_id: int | None
    plataforma:      str
    item_id:         str
    nome:         str
    categoria:    str | None
    preco:        float
    preco_orig:   float | None
    desconto:     float | None
    comissao:     float | None
    frete_gratis: bool
    url_canonica: str | None
    url_afiliado: str | None
    foto_url:     str | None
    bloqueado:    bool
    bloqueado_motivo: str | None
    fonte:        str | None
    nichos_ids:   list[int] = Field(default_factory=list)
    descoberto_em: datetime | None
    criado_em:    datetime
    atualizado_em: datetime

    model_config = {"from_attributes": True}


# ============================================================
# Template de mensagem
# ============================================================

class CriarTemplateRequest(BaseModel):
    nome:     str = Field(min_length=1, max_length=150)
    texto:    str = Field(min_length=1, max_length=4096)
    nicho_id: int | None = Field(default=None,
                                 description="None = template padrão (fallback)")
    ativo:    bool = True
    ordem:    int  = 0


class AtualizarTemplateRequest(BaseModel):
    nome:     str | None = Field(default=None, min_length=1, max_length=150)
    texto:    str | None = Field(default=None, min_length=1, max_length=4096)
    nicho_id: int | None = None
    ativo:    bool | None = None
    ordem:    int | None = None


class TemplatePublico(BaseModel):
    id:        int
    org_id:    int
    nicho_id:  int | None
    nome:      str
    texto:     str
    ativo:     bool
    ordem:     int
    vezes_usado: int
    ultimo_uso_em: datetime | None
    criado_em: datetime

    model_config = {"from_attributes": True}


# ============================================================
# Ingest (agente local envia produtos extraídos da busca)
# ============================================================

class IngestProdutoItem(BaseModel):
    """Um produto extraído pelo agente. URL canônica obrigatória (sem tag).

    Aceita campos extras (ex: `_personalizado_dono_id`) que o ingest do
    busca_service consome. Por isso `model_config = {"extra": "allow"}`.
    """
    plataforma:   str   = Field(default="ml", min_length=2, max_length=20)
    item_id:      str   = Field(min_length=1, max_length=100,
                                description="MLB12345 etc")
    nome:         str   = Field(min_length=1, max_length=500)
    preco:        float = Field(ge=0)
    preco_orig:   float | None = Field(default=None, ge=0)
    desconto:     float | None = Field(default=None, ge=0, le=100)
    comissao:     float | None = Field(default=None, ge=0, le=100,
                                       description="% de comissão (estimada por categoria)")
    frete_gratis: bool  = False
    categoria:    str | None = Field(default=None, max_length=200,
                                     description="Caminho completo do ML")
    url_canonica: str   = Field(min_length=1, max_length=2000,
                                description="URL crua, sem tag de afiliado")
    url_afiliado: str | None = Field(default=None, max_length=2000,
                                     description="URL com tag JÁ aplicada — meli.la, "
                                                 "s.shopee.com.br, amzn.to. None = "
                                                 "servidor calcula fallback.")
    foto_url:     str | None = Field(default=None, max_length=2000)

    model_config = {"extra": "allow"}


class IngestLoteRequest(BaseModel):
    """Lote de produtos enviado pelo agente após executar uma busca."""
    busca_id:  int | None = Field(default=None,
        description="Busca que originou. None = ingest manual/avulso.")
    tarefa_id: int | None = Field(default=None,
        description="Tarefa correspondente (se houver). Cloud marca concluida.")
    produtos: list[IngestProdutoItem] = Field(default_factory=list)


class ResultadoIngest(BaseModel):
    recebidos:   int
    criados:     int
    atualizados: int
    ignorados:   int = Field(description="Sem url_canonica, item_id duplicado interno, etc")
    com_nicho:   int = Field(description="Quantos receberam nicho automático via mapping")
    detalhes:    list[str] = Field(default_factory=list)


# ============================================================
# Lote (rodar postagem agora)
# ============================================================

class RodarLoteRequest(BaseModel):
    """Dispara um lote de postagens manuais.

    Estratégia: pega N produtos válidos da org (não-bloqueados, com nicho),
    pra cada um decide grupo elegível, renderiza template, enfileira tarefa.
    """
    max_produtos: int = Field(default=10, ge=1, le=50)
    canal_tipo:   str | None = Field(
        default=None,
        pattern=r"^(whatsapp_agente|telegram_bot)$",
        description="Filtrar só canais desse tipo. None = todos.",
    )


class ResultadoLote(BaseModel):
    produtos_avaliados: int
    tarefas_criadas:    int
    sem_grupo:          int = Field(description="Produtos sem grupo compatível")
    sem_template:       int = Field(description="Produtos cujo nicho não tem template")
    detalhes:           list[str] = Field(default_factory=list)

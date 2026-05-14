"""Padrões reutilizáveis: paginação, respostas genéricas."""
from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class Pagina(BaseModel, Generic[T]):
    """Resposta paginada genérica.

    Uso:
        return Pagina[ProdutoPublico](items=..., total=..., pagina=..., por_pagina=...)
    """
    items:      list[T]
    total:      int
    pagina:     int = Field(ge=1)
    por_pagina: int = Field(ge=1, le=200)

    @property
    def total_paginas(self) -> int:
        if self.por_pagina == 0:
            return 0
        return (self.total + self.por_pagina - 1) // self.por_pagina


class Mensagem(BaseModel):
    """Resposta simples com mensagem (pra DELETE, ações sem retorno)."""
    mensagem: str

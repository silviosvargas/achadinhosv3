"""
Importa TODOS os models aqui. Alembic precisa que estejam em `Base.metadata`
quando ele inspeciona o schema.

Regra: ao criar model novo, adicione na lista abaixo.
"""
from app.db.base import Base
from app.models.agente import Agente, Canal
from app.models.busca import BuscaML, NichoCategoriaML
from app.models.grupo import Grupo, GrupoNicho, Postagem
from app.models.organizacao import Organizacao, Plano
from app.models.produto import (
    Nicho,
    Plataforma,
    Produto,
    ProdutoNicho,
    TemplateMensagem,
    UsuarioProdutoPersonalizado,
)
from app.models.redirect import Redirect
from app.models.tarefa import StatusTarefa, Tarefa, TipoTarefa
from app.models.usuario import Usuario, UsuarioAfiliado

__all__ = [
    "Base",
    # Tenancy
    "Organizacao",
    "Plano",
    "Usuario",
    "UsuarioAfiliado",
    # Encurtador
    "Redirect",
    # Agentes / canais
    "Agente",
    "Canal",
    # Catálogo
    "Produto",
    "ProdutoNicho",
    "Plataforma",
    "Nicho",
    "TemplateMensagem",
    "UsuarioProdutoPersonalizado",
    # Buscas
    "BuscaML",
    "NichoCategoriaML",
    # Grupos / postagens
    "Grupo",
    "GrupoNicho",
    "Postagem",
    # Tarefas
    "Tarefa",
    "StatusTarefa",
    "TipoTarefa",
]

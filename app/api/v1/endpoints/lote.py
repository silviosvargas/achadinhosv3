"""
Endpoint pra rodar lote de postagem manualmente.

POST /lote/rodar    dispara seleção + enfileiramento de tarefas
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import usuario_atual
from app.db import get_db_async
from app.models import Usuario
from app.schemas.produto import RodarLoteRequest, ResultadoLote
from app.services import lote_service

router = APIRouter(prefix="/lote", tags=["lote"])


@router.post("/rodar", response_model=ResultadoLote)
async def rodar(
    body: RodarLoteRequest,
    user: Usuario = Depends(usuario_atual),
    db: AsyncSession = Depends(get_db_async),
) -> ResultadoLote:
    """Seleciona produtos elegíveis e enfileira postagens."""
    resultado = await lote_service.rodar_lote(
        db,
        org_id=user.org_id,
        max_produtos=body.max_produtos,
        canal_tipo=body.canal_tipo,
        criado_por_usuario_id=user.id,
        usuario=user,
    )
    return ResultadoLote(
        produtos_avaliados=resultado["produtos_avaliados"],
        tarefas_criadas=resultado["tarefas_criadas"],
        sem_grupo=resultado["sem_grupo"],
        sem_template=resultado["sem_template"],
        detalhes=resultado["detalhes"],
    )

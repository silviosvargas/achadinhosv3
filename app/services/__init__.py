"""Camada de regras de negócio."""
from app.services import agente_service, dispatcher
from app.services.agente_registry import registry

__all__ = ["agente_service", "dispatcher", "registry"]

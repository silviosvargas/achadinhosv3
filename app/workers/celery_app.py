"""
Celery app. Workers consomem tarefas de plataformas-cloud (Telegram, busca HTTP).

Tasks registradas:
- ping (teste)
- validar_canal_telegram
- postar_telegram
- agendar_buscas_devidas (rodada por Celery beat a cada minuto)
"""
from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

celery_app = Celery(
    "achadinhos",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="America/Sao_Paulo",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=600,         # 10 min hard limit
    task_soft_time_limit=540,    # 9 min soft limit (chance de cleanup)
    worker_max_tasks_per_child=100,  # recicla worker pra evitar leaks
    # ── Beat: agendador de tasks recorrentes ────────
    beat_schedule={
        "agendar-buscas-devidas-a-cada-minuto": {
            "task":     "agendar_buscas_devidas",
            "schedule": crontab(minute="*"),  # todo minuto
        },
    },
)


@celery_app.task(name="ping")
def ping() -> str:
    """Task de teste — confirma que worker tá vivo."""
    return "pong"


# ── Importa tasks pra que sejam registradas no celery_app ─────────
# Tem que vir DEPOIS da criação do celery_app pra evitar ciclo
# (telegram_tasks importa celery_app deste módulo).
from app.workers import scheduler_tasks, telegram_tasks  # noqa: E402, F401

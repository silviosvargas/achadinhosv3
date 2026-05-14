"""
Ambiente do Alembic. Lê config da app (não do alembic.ini) e
descobre os models automaticamente.
"""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Importa config + TODOS os models (importante: pra autogenerate detectar)
from app.core.config import settings
from app.models import Base  # noqa: F401 — re-exportado pra side-effect

# Config do Alembic (do alembic.ini)
config = context.config

# Configura logging do alembic.ini se houver
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Sobrescreve URL com a do .env
config.set_main_option("sqlalchemy.url", settings.postgres_url)

# Metadata-alvo pra autogenerate
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """
    Roda em modo 'offline' — gera SQL sem conectar.
    Usado pra revisar SQL antes de aplicar em produção.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,           # detecta mudança de tipo de coluna
        compare_server_default=True,  # detecta mudança de default
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Roda conectado no banco (uso normal)."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

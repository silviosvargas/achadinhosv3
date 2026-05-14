# syntax=docker/dockerfile:1.7
# ============================================================
# Imagem base do backend Achadinhos V3
# Usada por api, worker e beat — todos compartilham o mesmo código.
#
# Construir só com deps de produção:
#   docker build -t achadinhos-api -f docker/Dockerfile .
#
# Construir com extras de dev (pytest, ruff):
#   docker build -t achadinhos-api -f docker/Dockerfile \
#       --build-arg INSTALL_DEV=1 .
# ============================================================
FROM python:3.11-slim AS base

ARG INSTALL_DEV=0
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Dependências de sistema:
# - libpq-dev: pra psycopg2 compilar
# - build-essential: wheels nativos
# - curl: healthcheck simples
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq-dev \
        build-essential \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Instala deps em camada separada (cacheável)
COPY pyproject.toml ./
RUN pip install --upgrade pip && \
    if [ "$INSTALL_DEV" = "1" ]; then \
        pip install -e ".[dev]"; \
    else \
        pip install -e .; \
    fi

# Copia código depois (invalida cache só quando muda)
COPY . .

# Usuário não-root
RUN useradd --create-home --shell /bin/bash appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fs http://localhost:8000/api/v1/health || exit 1

# Default: produção (sem --reload). docker-compose dev sobrescreve com --reload.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

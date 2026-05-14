"""
Configuração centralizada do Achadinhos V3.

Lê variáveis do .env e expõe um objeto `settings` tipado.
Em qualquer lugar da app, importe assim:

    from app.core.config import settings
    print(settings.postgres_url)
"""
from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuração da aplicação.

    Pydantic valida tipos e converte variáveis do .env automaticamente.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",          # ignora variáveis desconhecidas no .env
    )

    # ── App ──────────────────────────────────────────
    app_name:      str  = "Achadinhos"
    app_env:       str  = "development"
    app_debug:     bool = True
    app_log_level: str  = "INFO"
    host_port:     int  = 8000

    # URL pública do app (sem path final). Usada pra montar URLs absolutas
    # entregues ao agente (api_url, ws_url do auto-registro) e em emails
    # futuros. Em dev: http://localhost:8000; em prod: https://achadinhos.<dominio>.
    public_base_url: str = "http://localhost:8000"

    # ── JWT ──────────────────────────────────────────
    jwt_secret:                       str = Field(min_length=32)
    jwt_algorithm:                    str = "HS256"
    jwt_access_token_expire_minutes:  int = 60
    jwt_refresh_token_expire_days:    int = 30

    # ── Cifragem de credenciais (Fase 4b.1) ──────────
    # Chave-mestre pra cifrar senhas de plataformas (ML, Shopee, etc)
    # armazenadas em `usuarios.senha_<plat>_cifrada`. Trocar a chave
    # invalida TODAS as senhas cifradas — re-cadastrar.
    credenciais_secret_key:           str = Field(min_length=32)

    # ── Postgres ─────────────────────────────────────
    # Em prod (Railway/Heroku/etc) usa DATABASE_URL diretamente.
    # Em dev local sem DATABASE_URL, monta a partir das variáveis discretas.
    database_url:      str = ""   # override total — se setado, ignora as 5 abaixo
    postgres_user:     str = "achadinhos"
    postgres_password: str = "achadinhos_dev_pwd"
    postgres_db:       str = "achadinhos"
    postgres_host:     str = "postgres"
    postgres_port:     int = 5432

    # ── Redis ────────────────────────────────────────
    # Em prod (Railway) usa REDIS_URL diretamente.
    redis_url_override: str = ""  # override total — vem de REDIS_URL no env
    redis_host: str = "redis"
    redis_port: int = 6379
    redis_db:   int = 0

    # ── Admin inicial ────────────────────────────────
    admin_login:    str = "admin"
    admin_password: str = "admin"
    admin_email:    str = "admin@local"
    admin_org_nome: str = "Achadinhos"

    # ── Afiliados (defaults globais) ─────────────────
    ml_affiliate_id:        str = ""
    shopee_affiliate_id:    str = ""
    amazon_affiliate_tag:   str = ""
    magalu_affiliate_id:    str = ""
    aliexpress_affiliate_id: str = ""

    # ── Telegram ─────────────────────────────────────
    telegram_bot_token_global: str = ""

    # ── Anthropic ────────────────────────────────────
    anthropic_api_key: str = ""

    # ── Limites globais ──────────────────────────────
    max_agentes_por_org:        int = 20
    max_postagens_dia_default:  int = 500

    # ── Computed: URLs prontas ───────────────────────
    @property
    def postgres_url(self) -> str:
        """URL síncrona pro SQLAlchemy / Alembic.

        Aceita formato `postgresql://...` (Railway/Heroku) e troca pra
        `postgresql+psycopg2://...` que o SQLAlchemy 2.x exige no sync.
        """
        if self.database_url:
            return _normalizar_pg(self.database_url, driver="psycopg2")
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def postgres_url_async(self) -> str:
        """URL async pro SQLAlchemy async (asyncpg)."""
        if self.database_url:
            return _normalizar_pg(self.database_url, driver="asyncpg")
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def redis_url(self) -> str:
        """URL do Redis pro Celery e cache."""
        if self.redis_url_override:
            return self.redis_url_override
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def public_ws_url(self) -> str:
        """
        URL do WebSocket do agente, derivada de public_base_url:
        http://...  → ws://.../api/v1/ws/agente
        https://... → wss://.../api/v1/ws/agente
        """
        base = self.public_base_url.rstrip("/")
        if base.startswith("https://"):
            return "wss://" + base[len("https://"):] + "/api/v1/ws/agente"
        if base.startswith("http://"):
            return "ws://" + base[len("http://"):] + "/api/v1/ws/agente"
        return base + "/api/v1/ws/agente"


def _normalizar_pg(url: str, *, driver: str) -> str:
    """
    Normaliza URL Postgres pra incluir driver SQLAlchemy.

    Railway/Heroku expõem `postgres://...` ou `postgresql://...`.
    SQLAlchemy 2.x exige `postgresql+psycopg2://...` (sync) ou
    `postgresql+asyncpg://...` (async).
    """
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = f"postgresql+{driver}://" + url[len("postgresql://"):]
    return url


@lru_cache
def get_settings() -> Settings:
    """Singleton — lê o .env uma vez por processo."""
    return Settings()  # type: ignore[call-arg]


# Atalho pra importação direta
settings = get_settings()

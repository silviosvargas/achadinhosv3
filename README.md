# Achadinhos V3 — Cloud-Ready SaaS

Sistema de automação de marketing de afiliados, evoluído da V2 pra arquitetura cloud + agente local.

> **Status:** em construção. Esta é a fundação (Fase 1).
> A V2 continua funcionando enquanto a V3 amadurece.

---

## 🎯 O que muda da V2 pra V3

| Aspecto | V2 | V3 |
|---|---|---|
| Onde roda | 1 PC Windows | Servidor cloud + agentes locais |
| Backend | Flask + SQLite | FastAPI + Postgres + Redis |
| Auth | Sessão Flask (cookies) | JWT (stateless, mobile-ready) |
| Multi-tenancy | Multi-usuário no mesmo banco | Multi-org com isolamento |
| Postagem WhatsApp | Acoplado ao servidor | Agente local separado (`.exe`) |
| Postagem Telegram | ❌ | ✅ Bot API direto da nuvem |
| Filas/Jobs | Threads Python | Celery + Redis |
| Deploy | `.bat` no Windows | Docker Compose |

---

## 🏗️ Arquitetura

```
       ┌──────────────────────────────────────┐
       │        SITE/API (FastAPI)            │
       │  Dashboard, JWT, WebSocket, Telegram │
       └──────────────┬───────────────────────┘
                      │
        ┌─────────────┼─────────────┐
        ▼             ▼             ▼
   ┌─────────┐  ┌──────────┐  ┌─────────┐
   │Postgres │  │  Redis   │  │ Workers │
   │ (dados) │  │(filas+ws)│  │(Celery) │
   └─────────┘  └──────────┘  └─────────┘
                      ▲
                      │ WebSocket TLS
                      │
        ┌─────────────┼─────────────┐
        ▼             ▼             ▼
   [Agente A]   [Agente B]   [Agente C]
   PC do        PC do        PC do
   afiliado     afiliado     afiliado
   (Chrome +    (Chrome +    (Chrome +
   WhatsApp)    WhatsApp)    WhatsApp)
```

---

## 📁 Estrutura

```
ACHADINHOSV3/
├── app/                      # Backend FastAPI
│   ├── api/v1/endpoints/    # Rotas REST + WebSocket
│   ├── core/                # Config, segurança, JWT
│   ├── db/                  # Conexão, sessão, base
│   ├── models/              # SQLAlchemy models
│   ├── schemas/             # Pydantic schemas
│   ├── services/            # Regras de negócio
│   └── workers/             # Tasks Celery
├── alembic/                  # Migrações de schema
├── docker/                   # Dockerfiles
├── tests/                    # Testes pytest
├── scripts/                  # CLIs auxiliares
├── docs/                     # Decisões e diagramas
├── docker-compose.yml        # Orquestração local
├── .env.example              # Configuração modelo
└── pyproject.toml            # Dependências
```

---

## 🚀 Como rodar local (desenvolvimento)

### Pré-requisitos
- **Docker Desktop** (Windows/Mac/Linux) — https://www.docker.com/products/docker-desktop
- **Python 3.11+** (só pra rodar scripts auxiliares fora do container)

### Setup
```bash
# 1. Clone/copie a pasta
cd ACHADINHOSV3

# 2. Configura variáveis
cp .env.example .env
# Edite .env conforme precisar (segredos JWT, etc)

# 3. Sobe os serviços
docker compose up -d

# 4. Roda migrações (na primeira vez)
docker compose exec api alembic upgrade head

# 5. Cria usuário admin inicial
docker compose exec api python -m scripts.criar_admin

# 6. Acessa
# Dashboard: http://localhost:8000
# Docs API:  http://localhost:8000/docs
```

### Parar
```bash
docker compose down
# (Mantém volumes — dados não são perdidos)

docker compose down -v
# (Apaga volumes — reset completo)
```

---

## 🗺️ Roadmap

- 🟡 **Fase 1 — Fundação cloud** *(em andamento)*
  - Schema Postgres multi-tenant
  - FastAPI + JWT
  - Docker Compose
- ⏳ **Fase 2 — Agente local**
  - WebSocket protocol
  - PyInstaller `.exe`
  - Recorta postagem WhatsApp da V2
- ⏳ **Fase 3 — Canais cloud-native**
  - Telegram Bot API
  - Buscas HTTP migradas pro worker
- ⏳ **Fase 4 — SaaS readiness**
  - Signup público
  - Planos + limites
  - Billing

---

## 🔗 Documentação interna

- [docs/decisoes.md](docs/decisoes.md) — log de decisões arquiteturais (ADR)
- [docs/protocolo_agente.md](docs/protocolo_agente.md) — contrato WebSocket cloud↔agente
- [docs/migrar_v2.md](docs/migrar_v2.md) — guia de migração de dados V2 → V3

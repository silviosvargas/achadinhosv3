# CLAUDE.md — Achadinhos V3

## Visão do produto (norte de TODAS as decisões)

**Achadinhos é um SaaS web** onde admin / usuário / afiliado:
1. Cria conta (signup público, multi-tenant)
2. **Instala um agente leve no PC** (`.exe` no Windows; futuramente Mac/Linux) —
   esse agente é a única coisa local que ele precisa
3. Gerencia tudo (buscas, produtos, templates, grupos, canais, lotes, equipe)
   pelo **dashboard web** — desktop OU mobile
4. O agente roda em background no PC dele: busca produtos no ML/Shopee/Amazon
   via Selenium e posta no WhatsApp Web. **Telegram é cloud** (Bot API, sem PC).

**Decisões consolidadas (não mudar sem motivo forte):**

| Tópico | Decisão |
|--------|---------|
| Frontend mobile | **Jinja2 + CSS responsivo + PWA** (instalável como app via Chrome/Safari). SPA fica pra quando PWA não bastar. |
| Mobile escopo | **Só dashboard.** Postagem sempre via PC com agente. Celular não roda Selenium + WhatsApp Web de forma estável. |
| Agente | Sempre desktop (Windows prioritário, depois Mac/Linux). Distribuído como `.exe` único, login com email+senha (não copiar token), auto-update. |
| API | REST JSON-only em `/api/v1/*` desde dia 1 (ADR-007). UI Jinja2 consome a mesma API conceitualmente. Pronto pra SPA/mobile no futuro. |
| Auth | JWT stateless (ADR-006). Mesmo token serve dashboard web, agente local e app mobile futuro. |
| Multi-tenant | `org_id` discriminator (ADR-003). Cada cliente isolado. |
| Plataformas de postagem | WhatsApp → agente (Selenium); Telegram → cloud (Bot API). |
| Plataformas de busca | ML/Shopee/Amazon/etc → agente (Selenium anti-detect). |

**Sequência de fases revisada (até "site funcional pra cliente externo"):**

1. ~~Fase 1-4b: fundação + agente + buscas ML~~ ✅
2. **Fase 4c — polir e validar lote real** (você usar de verdade por dias antes de abrir signup)
3. **Fase 5 — signup público + planos + onboarding guiado** (porta de entrada SaaS)
4. **Fase 6 — instalador `.exe` do agente** com login email/senha + auto-update
5. **Fase 7 — CSS responsivo + PWA** (mobile-friendly sem reescrever frontend)
6. **Fase 8 — Shopee/Amazon** (estender padrão do ML)
7. **Fase 9 (se necessário)** — SPA React + Capacitor pra app nativo

---

## O que é

SaaS multi-tenant de automação de marketing de afiliados. Importa produtos de
Mercado Livre / Shopee / Amazon e posta automaticamente em grupos do WhatsApp
e Telegram dos afiliados, usando templates por nicho. Evolução cloud-ready
da V2 (Flask + SQLite single-PC) pra arquitetura servidor + agentes locais.
Código e nomes em **português**.

## Arquitetura

**Stack:** FastAPI + SQLAlchemy 2.0 async + Pydantic v2 + Postgres 16 +
Redis 7 + Celery 5 + JWT (bcrypt direto) + Jinja2 (server-rendered).
Cifragem reversível: Fernet (cryptography). Lint: ruff. Logs: structlog.

**Containers** (`docker compose ps` → todos healthy):

| Service  | Porta | Função                                  |
|----------|-------|-----------------------------------------|
| api      | 8000  | FastAPI + uvicorn `--reload`            |
| postgres | 5432  | Banco                                   |
| redis    | 6379  | Broker Celery + pub/sub WebSocket       |
| worker   | —     | Celery worker (Telegram, jobs)          |
| beat     | —     | Celery beat (agendador `agendar_buscas_devidas`) |
| flower   | 5555  | UI de monitoramento Celery              |

Hot-reload: `./app`, `./alembic`, `./scripts` montados nos containers.

**Projeto do agente (separado):** `D:\achadinhos-agent\` — Python + Selenium +
undetected-chromedriver. Conecta no servidor via WebSocket. Hoje roda via
`python -m agent.main`; vira `.exe` na Fase 6.

## Fases entregues

- **3.0.0 — Fase 1:** fundação cloud (FastAPI, JWT, Postgres, Docker Compose, schema multi-tenant).
- **3.1.0 — Fase 2:** agente local — WebSocket `/api/v1/ws/agente`, registry em memória, protocolo cloud↔agente.
- **3.2.0 — Fase 3a:** canal Telegram via Bot API + Celery (`postar_telegram` task), tabela `postagens`.
- **3.3.0 — Fase 3b:** dispatcher unificado roteando WhatsApp→WS e Telegram→Celery; callbacks com retry.
- **3.4.0 — Fase 4a:** catálogo por org, templates com placeholders, `selecao_service` (matching nicho×nicho + dedup 7d), `lote_service`, botão "rodar lote".
- **3.4.1 — Fase 4a hotfix:** seleção/template fallback.
- **3.5.0 — Fase 4b:** buscas Mercado Livre via agente (Selenium + undetected-chromedriver), ingest REST, mapping `categoria_ml → nicho_id`, Celery beat pra agendar, UI completa.
- **3.5.1 — Sub-fase 4b.1:** infra de credenciais cifradas (Fernet) — `usuario_ml` + `senha_ml_cifrada` em `usuarios`, endpoint `GET /agentes/me/credenciais`, UI em `/usuarios/{id}/credenciais`. Pronto pra uso futuro; agente atual usa login manual (`python -m agent.login_ml`) porque ML tem 2FA real.

## Estado atual do banco

- 1 organização (`org_id=1`, slug `achadinhos`)
- 1 admin (`login=admin`, senha=`admin`, troca no primeiro acesso recomendado)
- 1 agente cadastrado (`id=1` "Dev Local")
- 10 produtos com categoria + nicho `tecnologia` (já elegíveis pra lote)
- 2 mappings categoria→nicho (`Eletrônicos > Áudio` e `Celulares > Fones` → tecnologia)
- 2 buscas ML cadastradas (id=1 e id=2, ambas "fone bluetooth")
- 0 templates, 0 canais, 0 grupos — **pendência da Fase 4c pra rodar lote real**

## Decisões arquiteturais

- **Multi-tenant via discriminator `org_id`** (ADR-003): catálogo isolado por org.
- **WhatsApp → agente local** (ADR-004): Selenium em cloud é detectado/banido.
- **Telegram → Celery cloud**: Bot API oficial, roda 24h sem PC ligado.
- **Tarefas em Postgres + notificação Redis** (ADR-005): fonte da verdade é a tabela `tarefas`.
- **JWT stateless** (ADR-006): mesmo mecanismo serve dashboard, agente e mobile futuro.
- **Jinja2 + API REST** (ADR-007): UI server-rendered hoje, API JSON desde dia 1 pra abrir SPA/mobile no futuro.
- **Produtos privados de afiliado** (ADR-008): `usuario_dono_id` em `produtos` + partial unique indexes. Afiliado vê só públicos da org + os seus.

## Como rodar

```powershell
docker compose up -d              # sobe tudo
docker compose ps                 # confirma healthy
docker compose logs -f api        # logs
docker compose down               # para (mantém dados)
docker compose down -v            # reset total
```

**Agente local (sessão de dev):**
```powershell
cd D:\achadinhos-agent
.venv\Scripts\activate
python -m agent.login_ml          # 1× ou quando sessão ML expirar
python -m agent.main --sem-tray   # conecta no WS, processa buscas/postagens
```

URLs: dashboard http://localhost:8000 · docs http://localhost:8000/docs · flower http://localhost:5555

## Migrations

**Automáticas no boot.** `docker-compose.yml` roda `alembic upgrade head` antes do uvicorn.

Criar nova: `docker compose exec api alembic revision --autogenerate -m "msg"`

## Próxima fase

**Fase 4c — polir Fase 4b + validar lote real ponta-a-ponta:**
1. Criar template no nicho `tecnologia` (10 produtos esperando)
2. Criar canal (Telegram Bot recomendado pra primeiro teste — sem precisar de agente WhatsApp)
3. Criar grupo nesse canal, atrelar ao nicho `tecnologia`
4. Botão "Rodar lote" → tarefa criada → postagem real saindo
5. Polimentos UI: edit/delete de busca, página de detalhe de tarefa, etc.

Depois disso → Fase 5 (signup público).

# CLAUDE.md — Achadinhos V3

## Visão do produto (norte de TODAS as decisões)

**SaaS web** onde admin / usuário / afiliado:
1. Cria conta (signup público multi-tenant)
2. **Instala um agente leve no PC** (`.exe` Windows; futuramente Mac/Linux) —
   única coisa local que ele precisa
3. Gerencia tudo (buscas, produtos, templates, grupos, canais, lotes, equipe)
   pelo **dashboard web** — desktop OU mobile
4. O agente roda em background no PC: busca produtos no ML/Shopee/Amazon via
   Selenium e posta no WhatsApp Web. **Telegram é cloud** (Bot API, sem PC).

| Tópico | Decisão |
|--------|---------|
| Frontend mobile | Jinja2 + CSS responsivo + PWA (instalável). SPA quando PWA não bastar. |
| Mobile escopo | **Só dashboard.** Postagem sempre via PC com agente. |
| Agente | Sempre desktop. `.exe` único, login email+senha (não copiar token), auto-update. |
| API | REST JSON em `/api/v1/*` desde dia 1 (ADR-007). |
| Auth | JWT stateless (ADR-006). Mesmo token serve dashboard, agente, mobile. |
| Multi-tenant | `org_id` discriminator (ADR-003). |

---

## 🚀 PRODUÇÃO

| Item | Valor |
|------|-------|
| URL pública | **https://achadinhos.maisseguidores.ia.br** |
| URL temporária Railway | https://achadinhosv3-production.up.railway.app |
| Repo GitHub | https://github.com/silviosvargas/achadinhosv3 |
| Hospedagem compute | Railway (projeto `balanced-ambition` / "ambição equilibrada") |
| DNS / proxy | Cloudflare (NS: buck.ns.cloudflare.com, maria.ns.cloudflare.com) |
| Domínio raiz | maisseguidores.ia.br (Registro.br) — WordPress continua intocado em `/` |
| Subdomínio | `achadinhos.maisseguidores.ia.br` → CNAME `jv7fcipn.up.railway.app` (DNS only/cinza no CF) |
| Validação | TXT `_railway-verify.achadinhos` = `railway-verify=ddf75203a7563d3e279a5f321e3f9e50a51fc34e044db3e8848a31d1e5097947` |

**Segredos de produção** (gerenciador de senhas — NÃO commitar):
- `JWT_SECRET` (64 url-safe chars)
- `CREDENCIAIS_SECRET_KEY` (48 url-safe chars — Fernet)
- `ADMIN_PASSWORD` (admin inicial: login `admin`)

**Admin de produção:** login `admin` na org `achadinhos` (slug). Senha guardada
no gerenciador de senhas do user.

---

## Stack

FastAPI + SQLAlchemy 2.0 async + Pydantic v2 + Postgres 16 + Redis 7 +
Celery 5 + JWT (bcrypt direto) + Jinja2. Cifragem reversível: Fernet
(cryptography). Lint: ruff. Logs: structlog.

**Containers (dev local — `docker compose ps`):**

| Service  | Porta | Função |
|----------|-------|--------|
| api      | 8000  | FastAPI + uvicorn `--reload` |
| postgres | 5432  | Banco |
| redis    | 6379  | Broker Celery + pub/sub WS |
| worker   | —     | Celery worker (Telegram, jobs) |
| beat     | —     | Celery beat (`agendar_buscas_devidas`) |
| flower   | 5555  | Monitoramento Celery |

**Containers em PROD (Railway services):**
- `acadinhosv3` (api) — **ATIVO**
- `Postgres` add-on
- `Redis` add-on
- `worker` — **ATIVO** · roda Celery worker **+ beat embedded**
  (`celery worker --beat --pool=solo`), porque o plano Free do Railway não dá
  pra ter beat como service separado. Schedule de `agendar_buscas_devidas`
  (crontab a cada minuto) roda dentro do worker. Notas:
  - **`--pool=solo`** (1 processo): default Celery é prefork c/ concurrency=nproc,
    e Railway no Free reporta 48 vCPUs → estourava RAM. Solo é leve, single-thread.
  - **`railway.worker.json`** no repo + setting `railwayConfigFile=railway.worker.json`
    no service worker: sobrescreve o `railway.json` padrão (sem healthcheck, sem
    preDeploy, startCommand do celery).
  - **`REDIS_URL_OVERRIDE=${{Redis.REDIS_URL}}`** como env var: a app só lê
    `REDIS_URL_OVERRIDE` (não `REDIS_URL` direto). Sem isso o worker tentava
    `redis://redis:6379/0` (hostname dev) e crashava.
  - Trade-off: restart do worker reseta o estado do beat (perde no máximo 1
    execução).

**Projeto agente (separado):** `D:\achadinhos-agent\` — Python + Selenium +
undetected-chromedriver. Hoje roda via `python -m agent.main`; vira `.exe`
na Fase 6 (já tem `agent/setup.py` interativo, falta empacotar PyInstaller).

---

## Fases entregues

- **3.0.0–3.4.1** — Fases 1-4a: fundação + agente + buscas + lote
- **3.5.0** — Fase 4b: buscas ML (Selenium + undetected-chromedriver), ingest REST, mapping categoria→nicho, Celery beat
- **3.5.1** — Sub-fase 4b.1: credenciais cifradas (Fernet) — `usuario_ml`+`senha_ml_cifrada` em `usuarios`
- **3.6.0** — Fase 4c: postagem WhatsApp real validada end-to-end
- **3.7.0** — Fase 5: signup público (`/signup`) + planos com limites + onboarding wizard (`/onboarding`)
- **3.8.0** — Fase 6: `POST /api/v1/agentes/registrar-self` + `agent/setup.py` (CLI interativa)
- **3.9.0** — Fase 7: CSS responsivo (mobile-first, hamburguer) + PWA (manifest, service worker, ícones 192/512)
- **3.10.0** — Deploy produção: Railway api online, Cloudflare DNS, subdomínio HTTPS
- **3.10.1** — Worker no Railway (Celery worker + beat embedded num único service, criado via Railway CLI + GraphQL API)

---

## Decisões arquiteturais (ADRs em `docs/decisoes.md`)

- ADR-003: multi-tenant via `org_id` discriminator
- ADR-004: WhatsApp → agente local; Telegram → Celery cloud
- ADR-005: tarefas em Postgres + notificação Redis
- ADR-006: JWT stateless
- ADR-007: Jinja2 + API REST
- ADR-008: produtos privados de afiliado (partial unique indexes)

---

## Como rodar (dev local)

```powershell
docker compose up -d              # sobe tudo
docker compose ps                 # confirma healthy
docker compose logs -f api        # logs
docker compose down               # para
```

**Agente local:**
```powershell
cd D:\achadinhos-agent
.venv\Scripts\activate
python -m agent.setup             # 1× — pede email/senha, registra agente
python -m agent.login_ml          # 1× ou quando sessão ML expirar
python -m agent.login_whatsapp    # 1× ou quando WhatsApp Web expirar
python -m agent.main --sem-tray   # conecta no WS, processa buscas/postagens
```

URLs dev: dashboard http://localhost:8000 · docs http://localhost:8000/docs · flower http://localhost:5555

---

## Migrations

**Automáticas no boot.** `docker-compose.yml` (dev) roda `alembic upgrade head`.
Em prod, `scripts/bootstrap_producao.py` (preDeployCommand do Railway) faz
isso + cria admin se vazio.

Migrations atuais:
- 0001 inicial
- 0002 produtos por org + templates
- 0003 buscas_ml + nicho_categoria_ml + produtos.usuario_dono_id
- 0004 credenciais cifradas (usuario_ml + senha_ml_cifrada)

Criar nova: `docker compose exec api alembic revision --autogenerate -m "msg"`

---

## Próxima fase imediata

**Ver `docs/sessao_continuacao.md` pra checklist completo.**

Resumo curto:
1. ✅ Worker no Railway (combinado com beat embedded) — feito 2026-05-15
2. Validar fluxo signup público real em https://achadinhos.maisseguidores.ia.br
3. Reconfigurar agente local pra apontar pra prod (`python -m agent.setup`)
4. Eventualmente: empacotar agente como `.exe` (PyInstaller)

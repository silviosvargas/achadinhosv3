# Continuação da sessão Claude — onde paramos

> Este arquivo é a **ponte entre sessões do Claude**. Quando uma sessão acaba
> (limite de contexto), você abre uma nova e diz: *"Lê CLAUDE.md e
> docs/sessao_continuacao.md, estado deve estar lá"* — Claude pega de onde
> parou.

---

## Estado em 2026-05-15 (terceira sessão — worker + agente conectado + plano Fase 9)

### O que está NO AR

✅ **https://achadinhos.maisseguidores.ia.br** — API + dashboard de produção
- HTTPS válido, signup funciona, login do admin funciona, dashboard responsivo
- Plano: Railway **Free** ($5 grátis/mês trial — atingiu limite de services,
  por isso worker+beat foram combinados num único service)
- Postgres + Redis como add-ons no projeto Railway `balanced-ambition`
- Cloudflare na frente (proxy DESLIGADO/cinza pra Railway validar — pode reativar laranja depois)

✅ **`worker` service no Railway** — Celery worker **+ beat embedded**
- StartCommand: `celery -A app.workers.celery_app worker --beat --pool=solo --loglevel=info`
  (definido em `railway.worker.json`, ativado via setting `railwayConfigFile`)
- Variables: 26 vars (25 copiadas do `acadinhosv3` + `REDIS_URL_OVERRIDE=${{Redis.REDIS_URL}}`)
- Sem healthcheck (worker não tem HTTP), sem preDeploy (api faz migrations)
- Por que combinado: Free plan limita services. Beat embedded é OK — restart do
  worker reseta schedule, mas crontab "todo minuto" se recupera em ≤60s.
- Por que `--pool=solo`: Free plan tem pouca RAM; prefork c/ 48 workers (default)
  estourava memória e matava o worker em loop.
- Por que `REDIS_URL_OVERRIDE` em vez de `REDIS_URL`: a app só lê env var
  `REDIS_URL_OVERRIDE` (`app/core/config.py:64`); `REDIS_URL` "puro" fica ignorado
  e a app cai no fallback `redis://redis:6379/0` (hostname dev) que não existe em
  prod. **TODO menor**: api tem o mesmo bug mas ainda não manifesta porque só usa
  Redis em pub/sub de WebSocket (lazy, sem agente conectado ainda).
- Criado via Railway CLI (`railway add --service worker`) + GraphQL API
  (variableCollectionUpsert + serviceConnect + serviceInstanceUpdate com
  `railwayConfigFile`)

✅ **Agente local conectado em prod via WSS**
- PC do dev (`HP_SILVIO`) tem `agent/main` apontando pra
  `wss://achadinhos.maisseguidores.ia.br/api/v1/ws/agente`.
- Config em `%APPDATA%\Achadinhos\config.json` (já reapontado pra prod).
- Org_id=1 (Achadinhos), user `SILVIOVARGAS`, agente_id=1.
- Servidor confirma `agente.conectado total_online=1` nos logs.

✅ **Signup público + wizard onboarding** validados em prod
- Smoke test parcial executado em 2026-05-15: criada conta `Teste Prod` em
  janela anônima, /onboarding renderizou os 4 cards (config ML, baixar
  agente, cadastrar canal, criar grupo). Conta de teste deletada do DB no
  fim (cascade limpo todas as FKs).

### O que NÃO está no ar / pendente

⚠️ **Telegram não testado end-to-end** — falta criar bot via @BotFather e
testar fluxo template → canal → grupo → lote → postagem. Pulado por escolha
do user na sessão de 2026-05-15.

⚠️ **Cloudflare proxy** está em DNS only (cinza). Pode ligar laranja pra ter
cache + DDoS. SSL mode deve ser **Full** (não Strict, não Flexible) quando ligar.

⚠️ **Fluxo de instalação do agente pro user final ainda é CLI** (Python + venv
+ 3 comandos). Inviável pra user comum. Resolvido pela Fase 9 (ver roadmap).

---

## Checklist pra próxima sessão (ordem sugerida)

### 1️⃣ Começar Fase 9.1 — build PyInstaller (Recommended)

**ADR-009 detalha** a arquitetura completa da Fase 9 (botão "Conectar meu
WhatsApp" no dashboard, agente como `.exe` instalável, ponte browser↔agente).
Próximo concreto:

1. Validar `D:\achadinhos-agent\build.spec` atual — possivelmente desatualizado.
2. Rodar `pyinstaller build.spec` no venv do agente, ver se gera `.exe` funcional.
3. Smoke test do `.exe`: rodar fora do venv, verificar que conecta no
   WebSocket de prod igual ao `python -m agent.main --sem-tray`.
4. Se faltar alguma dep (Selenium driver, chromedriver, etc.), ajustar `.spec`.
5. Commit do `.spec` ajustado + nota em `docs/decisoes.md` sobre artefatos
   gerados (tamanho, deps).

Saída esperada: 1 `.exe` standalone que vira a base das próximas mini-fases.

### 2️⃣ Telegram smoke test (paralelo, quando tiver bot)

Quando tiver bot @BotFather + grupo de teste:
- Cria canal Telegram no dashboard (com token do bot)
- Cria grupo apontando pro canal
- Cria template simples
- Cria produto manual via /produtos/novo
- Roda lote → confere postagem no grupo

Valida que worker Celery processa `postar_telegram` em prod.

### 3️⃣ Cloudflare proxy ON (independente)

Quando tudo estiver estável:
1. Cloudflare → DNS → CNAME `achadinhos`
2. Clica no ícone cinza pra virar laranja (Proxied)
3. SSL/TLS → Overview → modo **Full** (não Strict, não Flexible)
4. Testa de novo. Se quebrar (502), volta pra cinza.

---

## Roadmap futuro

### Fase 9 — Botão "Conectar meu WhatsApp" (ADR-009)

Quebra do roadmap original "Build `.exe` (1 sessão)" no plano completo:

| Sub-fase | Descrição | Tempo |
|----------|-----------|-------|
| **9.1** | Build PyInstaller funcionando (`.exe` standalone) | 1 sessão |
| **9.2** | HTTP local server no agente (127.0.0.1:5577 — `/ping`, `/pair`, `/abrir-tudo`, `/status`) | 1 sessão |
| **9.3** | Pareamento via JWT (substituí setup CLI pelo endpoint `/pair`) | 1 sessão |
| **9.4** | Botão "Conectar" no dashboard (UX combo HTTP→protocol→download) | 1 sessão |
| **9.5** | Inno Setup installer (registry handler + auto-start) | 1-2 sessões |
| **9.6** | URL protocol handler no agente (`achadinhos://` → HTTP local) | 0.5 sessão |
| **9.7** | (opcional) Auto-update do `.exe` via GitHub releases | 1 sessão |
| **9.8** | Status do agente no dashboard + UX de offline (crítico pro cenário "controle remoto via celular") | 0.5 sessão |

### Outras fases

| Fase | Descrição | Tempo estimado |
|------|-----------|---------------|
| **8** | Shopee + Amazon (estender padrão do ML) | 2 sessões |
| **10** | Email transacional (welcome, recuperar senha — SMTP) | 1 sessão |
| **11** | Página de upgrade de plano (free→pro→business, sem billing real) | 1 sessão |
| **12** | Métricas/analytics no dashboard (postagens/dia, top produtos) | 1 sessão |
| **13** | Tests pytest pra Fases 4b/5/6 | 1-2 sessões |

---

## Como abrir nova sessão e retomar

1. Abre nova conversa Claude Code no diretório `D:\ACHADINHOSV3`
2. Primeira mensagem ao Claude:

   > *"Estou continuando o Achadinhos V3. Lê CLAUDE.md, docs/sessao_continuacao.md
   > e docs/decisoes.md (ADR-009 sobre Fase 9). O estado completo está lá.
   > Próximo passo é começar a Fase 9.1 — validar/ajustar build PyInstaller
   > do agente."*

3. Claude vai ler os 2 arquivos e pegar o contexto completo. Sem precisar
   re-explicar arquitetura, decisões ou estado.

---

## Arquivos importantes pra Claude ler em ordem

1. `CLAUDE.md` — visão geral, fases, URLs prod
2. `docs/sessao_continuacao.md` — este arquivo (checklist próxima sessão)
3. `docs/deploy_railway.md` — guia detalhado de deploy
4. `docs/decisoes.md` — ADRs (decisões arquiteturais)
5. `docs/protocolo_agente.md` — contrato WS cloud↔agente

---

## Comandos úteis pra dev local

```powershell
# Subir
docker compose up -d
docker compose ps              # confirma healthy

# Logs
docker compose logs -f api
docker compose logs -f worker

# Migrations
docker compose exec api alembic upgrade head

# Criar admin
docker compose exec api python -m scripts.criar_admin

# Reset total (apaga volumes!)
docker compose down -v
```

## Comandos úteis pra git/deploy

```powershell
# Status
git status
git log --oneline -10

# Push (auto-deploy Railway)
git add .
git commit -m "msg"
git push

# Ver último deploy
# https://railway.app → projeto → service → Deployments
```

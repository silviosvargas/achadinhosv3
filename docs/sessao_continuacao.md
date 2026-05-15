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

✅ **Fase 9.1 — build PyInstaller validado** (2026-05-15)
- `agente/build.spec` rodou sem ajustes — todas as deps detectadas (selenium,
  undetected-chromedriver, pystray, pyautogui, websockets, structlog).
- Artefato: `agente/dist/AchadinhosAgent.exe` ~ **30 MB**.
- Smoke test do `.exe`: leu config salva, conectou no WSS de prod em **1.2s**
  (`agent.iniciando` → `ws.conectando` → `ws.conectado`). Servidor confirmou
  via logs (`agente.conectado total_online=1`). Comportamento idêntico ao
  `python -m agent.main`.
- Comando de build: `cd agente && pyinstaller --noconfirm --clean build.spec`.
- `dist/` e `build/` ignorados via `agente/.gitignore`.

✅ **Fase 9.2 — HTTP local server no agente** (2026-05-15)
- `agente/agent/local_server.py` — aiohttp em `127.0.0.1:5577` (fallback 5578, 5579).
- Roda em paralelo ao WS via `asyncio.gather` no `main.py`.
- Endpoints `/ping`, `/status` ativos. CORS pra prod + localhost.
- Deps: `aiohttp>=3.10`, `undetected-chromedriver>=3.5` (esta faltava no pyproject).
- TODO menor: hookar WSClient ↔ LocalServer pra `ws_conectado` refletir real.

✅ **Fase 9.3 — Pareamento via JWT (zero-CLI)** (2026-05-15)
- `POST /pair` agora **real**: recebe `{jwt, servidor_api}`, chama
  `POST /api/v1/agentes/registrar-self` no servidor, salva token + ws_url
  em `%APPDATA%\Achadinhos\config.json`, retorna `{ok, agente_id, agente_nome, servidor_ws}`.
- **`main.py` refatorado pra rodar SEM token**:
  - `montar_config()` retorna `None` quando não tem cfg (antes era `sys.exit(1)`).
  - `main_async()` aceita `cfg: Config | None`. Se `None`, sobe só `LocalServer` e aguarda
    evento `cfg_disponivel` setado pelo callback `on_paired` quando `/pair` chega.
  - Após /pair, cria WSClient com cfg novo e roda normal.
- **Re-pareamento durante runtime** (cfg já existia): config nova é salva,
  mas WS atual fica com token velho — log warning pede restart. Reconnect
  dinâmico fica pra fase futura (complicado).
- **Erros tratados**: 400 (body inválido), 401 (JWT rejeitado pelo server),
  502 (server offline / payload inesperado).
- **Validação end-to-end em prod**:
  1. Apagado `config.json` → agente subiu em modo `aguardando_pareamento_via_dashboard`
  2. `/ping` retornou `agente_id: null`, `/status` retornou `configurado: false`
  3. Login admin via API → JWT
  4. `POST /pair` → 200 `{agente_id: 2, agente_nome: "DESKTOP-326KJ6C"}`
  5. Agente logou `pair.ok → agent.pareado_inicial → ws.conectando → ws.conectado` (~1s total)
  6. Config restaurada + agentes de teste (id=2, 3) deletados do DB
- **Limitação encontrada (problema do servidor, anotar)**: `registrar-self` SEMPRE
  cria entrada nova em vez de UPDATE. Re-pareamento gera lixo — N entradas pro
  mesmo `(usuario_id, nome_PC)`. Fix futuro: índice único partial em agentes
  por `(org_id, usuario_id, nome)` OU endpoint dedicado `registrar-ou-atualizar`.

✅ **Agente movido pra monorepo** (2026-05-15)
- Source ficava em `D:\achadinhos-agent\` (pasta solta, sem git).
- Agora em `agente/` no mesmo repo do servidor (`silviosvargas/achadinhosv3`).
- O `D:\achadinhos-agent\` original ainda existe (com `.venv`, `dist`, `build`)
  — pode ser deletado quando o user validar que a versão monorepo roda igual.
- Pra rodar a versão monorepo, user precisa recriar venv:
  ```powershell
  cd D:\ACHADINHOSV3\agente
  python -m venv .venv
  .venv\Scripts\activate
  pip install -e .
  ```

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

### 1️⃣ Começar Fase 9.x — ação "abrir-tudo" real (Recommended)

**Toda a infra da Fase 9 (9.1–9.6) está pronta.** O que falta é a **ação
concreta** quando o dashboard pede `POST /abrir-tudo` (ou URL protocol
`achadinhos://abrir-tudo`): hoje o handler é stub que só loga.

Implementar `LocalServer.processar_uri()` (e/ou `_handle_abrir_tudo`) pra:

1. Subprocesar Chrome no perfil persistente do agente (já existe —
   `agent/chrome.py`), abrindo:
   - **WhatsApp Web** (`https://web.whatsapp.com`) — pra QR scan ou
     reutilizar sessão existente
   - **Mercado Livre** (`https://mercadolivre.com.br`) — pra login ML
   - **Outros marketplaces** que o admin tenha afiliado configurado
     (`shopee_affiliate_id`, `amazon_affiliate_tag`, `magalu_affiliate_id`,
     `aliexpress_affiliate_id` em `app/core/config.py` ou tabela `usuarios`)
2. Ordem: WhatsApp Web primeiro, marketplaces em sequência (cada um em
   nova aba do MESMO Chrome).
3. Retornar `{"abriu": ["whatsapp_web", "mercadolivre", ...]}` no /abrir-tudo.

Detalhes:
- Reaproveitar `agent/chrome.py` que já gerencia o Chrome em modo debug
  com perfil persistente. Usar `webbrowser.open` ou Selenium pra abrir
  novas tabs no instance existente.
- Marketplaces ativos vêm de um endpoint server-side novo, tipo
  `GET /api/v1/marketplaces/ativos` (lista os com afiliado configurado),
  OU o dashboard manda a lista no body do `/abrir-tudo`.
- Atualizar template `agente_baixar.html` pra também disparar
  `POST /abrir-tudo` depois do pair bem-sucedido (ou ter botão separado
  "Abrir minhas plataformas").

### O QUE FALTA EXTERNO PRA FAZER (sem código)

- **Acionar GitHub Actions** uma vez pra validar que o pipeline funciona:
  Actions → `release-agente` → Run workflow (manual `workflow_dispatch`).
  Ou criar tag `agente-v3.0.0` pra gerar release oficial.

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
| ✅ **9.1** | Build PyInstaller funcionando (`.exe` standalone) — **feita 2026-05-15** | — |
| ✅ **9.2** | HTTP local server no agente (`/ping`, `/status` ativos) — **feita 2026-05-15** | — |
| ✅ **9.3** | Pareamento via JWT (`/pair` real + main.py roda sem token) — **feita 2026-05-15** | — |
| ✅ **9.4** | Botão "Conectar" no dashboard (UX combo HTTP→download placeholder) — **feita 2026-05-15** | — |
| ✅ **9.5** | Inno Setup installer (registry handler + auto-start) + GitHub Actions CI — **feita 2026-05-15** | — |
| ✅ **9.6** | URL protocol handler (`--uri` parse + single-instance handoff + `processar_uri()`) — **feita 2026-05-15** | — |
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
   > e docs/decisoes.md (ADR-009 sobre Fase 9). Toda infra da Fase 9 está pronta;
   > falta implementar a ação real do `/abrir-tudo` (subprocess Chrome com
   > WhatsApp Web + marketplaces). Esse é o próximo passo."*

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

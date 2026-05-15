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

### 1️⃣ Começar Fase 9.4 — Botão "Conectar" no dashboard (Recommended)

**ADR-009 detalha** a arquitetura. 9.1, 9.2, 9.3 estão feitas (agente +
HTTP local + pareamento via JWT funcionam end-to-end). Próxima é 9.4 — fechar
o loop com a UX no dashboard.

Criar o botão "Conectar meu WhatsApp" no dashboard, com JS que orquestra:

1. **Detecção** — `fetch http://127.0.0.1:5577/ping`:
   - **HTTP 200 com `agente_id != null`** → agente instalado E pareado E ativo.
     Botão mostra "✓ Agente conectado (ID N)". Próximo passo: 9.x (abrir tabs).
   - **HTTP 200 com `agente_id == null`** → agente instalado mas SEM token.
     Botão dispara fluxo de pareamento (passo 2).
   - **Fetch falha** (CORS / connection refused) → agente não tá rodando.
     Tenta URL protocol `achadinhos://abrir` (Fase 9.6 cobre o handler).
     Se URL protocol também falha (timeout ~3s), mostra "Baixar agente"
     com link pra download do `.exe`.

2. **Pareamento** — `POST http://127.0.0.1:5577/pair`:
   ```js
   fetch('http://127.0.0.1:5577/pair', {
     method: 'POST',
     headers: {'Content-Type': 'application/json'},
     credentials: 'include',  // pra mandar cookie de sessão se houver
     body: JSON.stringify({
       jwt: <jwt_da_sessao_atual>,
       servidor_api: window.location.origin,
     }),
   })
   ```
   - Pegar o JWT da sessão: depende de como o dashboard guarda hoje (cookie,
     localStorage, meta tag injetada pelo Jinja). Olhar `app/api/v1/endpoints/auth.py`
     e os templates do dashboard pra ver. Se for HttpOnly cookie, expor via
     endpoint server-side `GET /api/v1/auth/me/jwt` que devolve o JWT
     atual baseado na sessão.
   - Trata response:
     - 200 → toast "Pareado! Agente {agente_nome} pronto." + atualizar UI.
     - 400 → mensagem clara (provavelmente body inválido, bug no JS).
     - 401 → "Sua sessão expirou — faça login de novo."
     - 502 → "Servidor não respondeu. Tente em alguns segundos."

3. **Local do botão** — onde colocar?
   - **Banner persistente** no topo do dashboard até agente pareado (preferível).
   - **Card no /onboarding** wizard (card 2 hoje aponta pra /agentes/baixar).
   - **Página /agentes/baixar** já existe, então o botão pode entrar ali primeiro
     (menor escopo) e depois replicar.

4. **Endpoint do servidor pra link de download** — Fase 9.5 vai gerar o
   installer real. Por enquanto pode ser placeholder `/api/v1/agentes/instalador`
   que retorna 404 com mensagem "Em construção — vem na Fase 9.5".

Considerações técnicas:
- CORS já configurado no agente pra `https://achadinhos.maisseguidores.ia.br`
  e `http://localhost:8000` (dev). Validar que cookies/credentials viajam.
- Pra detectar URL protocol não-instalado, padrão JS é tentar `window.location.href =
  'achadinhos://abrir'` e setar um timer; se a página não perde foco em ~2s,
  assume não instalado.
- Endpoint `/api/v1/auth/me/jwt` (se precisar criar): rota autenticada que
  devolve `{jwt: <token_da_sessao>}`. Usado SÓ pelo dashboard pra pareamento.

Saída esperada: user logado clica botão → agente local (já rodando) recebe
JWT → pareia → toast de sucesso. Fluxo zero-CLI completo end-to-end.

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
   > Próximo passo é Fase 9.4 — botão 'Conectar' no dashboard."*

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

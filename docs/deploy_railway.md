# Deploy em produção — Railway + Cloudflare

Guia pra subir o Achadinhos em `https://achadinhos.seudominio.com.br`
usando Railway (compute) + Cloudflare (DNS/proxy/HTTPS).

## Pré-requisitos

- [ ] Repositório no GitHub (este projeto)
- [ ] Conta Railway (login com GitHub é o mais fácil)
- [ ] Conta Cloudflare (gratuita)
- [ ] Domínio registrado (ex: `seudominio.com.br` no Registro.br)

---

## Passo 1 — Conectar repo no Railway

1. https://railway.app → "New Project" → "Deploy from GitHub repo"
2. Autoriza Railway a ler seu GitHub
3. Escolhe o repo do Achadinhos
4. Railway detecta automaticamente o `Dockerfile` em `docker/Dockerfile` e o
   `railway.json` na raiz.

## Passo 2 — Add-ons Postgres e Redis

No projeto Railway, painel "+ New":
1. **Database → Add PostgreSQL** → cria, gera `DATABASE_URL`
2. **Database → Add Redis** → cria, gera `REDIS_URL`

Nos serviços (api/worker/beat), na aba **Variables**:
- Linkar `DATABASE_URL` do Postgres (botão "Reference" do Railway)
- Linkar `REDIS_URL` do Redis (mesma coisa)

## Passo 3 — Criar serviços worker e beat

Railway, no mesmo projeto:
1. "+ New" → "Empty Service" → conecta no mesmo repo
2. Nome: **worker**
3. Em Settings → Start Command: `celery -A app.workers.celery_app worker --loglevel=info`
4. Repete pra **beat**:
   - Start Command: `celery -A app.workers.celery_app beat --loglevel=info`

Cada serviço tem seu próprio container mas compartilha o código do repo.

## Passo 4 — Variáveis de ambiente

Pra cada um dos 3 serviços (api, worker, beat), em **Variables**:

```bash
# Obrigatórias
APP_ENV=production
APP_DEBUG=false
APP_LOG_LEVEL=INFO

# Segredos — GERAR novos pra produção (NÃO usar os do .env de dev)
# python -c "import secrets; print(secrets.token_urlsafe(64))"
JWT_SECRET=<sua-chave-jwt-de-64-chars>
# python -c "import secrets; print(secrets.token_urlsafe(48))"
CREDENCIAIS_SECRET_KEY=<sua-chave-fernet-de-48-chars>

# Database/Redis — Railway preenche automático ao linkar add-ons
DATABASE_URL=${{Postgres.DATABASE_URL}}
REDIS_URL=${{Redis.REDIS_URL}}

# URL pública (precisa do domínio configurado no Cloudflare — passo 5)
PUBLIC_BASE_URL=https://achadinhos.seudominio.com.br

# Admin inicial (criado uma vez pelo bootstrap)
ADMIN_LOGIN=admin
ADMIN_PASSWORD=<senha-forte-aqui>
ADMIN_EMAIL=seuemail@exemplo.com
ADMIN_ORG_NOME=Achadinhos
```

## Passo 5 — Domínio público (Cloudflare + Registro.br)

### 5a. Cadastrar domínio no Cloudflare

1. Cloudflare → "Add a site" → digita `seudominio.com.br`
2. Plano: **Free**
3. CF varre DNS atual e mostra registros existentes (importa do Registro.br/cPanel)
4. CF mostra **2 nameservers** (ex: `mike.ns.cloudflare.com`, `lisa.ns.cloudflare.com`)

### 5b. Mudar nameservers no Registro.br

1. https://registro.br → login → seu domínio → "Editar zona / DNS"
2. Substitui os nameservers atuais pelos do Cloudflare
3. **Propagação leva de 5 min a 24h.** CF avisa por email quando concluir.

### 5c. Apontar subdomínio pra Railway

Na Railway, no serviço **api** → Settings → **Networking** → **Custom Domain**:
- Adiciona `achadinhos.seudominio.com.br`
- Railway mostra um CNAME pra criar (ex: `xyz123.up.railway.app`)

No Cloudflare → DNS → adiciona:
- Type: `CNAME`
- Name: `achadinhos`
- Target: `xyz123.up.railway.app` (o que Railway pediu)
- Proxy status: **Proxied (laranja)** — esconde IP do Railway, DDoS protection

### 5d. SSL/TLS

Cloudflare → SSL/TLS → modo: **Full (strict)**
(Railway já tem cert HTTPS na ponta deles; CF valida e re-cifra)

## Passo 6 — Validação

1. Aguarda propagação DNS (~5-30 min após criar CNAME)
2. Abre `https://achadinhos.seudominio.com.br`
3. Tela de login deve aparecer com cadeado verde
4. Loga com admin / `<senha>` (configurada nas env vars)
5. Confere `/dashboard` carrega
6. Confere `/signup` (logout primeiro) abre e cria conta nova

### Validação do WebSocket (agente local)

No PC com agente:
```powershell
cd D:\achadinhos-agent
.venv\Scripts\activate
python -m agent.setup
# Quando pedir URL do servidor, digita: https://achadinhos.seudominio.com.br
```

O setup vai chamar `https://.../api/v1/agentes/registrar-self` e receber
`wss://.../api/v1/ws/agente` como ws_url. Rodando `python -m agent.main`
deve mostrar `ws.conectado`.

## Manutenção

- **Deploy nova versão:** `git push origin main` — Railway builda e sobe automático
- **Logs:** Railway → serviço → tab "Deploy logs" ou "Application logs"
- **Backup Postgres:** Railway → Postgres → "Backups" (faz dump automático)
- **Custo estimado:** $15-25/mês (ver dashboard de billing)

## Troubleshooting

- **WebSocket cai com 502:** Cloudflare bloqueando? CF Free não bloqueia WS por
  padrão. Verifica se `wss://` (não `ws://`) no agente.
- **CORS error:** API expõe paths `/api/v1/*` mas o front consome de outro origin?
  Se for SPA separado, adicionar middleware CORS em `app/main.py`.
- **`alembic upgrade head` falha:** ver logs do bootstrap. Variável `DATABASE_URL`
  preenchida corretamente?

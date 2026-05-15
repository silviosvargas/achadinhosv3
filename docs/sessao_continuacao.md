# Continuação da sessão Claude — onde paramos

> Este arquivo é a **ponte entre sessões do Claude**. Quando uma sessão acaba
> (limite de contexto), você abre uma nova e diz: *"Lê CLAUDE.md e
> docs/sessao_continuacao.md, estado deve estar lá"* — Claude pega de onde
> parou.

---

## Estado em 2026-05-15 (segunda sessão — worker no ar)

### O que está NO AR

✅ **https://achadinhos.maisseguidores.ia.br** — API + dashboard de produção
- HTTPS válido, signup funciona, login do admin funciona, dashboard responsivo
- Plano: Railway **Free** ($5 grátis/mês trial — atingiu limite de services,
  por isso worker+beat foram combinados num único service)
- Postgres + Redis como add-ons no projeto Railway `balanced-ambition`
- Cloudflare na frente (proxy DESLIGADO/cinza pra Railway validar — pode reativar laranja depois)

✅ **`worker` service no Railway** — Celery worker **+ beat embedded**
- StartCommand: `sh -c 'celery -A app.workers.celery_app worker --beat --loglevel=info'`
- Variables: 25 vars copiadas do service `acadinhosv3` (incluindo JWT_SECRET,
  CREDENCIAIS_SECRET_KEY, DATABASE_URL, REDIS_URL, ADMIN_*)
- Sem healthcheck (worker não tem HTTP), sem preDeploy (api faz migrations)
- Por que combinado: Free plan permite poucos services. Beat embedded é OK pra essa
  escala — restart do worker reseta schedule, mas crontab "todo minuto" se recupera
  em ≤60s.
- Criado via: Railway CLI (`railway add --service worker`) + GraphQL API
  (variableCollectionUpsert + serviceConnect + serviceInstanceUpdate)

### O que NÃO está no ar (próxima sessão)

⚠️ **Agente local** ainda apontando pra dev — precisa rodar `python -m agent.setup` de novo passando URL de produção.

⚠️ **Cloudflare proxy** está em DNS only (cinza). Pode ligar laranja pra ter cache + DDoS.

⚠️ **Beat como service separado** — só se fizer upgrade pro plano Hobby ($5/mês real).
Por enquanto fica embedded no worker.

---

## Checklist pra próxima sessão (ordem sugerida)

### 1️⃣ Reconfigurar agente local pra produção

```powershell
cd D:\achadinhos-agent
.venv\Scripts\activate

# Apaga config antiga (que aponta pra localhost)
del "$env:APPDATA\Achadinhos\config.json"

# Setup novo passando URL de prod
python -m agent.setup
# Quando perguntar "URL do servidor": https://achadinhos.maisseguidores.ia.br
# Login: admin
# Senha: <a do gerenciador>
# Nome PC: o que quiser

# Roda
python -m agent.main --sem-tray
# Deve mostrar "ws.conectado" — agora conectado em wss://achadinhos.maisseguidores.ia.br
```

### 2️⃣ Smoke test e2e em produção

- [ ] Abre `https://achadinhos.maisseguidores.ia.br/signup` em janela anônima
- [ ] Cria conta nova (org `Teste Prod`, login `teste`, senha forte)
- [ ] Vai pra `/onboarding` automaticamente
- [ ] Vai pra `/agentes/baixar` e segue instruções
- [ ] Loga de volta como admin
- [ ] Cria template, canal Telegram com bot real, grupo
- [ ] Roda lote → vê postagem chegar no grupo Telegram

### 3️⃣ Cloudflare proxy ON (opcional)

Quando tudo estiver estável:
1. Cloudflare → DNS → CNAME `achadinhos`
2. Clica no ícone cinza pra virar laranja (Proxied)
3. SSL/TLS → Overview → modo **Full** (não Strict, não Flexible)
4. Testa de novo. Se quebrar (502), volta pra cinza.

---

## Roadmap futuro (depois do worker+beat)

| Fase | Descrição | Tempo estimado |
|------|-----------|---------------|
| **8** | Shopee + Amazon (estender padrão do ML) | 2 sessões |
| **9** | Build `.exe` real do agente (PyInstaller) | 1 sessão |
| **10** | Email transacional (welcome, recuperar senha — SMTP) | 1 sessão |
| **11** | Página de upgrade de plano (free→pro→business, sem billing real) | 1 sessão |
| **12** | Métricas/analytics no dashboard (postagens/dia, top produtos) | 1 sessão |
| **13** | Tests pytest pra Fases 4b/5/6 | 1-2 sessões |

---

## Como abrir nova sessão e retomar

1. Abre nova conversa Claude Code no diretório `D:\ACHADINHOSV3`
2. Primeira mensagem ao Claude:

   > *"Estou continuando o Achadinhos V3. Lê CLAUDE.md e
   > docs/sessao_continuacao.md, o estado completo está lá. Próximo passo
   > é criar os services worker e beat no Railway."*

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

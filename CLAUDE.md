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

**Agente desktop (monorepo):** `agente/` no mesmo repo — Python + Selenium +
undetected-chromedriver. Build do `.exe` via PyInstaller já funciona
(`pyinstaller build.spec` → `agente/dist/AchadinhosAgent.exe`, ~30 MB).
Empacotamento user-friendly (installer Inno Setup, ponte browser↔agente,
botão "Conectar" no dashboard) vem na Fase 9 — ver ADR-009.

**Setup dev do agente (uma vez):**
```powershell
cd D:\ACHADINHOSV3\agente
python -m venv .venv
.venv\Scripts\activate
pip install -e .
```

**Rodar agente dev:**
```powershell
cd D:\ACHADINHOSV3\agente
.venv\Scripts\activate
python -m agent.setup             # 1× — pede email/senha, registra agente
python -m agent.login_ml          # 1× ou quando sessão ML expirar
python -m agent.login_whatsapp    # 1× ou quando WhatsApp Web expirar
python -m agent.main --sem-tray   # roda
```

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
- **3.10.2** — Agente local apontando pra prod via WSS, signup público + onboarding validados em prod, ADR-009 (Fase 9 expandida em 9.1-9.8)
- **3.11.0** — Fase 9.1: build PyInstaller validado (`dist/AchadinhosAgent.exe` ~30 MB, conecta no WSS prod em 1.2s). Agente movido pra monorepo (`agente/`).
- **3.11.1** — Fase 9.2: `agente/agent/local_server.py` (aiohttp em `127.0.0.1:5577`, fallback 5578/5579). Endpoints `/ping`, `/status` ativos; `/pair`, `/abrir-tudo` stub 501. CORS pronto pra origem prod + localhost dev. Roda em paralelo ao WS no `main.py`. Validado via Python e via `.exe` rebuilt.
- **3.11.2** — Fase 9.3: pareamento via JWT no `/pair` real. `main.py` agora roda **sem token** (sobe local_server, aguarda `POST /pair`, daí sobe WS dinamicamente). Re-pareamento durante runtime salva config mas pede restart (token novo só ativa no próximo boot). Fluxo end-to-end **zero-CLI** validado em prod: agente sem cfg → dashboard envia JWT → agente chama `registrar-self` no servidor → token salvo → WS conectado em ~1s.
- **3.11.3** — Fase 9.4: botão **"Conectar meu agente"** em `/agentes/baixar`. JS detecta `127.0.0.1:5577-5579/ping`, dá 3 UX (já pareado / rodando-sem-token / não-instalado). Endpoint server `GET /api/v1/auth/me/pair-token` devolve JWT pro JS (cookie HttpOnly não dá pra ler). Placeholder `GET /api/v1/agentes/download` retorna 503 (Fase 9.5 entrega real).
- **3.11.4** — Fase 9.5: `agente/installer.iss` (Inno Setup) + `.github/workflows/release-agente.yml`. CI builda `.exe` + installer Windows nativo no runner `windows-latest`. Installer per-user (sem admin), registra `achadinhos://` URL protocol, opt-in pra auto-start no Windows e atalho no desktop. Disparado por tag `agente-v*` (cria GitHub Release) ou `workflow_dispatch` manual.
- **3.11.5** — Fase 9.6: URL protocol handler no agente. `parse_args()` aceita `--uri`. Se outra instância já está rodando, o 2º processo encaminha o URI pra ela via `POST /uri-trigger` (single-instance handoff) e sai. Senão, roteia via `LocalServer.processar_uri()` que parseia `achadinhos://acao?args` e dispatch pra ação (`abrir-tudo`, `ping`, ...).
- **3.11.6** — Fase 9 fim do caminho zero-CLI: `/abrir-tudo` **real** via `webbrowser.open()` em loop (stdlib, abre cada URL no browser default do user — sem instanciar Chrome próprio). Default: `[web.whatsapp.com, mercadolivre.com.br]`. Dashboard chama o endpoint logo após `/pair` bem-sucedido e mostra "Abrir minhas plataformas" como botão persistente quando o agente já está pareado. Fecha o ciclo "user clica botão → algo visível acontece no PC dele".
- **3.11.7** — Fase 9.8: badge "Agentes online" no header do dashboard. Endpoint `GET /api/v1/agentes/status` cruza DB com `agente_registry` em memória. JS no `base.html` faz polling a cada 20s; bolinha verde/amarela/vermelha/cinza dependendo de quantos agentes da org estão com WS conectado. Crítico pro cenário "controle remoto via celular".
- **3.11.8** — Fase 9.9: signup free restrito. Migration 0005 adiciona 3 flags em `planos` (`pode_cadastrar_afiliado`, `pode_criar_buscas`, `pode_criar_produto_proprio`). Plano `free` tem todas false; `pro` e `business` todas true. Gates server-side em PATCH credenciais, POST buscas, POST produtos. UI no `base.html` esconde menu "Buscas"/"Nichos" se plano free; `onboarding.html` esconde card de credenciais ML. Resultado: novo signup público vira admin da própria org mas só consome catálogo do admin (org `achadinhos`), postando com afiliado do admin.
- **3.11.9** — Fase 11 (parcial) + catálogo compartilhado: página `/planos` com tabela comparativa (free/pro/business), link "⭐ Upgrade" no header, CTA no onboarding. `settings.admin_org_id` (default 1) + `_org_ids_visiveis()` em produtos + `produtos_elegiveis` (lote): plano free vê produtos da org admin com link de afiliado do admin. Bug fix: `Usuario.organizacao` agora `lazy="joined"` (sem MissingGreenlet em async).
- **3.11.10** — UPSERT em `criar_agente`: pareamento re-entry (mesmo PC, mesmo user, mesmo nome) **reutiliza** o agente existente em vez de duplicar — só gera token novo. Migration 0006 cria índice único partial `(org_id, usuario_id, nome) WHERE ativo=true` pra blindar a invariante a nível de DB.
- **3.11.11** — UX bugs do dashboard: `usuario_atual` agora aceita JWT via **cookie HttpOnly** (não só Bearer header) — resolve badge "🟢/🔴 N online" no header que ficava cinza "status?". Página `/agentes/baixar` ganhou botão `⬇ Baixar agente` prominente quando agente offline; setup dev movido pra `<details>` recolhido com summary "Sou desenvolvedor". Nova rota `GET /agentes/instalador` que redireciona pro último GitHub Release (fallback HTML "em breve" se não tem release).
- **3.11.12** — **Primeira release oficial do agente**: tag `agente-v3.0.0` disparou o workflow `release-agente` (Fase 9.5), que em ~2.5min buildou via PyInstaller, gerou installer via Inno Setup e publicou no GitHub Releases. Asset: `AchadinhosAgent-Setup-3.0.0.exe` ~33 MB em [github.com/silviosvargas/achadinhosv3/releases/tag/agente-v3.0.0](https://github.com/silviosvargas/achadinhosv3/releases/tag/agente-v3.0.0). `/agentes/instalador` confirmado em prod redirecionando 302 direto pro arquivo. **Caminho zero-CLI ponta-a-ponta agora 100% funcional pro user final.**
- **3.12.0** — Backfill mappings categoria_ml→nicho (migration 0007, 21 entries) + cascata tag ML estendida pra admin org central + página `/conta` com troca de senha via UI.
- **3.13.0** — Fase 13: tabela `usuarios_afiliados` (1 row por user × marketplace), 6 marketplaces suportados (ML, Shopee, Amazon, Magalu, AliExpress, TikTok). Substituiu `usuarios.afiliado_ml` + drop dos campos vestigiais `usuario_ml`/`senha_ml_cifrada` (migration 0008). UI nova `/usuarios/{id}/afiliados` com tabela + botão "+ Adicionar marketplace". Cascata genérica em `afiliado_service.tag_com_cascata(plataforma, usuario_id, org_id)`.
- **3.14.0** — Fase 14: encurtador próprio `/r/{slug}` (migration 0009 tabela `redirects`). Lote chama `redirect_service.criar_ou_atualizar_pro_produto` antes de postar, retorna shortlink `achadinhos.maisseguidores.ia.br/r/XXX`. Click incrementa `total_clicks`, faz 302 pra URL com tag de afiliado. **Late binding** da tag no lote (`lote_service._url_pro_produto` recalcula URL a cada postagem, não usa `produto.url_afiliado` congelado).
- **3.15.0** — Fase 15: linkbuilder ML real via scraping do painel oficial. `agente/agent/linkbuilder_ml.py` abre `chrome_perfil_ml` (sessão logada em afiliados ML), navega pra `mercadolivre.com.br/afiliados/linkbuilder`, cola URLs em lotes de 10, captura `meli.la/XXX` via regex. Servidor enfileira `TipoTarefa.GERAR_LINK` após `ingerir_produtos`; agente devolve mapping; `afiliado_ml_writer.aplicar_mapping` atualiza `produtos.url_afiliado` + `redirects.url_destino`. Lote prioriza `meli.la` sobre fallback `?matt_word=`. ML credita comissão de verdade.
- **3.15.1** — Detecção de versão do agente: endpoint `GET /api/v1/agentes/versao-atual` (cache 5min consulta GitHub releases) + JS no `/agentes/baixar` compara semver com `/ping` local. Se desatualizado, mostra status "⚠ atualização disponível: vX.Y.Z" + botão amarelo "⬆ Atualizar agente".
- **3.15.2** — Release `agente-v3.0.1` publicada (inclui Fase 15 + detecção de update).
- **3.16.0** — Fase 16.1+16.2: UI nova `/buscas/nova` com dropdown "tipo de busca" (`termo_livre`/`por_url`/`mais_vendidos`/`melhor_comissao`/`em_alta`) + checkbox 6 marketplaces. Schema (migration 0010) adiciona `tipo` + `marketplaces` (JSON array) em `buscas_ml`. Backend route POST aceita `tipo` + `marketplaces[]` e converte pra entrada compatível.
- **3.16.3** — Fase 16.3: scraper "mais vendidos" ML (8 categorias hardcoded da V2). `agente/agent/busca_ml.py` ganha `_varrer_mais_vendidos_sync` que itera Roupas/Esportes/Beleza/Bebês/Casa/Eletrônicos/Informática/Ferramentas. Display names casam com `nicho_categoria_ml` (auto-classificação funciona). Roteamento em `executar_busca` por `msg.tipo_busca`. Release `agente-v3.0.2` publicada (`AchadinhosAgent-Setup-3.0.2.exe` 31.8 MB).
- **3.16.4** — Hotfix conflito de chave `tipo` no payload WS: `dispatcher._tentar_entrega` monta msg como `{"tipo": comando_ws, **tarefa.payload}` e o spread sobrescrevia o `tipo` do nível superior (comando WS) com o `tipo` da busca (definido na Fase 16.1). Agente recebia `tipo=mais_vendidos` em vez de `tipo=iniciar_busca_ml`, abortava com `ws.tipo_sem_handler`. Fix: renomeia campo no payload pra `tipo_busca` (agente já lê assim em `executar_busca`). Release `agente-v3.0.3` publicada. **Busca "Mais vendidos" validada em prod pelo user.**
- **3.17.0** — Fase 16.4: busca personalizada por URL/link. Nova `_varrer_produto_unico_sync` em `agente/agent/busca_ml.py` que abre 1 URL de produto ML e extrai dados (nome, preço, foto, categoria) via cascata JSON-LD Product → OpenGraph meta → CSS. Roteamento em `executar_busca` quando `tipo_busca == 'por_url'`. Pipeline GERAR_LINK (Fase 15) gera `meli.la` automaticamente após ingest. Dono do produto segue regra de afiliado: afiliado = privado, admin/usuario = público da org. Bug fix oportuno: `app/workers/scheduler_tasks.py` agora inclui `tipo_busca` + `marketplaces` no payload das buscas agendadas (faltava — Celery beat estava degradando qualquer busca agendada pra `termo_livre`). Release `agente-v3.0.4` publicada.
- **3.17.1** — Hotfix defensivo no dispatcher: `_tentar_entrega` agora monta msg WS como `{**tarefa.payload, "tipo": comando_ws, "tarefa_id": tarefa.id}` — comando WS sempre vence sobre chaves legadas do payload. Sintoma observado em prod: tarefas LEGADAS (criadas antes do hotfix v3.0.3) ficaram PENDENTE no DB e quando agente reconectou, `reentregar_pendentes` re-despachava com payload velho (`tipo=mais_vendidos`) que sobrescrevia o comando. Agente caía em `ws.tipo_sem_handler`. Mesmo fix em `busca_service._entregar_para_agente`. Loga `tarefa.payload_chave_conflitante` quando detecta caso legado.
- **3.18.0** — Fase 16.5 (parcial — scrapers ML completos por tipo): refatorou `agente/agent/busca_ml.py` pra ter 1 função dedicada por tipo de busca, todas usando template comum `_varrer_lista_urls_sync` (com helper `_bloqueado_por_login`, `_scroll_lazy_load`). 5 caminhos explícitos: `_varrer_termo_livre_sync` (paginação de listagem), `_varrer_produto_unico_sync` (por URL — Fase 16.4), `_varrer_mais_vendidos_sync` (8 categorias), `_varrer_melhor_comissao_sync` (top 4 categorias por comissão DESC — Roupas/Esportes/Beleza), `_varrer_em_alta_sync` (URL `/ofertas`). `executar_busca` rotea com 5 branches explícitas + fallback. Release `agente-v3.0.5` publicada.

---

## Decisões arquiteturais (ADRs em `docs/decisoes.md`)

- ADR-003: multi-tenant via `org_id` discriminator
- ADR-004: WhatsApp → agente local; Telegram → Celery cloud
- ADR-005: tarefas em Postgres + notificação Redis
- ADR-006: JWT stateless
- ADR-007: Jinja2 + API REST
- ADR-008: produtos privados de afiliado (partial unique indexes)
- ADR-009: Fase 9 — botão "Conectar meu WhatsApp" (agente como `.exe` instalável + ponte browser↔agente via HTTP local + URL protocol)

---

## Como rodar (dev local)

```powershell
docker compose up -d              # sobe tudo
docker compose ps                 # confirma healthy
docker compose logs -f api        # logs
docker compose down               # para
```

**Agente local:** ver bloco "Setup dev do agente" e "Rodar agente dev" mais acima.

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

**Leia `docs/sessao_continuacao.md` PRIMEIRO — tem tudo consolidado.**

Estado atual: caminho zero-CLI 100% funcional. Agente em v3.0.2.
Buscas multi-tipo com scraper "mais vendidos" ML (8 categorias) já entregue.

**Próximas fases na ordem:**
1. Fase 16.5 — scraper Shopee (API interna retorna `long_link` afiliado pronto)
2. Fase 17 — curadoria automatizada TOP 50 (Celery beat diário)
3. Fase 18 — métricas no dashboard (clicks do `/r/{slug}`)

Bugs anotados:
- `REDIS_URL_OVERRIDE` vs `REDIS_URL` em `app/core/config.py` (api funciona por sorte)
- `ADMIN_PASSWORD` env var no Railway desatualizada (user trocou via `/conta`)

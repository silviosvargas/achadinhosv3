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
- **3.18.1** — Fase produtos UI: CRUD individual + apagar em massa. `/produtos` ganha botões por linha: **🔗 Ver produto** (abre `url_afiliado` em nova aba), **✏️ editar** (form pré-preenchido em `/produtos/{id}/editar` — plataforma/item_id readonly), **🗑️ excluir** (com `confirm()` mostrando nome). Header ganha **🗑️ Apagar todos** com confirmação tripla: 2× `confirm()` + 1× `prompt()` exigindo token literal `APAGAR TUDO`; servidor revalida o mesmo token no body (defense in depth). Template `produto_form.html` adapta criação/edição via flag `produto`. CASCADE no DB remove `produto_nichos` + `redirects` automaticamente. Também: endpoint `POST /produtos/regenerar-meli-la` re-enfileira GERAR_LINK pros produtos da org sem `meli.la/` no `url_afiliado` (+ cleanup automático de URLs com fragment legado).
- **3.18.2** — Linkbuilder: normalização URL no agente + match em 3 níveis no servidor + cache versão 60s. `agente/agent/linkbuilder_ml.py:_normalizar_url(url)` tira fragment/query antes de submeter ao painel ML (mantém URL original como chave do mapping). `app/services/afiliado_ml_writer.py:aplicar_mapping` faz match exato → URL limpa LIKE → MLB ID LIKE, com regex `MLB[A-Z]?-?\d{8,15}` que aceita `MLBU` (catálogo unificado). `/api/v1/agentes/versao-atual` reduziu cache de 5min → 60s + bypass via `?nocache=1`. Release `agente-v3.0.6`.
- **3.18.3** — 4 fixes baseados em log real do agente. (1) `dispatcher.reentregar_pendentes` filtra só `StatusTarefa.PENDENTE` (não mais PROCESSANDO) — re-entrega após reconexão WS estava causando execução duplicada e crashando o 2º Chrome com `SessionNotCreatedException`. (2) `_LOCK_CHROME_ML = threading.Lock()` no agente serializa criação de driver ML + `time.sleep(1.5)` após `quit()` pra liberar `--user-data-dir`. (3) `_limpar_url_canonica` no `_upsert_produto` salva URL sem `#polycard_client=...` no DB. (4) `/produtos/regenerar-meli-la` faz UPDATE batch limpando URLs antes de enfileirar. Release `agente-v3.0.7`.
- **3.18.4** — Limpeza URL no momento da extração do card (igual V2). `agente/agent/busca_ml.py:_achar_url` portado 1:1 da V2 (`src/buscar/ml.py:121`): itera múltiplos seletores de anchor (`a.poly-component__title`, etc), limpa URL com `split('?')[0].split('#')[0]`, filtra `click1`/`publicidade` (patrocinados sem comissão) e URLs absurdamente curtas. Mesmo em `_extrair_produto_unico` (busca por_url). Razão: V2 sempre funcionou porque limpava na origem; V3 mantinha URL crua e tentava limpar em N camadas — cada falha gerou bug diferente. Release `agente-v3.0.8`.
- **3.18.5** — Linkbuilder INLINE no agente (igual V2 — mesmo driver). `_gerar_meli_la_no_driver(driver, produtos, log_prefixo)` recebe driver aberto + lista de produtos, abre painel linkbuilder ML NO MESMO driver, captura `meli.la`, atualiza `produto["url_afiliado"]` in-place. Chamada antes do `finally` que fecha driver em `_varrer_lista_urls_sync` / `_varrer_termo_livre_sync` / `_varrer_produto_unico_sync`. `_upsert_produto` no servidor detecta `item.url_afiliado` como `meli.la` e salva direto (fallback `?matt_word=` só se agente não conseguiu gerar). REMOVIDO o trecho que enfileirava GERAR_LINK pós-ingest — não precisa mais. Vantagens: 1 só Chrome ML por vez, sem race de re-entrega WS, sem callback assíncrono que pode falhar. Release `agente-v3.0.9`.
- **3.19.0** — **BUG RAIZ encontrado e corrigido** (escondido desde a Fase 15, v3.5.0). `agente/agent/main.py:handler_gerar_links_ml` retornava `{"mapping": ..., "total": ...}` SEM `"ok": True`. O `ws_client._executar_handler` decide `tarefa_concluida` vs `tarefa_falhou` por `resultado.get("ok")`. Sem `ok=True`, SEMPRE caía em `tarefa_falhou` → servidor marcava tarefa FALHOU → `dispatcher.marcar_concluida` nunca chamava `afiliado_ml_writer.aplicar_mapping` → `produtos.url_afiliado` nunca virava `meli.la`. Os 6 fixes anteriores (v3.0.4 → v3.0.9) eram melhorias reais mas nenhum atacava esse bug porque o callback nem chegava. Fix: retornar `{"ok": True, "mapping", "total", "pedidos"}`. Contrato documentado em `docs/contrato_handlers_ws.md`. **Validado em prod com regenerar meli.la — produtos receberam `https://meli.la/XXX` no `url_afiliado`.** Release `agente-v3.0.10` publicada.
- **3.20.0** — Fase 16.5 (Shopee) + 16.6 (Amazon): **3 marketplaces ativos**.
  - **Shopee** (v3.1.0): API interna `affiliate.shopee.com.br/api/v3/offer/product/list` retorna `long_link` afiliado pronto (sem segundo passo de linkbuilder). `agente/agent/busca_shopee.py` faz fetch via `driver.execute_script` no painel logado pra reaproveitar cookies. Lock `_LOCK_CHROME_SHOPEE`. Login manual via `agent.login_shopee`. 6 marketplaces na UI agora.
  - **Modo interativo Shopee** (v3.1.1, 3.1.2): banner amarelo no Chrome + aviso no dashboard via WS (`agent.avisos.publicar`) quando detecta captcha/login expirado. Endpoint `GET /api/v1/agentes/avisos` + polling JS no `base.html` (8s) mostra toast persistente. Captcha = 30s fixos × 3 tentativas; login = polling 5min.
  - **Amazon** (v3.2.0): `agente/agent/busca_amazon.py` scraping de 10 categorias `/gp/bestsellers/` + SiteStripe (`#amzn-ss-get-link-button`) gera `amzn.to/XXX`. Cards `div.p13n-sc-uncoverable-faceout`, ASIN extraído de id ou regex `/dp/([A-Z0-9]{10})`. Comissões estimadas por categoria (3-10%). Sem login Associates → cai em fallback `?tag=<sua_tag>` no servidor.
- **3.20.1** — **Padronização final do retry interativo** (v3.2.1): unifica estratégia pra captcha + login_expirado em TODOS os marketplaces. 30s fixos × 3 tentativas (substitui polling 5min do login). `_verificar_login_amazon` acessa `associados.amazon.com.br/home` (página protegida) ANTES de iterar categorias — se redirect pra signin, dispara retry. Após desbloqueio aparente, verificação dupla pra confirmar login real (evita falso positivo de redirect intermediário). Aviso no dashboard agora inclui texto "Após resolver, vou re-testar automaticamente". **Padrão documentado em `docs/contrato_busca_marketplace.md` — usar nos próximos marketplaces (Magalu, AliExpress, TikTok).**
- **3.21.0** — Fase 17: **Produtos Personalizados**. Nova seção `/produtos/personalizados` (item 🛍️ no sidebar grupo Catálogo) onde qualquer user cadastra produtos manualmente. Migration `0011_prod_criado_por` adiciona `produtos.criado_por_usuario_id` (FK opcional) — diferente de `usuario_dono_id` (que rege visibilidade pública/privada — ADR-008), `criado_por` rastreia QUEM cadastrou pra UI mostrar "meus produtos". Regras de dono (do usuário): admin/usuário comum → produto **público** (`usuario_dono_id=NULL`, postado com tag central do admin); afiliado COM tag em `usuarios_afiliados` → produto **privado** (`usuario_dono_id=afiliado.id`, SÓ ele posta); afiliado SEM tag → público. Form com palavra-chave (busca termo_livre limit 10) OU link de marketplace (busca por_url) OU link de social com checkbox 🤖 IA (Claude Haiku 4.5 lê `og:title`/`og:description` pra inferir palavra-chave — requer `ANTHROPIC_API_KEY`). Grid de cards estilo V2 (esmeralda V3). Botões ⚡ Postar (lote_service.postar_produto_imediato dedicado, não passa pelo `rodar_lote`), 🔗 Abrir, ✏️ Editar, 🗑 Apagar. Ações em massa: Postar todos / Limpar todos.
- **3.21.1** — Hotfix Fase 17: **schema Pydantic descartava `url_afiliado` silenciosamente**. `IngestProdutoItem` em `app/schemas/produto.py` não declarava `url_afiliado` nem `comissao` → Pydantic com `extra="ignore"` (default) cortava esses campos do payload do agente, fazendo `meli.la/XXX` capturado pelo linkbuilder NUNCA chegar ao `_upsert_produto` → DB salvava sempre o fallback `?matt_word=`. Fix: declara ambos os campos + `model_config = {"extra": "allow"}` pra aceitar marcadores internos (`_personalizado_dono_id`, `_personalizado_criador_id`). **Lição registrada em armadilhas conhecidas.**
- **3.21.2** — Fix Fase 17 robustez: 500 no `/postar` (rota chamava `rodar_lote(max_produtos=1)` esperando pegar produto específico, mas é genérico). Nova função dedicada `lote_service.postar_produto_imediato(produto_id, ...)` — carrega produto + nichos, acha 1 grupo compatível não-postado-recentemente, renderiza template + late-binding tag, enfileira via dispatcher. Retorna `{ok, erro}` explícito mostrado no redirect.
- **3.21.3** — Fix Fase 17 scraper por_url ML (v3.2.2): produtos legacy com MLB curto (`MLB6087`, 4-7 dígitos) eram rejeitados pelo regex `\d{8,15}` → **regex permissivo** `MLB[A-Z]?-?\d{4,15}`. Página de catálogo com layout diferente do produto único tinha `dados_insuficientes preco=None tem_nome=False` → **espera explícita** com `WebDriverWait` até `h1.ui-pdp-title` OU `<script type="application/ld+json">` OU `meta og:title` (12s timeout), `_scroll_lazy_load` agressivo, **cascata de 5 seletores de preço** (PDP + catálogo + meta Schema.org), **diagnóstico em disco** (HTML+screenshot em `%APPDATA%\Achadinhos\debug\ml_porurl_*.png,html`) quando extração falha.
- **3.22.0** — Fase 18: **Curadoria via nota no produto + precisão de dados** (v3.3.0). Reformulação total da Fase 18 anterior (descartada — usava snapshot diário). Migration 0012 adiciona em `produtos`: `nota` (0..100), `is_bestseller`, `is_em_alta`, `total_vendidos`, `comissao_fonte` (`ml_painel`/`shopee_api`/`amazon_tabela`/`estimativa`), `comissao_validada`, `preco_atualizado_em`, `comissao_atualizada_em`, `vendidos_atualizado_em` + índice `(org_id, nota DESC)`. **Captura precisa de dados nos 3 scrapers**: (a) `busca_ml.py:_achar_vendidos` parseia "+5 mil vendidos" do card; (b) `linkbuilder_ml.py:_gerar_lote_sync` agora extrai % comissão real da tabela do painel ML e propaga via `_gerar_meli_la_no_driver` → `comissao_fonte="ml_painel"`; (c) `busca_shopee.py` adiciona `historical_sold`/`sold` + marca `is_em_alta=True` + `comissao_fonte="shopee_api"` (comissão já era real); (d) `busca_amazon.py` marca `is_bestseller=True` + usa rank como proxy de vendas (`_rank_para_vendas_estimadas`). Servidor: `app/core/comissoes.py` tem ranges esperados por marketplace (ML 0.5-25%, Shopee 0.5-30%, Amazon 1-12%) usados em `validar_comissao`. `app/services/scoring.py:calcular_nota` é função pura: 30% preço × 40% comissão (zerado se !validada) × 30% vendas. Aplicada em `busca_service._upsert_produto` no ingest. `app/services/curadoria_service.py` faz `listar_top` via query direta (`SELECT FROM produtos WHERE nota >= 30 ORDER BY nota DESC LIMIT 50` + filtro NOT EXISTS postagem últimos 7d) — **sem snapshot, sem beat task, live**. Cascata de fallback Fase 11 (admin_org). Endpoints `GET /curadoria/top` + `POST /recalcular-notas` + `POST /revalidar-comissoes` (admin). UI: página `/curadoria/top` com badge ⭐ N/100 + breakdown comissão/fonte/vendas + filtro de nota mínima. Sidebar grupo Catálogo ganha **🏆 Top por nota**. Dashboard mini-grid de 6 cards.

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
- 0005 planos flags restrição (signup free)
- 0006 agentes índice único partial (UPSERT)
- 0007 mappings nichos ML (backfill 21 entries)
- 0008 usuarios_afiliados (multi-marketplace)
- 0009 redirects curto (Fase 14 encurtador)
- 0010 busca tipo + marketplaces (Fase 16)
- 0011 produto criado_por (Fase 17 Personalizados)
- 0012 produtos nota + flags vendas + comissao_fonte + 3 timestamps (Fase 18)

Criar nova: `docker compose exec api alembic revision --autogenerate -m "msg"`

---

## Workflow de release do agente — LEIA antes de mexer no `agente/`

Toda mudança em `agente/agent/*.py` (scrapers, linkbuilder, login_*, main, ws_client,
local_server) **precisa virar release** porque o user roda o `.exe` instalado
no PC dele — não pega mudanças via git pull. Sem release, o servidor pode estar
no v3.X.Y mas o agente do user continua no v3.X.Y-1, gerando bugs silenciosos
(ex: campos novos no schema do servidor que o agente nunca envia).

### Quando bumpar

| Tipo de mudança | Bump |
|---|---|
| Bug fix em scraper / linkbuilder / handler | **patch** (3.3.0 → 3.3.1) |
| Captura de dado novo, novo tipo de busca, mudança em formato de retorno | **minor** (3.3.1 → 3.4.0) |
| Quebra de protocolo WS (raríssimo) | **major** (3.X.Y → 4.0.0) |

**Regra de bolso:** mexeu em qualquer `.py` em `agente/agent/`? Bump no fim.

### Como bumpar (3 arquivos, sempre os mesmos)

1. `agente/agent/local_server.py` → `VERSAO_AGENTE = "X.Y.Z"`
2. `agente/pyproject.toml`       → `version = "X.Y.Z"`
3. `agente/installer.iss`        → `#define MyAppVersion "X.Y.Z"`

Faltar um quebra: `local_server.py` retorna versão errada via `/ping`,
`pyproject.toml` é fonte oficial pro PyPI/pip, `installer.iss` define o nome
do arquivo `AchadinhosAgent-Setup-X.Y.Z.exe` no Inno Setup.

### Como publicar

```powershell
git commit -am "feat(agente): mudança X (bump X.Y.Y → X.Y.Z)"
git push origin <branch>:main                    # API redeployar
git tag -a agente-vX.Y.Z -m "Descrição curta"
git push origin agente-vX.Y.Z                    # dispara build
```

Tag `agente-v*` dispara workflow `.github/workflows/release-agente.yml`:
- Roda em `windows-latest` (PyInstaller só builda Windows)
- Compila `.exe` + Inno Setup empacota installer
- Publica release no GitHub com asset `AchadinhosAgent-Setup-X.Y.Z.exe`
- **Demora ~3min**

### Monitoramento OBRIGATÓRIO após push da tag

**Sempre verificar que o build terminou com sucesso** antes de pedir pro user
instalar OU antes de seguir com próximas mudanças no agente. Sem monitoramento,
release pode ter falhado (deps quebradas, sintaxe Inno Setup, erro PyInstaller)
e o user fica com versão antiga **sem saber**.

Comandos pra checar status:

```bash
# Workflow runs (status: in_progress | completed; conclusion: success | failure)
curl -s "https://api.github.com/repos/silviosvargas/achadinhosv3/actions/workflows/release-agente.yml/runs?per_page=2" \
  | python -c "import json,sys; d=json.load(sys.stdin); [print(f\"{r['head_branch']:18} {r['status']:12} {r['conclusion'] or '-':10} {r['updated_at']}\") for r in d.get('workflow_runs', [])]"

# Release publicada (id existe + asset listado)
curl -s https://api.github.com/repos/silviosvargas/achadinhosv3/releases/tags/agente-vX.Y.Z \
  | python -c "import json,sys; d=json.load(sys.stdin); print('publicada:', bool(d.get('id'))); [print(' -', a['name'], a['size'], 'bytes') for a in d.get('assets',[])]"
```

Pra **monitorar em background** até completar (Claude recebe notificação
automática quando o background process termina):

```bash
# Bash com run_in_background=true:
until [ "$(curl -s '<workflow_runs_url>' | python -c '...status')" = "completed" ]; do
  sleep 30
done
# Print resumo final do conclusion + asset
```

Padrão usado nesta sessão (commit 033ed54 → tag agente-v3.3.1).

### Após release validada, comunicar ao user:
1. Badge "⚠ atualização disponível" aparece automaticamente em `/agentes/baixar`
   (a detecção de versão da Fase 15.1 consulta `/api/v1/agentes/versao-atual`
   que lê o `tag_name` do GitHub releases — cache 60s)
2. Link direto: `https://github.com/silviosvargas/achadinhosv3/releases/tag/agente-vX.Y.Z`
3. Após user instalar, validar com 1 busca real do tipo afetado pela mudança
   (ex: se mexeu em `busca_ml.py:_achar_vendidos`, pede pra rodar busca tipo
   mais_vendidos e conferir `total_vendidos > 0` no produto resultante)

### Ordem servidor × agente importa

Quando mudança envolve servidor + agente juntos (caso comum):
1. **Servidor PRIMEIRO** (push pra main → Railway redeploy + migration)
2. **Agente DEPOIS** (tag + build + user instala)

Razão: agente novo pode enviar campos que precisam de coluna no schema do
servidor (Fase 18 v3.3.0 é exemplo: agente manda `total_vendidos` que precisa
existir em `produtos` antes do ingest aceitar). Inverter ordem → agente envia
dados que `extra="allow"` aceita silenciosamente mas nunca grava → mesmo
padrão do bug v3.21.1.

### Agente NÃO precisa de release quando

- Mudança só em `app/` (servidor) — Railway redeploy automático cobre
- Mudança em CSS/Jinja templates — idem
- Mudança em migrations — Railway aplica via `bootstrap_producao.py`
- Mudança em `.github/workflows/` (exceto release-agente.yml)
- Mudança em docs / CLAUDE.md / `docs/*.md`

---

## Armadilhas conhecidas — LEIA antes de mexer

### Handlers WS sempre retornam `ok=True/False`

`agente/agent/ws_client.py:_executar_handler` decide `tarefa_concluida` vs
`tarefa_falhou` pelo campo `ok` do retorno. **Handler sem `"ok": True`
é falha silenciosa** — servidor marca FALHOU e nunca chama hooks de
pós-conclusão (ex: `aplicar_mapping`). Contrato completo + template em
[docs/contrato_handlers_ws.md](docs/contrato_handlers_ws.md).

Esse bug ficou 5 meses escondido (Fase 15 → v3.0.10) e custou 6 releases
atacando sintomas. **Sempre teste o callback antes da lógica de domínio**
quando algo "rodou mas não salvou".

### Chave `tipo` em payload WS

`dispatcher._tentar_entrega` monta msg como
`{**tarefa.payload, "tipo": comando_ws, "tarefa_id": tarefa.id}`. Chaves
`tipo`/`tarefa_id` no payload eram sobrescritas pelo comando WS (ordem
do spread protege agora desde v3.18.1), mas pra não depender disso:
**nunca use as chaves `tipo`/`tarefa_id` no payload da tarefa**. Use
`tipo_busca` ou similar.

### URL canônica ML — limpar na origem (no agente)

Cards do ML carregam `#polycard_client=...&tracking_id=...` no fragment +
query do `href`. Sempre limpar com `split('?')[0].split('#')[0]` no
momento da extração (`_achar_url`), não em camadas posteriores. V2 fazia
isso desde sempre — V3 tentou limpar em N camadas e cada falha causou
bug.

### Variantes do MLB ID

URL ML tem 2 formatos: `/p/MLB1234567890` (catálogo simples) e
`/up/MLBU3387021403` (catálogo unificado, com letra `U`). Regex pra match
flexível precisa aceitar `MLB[A-Z]?-?\d{8,15}`.

### `reentregar_pendentes` só re-envia PENDENTE

Tarefa em status `PROCESSANDO`, se WS cair no meio, NÃO deve ser
re-entregue na reconexão — o agente provavelmente terminou (ou está
terminando) e o callback chega quando WS subir. Re-entregar duplica
execução → conflito de Chrome ML (`SessionNotCreatedException`).

### Chrome ML em `--user-data-dir` único — usa lock

`agente/agent/linkbuilder_ml.py:_LOCK_CHROME_ML` (threading.Lock) garante
1 driver ML por vez no agente. Sem ele, duas GERAR_LINK chegando próximas
crashavam o 2º Chrome porque o perfil estava bloqueado.

### Adicionar marketplace novo? Use o contrato

`docs/contrato_busca_marketplace.md` tem o **checklist completo** + template
de código pra criar `busca_<marketplace>.py` seguindo o padrão dos 3 já
funcionando (ML, Shopee, Amazon). Padronização do modo interativo (30s × 3
retry), formato de produto retornado, integração no orquestrador, UI, build.

Não invente padrão novo — copia `busca_amazon.py` (template mais robusto)
e adapta. Pra novo tipo de busca dentro de um marketplace existente (ex:
"melhor avaliação"), adiciona handler dentro do módulo do marketplace
alvo (não cria módulo separado).

### Pydantic com `extra="ignore"` descarta silenciosamente

`app/schemas/produto.py:IngestProdutoItem` perdeu 5 meses até v3.21.1
porque NÃO declarava `url_afiliado`. Pydantic v2 com `extra="ignore"`
(default) descarta campos não declarados do payload sem warning, sem
error, sem nada. `meli.la/XXX` capturado pelo agente sumia entre
`enviar_lote` (cliente) e `ingerir_produtos` (servidor).

**Sempre que mexer em schema de ingest/payload**: declare TODOS os campos
que o caller pode mandar. Pra aceitar marcadores ad-hoc (ex:
`_personalizado_dono_id`), use `model_config = {"extra": "allow"}`.
Sintoma do bug: agente loga sucesso (`linkbuilder_aplicado aplicados=N`),
mas DB tem o fallback.

### Regex de MLB precisa ser permissivo

URL do ML tem variantes:
- `/p/MLB1234567890` (catálogo moderno, 10 dígitos)
- `/p/MLB6087` (legacy, 4-7 dígitos — produtos antigos ainda existem)
- `/up/MLBU3387021403` (catálogo unificado, letra `U`)
- `MLB-1234567890` (formato muito antigo, com hífen)

**Regex robusto**: `MLB[A-Z]?-?\d{4,15}`. Antes era `\d{8,15}` e rejeitava
legacy. Aplica tanto em `_extrair_item_id` (agente) quanto em
`afiliado_ml_writer._RE_MLB` (servidor).

### Comissão real do ML: fluxo OBRIGATÓRIO `meli.la → /social/ → clicar "Ir para produto" → barra`

A comissão REAL do produto (que considera o programa do afiliado +
bônus EXTRAS temporários) só aparece corretamente na barra preta quando
o agente **entra pelo link de afiliado e clica o botão "Ir para produto"**.

**Fluxo correto** (decisão do user, registrada em v3.4.3):

```python
# 1. Abre o link de afiliado (não a URL canônica do produto)
driver.get("https://meli.la/XXXXX")
# 2. ML redireciona pra página de "perfil social" do afiliado:
#    mercadolivre.com.br/social/<usuario>?matt_word=<usuario>&matt_tool=...
#    Essa página mostra um CARD do produto + botão "Ir para produto"
#    A barra preta de afiliados AINDA NÃO ESTÁ visível aqui.
time.sleep(2)

# 3. Procura e CLICA no botão "Ir para produto"
href = driver.execute_script("""
    var els = document.querySelectorAll('a, button');
    for (var el of els) {
        var t = (el.textContent || '').trim().toLowerCase();
        if (t.includes('ir para produto')) {
            return el.tagName === 'A' && el.href ? el.href : null;
        }
    }
    return null;
""")
if href:
    driver.get(href)
else:
    # Fallback: clicar via JS (caso seja button, não anchor)
    driver.execute_script("""...el.click()...""")
time.sleep(2)

# 4. AGORA está na página do produto com a barra preta visível:
#    "GANHOS EXTRAS 24%" (ou "GANHOS 5%" se não tem programa Mais por Mais)
# 5. Captura via regex `GANHOS\s+(?:EXTRAS\s+)?(\d+(?:[.,]\d+)?)\s*%`
```

**Por que esse fluxo e não outros**:

1. **`meli.la` em vez de URL canônica direta**: o `meli.la` carrega o
   contexto de afiliado certo (tag + tool + ref específicos daquele
   shortlink). Abrir URL canônica direta pode pegar comissão genérica
   do Chrome logado, **não a comissão real** do programa daquele link.

2. **Página `/social/` NÃO mostra a barra**: o ML usa essa página como
   "perfil social" do afiliado, com card resumo do produto. A barra
   preta com a comissão só aparece depois de clicar **"Ir para produto"**
   e ir pra página completa do produto.

3. **Erro a NÃO cometer (v3.4.2 e revisões anteriores)**: tentar abrir
   só o `meli.la` e tentar capturar a barra ali mesmo — a barra não
   existe na `/social/`. Resultado: captura `null`, todos os produtos
   ficam com `(estimativa)`.

**Implementação**:
- Função: `agente/agent/busca_ml.py:_capturar_comissao_da_barra(driver, url)`
  recebe URL meli.la, faz fluxo completo (redirect → clica botão → captura).
- Servidor manda `items=[{produto_id, url_afiliado}]` (sempre meli.la).
  Servidor: `app/services/curadoria_service.py:disparar_revalidacao_comissoes_via_agente`.

**ATENÇÃO PRA SESSÕES FUTURAS**: se algo "errou", NUNCA inventar uma
abordagem nova. Pergunte ao user qual o fluxo correto. O user é a fonte
de verdade sobre o comportamento do painel ML afiliados — ele OPERA o
sistema, não a Claude.


### Hierarquia de `comissao_fonte`: NÃO sobrescrever fonte alta com baixa

`busca_service._upsert_produto` aplica os campos vindos do agente quando
o produto já existe. Bug v3.4.4: o código sobrescrevia `comissao_fonte`
sem checar a hierarquia de confiança das fontes.

**Hierarquia** (`app/services/busca_service.py:_HIERARQUIA_FONTE_COMISSAO`):
1. `ml_barra_afiliados` — barra preta ML afiliados (Fase 18.3) — fonte de verdade
2. `ml_painel`          — painel linkbuilder ML (Fase 18.0)
3. `shopee_api`         — Shopee API direta
4. `amazon_tabela`      — tabela oficial Amazon BR por categoria
5. `categoria_ml_v2`    — tabela do servidor com ~50 categorias
6. `estimativa`         — categoria pai hardcoded no agente (otimista)

**Cenário do bug**:
- Busca 1: agente capturou 26% via barra → DB tem `ml_barra_afiliados=26%`
- Busca 2: produto reaparece em listagem, agente FALHOU captura da barra
  (sessão expirou, captcha, etc) → vem com `estimativa`
- Servidor refina pra `categoria_ml_v2=12%` (Calçados)
- Código antigo: `produto.comissao_fonte = "categoria_ml_v2"` → **SOBRESCRITO**
  o dado real bom com estimativa antiga ruim
- UI mostra `🟡 categoria ML 12%` em vez do verdadeiro `✅ ML barra 26%`

**Fix em `_upsert_produto`**: compara `_confianca_fonte(comissao_fonte_nova)`
com `_confianca_fonte(produto.comissao_fonte)` antes de atualizar. Só
sobrescreve se nova ≥ atual.

**Regra geral pra outros campos sensíveis**: quando o agente pode mandar
dados de qualidade variável (captura real vs estimativa), o servidor
DEVE preservar o melhor dado já no DB. Aplicável também a `total_vendidos`
(captura real vs proxy estimado) — verificar futuramente.

Captura ML da barra **prefere "GANHOS EXTRAS" sobre "GANHOS" base**: páginas
com promoção Mais por Mais mostram ambos. Pegar o primeiro match pegava
o base (errado). Implementado em `_capturar_comissao_da_barra` JS.


### Mudou código do `agente/`? Sempre bump + tag + monitorar

Mudança em `agente/agent/*.py` SEM bump de versão + tag + verificação do
build = release fantasma. User continua com `.exe` antigo, servidor já tem
schema novo, dados são descartados silenciosamente. Passo a passo completo
em "Workflow de release do agente" acima. Resumo:

1. Bump 3 arquivos: `local_server.py`, `pyproject.toml`, `installer.iss`
2. Commit + push pra main + tag `agente-vX.Y.Z` + push da tag
3. **Monitorar workflow até `completed/success`** (curl GitHub API ou
   background script com until-loop)
4. Conferir asset `AchadinhosAgent-Setup-X.Y.Z.exe` publicado
5. SÓ ENTÃO comunicar pro user instalar + validar com busca real

Se mudança envolve servidor + agente: **servidor primeiro, agente depois**
(senão agente manda campo que servidor ainda não tem coluna pra gravar).


### Página ML carrega lazy — espera elemento específico

`driver.get(url)` + `WebDriverWait(body)` não basta — o ML serve HTML
esqueleto e popula via JS. Pra extrair dados confiavelmente:

```python
WebDriverWait(driver, 12).until(
    lambda d: (
        d.find_elements(By.CSS_SELECTOR, "h1.ui-pdp-title")
        or d.find_elements(By.CSS_SELECTOR, "script[type='application/ld+json']")
        or d.find_elements(By.CSS_SELECTOR, "meta[property='og:title']")
    )
)
```

E **scroll progressivo** depois pra forçar lazy load de fotos/preços.
Quando extração falhar, salva HTML+screenshot em
`%APPDATA%\Achadinhos\debug\` pra próxima sessão analisar layout novo
do ML.

---

## Marketplaces ativos

| Marketplace | Estratégia | Login | Link de afiliado | Status |
|---|---|---|---|---|
| **🛒 Mercado Livre** | Scraping listagem/produto + linkbuilder painel ML afiliados | `python -m agent.login_ml` | `meli.la/XXX` via scraping inline | ✅ Prod |
| **🛍️ Shopee** | API interna `affiliate.shopee.com.br/api/v3/...` | `python -m agent.login_shopee` | `long_link` direto da API | ✅ Prod |
| **📦 Amazon** | Scraping `/gp/bestsellers/<cat>` + SiteStripe (`#amzn-ss-get-link-button`) | `python -m agent.login_amazon` | `amzn.to/XXX` via SiteStripe, fallback `?tag=` | ✅ Prod |
| 🌟 Magalu / 🌏 AliExpress / 🎵 TikTok | A implementar | — | — | 🚧 |

**Modo interativo** (banner Chrome + aviso dashboard) é universal — 30s
fixos × 3 tentativas pra captcha e login. Implementação detalhada em
`docs/contrato_busca_marketplace.md`.

---

## Próxima fase imediata

**Leia `docs/sessao_continuacao.md` PRIMEIRO — tem tudo consolidado.**

Estado atual: caminho zero-CLI 100% funcional. Agente em **v3.0.10**.
Pipeline completo de busca ML + geração `meli.la` + ingest **validado em prod**.

Fases novas entregues nesta sessão (15-16/05/2026):
- Fase 16.4 — busca por URL/link (v3.0.4)
- Fase 16.5 parcial — handlers dedicados por tipo (v3.0.5)
- CRUD produtos UI (v3.18.1)
- Linkbuilder inline + bug raiz resolvido (v3.0.10)

**Próximas fases na ordem:**
1. Fase 16.5 — scraper Shopee (API interna retorna `long_link` afiliado pronto)
2. Fase 17 — curadoria automatizada TOP 50 (Celery beat diário)
3. Fase 18 — métricas no dashboard (clicks do `/r/{slug}`)

Bugs anotados (não bloqueiam, vale fixar quando tiver tempo):
- `REDIS_URL_OVERRIDE` vs `REDIS_URL` em `app/core/config.py` (api funciona por sorte)
- `ADMIN_PASSWORD` env var no Railway desatualizada (user trocou via `/conta`)

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
- **3.22.0** — Fase 18: **Curadoria via nota no produto + precisão de dados** (v3.3.0). Reformulação total da Fase 18 anterior (descartada — usava snapshot diário). Migration 0012 adiciona em `produtos`: `nota` (0..100), `is_bestseller`, `is_em_alta`, `total_vendidos`, `comissao_fonte` (`ml_painel`/`shopee_api`/`amazon_tabela`/`estimativa`), `comissao_validada`, `preco_atualizado_em`, `comissao_atualizada_em`, `vendidos_atualizado_em` + índice `(org_id, nota DESC)`. **Captura precisa de dados nos 3 scrapers**: (a) `busca_ml.py:_achar_vendidos` parseia "+5 mil vendidos" do card; (b) `linkbuilder_ml.py:_gerar_lote_sync` agora extrai % comissão real da tabela do painel ML e propaga via `_gerar_meli_la_no_driver` → `comissao_fonte="ml_painel"`; (c) `busca_shopee.py` adiciona `historical_sold`/`sold` + marca `is_em_alta=True` + `comissao_fonte="shopee_api"` (comissão já era real); (d) `busca_amazon.py` marca `is_bestseller=True` + usa rank como proxy de vendas (`_rank_para_vendas_estimadas`). Servidor: `app/core/comissoes.py` tem ranges esperados por marketplace (ML 0.5-25%, Shopee 0.5-30%, Amazon 1-12%) usados em `validar_comissao`. `app/services/scoring.py:calcular_nota` é função pura: 30% preço × 40% comissão (zerado se !validada) × 30% vendas. Aplicada em `busca_service._upsert_produto` no ingest. `app/services/curadoria_service.py` faz `listar_top` via query direta — **sem snapshot, sem beat task, live**.
- **3.22.1** — Hotfix preço (v3.3.1): scraper ML pegava preço RISCADO (`<s>`) em vez do promocional. Fix com XPath excluindo descendentes de `<s>`. Tênis Puma de R$269,99 (com 46% OFF) era exibido como R$499,99.
- **3.22.2** — v3.4.0/v3.4.1: captura comissão da barra preta ML DURANTE busca + lockfix `_LOCK_CHROME_ML`. Tabela detalhada `comissoes_ml_categorias.py` (50 categorias) substitui as 8 hardcoded do agente.
- **3.22.3** — v3.4.2/v3.4.3: 3 ping-pongs do fluxo de captura. Fim da iteração: `meli.la → /social/ → clicar "Ir para produto" → barra` (decisão do user na época).
- **3.22.4** — v3.4.4: **hierarquia de `comissao_fonte`** (`_HIERARQUIA_FONTE_COMISSAO` em busca_service.py). Servidor NÃO sobrescreve fonte alta com baixa. Bug raiz: busca rebuscando produto com captura ok salvava estimativa por cima. JS prefere `GANHOS EXTRAS` sobre `GANHOS` base. Doc nova armadilha em CLAUDE.md.
- **3.22.5** — Edição manual de comissão (servidor-only): admin edita em `/produtos/{id}/editar` → `comissao_fonte=manual` (topo da hierarquia, imune a sobrescrita automática). UI label `✏️ manual`. Filtros novos `/produtos`: faixa de comissão (%) e faixa de preço (R$). Coluna "Comissão" na tabela `/produtos` com label da fonte.
- **3.23.0** — Fase 19: **Buscas padrão** (v3.5.0). Lista hardcoded `app/core/buscas_padrao.py` (não tabela DB). Primeira entry: `ml_mais_vendidos_completo` itera 8 categorias × 30 candidatos, gera meli.la, abre cada um pra capturar comissão real, ordena por (preço × comissão_real), top 10. Service `buscas_padrao_service.disparar(slug, org_id)` cria Tarefa(BUSCAR_MERCADO_LIVRE) com `tipo_busca=padrao_mais_vendidos_completo`. UI: seção "⭐ Buscas padrão" no topo de `/buscas` com cards e botão "▶ Rodar agora".
- **3.23.1** — v3.5.1: busca padrão **descarta produtos sem captura real** (não polui DB com estimativa). Mantém só os que tiveram `comissao_fonte=ml_barra_afiliados`. Candidatos aumentado de 20→30 pra compensar descartes.
- **3.24.0** — Fase 20: **Barra de progresso em tempo real no dashboard** (v3.6.0). Migration 0013 (`tarefas.progresso_pct/mensagem/atualizado_em`). WS handler `_h_busca_progresso` persiste DB. Endpoint `GET /api/v1/tarefas/em-progresso`. Card flutuante no dashboard com polling 3s, barra animada (gradient verde, transição CSS suave). Agente helper `ws_progresso.reportar(tarefa_id, pct, msg)` chamado em checkpoints da busca padrão (0% → 12.5% → 25% → ... → 100%).
- **3.24.1** — v3.6.1: botão **"✕ Cancelar"** na barra de progresso. Cancelamento cooperativo via flag global thread-safe (`agent/cancelamento.py`). `dispatcher.cancelar` envia comando WS pro agente + marca CANCELADA. Agente checa flag entre etapas, para gracioso.
- **3.25.0** — Fase 20.1: captura simplificada (v3.7.0). User REVERTEU orientação anterior: agora abrir URL canônica DIRETO (não meli.la → /social/). Chrome do agente está logado como afiliado em `chrome_perfil_ml` → barra preta aparece automática. ~3x mais rápido. **Tempo decorrido** na barra (`⏱ Xmin Ys`) calculado server-side. **`tarefas.duracao_seg`** (migration 0014) preenchido em `_calcular_duracao_seg` quando tarefa termina. Mensagem final "✓ Concluído em Xmin Ys".
- **3.25.1** — v3.7.1: **meli.la gerado em batches incrementais** durante `_processar_categoria` (a cada 10 capturados, não no fim). Check de cancelamento DENTRO do loop (entre produtos, não só entre categorias). Garantia: se cancelar no produto 4 de uma categoria, gera meli.la pros 4 antes de parar — nenhum produto perdido.
- **3.26.0** — Fase 21 (Coluna `produtos.comissao_extra` + busca padrão `ml_comissao_extra`). Migration 0015 adiciona coluna FLOAT NULL (% do bônus GANHOS EXTRAS). `_upsert_produto` lê `item.comissao_extra` e grava respeitando hierarquia de fonte. Nova busca padrão `ml_comissao_extra` itera 8 categorias e mantém só produtos com bônus EXTRAS > 0. `/produtos` ganhou filtro "🎁 Só com bônus EXTRAS" + badge dourado `🎁 +X% extra` na coluna comissão. Servidor commit `dad7fdc`.
- **3.26.1** — v3.8.0 lançado: `_capturar_comissao_e_preco_no_destino` passou a retornar tupla `(efetiva, extra, preco)` separadamente (antes só efetiva). Permite servidor marcar `produtos.comissao_extra` quando agente capturar bônus. Busca padrão completa (`mais_vendidos_completo`) também passou a salvar `comissao_extra` automaticamente quando detecta.
- **3.26.2** — v3.8.1-3.8.4 (4 releases queimadas tentando capturar EXTRAS). v3.8.1 telemetria + diagnóstico em disco. v3.8.2 simplificou JS pra body inteiro. v3.8.3 multi-fontes (body + iframes + outerHTML) + sleep 3s. v3.8.4 **fix definitivo**: user mostrou DevTools — `span.stripe-commission__percentage` + `span.stripe-commission__pillsecond` são os seletores reais. Regex no body falhava porque ML renderiza spans BEM sem whitespace entre tags (`"EXTRAS9%"` → regex com `\s+` não match). Captura via seletor CSS direto. Nova memória `feedback_ml_seletor_stripe.md` + armadilha em CLAUDE.md.
- **3.26.3** — v3.8.5: regra da busca extras mudou de `alvo_total=10` (global) pra `min_por_categoria=3` (sem teto). Visita TODAS as 8 categorias, mantém ≥3 com extras por categoria ou esgota candidatos. Sem limite total.
- **3.27.0** — Fase 22 (Curadoria TOP melhorias). Commit `5427524` servidor-only. Botão "🔄 Atualizar TOP" recarrega lista (query já filtra postados nos últimos 7 dias). Botão 🗑️ excluir por card (POST `/curadoria/top/{id}/excluir` com confirmação dupla, CASCADE limpa nichos + redirects). Limite default 50 → 30. Endpoint aceita `?limite=N` (clamp 10–100).
- **3.27.1** — Fase 22.1 (Buscas padrão Shopee + Amazon). Commit `960459b` servidor-only. 2 novas entradas em `app/core/buscas_padrao.py`: `shopee_mais_vendidos` (50 produtos via API afiliados, ~30s) + `amazon_bestsellers` (50 via SiteStripe, ~3min). Reaproveitam `buscar_shopee`/`buscar_amazon` existentes (zero release agente). Service aceita campo `mensagem_run` custom por busca. Template `buscas.html` ganhou `data-confirm` genérico.
- **3.27.2** — Hotfix cache `/agentes/instalador` (commit `5efde96`). TTL reduzido 5min → 60s + bypass `?nocache=1`. User reportou botão "Atualizar agente" baixando v3.8.3 mesmo após publicar v3.8.4. Cache do redirect estava prendendo URL antiga.
- **3.28.0** — Maratona Shopee captcha v3.8.6 → v3.8.14 (9 releases até estabilizar). Resumo:
  - **v3.8.6**: copiou `_aguardar_com_retry` da Amazon (30s × 3 com reload). Quebrou Shopee — cada `driver.get(URL_PAINEL)` em sessão marcada re-emite captcha → loop infinito. User: "ESSA SESSAO ESTRAGOU O CODIGO".
  - **v3.8.7**: detecção de captcha modal via DOM (URL não muda). + retry agressivo no meio do loop API. Piorou.
  - **v3.8.8**: revertido pro commit `f6f177a` + patch mínimo (`_aguardar_captcha` sem reload).
  - **v3.8.9**: detecção DOM + sleep(30) puro.
  - **v3.8.10**: forçava captcha quando URL fora do painel.
  - **v3.8.11**: status!=200 sempre força + ping inicial + **botão clicável "✅ CAPTCHA RESOLVIDO! Continuar..."**.
  - **v3.8.12**: detecta Chrome fechado durante polling (Selenium WebDriverException).
  - **v3.8.13**: ANALISOU V2 (`ACHADINHOSV2 - FUNCIONAL/src/buscar/shopee.py`) — modelo é **URL only + input() bloqueante + sem retry**. Simplificou pra espelhar V2. 102 linhas removidas.
  - **v3.8.14**: user reforçou "AGUARDAR 30s OBRIGATÓRIOS, independente de qualquer clique ou reload". `_aguardar_captcha` virou literalmente `time.sleep(30)` puro. Sem polling, sem botão de interrupção, sem timeout. Lição registrada: nova memória `feedback_shopee_captcha_no_reload.md` — política de retry da Amazon NÃO se aplica à Shopee.
- **3.29.0** — **Refundação arquitetural per-user** (17/05/2026 noite — Fases A→D + refinamentos). User definiu 3 regras:
  - **(1)** Cliente sempre usa produtos + afiliado do admin central
  - **(2)** Agente é único, capabilities decidem o que ele faz (admin=tudo / afiliado=WA+marketplaces com tag / usuário=só WA)
  - **(3)** Personalizado do cliente vai pra fila admin, processada em até 2h

  **Fase A — Bloquear cadastros do cliente** (commit `c9f932c`): nova property `Usuario.eh_admin_central` (= admin AND org_id == admin_org_id) substitui flags `pode_*` do Plano. Endpoints e UI gateados via essa property. Menu lateral esconde Buscas/Nichos pra non-admin-central.

  **Fase B — Favoritar produtos** (commit `d4c062d`): migration 0016 cria `usuario_produto_personalizado` (M:N). POST `/produtos/{id}/personalizar` + `/despersonalizar`. `/produtos` ganha botão ⭐ Personalizar. `/produtos/personalizados` agora retorna UNION dos solicitados + favoritados.

  **Fase C — Fila admin de solicitações** (commit `5967d39`): migration 0017 cria `solicitacoes_personalizadas`. Cliente cadastra → vai pra fila. Novo `/admin/fila-personalizados` admin processa. Celery beat hourly (`crontab(minute=0)`) processa automaticamente. Hook em `dispatcher.marcar_concluida` lê `payload.solicitacao_id` e atualiza status. Service novo `solicitacao_service.py`.

  **Fase D — Capabilities por user** (commits `e118f10` servidor + `81d1964` agente v3.9.0): novo service `capabilities_service.capabilities_do_agente(agente_id)`. Handshake WS envia `{tipo:"capabilities", capabilities:[...]}`. Agente recebe e armazena em singleton `agent/capabilities.py`. `executar_busca` chama `caps_mod.tem(mkt)` antes de disparar Selenium. `/agentes` mostra badges 🟢/🔒 por marketplace.

- **3.30.0** — **Privacidade per-user em grupos/canais/templates** (17/05/2026 noite tarde):
  - **Grupos** (commit `b756c01`): qualquer user cria/edita/exclui próprios via `proprietario_id`. Listagem `/grupos` filtra por dono (admin central vê tudo).
  - **Templates personalizadas** (commit `f3b6cb8` + migration 0018): nova coluna `templates_mensagem.criado_por_usuario_id`. Renomeado de "Templates" pra "Templates personalizadas" no menu. Lista mostra TODAS da org (cliente PODE usar templates do admin), mas edita só as próprias. Renderização (`selecionar_template`) tem cascata: prefere templates do user; fallback pra qualquer da org se ele não tem.
  - **Canais** (commit `3e5f8b9`): mesmo padrão via `usuario_id`. Tipo readonly na edição (mudar tipo quebra config). Excluir bloqueado se há grupos vinculados.
  - **Postagem só em grupos próprios** (commit `9a7dece`): `selecao_service.grupos_com_nichos` ganha arg `proprietario_id`. `lote_service.postar_produto_imediato` ganha arg `proprietario_grupo_id`. Endpoints (`personalizado_postar`, `curadoria_postar_um`) passam `user.id` pra non-admin-central. Impede user A postar em grupo do user B (mesma org).

- **3.30.1** — Hotfix `lote_service`: faltava `from sqlalchemy import select` no topo (commit `37ce591`). Bug latente desde Fase 17 — `postar_produto_imediato` nunca funcionou de fato. Só apareceu agora porque os callers passaram a fazer POST de produto pela primeira vez (botão ⚡ Postar do TOP e Personalizados).
- **3.30.2** — Fix `personalizado_postar` aceita produtos favoritados (commit `2fe4b41`). Permissão de postar agora é OR: admin_central / criei / minha org / favoritei via UPP. Excluir também ficou inteligente: cliente comum só remove da seleção (UPP), produto continua no catálogo do admin.

- **3.31.0** — **Admin central: visão sistêmica + filtros + paginação** (17/05/2026 noite final):
  - **/usuarios** (commit `798f4a9`): admin central vê TODOS users do sistema (não só sua org). Filtros: papel (admin/afiliado/usuário), busca em login+nome+email, datas DESDE/ATÉ. Coluna nova "Org" mostrando organização de cada um. Coluna "Cadastro" visível pra todos.
  - **Paginação 50/página** em 7 páginas (commits `6459b43` + `c02eca6`): macro reutilizável `templates/_macros/paginacao.html`. Aplicada em `/usuarios`, `/canais`, `/grupos`, `/tarefas`, `/produtos`, `/curadoria/top`, `/templates`. Renderiza `« 1 2 ... N »` com truncamento elegante. Preserva querystring de filtros via `request.query_params`.
  - **Script `scripts/limpar_banco.py`** (commit `e27f1e9`): destrutivo com confirmação `--confirmar APAGAR`. Mantém só admins/super + orgs deles + seeds (planos/nichos/categorias). **Executado em prod (17/05/2026 12:30)** — apagados 105 tarefas, 87 produtos, 5 solicitações, 3 tags afiliado, 2 grupos/canais/agentes/templates, 1 user não-admin, 2 favoritos UPP. Permaneceram 6 admins + 5 orgs.

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
- 0013 tarefas progresso_pct + mensagem + atualizado_em (Fase 20 barra)
- 0014 tarefas duracao_seg (Fase 20.1 tempo total)
- 0015 produtos comissao_extra (Fase 21 — bônus GANHOS EXTRAS ML)
- 0016 usuario_produto_personalizado (Fase B — favoritar M:N)
- 0017 solicitacoes_personalizadas (Fase C — fila admin)
- 0018 templates_mensagem.criado_por_usuario_id (Fase 3.30 — ownership)

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

### Comissão real do ML: abrir URL CANÔNICA direto (revisão 2026-05-16 tarde, v3.7.0)

**Decisão ATUAL do user** (16/05/2026 — REVERTEU a orientação anterior
desta mesma sessão): pra capturar a barra preta de afiliados ML com
GANHOS EXTRAS, abrir a URL canônica do produto DIRETO. Chrome do
agente já está logado como afiliado em `chrome_perfil_ml` (sessão
persistente) → a barra preta aparece automática em qualquer página
de produto que abrir, com a comissão correta incluindo bônus EXTRAS.

```python
def _capturar_comissao_da_barra(driver, url_canonica):
    driver.get(url_canonica)   # ex: produto.mercadolivre.com.br/MLB-...
    time.sleep(1.5)             # ML renderiza barra após DOM load
    pct = driver.execute_script(r"""
        // Prefere "GANHOS EXTRAS X%" sobre "GANHOS X%" base
        var txt = document.body.textContent || '';
        var mE = txt.match(/GANHOS\s+EXTRAS\s+(\d{1,2}(?:[.,]\d{1,2})?)\s*%/i);
        if (mE) return parseFloat(mE[1].replace(',','.'));
        var mB = txt.match(/GANHOS\s+(\d{1,2}(?:[.,]\d{1,2})?)\s*%/i);
        if (mB) return parseFloat(mB[1].replace(',','.'));
        return null;
    """)
    return pct
```

**Por que canônica e não meli.la**:
- Chrome está logado como afiliado → barra mostra contexto correto em
  qualquer página de produto, sem precisar passar pelo shortlink
- ~3x mais rápido: 1 navegação vs 3 (meli.la → social → clicar → produto)
- Mesmo resultado: comissão com EXTRAS já aparece

**Histórico de reviravoltas nesta sessão (não repetir o ping-pong)**:
- v3.4.0: usei URL canônica → user reclamou
- v3.4.1-3: mudei pra meli.la → /social/ → clicar "Ir para produto"
- **v3.7.0**: user observou na prática que canônica funciona perfeito,
  voltamos pra canônica (esta versão atual)

**Pré-requisito CRÍTICO**: Chrome do agente PRECISA estar logado em
`mercadolivre.com.br/afiliados` via `python -m agent.login_ml` —
sessão persistente em `chrome_perfil_ml`. Sem isso, barra não aparece.

**Pra sessões futuras**: se algo falhar (captura volta null, valores
errados, etc), **PERGUNTAR ao user qual o comportamento ATUAL do painel**
antes de mexer no código. User é fonte de verdade — opera o painel
diariamente. Já fui corrigido 3+ vezes neste assunto.


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


### Captura comissão ML — SELETOR CSS, nunca regex no body (v3.8.4+)

Captura da barra preta de afiliados ML SEMPRE via seletor CSS:
- `span.stripe-commission__percentage` → texto do número (ex: "9%")
- `span.stripe-commission__pillsecond`  → presença confirma EXTRAS (bônus). Ausência = só base.

Implementado em:
- `agente/agent/busca_padrao_ml.py:_capturar_comissao_e_preco_no_destino`
- `agente/agent/busca_ml.py:_capturar_comissao_da_barra`

```js
var pctEl   = document.querySelector('span.stripe-commission__percentage');
var pillsec = document.querySelector('span.stripe-commission__pillsecond');
if (pctEl) {
    var n = parseFloat(pctEl.textContent.replace(/[^\d.,]/g, '').replace(',','.'));
    if (pillsec && /EXTRAS/i.test(pillsec.textContent)) {
        melhor.extras = n;   // bônus GANHOS EXTRAS
    } else {
        melhor.base = n;     // comissão base GANHOS
    }
}
```

**LIÇÃO v3.8.0-3 (4 releases queimadas)**: tentei regex `/GANHOS\s+EXTRAS\s+\d/` em
`document.body.textContent`. ML renderiza os spans BEM SEM whitespace entre
tags → `textContent` concatena como `"EXTRAS9%"` (sem espaços). Regex com `\s+`
NÃO dá match. Adicionei iframes, outerHTML, sleep 3s, nada resolvia.

User abriu DevTools em 2026-05-16 e mostrou as classes — fix em ~5min.
**Sempre que captura ML voltar a falhar, pedir DevTools antes de chutar.**

Mantém fallback regex no body com `\s*` (zero ou mais espaços, não `\s+`) só
pra defesa caso ML mude as classes — mas seletor CSS é a fonte primária.


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

Estado atual: **agente v3.9.0 publicado**. Migration head `0018_tpl_cpu`.
- Banco **limpo em 17/05/2026** (script `scripts/limpar_banco.py`): só 6 admins + 5 orgs + seeds
- Arquitetura nova com **3 regras** do user (Fases A→D) — Usuario.eh_admin_central, capabilities por agente, fila admin de personalizados
- **Privacidade per-user** ativa em grupos/canais/templates: listagens filtradas, postagem gateada
- **Paginação 50/página** em 7 rotas principais
- **/usuarios** pra admin central mostra TODOS do sistema com filtros (papel/busca/datas)
- 4 buscas padrão ML+Shopee+Amazon
- Shopee captcha `time.sleep(30)` puro (modelo V2)

**Próximas fases na ordem:**
1. **Validar agente v3.9.0** com user comum (admin central baixa exe + acha que tudo continua, user comum tenta ML → recusa graciosa)
2. **Página `/relatorios`** — histórico de tarefas concluídas com `duracao_seg`, média por tipo, gráfico simples
3. **Servidor-side: evitar tarefas duplicadas** de busca padrão (user clica 4× rodar → 4 tarefas em fila — usar UNIQUE em (status, tipo, payload->slug))
4. **Magalu** (4º marketplace — segue `docs/contrato_busca_marketplace.md`)
5. **AliExpress + TikTok** (após Magalu)
6. **Configurar `ANTHROPIC_API_KEY`** no Railway pra ativar IA dos Personalizados (Claude Haiku 4.5 extrai palavra-chave de link social)

Pedidos do user no fim da sessão (não implementados ainda — anotar):
- Admin central: opções pra editar/excluir/trocar papel de qualquer usuário direto na lista `/usuarios`
- Conceito de "super admin estrela" — quem cria outros admins e tem botão de promoção

Bugs anotados (não bloqueiam, vale fixar quando tiver tempo):
- `REDIS_URL_OVERRIDE` vs `REDIS_URL` em `app/core/config.py` (api funciona por sorte)
- `ADMIN_PASSWORD` env var no Railway desatualizada (user trocou via `/conta`)

---

## 🤖 POLÍTICA OBRIGATÓRIA pra próximas sessões da Claude

**Ao final de qualquer sessão substancial** (≥3 commits OU mudança de
fluxo OU release de agente), SEMPRE atualizar — sem precisar o user
pedir:

1. `docs/sessao_continuacao.md` — estado atual + próximos passos
2. CLAUDE.md "Fases entregues" — adicionar novas versões
3. CLAUDE.md "Migrations atuais" — se tiver migration nova
4. Lista "Próxima fase imediata" — atualizar com o que falta

**Por que essa política**: o user pediu explicitamente em 2026-05-16:
> *"informe esse pedido na documentação para sempre aplicar dessa forma
> nas novas sessões sem a necessidade de eu explicar"*

Documentado em `memory/feedback_documentar_sempre.md`. Tratar como
hábito automático, igual `git status` antes de commit.

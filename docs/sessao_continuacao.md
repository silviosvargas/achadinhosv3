# Continuação da sessão Claude — Achadinhos V3

> **Este arquivo é a fonte de verdade entre sessões.** Quando uma sessão acaba,
> abre nova e diz: *"Lê CLAUDE.md + docs/sessao_continuacao.md + docs/decisoes.md"*.
> Próxima Claude pega do zero sem perder tempo redescobrindo coisas.

**Última atualização:** 2026-05-16 (após hotfix `tipo_busca` + release v3.0.3)
**Versão do agente em prod:** `3.0.3` (publicada como GitHub Release — **busca "Mais vendidos" validada em prod**)
**Migration head:** `0010_busca_tipo_mkt`

---

## ⚡ Estado atual — o que TÁ FUNCIONANDO em produção

| Componente | Status | URL/Detalhe |
|---|---|---|
| API + dashboard | ● Online | https://achadinhos.maisseguidores.ia.br |
| Postgres | ● Online | Railway add-on, projeto `balanced-ambition` |
| Redis | ● Online | Railway add-on |
| Worker (Celery + beat embedded) | ● Online | `--pool=solo`, `railway.worker.json` |
| Agente desktop `.exe` | Released v3.0.3 | [github releases](https://github.com/silviosvargas/achadinhosv3/releases) |
| Signup público | OK | `/signup` cria org + admin com plano free restrito |
| Onboarding wizard | OK | 4 cards adaptativo por plano |
| Pareamento zero-CLI | OK | `/agentes/baixar` → `Conectar meu agente` |
| URL protocol `achadinhos://` | OK | Registrado pelo installer |
| Auto-start no Windows | OK | Run key, agente sobe com Windows |
| Auto-detecção de atualização | OK | Badge amarelo + botão "⬆ Atualizar" |
| Multi-afiliados por user | OK | `/usuarios/{id}/afiliados`, 6 marketplaces na UI |
| Catálogo compartilhado | OK | Plano free vê produtos do admin Achadinhos |
| Página de planos | OK | `/planos` com CTAs de upgrade |
| Trocar senha via UI | OK | `/conta` |
| Encurtador `/r/{slug}` | OK | Redirect 302 com cache + click counter |
| Linkbuilder ML real | OK | Scraping painel ML afiliados gera `meli.la` |
| Buscas multi-tipo UI | OK | `/buscas/nova` com dropdown tipo + checkbox marketplaces |
| Scraper "mais vendidos" ML | OK | 8 categorias hardcoded da V2 |
| Lote com late binding tag | OK | Recalcula URL na hora da postagem |
| Badge "agentes online" | OK | Polling 20s, cookie-aware |

---

## 📜 Tudo que foi entregue nessa sessão (cronológico)

| # | Commit | Fase | O que entrega |
|---|---|---|---|
| 1 | [7826dcb](https://github.com/silviosvargas/achadinhosv3/commit/7826dcb) | Setup | `.claude/settings.json` com allow rules largas pra fluxo contínuo |
| 2 | [c4a7457](https://github.com/silviosvargas/achadinhosv3/commit/c4a7457) | 9.4 | Botão "Conectar meu agente" + JS combo HTTP/protocol/download |
| 3 | [05c82c1](https://github.com/silviosvargas/achadinhosv3/commit/05c82c1) | fix | Ordem de rotas: `/download` antes de `/{agente_id}` |
| 4 | [6e9741c](https://github.com/silviosvargas/achadinhosv3/commit/6e9741c) | 9.5 | Inno Setup installer + GitHub Actions workflow |
| 5 | [e1e94c6](https://github.com/silviosvargas/achadinhosv3/commit/e1e94c6) | 9.6 | URL protocol handler + single-instance handoff |
| 6 | [b67016b](https://github.com/silviosvargas/achadinhosv3/commit/b67016b) | 9.x | Ação real `/abrir-tudo` (`webbrowser.open()`) |
| 7 | [0460407](https://github.com/silviosvargas/achadinhosv3/commit/0460407) | 9.8 | Badge "Agentes online" no header |
| 8 | [3c4786a](https://github.com/silviosvargas/achadinhosv3/commit/3c4786a) | docs | Marca Fase 9 completa |
| 9 | [80255c9](https://github.com/silviosvargas/achadinhosv3/commit/80255c9) | 9.9 | Signup free restrito (flags em `planos`, gates server-side) |
| 10 | [849ecef](https://github.com/silviosvargas/achadinhosv3/commit/849ecef) | 11 | Página `/planos` + CTAs upgrade |
| 11 | [369e4ba](https://github.com/silviosvargas/achadinhosv3/commit/369e4ba) | extra | Catálogo compartilhado (free vê produtos do admin) |
| 12 | [3283e64](https://github.com/silviosvargas/achadinhosv3/commit/3283e64) | fix | `Usuario.organizacao` com `lazy="joined"` (resolve `MissingGreenlet`) |
| 13 | [21405cf](https://github.com/silviosvargas/achadinhosv3/commit/21405cf) | fix | UPSERT em `criar_agente` + índice único partial |
| 14 | [7d8ef0f](https://github.com/silviosvargas/achadinhosv3/commit/7d8ef0f) | fix | Migration revision id ≤ 32 chars |
| 15 | [1b65166](https://github.com/silviosvargas/achadinhosv3/commit/1b65166) | hotfix UX | Cookie auth no `usuario_atual` + UI revamp de `/agentes/baixar` |
| 16 | [a1e8a4b](https://github.com/silviosvargas/achadinhosv3/commit/a1e8a4b) | release | Primeira release oficial v3.0.0 |
| 17 | [185dca7](https://github.com/silviosvargas/achadinhosv3/commit/185dca7) | fix lote | Seed mappings categoria_ml→nicho + backfill |
| 18 | [a0b233b](https://github.com/silviosvargas/achadinhosv3/commit/a0b233b) | fix | Cascata tag ML estendida pra admin org central |
| 19 | [b708a35](https://github.com/silviosvargas/achadinhosv3/commit/b708a35) | conta | Página `/conta` trocar senha |
| 20 | [8991dba](https://github.com/silviosvargas/achadinhosv3/commit/8991dba) | 13 | `usuarios_afiliados` (multi-marketplace) + UI nova |
| 21 | [cdebcbc](https://github.com/silviosvargas/achadinhosv3/commit/cdebcbc) | fix lote | Late binding da tag (recalcula URL na postagem) |
| 22 | [a15ec64](https://github.com/silviosvargas/achadinhosv3/commit/a15ec64) | 14 | Encurtador próprio `/r/{slug}` |
| 23 | [8f1907f](https://github.com/silviosvargas/achadinhosv3/commit/8f1907f) | 15 | Linkbuilder ML real (scraping painel afiliados → `meli.la`) |
| 24 | [6b757c5](https://github.com/silviosvargas/achadinhosv3/commit/6b757c5) | extra | Detecção agente desatualizado + botão Atualizar |
| 25 | [f97f963](https://github.com/silviosvargas/achadinhosv3/commit/f97f963) | release | Bump v3.0.1 + tag (release publicada) |
| 26 | [07bf80d](https://github.com/silviosvargas/achadinhosv3/commit/07bf80d) | 16.1+16.2 | UI multi-marketplace `/buscas/nova` + schema |
| 27 | [968e614](https://github.com/silviosvargas/achadinhosv3/commit/968e614) | 16.3 | Scraper ML `mais_vendidos` (8 categorias) |
| 28 | [a8f835a](https://github.com/silviosvargas/achadinhosv3/commit/a8f835a) | release | Bump v3.0.2 + tag |
| 29 | [ba876e5](https://github.com/silviosvargas/achadinhosv3/commit/ba876e5) | docs | Consolidação completa pra próxima sessão |
| 30 | [03f63d1](https://github.com/silviosvargas/achadinhosv3/commit/03f63d1) | hotfix | Conflito chave `tipo` no payload WS → renomeia pra `tipo_busca` + bump v3.0.3 |

**Total:** 30 commits + 4 releases (v3.0.0, v3.0.1, v3.0.2, v3.0.3).
**Busca "Mais vendidos" ML validada em prod com a v3.0.3.**

---

## 🗄️ Modelos de dados críticos (schema)

### Multi-tenancy
- `organizacoes` — 1 por signup público, contém `plano_id` (FK), `slug`, `nome`
- `planos` — 3 entries fixas: `free` (id=1), `pro` (id=2), `business` (id=3)
  - Flags: `pode_cadastrar_afiliado`, `pode_criar_buscas`, `pode_criar_produto_proprio` (Fase 9.9)
  - Free = todas false. Pro/Business = todas true.
- `usuarios` — `org_id` FK, `papel` ∈ {admin, usuario, super, afiliado}. Quem cria org via signup vira `admin` da própria org.
- **`settings.admin_org_id = 1`** (env var override `ADMIN_ORG_ID`) — org "Achadinhos" cujo catálogo é compartilhado pra plano free.

### Afiliados (Fase 13)
- `usuarios_afiliados (id, usuario_id, plataforma, tag, UNIQUE(user, plat))`
- Plataformas suportadas: `ml`, `shopee`, `amazon`, `magalu`, `aliexpress`, `tiktok` (em `app/core/marketplaces.py`)
- Hoje só `ml` tem scraper ativo; resto é "em prep" na UI
- **`usuarios.afiliado_ml` (legacy)** ainda existe pra dual-read (vai sumir em migration futura)

### Produtos
- `produtos (id, org_id, plataforma, item_id, nome, preco, categoria, url_canonica, url_afiliado, comissao, ...)`
- `produto_nichos (produto_id, nicho_id)` — auto-classificado no ingest via `nicho_categoria_ml`
- Visibilidade ADR-008: `usuario_dono_id IS NULL` = público da org; `NOT NULL` = privado de afiliado

### Buscas (Fase 16)
- `buscas_ml` (apesar do nome, agora é multi-marketplace via campo `marketplaces`)
- Novos campos: `tipo` (`termo_livre`/`por_url`/`mais_vendidos`/`melhor_comissao`/`em_alta`), `marketplaces` (JSON array)

### Encurtador (Fase 14)
- `redirects (id, slug UNIQUE, produto_id UNIQUE FK, url_destino, total_clicks, ...)`
- 1 row por produto. URL atualiza quando tag/meli.la muda; slug fica.

### Pareamento + tarefas
- `agentes (org_id, usuario_id, nome)` — índice único partial `(org_id, usuario_id, nome) WHERE ativo=true`
- `tarefas` — `tipo` ∈ `TipoTarefa` enum (`POSTAR_WHATSAPP`, `BUSCAR_MERCADO_LIVRE`, `GERAR_LINK`, etc.)

---

## 🔄 Fluxos críticos (resumo)

### 1. Signup → onboarding → primeiro post

```
/signup → cria org nova + user admin (plano free)
   ↓ redirect /onboarding
4 cards: afiliado (escondido se free) / agente / canal / grupo
   ↓ /agentes/baixar
JS detecta /ping em 127.0.0.1:5577
   ├─ Não rodando: botão "Baixar agente" → /agentes/instalador → 302 GitHub release
   ├─ Rodando sem token: botão "Conectar" → POST /pair com JWT da sessão
   └─ Rodando pareado: botão "Abrir minhas plataformas" → POST /abrir-tudo
       → webbrowser.open(WhatsApp Web, ML)
   ↓
/lote → "Rodar agora"
   ↓ produtos_elegiveis (cataloga admin + own org se free)
   ↓ pra cada combinação produto×grupo:
       _url_pro_produto: prioriza p.url_afiliado=meli.la se existe;
                          senão linkbuilder genérico + /r/{slug}
   ↓ enfileira tarefa POSTAR_WHATSAPP
   ↓ agente recebe via WS, posta no grupo
```

### 2. Cascata da tag de afiliado (Fase 13/15)

`afiliado_service.tag_com_cascata(plataforma, usuario_id, org_id)`:

1. `usuarios_afiliados WHERE usuario_id AND plataforma` (+ dual-read `usuarios.afiliado_ml` se ml)
2. Tag do admin da org do user (mesmo lookup, recursivo)
3. Tag do admin da org `settings.admin_org_id` (org Achadinhos)
4. `settings.{plat}_affiliate_id` (env var global)
5. None → linkbuilder devolve URL canônica crua

### 3. Linkbuilder ML real (Fase 15)

```
busca_service.ingerir_produtos termina
   ↓ filtra produtos ML que ainda não têm url_afiliado=meli.la
   ↓ cria Tarefa(GERAR_LINK, payload={urls: [...]}, agente_id=...)
   ↓ dispatcher manda comando WS "gerar_links_afiliado_ml"
agente recebe:
   ↓ linkbuilder_ml.gerar_links_em_lote() abre Chrome ML (perfil persistente)
   ↓ navega pra mercadolivre.com.br/afiliados/linkbuilder
   ↓ cola URLs em lotes de 10, captura meli.la/XXX via regex
   ↓ retorna {ok: true, mapping: {url_canonica: meli.la}}
servidor recebe tarefa_concluida:
   ↓ afiliado_ml_writer.aplicar_mapping atualiza produtos.url_afiliado + redirects.url_destino
```

### 4. Busca "Mais vendidos" (Fase 16.3)

```
/buscas/nova com tipo=mais_vendidos, marketplaces=[ml]
   ↓ POST /buscas/nova salva BuscaML(tipo, marketplaces='["ml"]')
   ↓ "▶ Rodar" enfileira Tarefa(BUSCAR_MERCADO_LIVRE, payload={tipo, marketplaces, ...})
agente recebe:
   ↓ executar_busca roteia por msg.tipo
   ↓ _varrer_mais_vendidos_sync itera 8 categorias hardcoded
       (Roupas, Esportes, Beleza, Bebês, Casa, Eletrônicos, Informática, Ferramentas)
   ↓ extrai cards (reusa _extrair_cards_da_pagina)
   ↓ enriquece com categoria + comissão estimada
   ↓ POST /api/v1/produtos/ingest
servidor:
   ↓ upsert produtos
   ↓ auto-classifica nicho via nicho_categoria_ml (mappings da Fase 9.9)
   ↓ enfileira tarefa GERAR_LINK (Fase 15) pra gerar meli.la
```

---

## 🔧 Configurações persistentes (não perder)

### Railway prod (projeto `balanced-ambition`)

| Var | Valor / origem |
|---|---|
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` (private) |
| `REDIS_URL_OVERRIDE` | `${{Redis.REDIS_URL}}` (note: usa `_OVERRIDE`, não `REDIS_URL` direto) |
| `JWT_SECRET` | `CErhxiY2Ui45JacD...` (no gerenciador de senhas) |
| `CREDENCIAIS_SECRET_KEY` | `piogTQGMY8VjdFbsy...` |
| `ADMIN_LOGIN` | `admin` |
| `ADMIN_PASSWORD` | `IzT9V7c5J6dp7Eft7lwD` ⚠️ **DESATUALIZADO** — user trocou via `/conta` na sessão atual. Atualizar quando puder. |
| `PUBLIC_BASE_URL` | `https://achadinhos.maisseguidores.ia.br` |
| `ADMIN_ORG_ID` | (default 1, não precisa setar) |

### DNS Cloudflare
- CNAME `achadinhos` → `jv7fcipn.up.railway.app` (DNS only/cinza)
- TXT `_railway-verify.achadinhos` = `railway-verify=ddf75203a7563d...`
- **Proxy laranja não ativado** (deixa cinza pra não complicar SSL)

### Worker no Railway
- `railway.worker.json` (no repo) com `--pool=solo` (RAM low Free plan)
- Beat embedded (`--beat` flag) — sem beat service separado
- Schedule: `agendar_buscas_devidas` crontab "*/1 * * * *"

### Agente local — paths importantes
- Config: `%APPDATA%\Achadinhos\config.json` (token JWT + servidor_ws)
- Chrome perfil WhatsApp: `%APPDATA%\Achadinhos\chrome_perfil`
- Chrome perfil ML (dedicado): `%APPDATA%\Achadinhos\chrome_perfil_ml`
- Token de admin Railway (CLI): `RAILWAY_API_TOKEN` env var (`7fcbeb71-e1a6-4a7b-bcfd-8b7de440826c`)

### GitHub Actions
- Workflow `release-agente` disparado por tag `agente-v*` (push) ou `workflow_dispatch`
- Roda em `windows-latest`, PyInstaller + Inno Setup
- Asset publicado: `AchadinhosAgent-Setup-X.Y.Z.exe`

---

## ⚠️ Limitações conhecidas (não bater a cabeça em vão)

### Cosméticas
- URL postada pelo lote ainda inclui fragment `#polycard_client=...` do scraping ML (lixo). Não afeta funcionalidade.
- Backfill da Fase 7 (mappings categoria_ml→nicho na org admin) cobre só ~21 categorias raiz comuns. Categorias menos comuns ficam sem nicho.

### Bugs anotados mas não corrigidos
- **`REDIS_URL_OVERRIDE` vs `REDIS_URL`**: `app/core/config.py:64` só lê `REDIS_URL_OVERRIDE`. Worker tá certo, api funciona por sorte (lazy WS). Ajustar pra aceitar ambas.
- **Senha admin no Railway Variables**: foi trocada via `/conta` mas `ADMIN_PASSWORD` env var ficou com valor antigo. Cosmético (só usado em CREATE inicial).

### Limitações estruturais
- **ML afiliados não tem API pública** pra gerar shortlinks → scraping é a única forma (Fase 15). Frágil a mudanças no HTML.
- **Outros marketplaces (Shopee, Amazon, Magalu, AliExpress, TikTok)**: UI tem checkbox mas backend só ML tem scraper. Próxima rodada implementa.
- **Build do `.exe` no PyInstaller**: roda só no Windows runner. Mac/Linux ficam pra futuro.

---

## 🎯 Próximas fases planejadas (em ordem)

### Fase 16.4 — Busca personalizada (`por_url`)
**O que falta:** quando user cola URL de produto específico, agente abre, extrai dados (nome, preço, foto, categoria), gera afiliado ML, salva como produto privado.
- V2 tem: `src/buscar_palavra/extrator_link.py` (com detecção de plataforma por domínio)
- Local: criar função `_buscar_por_url_sync(driver, url)` em `agente/agent/busca_ml.py`
- Rotear em `executar_busca` quando `tipo == 'por_url'`

### Fase 16.5+ — Scrapers outros marketplaces
- **Shopee** (mais fácil): API interna `affiliate.shopee.com.br/api/v3/offer/product/list` retorna `long_link` afiliado pronto. Porta de V2 `src/buscar/shopee.py`.
- **Amazon**: SiteStripe (lento, 1 ASIN por vez) OU PA-API (precisa credenciais). Porta de V2 `src/buscar/amazon.py`.
- **Magalu, AliExpress, TikTok**: ainda menos infraestrutura. Provavelmente só scraping + tag em query param. Roadmap futuro.

### Fase 17 — Curadoria automatizada TOP 50
- Celery beat task diária roda buscas em todos marketplaces
- `curadoria_service.ranquear()` calcula score = `(preço × comissão%) × peso_vendas × peso_em_alta`
- Top 50 viram destaque no `/dashboard`
- V2 tem: `src/super_produtos/ranqueador.py` pra portar

### Fase 18 — Métricas no dashboard
- Page `/metrics` com gráficos: clicks por produto (do encurtador), postagens/dia, conversão
- Tem dados base no `redirects.total_clicks` (Fase 14) e `postagens` table

### Pendentes menores
- Smoke test e2e Telegram (precisa user criar bot @BotFather)
- Cloudflare proxy ON (SSL mode Full)
- Limpar fragment `#polycard_client=...` do scraping ML

---

## 🚀 Como começar a próxima sessão

### Prompt sugerido pra primeira mensagem
> *"Estou continuando o Achadinhos V3. Leia esses 3 arquivos NA ORDEM:
> 1. CLAUDE.md (overview e fases entregues)
> 2. docs/sessao_continuacao.md (estado atual + tudo entregue + próximas fases)
> 3. docs/decisoes.md (ADRs, especialmente ADR-009 sobre Fase 9)
>
> O agente está em v3.0.3, release publicada no GitHub. Pipeline completo
> funciona: signup → onboarding → instalar `.exe` → conectar → busca → lote.
> A busca tipo "Mais vendidos" ML (8 categorias hardcoded) foi validada
> em prod — funciona end-to-end com 50 produtos importados, auto-classificados
> por nicho, com shortlinks `meli.la` gerados pelo agente.
>
> Próximo passo planejado é Fase 16.4 — busca personalizada por URL/link.
> A V2 (em `D:\ACHADINHOSV2 - FUNCIONAL\`) tem código pra portar
> (`src/buscar_palavra/extrator_link.py` faz detecção de plataforma por
> domínio + extração de dados).
>
> Aguarde minha confirmação antes de implementar qualquer coisa."*

### Coisas que próxima Claude PRECISA saber
1. **Não tente login admin com `IzT9V7c5J6dp7Eft7lwD`** — user trocou pela UI `/conta`. Pede a nova se precisar fazer smoke test.
2. **Railway API token está em env var `RAILWAY_API_TOKEN`** quando precisar mexer em prod (`7fcbeb71-...`). Token de longa duração, válido por meses.
3. **Agente já tá pareado** (HP_SILVIO, agente_id=1). Não rode `agent.setup` de novo — apaga config dele.
4. **`.exe` em prod é v3.0.3** mas o user pode ainda estar com versão antiga instalada. Sempre confere via `/api/v1/agentes/versao-atual` se em dúvida.
5. **Worktree atual**: `D:\ACHADINHOSV3\.claude\worktrees\youthful-mendel-0e879a\` — branch `claude/youthful-mendel-0e879a`. Push vai pra `main` direto (mapeamento `:main`).
6. **Pareamento exige sessão ML logada no painel afiliados** pra linkbuilder funcionar. Se scraping volta vazio, é sessão expirada — user precisa relogar.
7. **Permissões liberadas** em `.claude/settings.json` — pode rodar comandos sem prompts pra `railway *`, `python -m agent.*`, `git *`, `curl *` pra hosts do projeto, `docker run --rm postgres:18`, etc.
8. **⚠️ Cuidado com chave `tipo` em payload WS** (lição aprendida — hotfix v3.0.3): `dispatcher._tentar_entrega` monta msg via `{"tipo": comando_ws, **tarefa.payload}`. Se a busca/tarefa tiver um campo `tipo` no payload, o spread `**` SOBRESCREVE o comando WS de cima. Resultado: agente recebe tipo errado e cai em `ws.tipo_sem_handler`. Solução: usar `tipo_busca` (ou qualquer chave que não seja `tipo`) dentro do payload. Vale pra qualquer feature nova que enfileire tarefa.

### Arquivos importantes pra ler em ordem
1. `CLAUDE.md` — overview + fases marcadas
2. `docs/sessao_continuacao.md` — este arquivo
3. `docs/decisoes.md` — ADRs (especialmente ADR-009 sobre Fase 9)
4. `docs/protocolo_agente.md` — contrato WS cloud↔agente
5. `app/services/lote_service.py` — entender pipeline lote (entrega final)
6. `agente/agent/busca_ml.py` — scraper ML (Fase 16.3 adicionou)

### Comandos úteis pra dev local
```powershell
# Servidor (Railway redeploy automático via push pra main)
git status
git log --oneline -10
git push origin claude/youthful-mendel-0e879a:main   # NOTA: empurra worktree pra main

# Agente local em modo dev
cd D:\ACHADINHOSV3\agente
.venv\Scripts\activate
python -m agent.main --sem-tray

# Build .exe local
cd D:\ACHADINHOSV3\agente
.venv\Scripts\activate
pyinstaller --noconfirm --clean build.spec
# → dist/AchadinhosAgent.exe (~30 MB)

# Rodar release nova (bump + tag + GitHub Actions builda)
# 1. Editar versão em 3 arquivos:
#    - agente/agent/local_server.py    → VERSAO_AGENTE = "X.Y.Z"
#    - agente/pyproject.toml           → version = "X.Y.Z"
#    - agente/installer.iss            → MyAppVersion "X.Y.Z"
# 2. Commit + tag + push:
git commit -am "chore(agente): bump X.Y.W → X.Y.Z"
git push origin claude/youthful-mendel-0e879a:main
git tag -a agente-vX.Y.Z -m "..."
git push origin agente-vX.Y.Z
# 3. GitHub Actions roda em ~3min, publica release com installer
```

### Comandos pra acessar DB de prod (via Railway TCP proxy)
```bash
# Pegar URL pública do Postgres via GraphQL Railway
curl -X POST https://backboard.railway.com/graphql/v2 \
  -H "Authorization: Bearer $RAILWAY_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query":"{ variables(projectId:\"79493ebd-19bf-4466-a295-330a0d0c0a86\",environmentId:\"a9e2eb06-e881-4664-973b-fbd816ef65c3\",sid:\"cedfcff3-c832-4db9-8829-b5fecef0fc1d\") }"}'
# Resposta tem DATABASE_PUBLIC_URL

# psql via Docker (não precisa instalar psql)
docker run --rm postgres:18 psql "$DATABASE_PUBLIC_URL" -c "SELECT ..."
```

---

## 🗂️ Arquitetura — arquivos críticos por feature

| Feature | Arquivos |
|---|---|
| Pareamento | `agente/agent/main.py`, `agente/agent/local_server.py` |
| URL protocol | `agente/agent/main.py` (`_handoff_uri_pra_instancia_rodando`) |
| Linkbuilder ML | `agente/agent/linkbuilder_ml.py`, `app/services/afiliado_ml_writer.py` |
| Cascata afiliado | `app/services/afiliado_service.py` (`tag_com_cascata`) |
| Lote | `app/services/lote_service.py` (`_url_pro_produto`) |
| Encurtador | `app/web/routes.py` (`/r/{slug}`), `app/services/redirect_service.py` |
| Busca multi-tipo | `agente/agent/busca_ml.py` (`executar_busca`, `_varrer_mais_vendidos_sync`) |
| UI buscas | `app/web/templates/busca_form.html` |
| Afiliados UI | `app/web/templates/usuario_afiliados.html` |
| Onboarding | `app/web/templates/onboarding.html` |
| Marketplaces const | `app/core/marketplaces.py` |
| Status agente | `app/api/v1/endpoints/agentes.py` (`/status`, `/versao-atual`) |
| Permissions Claude | `.claude/settings.json` (rules largas pra fluxo contínuo) |

---

## 🎯 Foco recomendado pra próxima sessão

Em ordem de prioridade:

1. **Fase 16.4 — busca por URL** (rápido, alto valor): admin cola link, sistema importa 1 produto + gera afiliado. ~1 sessão.
2. **Scraper Shopee** (Fase 16.5a): a V2 já tem código com API interna que retorna `long_link` pronto. Mais fácil que ML. ~1 sessão.
3. **Fase 17 curadoria automatizada**: Celery beat diário que mantém top 50 sempre frescos. ~1-2 sessões.
4. **Limpar bug `REDIS_URL_OVERRIDE`** (fix técnico): ~15 min.

Tudo o que está em "Próximas fases planejadas" acima é executável — V2 tem código pra portar quando preciso (`D:\ACHADINHOSV2 - FUNCIONAL\`).

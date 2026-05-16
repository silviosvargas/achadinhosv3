# Continuação da sessão Claude — Achadinhos V3

> **Este arquivo é a fonte de verdade entre sessões.** Quando uma sessão acaba,
> abre nova e diz: *"Lê CLAUDE.md + docs/sessao_continuacao.md + docs/decisoes.md"*.
> Próxima Claude pega do zero sem perder tempo redescobrindo coisas.

**Última atualização:** 2026-05-17 (Fase 18 Curadoria via nota + precisão de dados)
**Versão do agente:** `3.3.0` (em código; release ainda não publicada — testar local antes)
**Versão em prod:** `3.2.2` (anterior — pré-Fase 18)
**Migration head:** `0012_curadoria`

## 🔥 LEITURA OBRIGATÓRIA antes de mexer em busca/linkbuilder

1. [docs/contrato_handlers_ws.md](contrato_handlers_ws.md) — **handlers WS PRECISAM
   retornar `"ok": True/False`**. Sem isso, ws_client envia `tarefa_falhou` e
   servidor nunca chama hooks de pós-conclusão. Bug ficou escondido 5 meses.

## 📦 LEITURA OBRIGATÓRIA antes de mexer em `agente/`

Toda mudança em `agente/agent/*.py` PRECISA de release nova (`.exe`) — user
roda o installer no PC, não faz `git pull`. Sem release, servidor já tem
schema novo mas agente continua antigo → dados descartados silenciosamente.

**Procedimento completo em [CLAUDE.md → "Workflow de release do agente"](../CLAUDE.md)**.
Resumo:

1. Bump 3 arquivos sempre juntos: `local_server.py:VERSAO_AGENTE`,
   `pyproject.toml:version`, `installer.iss:#define MyAppVersion`
2. Commit + push `:main` (servidor) + tag `agente-vX.Y.Z` + push da tag
3. **MONITORAR workflow até `completed/success`** (curl GitHub API ou
   background script). Sem monitoramento, release pode ter falhado e
   user fica com `.exe` antigo sem aviso.
4. Conferir asset `AchadinhosAgent-Setup-X.Y.Z.exe` publicado em
   `github.com/silviosvargas/achadinhosv3/releases/tag/agente-vX.Y.Z`
5. SÓ ENTÃO comunicar pro user instalar + validar com busca real
   do tipo afetado pela mudança

Quando muda servidor + agente: **servidor primeiro** (Railway redeploy
+ migration), **agente depois** (release). Inverter quebra silencioso
(Pydantic `extra="allow"` aceita campos novos mas servidor não tem
coluna pra gravar).
2. **[docs/contrato_busca_marketplace.md](contrato_busca_marketplace.md)** — guia
   pra adicionar marketplace novo (Magalu/AliExpress/TikTok). Checklist completo
   + template de código + padrão de modo interativo (banner Chrome + aviso
   dashboard + retry 30s × 3 pra captcha/login). **Padrão usado em ML/Shopee/Amazon.**
3. [docs/sessao_2026-05-15-16.md](sessao_2026-05-15-16.md) — sessão completa
   2026-05-15→16 com cada alteração funcional, em ordem cronológica + lições.
4. Seção "Armadilhas conhecidas" no [CLAUDE.md](../CLAUDE.md) — 5 armadilhas
   já pisadas e como evitar.

## ✅ Marketplaces operacionais (validados em prod)

| Marketplace | Versão | Estratégia | Link de afiliado | Login |
|---|---|---|---|---|
| **🛒 Mercado Livre** | v3.0.10+ | Scraping + linkbuilder painel ML | `meli.la/XXX` | `agent.login_ml` |
| **🛍️ Shopee** | v3.1.2+ | API interna `/api/v3/offer/product/list` | `long_link` direto API | `agent.login_shopee` |
| **📦 Amazon** | v3.2.1+ | Scraping `/gp/bestsellers/` + SiteStripe | `amzn.to/XXX` (fallback `?tag=`) | `agent.login_amazon` |

Modo interativo (banner Chrome + aviso dashboard) universal nos 3 — 30s×3 retry.

## 🏆 Curadoria via nota + precisão de dados (Fase 18 — v3.22 / agente 3.3.0)

**A nota é calculada NO INGEST** (`busca_service._upsert_produto`). Os
endpoints de TOP apenas filtram `produtos.nota >= 30 ORDER BY nota DESC`.
Sem snapshot, sem beat task — live.

### Fórmula `app/services/scoring.py:calcular_nota` (0..100)

```
score_preco    = 0..30  (desconto: ≥30% → 30; 15-29% → 20; 1-14% → 10; 0% → 0)
score_comissao = 0..40  (comissao/15 × 40, cap 40; **ZERO se !validada**)
score_vendas   = 0..30  (bestseller → +20; em_alta → +10;
                         total_vendidos: log10(v+1) × 10, cap 30)
```

### Captura precisa por marketplace

| Marketplace | Comissão real | Total vendido | Implementação |
|---|---|---|---|
| **🛒 ML** | Painel linkbuilder mostra % por URL → `linkbuilder_ml._gerar_lote_sync` extrai e propaga via `_gerar_meli_la_no_driver` → `comissao_fonte="ml_painel"` | `_achar_vendidos(card)` parseia "+5 mil vendidos" do card | busca_ml.py + linkbuilder_ml.py |
| **🛍️ Shopee** | API `seller_commission_rate` (já existia) | API `historical_sold`/`sold` | busca_shopee.py |
| **📦 Amazon** | Tabela oficial por categoria (3-12%) → `comissao_fonte="amazon_tabela"` | Proxy de rank no `/gp/bestsellers/` (rank 1 = 5000 vendas, rank 50 = 100) | busca_amazon.py |

### Validação de comissão

`app/core/comissoes.py:validar_comissao(plat, pct)` confere ranges:
- ML: 0.5–25%
- Shopee: 0.5–30%
- Amazon: 1–12%

Fora do range → `comissao_validada = False` → `score_comissao = 0` (produto
sai do TOP automaticamente). Útil pra detectar:
- Painel ML pifou (retornou 0% por sessão expirada)
- Bug no scraper (parseou unidade errada)

### Tools admin

`POST /api/v1/curadoria/recalcular-notas` — re-aplica `calcular_nota` em
todos produtos da org. Necessário: (1) primeira vez após deploy (produtos
antigos têm nota=0); (2) ao mudar pesos da fórmula.

`POST /api/v1/curadoria/revalidar-comissoes` — passa todas comissões pela
validação de range + recalcula notas. Útil quando suspeita de batch ruim.

### UI

- `/curadoria/top` — grid com badge ⭐ N/100 + breakdown (preço, comissão
  com fonte e ✓/⚠ de validação, vendas, idade do preço)
- Sidebar grupo Catálogo: item **🏆 Top por nota**
- Dashboard: mini-grid de 6 cards "🏆 TOP por nota" + link "Ver todos"

### 3 timestamps específicos (cada campo separadamente)

- `preco_atualizado_em` — só muda quando preço efetivamente mudou
- `comissao_atualizada_em` — idem
- `vendidos_atualizado_em` — idem

Diferente do `atualizado_em` (TimestampMixin, muda em qualquer UPDATE).
UI mostra "preço de 3 dias atrás" mesmo quando produto sofreu outro update
mais recente (ex: re-tag de afiliado).

## 🛍️ Produtos Personalizados (Fase 17 — v3.21+)

Página `/produtos/personalizados` permite qualquer user cadastrar produtos
manualmente. **Validado em prod com `meli.la` salvando corretamente.**

| Quem cadastra | Visibilidade do produto | Quem posta |
|---|---|---|
| Admin | Público (`usuario_dono_id=NULL`) | Admin com tag central |
| Usuário comum | Público | Admin com tag central |
| Afiliado COM tag ML | Privado (`usuario_dono_id=afiliado.id`) | **SÓ o afiliado** |
| Afiliado SEM tag | Público | Admin com tag central |

**3 modos de entrada**:
1. Palavra-chave → busca termo_livre ML (limit 10)
2. Link de marketplace (ML/Shopee/Amazon) → busca por_url
3. Link de social (TikTok/Insta/YT) + IA → Claude infere palavra-chave
   (requer `ANTHROPIC_API_KEY` no Railway)

Função dedicada `lote_service.postar_produto_imediato(produto_id, ...)`
pro botão "⚡ Postar" individual — não passa pelo `rodar_lote` (que é genérico).

## 🐛 Bugs críticos resolvidos nesta sessão (2026-05-16 noite)

1. **`IngestProdutoItem` Pydantic descartava `url_afiliado` silenciosamente** —
   schema não declarava o campo, `extra="ignore"` (default) cortava do payload.
   5 meses de bug. Fix: declarei `url_afiliado` + `comissao` + `extra="allow"`.
2. **MLB legacy 4-7 dígitos rejeitado** — regex `\d{8,15}` → `\d{4,15}`.
3. **`/postar` 500** — função dedicada em vez de `rodar_lote`.
4. **`dados_insuficientes` em ML por_url** — espera explícita do DOM
   (h1/ld_json/og_title), scroll progressivo, 5 seletores de preço em cascata,
   diagnóstico em disco quando falha.

---

## 🛑 LEIA ANTES DE TUDO (instrução pra Claude de nova sessão)

**Sistema 100% funcional. NÃO mexa em nada ao abrir esta sessão.**

Sua primeira tarefa é:
1. Ler este documento inteiro + `CLAUDE.md` + `docs/decisoes.md`
2. Apresentar pro user um **menu de opções** de continuação (ver seção "Foco recomendado" no fim deste doc)
3. **Aguardar a escolha do user** antes de qualquer ação

❌ NÃO rode comandos · NÃO edite código · NÃO faça smoke tests · NÃO crie arquivos
✅ Apenas leia, sintetize, apresente opções, aguarde

A última ação validada foi a busca "Mais vendidos" ML rodando em prod com sucesso
(50 produtos importados, classificados por nicho, com `meli.la` gerados). User
confirmou funcionamento por screenshot. Não há nada quebrado pra consertar.

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
| Busca por URL/link (Fase 16.4) | OK | 1 produto ML extraído via cascata JSON-LD → OG → CSS |
| Busca termo_livre (paginação) | OK | `_varrer_termo_livre_sync` — listagem ML por palavra-chave |
| Busca melhor_comissao | OK | Top 4 categorias por comissão DESC (Roupas/Esportes/Beleza) |
| Busca em_alta | OK | `/ofertas` ML (promoções relâmpago) |
| Linkbuilder INLINE | OK | meli.la gerado no MESMO driver da busca (igual V2) |
| `url_afiliado` no DB | OK | `https://meli.la/XXX` salvo desde v3.0.10 (bug raiz resolvido) |
| CRUD produtos UI | OK | Editar / Excluir individual + Apagar todos (confirm tripla) |
| Botão Ver produto | OK | Abre `url_afiliado` em nova aba |
| Botão Regenerar meli.la | OK | Re-enfileira GERAR_LINK pros pendentes |
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
| 31 | [2eb403a](https://github.com/silviosvargas/achadinhosv3/commit/2eb403a) | 16.4 | Fase 16.4 — busca personalizada por URL/link (extrator de produto único ML) + fix scheduler tipo_busca/marketplaces + bump v3.0.4 |
| 32 | [a0b7d61](https://github.com/silviosvargas/achadinhosv3/commit/a0b7d61) | hotfix | Dispatcher defensivo: tipo/tarefa_id sempre ganham no spread WS |
| 33 | [9be0771](https://github.com/silviosvargas/achadinhosv3/commit/9be0771) | 16.5 | Fase 16.5 parcial: 1 handler dedicado por tipo de busca ML (v3.0.5) |
| 34 | [a9052e4](https://github.com/silviosvargas/achadinhosv3/commit/a9052e4) | crud | Produtos: editar + excluir individual + apagar todos (confirm tripla) |
| 35 | [c9deb55](https://github.com/silviosvargas/achadinhosv3/commit/c9deb55) | ui | Botão "Ver produto" abre link de afiliado em nova aba |
| 36 | [b81e1f0](https://github.com/silviosvargas/achadinhosv3/commit/b81e1f0) | fix | Campo `link_produto`→`url_canonica` no ingest + endpoint regenerar-meli-la |
| 37 | [437b55b](https://github.com/silviosvargas/achadinhosv3/commit/437b55b) | fix | Linkbuilder: normaliza URL no agente + match flexível no servidor (v3.0.6) |
| 38 | [6ce561c](https://github.com/silviosvargas/achadinhosv3/commit/6ce561c) | fix | Cache versao-atual 5min → 60s + bypass via ?nocache=1 |
| 39 | [6aedcd1](https://github.com/silviosvargas/achadinhosv3/commit/6aedcd1) | fix | 4 fixes do log: dispatcher só PENDENTE, regex MLBU, URL limpa ingest, lock Chrome (v3.0.7) |
| 40 | [da30287](https://github.com/silviosvargas/achadinhosv3/commit/da30287) | fix | Limpeza URL na extração do card (igual V2) (v3.0.8) |
| 41 | [d228826](https://github.com/silviosvargas/achadinhosv3/commit/d228826) | feat | Linkbuilder INLINE no agente (igual V2 — mesmo driver) (v3.0.9) |
| 42 | [1a76992](https://github.com/silviosvargas/achadinhosv3/commit/1a76992) | **fix** | **BUG RAIZ: handler_gerar_links_ml retornava sem `ok=True` (v3.0.10)** |

**Total:** 42 commits + 11 releases publicadas (v3.0.0 … v3.0.10).
**Busca "Mais vendidos" + linkbuilder + `meli.la` no DB validado em prod com v3.0.10.**

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

### ~~Fase 16.4 — Busca personalizada (`por_url`)~~ ✅ ENTREGUE (v3.0.4)
Implementado em `agente/agent/busca_ml.py`:
- `_varrer_produto_unico_sync(cfg, url)` abre URL ML, extrai dados via cascata
  JSON-LD Product → OpenGraph meta tags → CSS direto
- Roteamento em `executar_busca` quando `tipo_busca == 'por_url'`
- Pipeline GERAR_LINK (Fase 15) gera `meli.la` automaticamente após ingest
- Dono do produto segue regra padrão: afiliado=privado, admin/usuario=público

### ~~Fase 16.5 (parcial) — Handlers ML dedicados~~ ✅ ENTREGUE (v3.0.5+)
5 funções dedicadas (`mais_vendidos`, `melhor_comissao`, `em_alta`,
`termo_livre`, `por_url`) usando template comum `_varrer_lista_urls_sync`.
Refatoração em v3.0.5; linkbuilder INLINE adicionado em v3.0.9.

### ~~CRUD produtos UI~~ ✅ ENTREGUE (v3.18.1)
Editar/excluir individual + apagar todos com confirmação tripla. Botão
"Ver produto" abre `url_afiliado` em nova aba. Botão "Regenerar meli.la"
re-enfileira GERAR_LINK pros pendentes.

### ~~Bug raiz do `meli.la` não salvar~~ ✅ RESOLVIDO (v3.0.10)
Handler `handler_gerar_links_ml` agora retorna `ok=True` no sucesso.
Ver [docs/contrato_handlers_ws.md](contrato_handlers_ws.md) e
[docs/sessao_2026-05-15-16.md](sessao_2026-05-15-16.md).

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

### ⛔ NÃO MEXER EM NADA AO INICIAR A PRÓXIMA SESSÃO

**O sistema está 100% funcional em prod.** A última coisa que o user fez foi
validar a busca "Mais vendidos" ML end-to-end com sucesso (v3.0.3).

**Regras pra primeira interação da nova sessão:**

1. Leia os 3 arquivos (CLAUDE.md, este, docs/decisoes.md) pra absorver contexto.
2. **NÃO** rode comandos, **NÃO** crie arquivos, **NÃO** edite código.
3. **NÃO** sugira "vou verificar X" ou "deixa eu testar Y" — nada disso é necessário.
4. **Apresente as opções** de continuação como menu, pra user escolher por onde seguir.
5. **Aguarde a escolha do user** antes de qualquer ação.

As opções a apresentar (do mais ao menos prioritário):
- **Fase 16.5 (Shopee)** — scraper Shopee (API interna já retorna shortlink afiliado)
- **Fase 17** — curadoria automatizada TOP 50 (Celery beat diário)
- **Fase 18** — métricas no dashboard (clicks do `/r/{slug}`)
- **Bug menor**: ajustar `REDIS_URL_OVERRIDE` em `app/core/config.py`
- **Bug menor**: atualizar `ADMIN_PASSWORD` env var no Railway
- **Outra coisa que o user trouxer**

Fases já entregues nesta sessão (NÃO repetir):
- ✅ Fase 16.4 (busca por URL/link, v3.0.4)
- ✅ Fase 16.5 parcial — handlers ML por tipo (v3.0.5)
- ✅ CRUD produtos UI (v3.18.1)
- ✅ Linkbuilder inline + bug raiz `meli.la` (v3.0.10)

### Prompt sugerido pra primeira mensagem
> *"Estou continuando o Achadinhos V3. Leia esses 3 arquivos NA ORDEM:*
> *1. CLAUDE.md (overview e fases entregues)*
> *2. docs/sessao_continuacao.md (estado atual + próximas fases)*
> *3. docs/decisoes.md (ADRs)*
>
> ***IMPORTANTE: tudo está funcionando. NÃO mexa em nada, NÃO rode comandos,*
> *NÃO sugira testes. Apenas leia os arquivos e me apresente as opções de*
> *continuação como menu pra eu escolher.***
>
> *Última coisa validada em prod: busca "Mais vendidos" ML com 50 produtos*
> *importados + `meli.la` gerado + auto-classificação por nicho. Agente*
> *em v3.0.3."*

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

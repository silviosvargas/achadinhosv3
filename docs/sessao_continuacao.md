# Continuação da sessão Claude — Achadinhos V3

> **Este arquivo é a fonte de verdade entre sessões.** Quando abrir nova
> sessão, instrua: *"Lê CLAUDE.md + docs/sessao_continuacao.md +
> docs/decisoes.md antes de fazer qualquer coisa"*.
>
> ⚠ **POLÍTICA OBRIGATÓRIA**: ao final de qualquer sessão substancial
> (≥3 commits OU mudança que afete fluxo), atualize ESTE arquivo +
> CLAUDE.md. Lição em `memory/feedback_documentar_sempre.md`.

**Última atualização:** 2026-05-17 noite — sessão MUITO grande (refundação arquitetural per-user + paginação + visão sistêmica admin)
**Versão do agente publicada:** `3.9.0`
**Migration head:** `0018_tpl_cpu`

---

## 🚀 Estado atual em produção

| Componente | Status | Detalhes |
|---|---|---|
| API + dashboard | ● Online | https://achadinhos.maisseguidores.ia.br |
| Postgres | ● Online (banco LIMPO em 17/05) | só 6 admins + 5 orgs + seeds |
| Redis | ● Online | Railway add-on |
| Worker (Celery + beat) | ● Online | beat hourly processa fila personalizados |
| Migrations | `0018_tpl_cpu` (head) | aplicadas via preDeploy Railway |
| Agente desktop | v3.9.0 published | [release](https://github.com/silviosvargas/achadinhosv3/releases/tag/agente-v3.9.0) |

---

## 📜 Fases entregues nesta sessão (17/05/2026 inteiro)

### 🏗️ Refundação arquitetural (Fases A→D)

User definiu 3 regras estruturais:
1. **Cliente sempre usa produtos + afiliado do admin central** — sem catálogo próprio
2. **Agente único** — capabilities decidem o que ele faz (admin=tudo, afiliado=WA+marketplaces com tag, usuário=só WA)
3. **Personalizado do cliente** → fila admin processada em até 2h (Celery hourly)

**Fase A — Bloquear cadastros do cliente** (`c9f932c`):
- Property `Usuario.eh_admin_central` = admin AND org_id == admin_org_id
- Substitui flags `pode_*` do Plano em 6 lugares (endpoints + UI + menu)
- Onboarding: passo "afiliados" só pra admin_central (depois flexibilizado pra afiliado também na Fase D)
- Tabela `/planos` reformatada — focada em postagens/grupos/Telegram/catálogo central

**Fase B — Favoritar produtos** (`d4c062d`):
- Migration 0016: `usuario_produto_personalizado` (M:N user×produto)
- POST `/produtos/{id}/personalizar` + `/despersonalizar` (idempotente)
- `/produtos` ganha botão ⭐ Personalizar em cada card
- `/produtos/personalizados` agora retorna UNION (criados + favoritados)

**Fase C — Fila admin de solicitações** (`5967d39`):
- Migration 0017: `solicitacoes_personalizadas`
- Cliente em `/produtos/personalizados/buscar` → cria solicitação (em vez de chamar agente próprio direto)
- `/admin/fila-personalizados` admin lista pendentes + recentes
- Botões: ▶ Processar / ✗ Rejeitar / ⚡ Processar tudo
- **Celery beat hourly** (`crontab(minute=0)`) processa pendentes automaticamente
- Hook em `dispatcher.marcar_concluida` lê `payload.solicitacao_id` e atualiza status
- Service novo `app/services/solicitacao_service.py`

**Fase D — Capabilities por agente** (servidor `e118f10` + agente `81d1964` = **v3.9.0**):
- Servidor: `capabilities_service.capabilities_do_agente(agente_id)` calcula por tipo de user
- Handshake WS envia `{tipo:"capabilities", capabilities:[...]}` após `accept()`
- Agente: novo singleton `agent/capabilities.py` armazena
- `executar_busca` chama `caps_mod.tem(mkt)` antes de disparar Selenium — recusa graciosamente se sem permissão
- `/agentes` mostra badges 🟢/🔒 por marketplace (whatsapp/ml/shopee/amazon)
- Bump agente v3.8.14 → v3.9.0 (minor — protocolo WS novo)

### 🔒 Privacidade per-user (Fase 3.30)

User refinou após Fase D: cada user da org só vê e gerencia o seu.

- **Grupos** (`b756c01`): `Grupo.proprietario_id` (já existia). Lista filtra por dono. `/grupos/{id}/editar` + `/excluir` novos. Macro `_pode_editar_grupo`.
- **Templates personalizadas** (`f3b6cb8` + migration 0018): nova coluna `criado_por_usuario_id`. Renomeado de "Templates" → "Templates personalizadas" no menu. Lista mostra TODAS da org (cliente PODE usar templates do admin). Edição só pelo dono. Renderização (`selecionar_template`) tem cascata: prefere user → fallback admin.
- **Canais** (`3e5f8b9`): `Canal.usuario_id` (já existia). Mesmo padrão. Tipo readonly na edição (mudar tipo quebra config). Excluir bloqueado se há grupos vinculados.
- **Postagem só nos próprios grupos** (`9a7dece`): `selecao_service.grupos_com_nichos` ganha arg `proprietario_id`. `lote_service.postar_produto_imediato` ganha arg `proprietario_grupo_id`. Endpoints passam `user.id` pra non-admin-central. Impede user A postar em grupo do user B.

**Hotfixes que apareceram nessa fase:**
- `37ce591`: `from sqlalchemy import select` faltando em `lote_service` — bug latente desde Fase 17 que nunca tinha aparecido porque o `postar_produto_imediato` não tinha sido chamado.
- `2fe4b41`: `personalizado_postar` aceita produtos do catálogo central (favoritados via UPP).

### 👥 Admin central — visão sistêmica + filtros + paginação (Fase 3.31)

- **`/usuarios`** (`798f4a9`): admin central vê TODOS users do sistema (não só sua org). Filtros: papel (admin/afiliado/usuário), busca em login+nome+email, datas DESDE/ATÉ. Coluna "Org" + "Cadastro" novas.
- **Paginação 50/página** (`6459b43` + `c02eca6`): macro reutilizável `templates/_macros/paginacao.html`. Aplicada em 7 rotas: `/usuarios`, `/canais`, `/grupos`, `/tarefas`, `/produtos`, `/curadoria/top`, `/templates`. Truncamento elegante `« 1 ... 4 5 6 ... 10 »`. Preserva querystring de filtros.

### 🧹 Banco limpo

- **Script `scripts/limpar_banco.py`** (`e27f1e9`): destrutivo com `--confirmar APAGAR`. Mantém só admins/super + orgs deles + seeds.
- **Executado em prod (17/05/2026 12:30)**: apagados 105 tarefas, 87 produtos, 5 solicitações, 3 tags afiliado, 2 grupos/canais/agentes/templates, 1 user não-admin, 2 favoritos UPP. Permaneceram 6 admins + 5 orgs.

### 🎨 UX fixes durante a sessão

- `5efde96` — Fix cache `/agentes/instalador` (TTL 5min→60s + `?nocache=1` bypass) — user reportou baixando v3.8.3 mesmo após publicar v3.8.4.
- `3d36fab` — Onboarding passo 2 "Conectar agente" mais claro + `/canais` warning quando sem agente.
- Mudanças em produtos/personalizados durante várias iterações de captura ML (v3.8.0 a v3.8.14).

---

## 🔥 Armadilhas e padrões registrados nesta sessão

### Memórias persistentes novas
- `memory/feedback_ml_seletor_stripe.md` — captura ML via `span.stripe-commission__*`. Regex no body falha (textContent sem whitespace).
- `memory/feedback_shopee_captcha_no_reload.md` — política da Amazon (`driver.get(URL_PAINEL)` no retry) NÃO se aplica à Shopee. Custou 9 releases.

### Armadilhas adicionadas no CLAUDE.md
- "Captura comissão ML — SELETOR CSS, nunca regex no body (v3.8.4+)"

### Conceito de `Usuario.eh_admin_central`
Property `True se papel ∈ (admin, super) AND org_id == settings.admin_org_id`. Substitui as flags `pode_*` do Plano (que tratavam diferenças como comerciais; agora é arquitetural). Use SEMPRE essa property pra gates de admin sistêmico.

### Capabilities arquitetura
- Admin central: `["whatsapp", "ml", "shopee", "amazon", "magalu", "aliexpress"]`
- Afiliado: `["whatsapp"]` + cada `usuario_afiliados.plataforma` cadastrada
- Usuário comum: `["whatsapp"]`

Capabilities calculadas a cada handshake WS — sempre consistentes com user atual.

### Templates: cascata de seleção
`selecionar_template` tenta:
1. Template do user com nicho compatível
2. Template do user padrão (nicho NULL)
3. Qualquer da org com nicho compatível (fallback admin)
4. Qualquer da org padrão (fallback admin)
5. Fallback hardcoded

### Postagem entre users
`lote_service.postar_produto_imediato` aceita `proprietario_grupo_id`. Quando passado, `selecao_service` só considera grupos desse dono. Endpoints non-admin-central SEMPRE passam `user.id`. Admin central passa None (vê todos).

---

## 🎯 Próximos passos sugeridos

### Pedidos do user no fim da sessão (NÃO IMPLEMENTADOS — atacar primeiro)
- **Admin central: editar/excluir/trocar papel** de qualquer user direto na lista `/usuarios`
- **Conceito de "super admin estrela"** — quem cria outros admins, com botão de promoção ao lado do excluir

### Próximas fases prioritárias
1. **Validar agente v3.9.0 end-to-end** — admin central baixa exe, capabilities chegam, ML/Shopee/Amazon rodam. User comum tenta busca → recusa graciosa.
2. **Página `/relatorios`** — histórico de tarefas concluídas com `duracao_seg`, média por tipo, gráfico simples.
3. **Servidor-side: evitar tarefas duplicadas** de busca padrão — user clicou 4× e enfileirou 4 buscas. Usar UNIQUE em (status, tipo, payload->slug_padrao).
4. **Magalu** (4º marketplace seguindo `docs/contrato_busca_marketplace.md`)
5. **AliExpress + TikTok** (após Magalu)
6. **`ANTHROPIC_API_KEY`** no Railway — ativar IA dos Personalizados (Claude Haiku 4.5 extrai palavra-chave de link social)

### Bugs anotados (não bloqueiam)
- `REDIS_URL_OVERRIDE` vs `REDIS_URL` em `app/core/config.py` (api funciona por sorte)
- `ADMIN_PASSWORD` env var no Railway desatualizada (user trocou via `/conta`)

---

## 🛠️ Comandos pra começar nova sessão

```bash
# 1. Estado do worktree
git -C "D:/ACHADINHOSV3/.claude/worktrees/elated-kalam-25d020" log --oneline -15
git -C "D:/ACHADINHOSV3/.claude/worktrees/elated-kalam-25d020" status

# 2. Build do agente v3.9.0
curl -sI -o /dev/null -w "%{http_code}\n" https://github.com/silviosvargas/achadinhosv3/releases/download/agente-v3.9.0/AchadinhosAgent-Setup-3.9.0.exe
# 302 = ok publicada

# 3. Estado da versão recomendada
curl -s https://achadinhos.maisseguidores.ia.br/api/v1/agentes/versao-atual

# 4. Railway CLI já está linkado (achadinhosv3 service do projeto balanced-ambition)
railway status
```

---

## 📋 Tipos de commit & versionamento

| Mudança | Bump | Release? |
|---|---|---|
| Servidor (Python `app/`) | — | Não. Railway redeploy automático. |
| Frontend (CSS, Jinja) | — | Não. |
| Migration | — | Não. preDeploy Railway aplica. |
| `agente/agent/*.py` | **bump 3 arquivos + tag** | **SIM** (build via GitHub Action) |
| `.github/workflows/` | — | Não. |
| Docs (`docs/`, CLAUDE.md, memory) | — | Não. |

Bump 3 arquivos sempre juntos: `agente/agent/local_server.py` (VERSAO_AGENTE), `agente/pyproject.toml` (version), `agente/installer.iss` (MyAppVersion).

---

## 🧪 Comandos úteis dessa sessão

```bash
# Limpar banco (já executado em 17/05 12:30)
railway ssh "cd /app && python -m scripts.limpar_banco"  # preview
railway ssh "cd /app && python -m scripts.limpar_banco --confirmar APAGAR"  # executa

# SSH já configurado com chave em ~/.ssh/id_ed25519
# Project linked: balanced-ambition · service: achadinhosv3 · env: production
```

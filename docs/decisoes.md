# Decisões arquiteturais (ADR)

Cada decisão importante vira um item aqui. Formato curto: contexto, decisão, consequências.

---

## ADR-001 — FastAPI no lugar de Flask

**Contexto.** A V2 usa Flask síncrono. Pra suportar 20+ agentes WebSocket
conectados ao mesmo tempo + HTTP API, precisamos de async nativo.

**Decisão.** Migrar pra FastAPI (ASGI, async/await nativo, validação por Pydantic).

**Consequências.**
- ✅ WebSocket sem hack (Flask precisa de flask-sock + threading).
- ✅ Docs OpenAPI automáticas (`/docs`).
- ✅ Validação tipada de request/response.
- ⚠️ Reescrita das rotas (templates Jinja2 ficam, lógica HTTP muda).

---

## ADR-002 — Postgres no lugar de SQLite

**Contexto.** SQLite WAL aguenta 1 escritor + N leitores, mas trava
quando 20 agentes gravam histórico simultâneo. Backup vivo é frágil.

**Decisão.** Postgres 16 como banco principal. SQLAlchemy 2.x ORM.

**Consequências.**
- ✅ Concorrência real, índices melhores, FK confiável.
- ✅ Replicação possível quando crescer.
- ⚠️ Mais um serviço pra manter (Docker resolve em dev).
- ⚠️ Migração de dados V2 → V3 é necessária (script futuro).

---

## ADR-003 — Multi-tenancy via discriminator column

**Contexto.** SaaS exige isolamento de dados. Três opções:
1. DB-per-tenant (caro, complexo)
2. Schema-per-tenant (Postgres suporta, complica migrações)
3. Discriminator column (`org_id` em cada tabela)

**Decisão.** Opção 3 (`org_id`).

**Consequências.**
- ✅ Migrações simples (1 schema só).
- ✅ Custo baixo pra muitas orgs pequenas.
- ⚠️ TODA query precisa filtrar por org_id — risco de data leak se esquecer.
  Mitigação: helpers/decorators que injetam filtro automaticamente; revisão
  obrigatória em PRs que tocam SQL bruto.

---

## ADR-004 — Agente local pra WhatsApp, cloud pra Telegram

**Contexto.** WhatsApp Web não tem API oficial estável; Selenium/pyautogui
em servidor cloud é detectado e banido. Telegram tem Bot API oficial gratuita.

**Decisão.**
- WhatsApp → agente local (`AchadinhosAgent.exe`) no PC do afiliado.
- Telegram → tasks Celery na nuvem chamando Bot API.

**Consequências.**
- ✅ WhatsApp continua funcionando como na V2 (zero risco de ban).
- ✅ Telegram funciona 24h sem PC ligado.
- ⚠️ Afiliado precisa instalar 1 app local. Mitigação: instalador `.exe`
  amigável, auto-update, tray icon.

---

## ADR-005 — Tarefas em tabela Postgres + notificação Redis

**Contexto.** Agente offline pode perder comandos enviados só por Redis pub/sub.

**Decisão.** Fonte da verdade = tabela `tarefas`. Redis só notifica
("agente X, tem coisa nova"). Quando agente reconecta, ele faz
`SELECT pendentes WHERE agente_id = X` e processa em ordem.

**Consequências.**
- ✅ Zero perda de tarefas em queda de agente/servidor.
- ✅ Auditoria completa de tudo que rodou.
- ⚠️ Tabela cresce — particionar por mês quando passar de ~10M linhas.

---

## ADR-006 — JWT no lugar de sessão Flask

**Contexto.** Sessão Flask exige cookies + storage de sessão no servidor.
Não funciona pra agente local nem app mobile futuro.

**Decisão.** JWT com par access (60min) + refresh (30 dias).
Agente recebe token de longa duração (1 ano) que é revogável via banco.

**Consequências.**
- ✅ Stateless — escala horizontalmente sem session store compartilhado.
- ✅ Mesmo mecanismo serve dashboard, agente e mobile.
- ⚠️ Revogação ativa precisa de check no banco (custa 1 query por request).
  Aceito por enquanto; otimizar com cache Redis se virar gargalo.

---

## ADR-008 — Produtos privados de afiliado (Fase 4b)

**Contexto.** Buscas ML rodam no agente local (ADR-004). Quando um afiliado
dispara uma busca, ele espera que os produtos achados:
1. Usem a tag de afiliado dele (não a do admin da org).
2. Não fiquem visíveis pra outros afiliados da mesma org (concorrência interna).

Quando admin (ou usuário comum) dispara, produtos são compartilhados com toda
a org com a tag do admin.

**Decisão.** Adicionar coluna `produtos.usuario_dono_id`:
- `NULL` → produto público da org. Aparece pra todo mundo. Tag do admin.
- `NOT NULL` (= afiliado.id) → produto privado dele. Só ele e admins veem.
  Tag dele (`Usuario.afiliado_ml`).

Unicidade implementada com partial unique indexes em Postgres:
- `uq_produtos_publico`: `(org_id, plataforma, item_id)` WHERE dono IS NULL
- `uq_produtos_privado`: `(org_id, usuario_dono_id, plataforma, item_id)` WHERE dono NOT NULL

Permite mesmo MLB existir como público da org E privado de cada afiliado
em paralelo, cada um com a tag certa.

**Consequências.**
- ✅ Afiliados não pisam no pé um do outro nem dependem do admin.
- ✅ Admin mantém controle total — vê e gerencia tudo.
- ✅ `selecao_service` filtra por dono na hora do lote: cada usuário só
  posta o que pode ver.
- ⚠️ Toda query em `produtos` precisa filtrar por dono — esquecer = vazamento
  entre afiliados. Helpers em `_get_da_org` e `selecao_service` centralizam
  a regra; revisar PRs que tocam SQL bruto em `produtos`.
- ⚠️ Catálogo cresce mais (mesmo produto pode existir N+1 vezes). Aceito —
  privacidade vale o custo de storage.

---

## ADR-007 — Jinja2 no frontend (por enquanto)

**Contexto.** V2 tem 22 templates funcionais. Reescrever em SPA custa 3-4
semanas e não traz valor de produto imediato.

**Decisão.** Manter Jinja2. API JSON em `/api/v1/*` desde dia 1
pra abrir caminho pra SPA/mobile no futuro.

**Consequências.**
- ✅ Reaproveita templates da V2 com pequenas adaptações.
- ✅ Frontend pode evoluir pra SPA quando houver demanda real (mobile).
- ⚠️ HTML server-side limita interatividade pesada — aceitar pelo MVP.

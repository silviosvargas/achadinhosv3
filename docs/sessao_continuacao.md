# Continuação da sessão Claude — Achadinhos V3

> **Este arquivo é a fonte de verdade entre sessões.** Quando abrir nova
> sessão, instrua: *"Lê CLAUDE.md + docs/sessao_continuacao.md +
> docs/decisoes.md antes de fazer qualquer coisa"*.
>
> ⚠ **POLÍTICA OBRIGATÓRIA**: ao final de qualquer sessão substancial
> (≥3 commits OU mudança que afete fluxo), atualize ESTE arquivo +
> CLAUDE.md. Lição em `memory/feedback_documentar_sempre.md`.

**Última atualização:** 2026-05-16 noite (sessão muito longa — 15 releases agente)
**Versão do agente publicada:** `3.8.14`
**Versão do agente recomendada:** `3.8.14`
**Migration head:** `0015_extra`

---

## 🚀 Estado atual em produção

| Componente | Status | Detalhes |
|---|---|---|
| API + dashboard | ● Online | https://achadinhos.maisseguidores.ia.br |
| Postgres | ● Online | Railway add-on |
| Redis | ● Online | Railway add-on |
| Worker (Celery + beat) | ● Online | `--pool=solo` |
| Migrations | `0015_extra` (head) | Aplicadas via preDeploy Railway |
| Agente desktop | v3.8.14 published | [release](https://github.com/silviosvargas/achadinhosv3/releases/tag/agente-v3.8.14) |

---

## 📜 Fases entregues nesta continuação (16/05/2026 noite)

### Fase 21 — Comissão extra (bônus GANHOS EXTRAS ML)

- **v3.8.0** — Migration 0015 adiciona `produtos.comissao_extra` FLOAT NULL. Schema `IngestProdutoItem` + `_upsert_produto` gravam respeitando hierarquia de fonte. Nova busca padrão `ml_comissao_extra` (servidor + agente). UI: filtro "🎁 Só com bônus EXTRAS" em `/produtos` + badge dourado `🎁 +X% extra`.
- **v3.8.1** — Telemetria por produto (log INFO `efetiva/extra/preco/status`) + diagnóstico em disco quando categoria zera.
- **v3.8.2** — JS de captura simplificado pra busca no `body.textContent` inteiro (era loop em seletores específicos).
- **v3.8.3** — Multi-fontes: body + iframes + outerHTML + sleep aumentado pra 3s.
- **v3.8.4** — **FIX DEFINITIVO via DevTools do user**: a barra usa `span.stripe-commission__percentage` (número) + `span.stripe-commission__pillsecond` (texto "EXTRAS"). Regex no body falha porque ML renderiza spans BEM SEM whitespace entre tags (`"EXTRAS9%"` → `\s+` não match). Captura via seletor CSS direto. Memória nova `feedback_ml_seletor_stripe.md`.
- **v3.8.5** — Regra busca extras: `alvo_total=10` → `min_por_categoria=3` (sem teto). Visita TODAS as 8 categorias, mantém ≥3 com extras por categoria.

### Fase 22 — Curadoria TOP melhorias

- **Servidor `5427524`** — Botão "🔄 Atualizar TOP" (recarrega; query já filtra postados 7d). Botão 🗑️ excluir por card (CASCADE limpa nichos + redirects). Limite default 50 → 30.

### Fase 22.1 — Buscas padrão Shopee + Amazon

- **Servidor `960459b`** (zero release agente) — 2 entradas novas em `app/core/buscas_padrao.py`:
  - `shopee_mais_vendidos`: API afiliados Shopee (`list_type=2`), 50 produtos, ~30s
  - `amazon_bestsellers`: 10 categorias SiteStripe, 50 produtos, ~3min
  - Service `buscas_padrao_service` aceita campo `mensagem_run` custom por busca.

### Maratona Shopee captcha (9 releases até estabilizar)

**Cenário:** user reportou que busca Shopee não esperava resolver captcha.

- **v3.8.6** — copiou `_aguardar_com_retry` da Amazon (30s × 3 com reload). **QUEBROU**: cada `driver.get(URL_PAINEL)` em sessão marcada re-emite captcha → loop infinito.
- **v3.8.7** — detecção via DOM + retry no meio do loop. Piorou.
- **v3.8.8** — `git checkout f6f177a` (revert) + patch mínimo.
- **v3.8.9** — detecção DOM + sleep(30) puro.
- **v3.8.10** — força captcha se URL fora do painel.
- **v3.8.11** — status!=200 sempre força + ping inicial + **botão clicável "✅ CAPTCHA RESOLVIDO"**.
- **v3.8.12** — detecta Chrome fechado (`WebDriverException`).
- **v3.8.13** — **user mandou analisar V2** (`ACHADINHOSV2 - FUNCIONAL/src/buscar/shopee.py`). V2 usava `input("Pressione ENTER...")` bloqueante, sem retry/ping. Simplificou pra espelhar V2. 102 linhas removidas.
- **v3.8.14** — user reforçou "AGUARDAR 30s OBRIGATÓRIOS, independente de qualquer clique ou reload". `_aguardar_captcha` virou literalmente `time.sleep(30)` puro. Sem polling, sem botão de interrupção.

**Lição:** Política de retry da Amazon NÃO se aplica à Shopee (recarregar URL re-emite captcha). Registrada em `memory/feedback_shopee_captcha_no_reload.md`.

---

## 🔥 Armadilhas conhecidas (LEIA)

Tudo já está documentado em CLAUDE.md → "Armadilhas conhecidas". Resumo das críticas:

1. **ML captura comissão**: SEMPRE seletor CSS `.stripe-commission__percentage` + `.stripe-commission__pillsecond`. Nunca regex no body (ML usa spans sem whitespace).
2. **Shopee captcha**: nunca recarregar URL no meio do retry. Use sleep puro de 30s.
3. **Cache `/agentes/instalador`** TTL agora 60s + `?nocache=1` bypass.
4. **Handler WS sempre `ok=True`** (`docs/contrato_handlers_ws.md`).
5. **`url_afiliado` no schema Pydantic** — precisa estar declarado com `extra="allow"`.
6. **Hierarquia de `comissao_fonte`** — nunca sobrescrever fonte alta com baixa.

---

## 🎯 Próximos passos sugeridos

1. **Validar v3.8.14 end-to-end** — user instala exe, dispara busca padrão Shopee, confirma que pausa 30s e continua.
2. **Servidor-side: evitar tarefas duplicadas** — user clicou 4× em "Rodar agora" e gerou 4 tarefas enfileiradas. Adicionar verificação no `buscas_padrao_service.disparar` que recusa nova tarefa se já há uma em PROCESSANDO pra mesma busca.
3. **Página `/relatorios`** — histórico de tarefas concluídas com `duracao_seg`, média por tipo, gráfico simples. Usa coluna que já existe (migration 0014).
4. **Magalu** — 4º marketplace seguindo `docs/contrato_busca_marketplace.md`.
5. **AliExpress + TikTok** — após Magalu.
6. **Configurar `ANTHROPIC_API_KEY`** no Railway — ativa IA dos Personalizados.

---

## 🛠️ Comandos pra começar nova sessão

```bash
# 1. Ver estado do worktree
git -C "D:/ACHADINHOSV3/.claude/worktrees/elated-kalam-25d020" log --oneline -10
git -C "D:/ACHADINHOSV3/.claude/worktrees/elated-kalam-25d020" status

# 2. Ver release atual do agente
curl -sI -o /dev/null -w "%{http_code}\n" https://github.com/silviosvargas/achadinhosv3/releases/download/agente-v3.8.14/AchadinhosAgent-Setup-3.8.14.exe
# 302 = ok publicada

# 3. Verificar versão recomendada via servidor
curl -s https://achadinhos.maisseguidores.ia.br/api/v1/agentes/versao-atual
```

---

## 📋 Tipos de mudança & versionamento

| Mudança | Bump | Release? |
|---|---|---|
| Servidor (Python `app/`) | — | Não. Railway redeploy automático. |
| Frontend (CSS, Jinja) | — | Não. |
| Migration | — | Não. preDeploy Railway aplica. |
| `agente/agent/*.py` | **bump 3 arquivos + tag** | **SIM** (build via GitHub Action) |
| `.github/workflows/` | — | Não. |
| Docs (`docs/`, CLAUDE.md, memory) | — | Não. |

Bump 3 arquivos sempre juntos: `agente/agent/local_server.py` (VERSAO_AGENTE), `agente/pyproject.toml` (version), `agente/installer.iss` (MyAppVersion).

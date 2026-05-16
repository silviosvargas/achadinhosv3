# Continuação da sessão Claude — Achadinhos V3

> **Este arquivo é a fonte de verdade entre sessões.** Quando abrir nova
> sessão, instrua: *"Lê CLAUDE.md + docs/sessao_continuacao.md +
> docs/decisoes.md antes de fazer qualquer coisa"*.
>
> ⚠ **POLÍTICA OBRIGATÓRIA**: ao final de qualquer sessão substancial
> (≥3 commits OU mudança que afete fluxo), atualize ESTE arquivo +
> CLAUDE.md com:
> 1. Versão atual em prod (servidor + agente)
> 2. Migration head
> 3. Fases entregues (cronológico)
> 4. Decisões importantes feitas
> 5. Próximos passos sugeridos
>
> NÃO esperar o user pedir — registrar é automático. Lição registrada
> em `memory/feedback_documentar_sempre.md` (2026-05-16).

**Última atualização:** 2026-05-16 noite (sessão extensa — Fases 18-20)
**Versão do agente publicada:** `3.7.1`
**Versão do agente recomendada pra user instalar:** `3.7.1` (release mais recente)
**Migration head:** `0014_duracao`

---

## 🚀 Estado atual em produção

| Componente | Status | Detalhes |
|---|---|---|
| API + dashboard | ● Online | https://achadinhos.maisseguidores.ia.br |
| Postgres | ● Online | Railway add-on |
| Redis | ● Online | Railway add-on |
| Worker (Celery + beat) | ● Online | `--pool=solo` |
| Migrations | `0014_duracao` (head) | Aplicadas via preDeploy Railway |
| Agente desktop | v3.7.1 published | [release](https://github.com/silviosvargas/achadinhosv3/releases/tag/agente-v3.7.1) |

---

## 📜 Fases entregues nesta sessão (cronológico)

### Fase 18 — Curadoria via nota + precisão de dados
- **v3.3.0** — `produtos.nota` (0-100), `is_bestseller`, `is_em_alta`, `total_vendidos`, `comissao_fonte`, `comissao_validada`, 3 timestamps específicos (`preco_atualizado_em`, `comissao_atualizada_em`, `vendidos_atualizado_em`). Migration 0012. Scoring 30% preço + 40% comissão + 30% vendas. Tabela ranges válidos por marketplace (`app/core/comissoes.py`).
- **v3.3.1 hotfix** — preço pegava o riscado (`<s>`) em vez do promocional. Fix com XPath `not(ancestor::s)`.
- **v3.4.0** — captura comissão real ML via barra preta durante busca (ainda usando URL canônica).
- **v3.4.1** — NameError `_LOCK_CHROME_ML` corrigido.
- **v3.4.2** — tentou abrir meli.la (fluxo errado).
- **v3.4.3** — fluxo `meli.la → /social/ → clicar "Ir para produto" → barra` (decisão do user na época).
- **v3.4.4** — hierarquia de `comissao_fonte` no servidor (não sobrescreve fonte alta com baixa) + JS prefere `GANHOS EXTRAS` sobre `GANHOS` base.
- **edição manual** — admin pode editar comissão pela UI; vira `comissao_fonte=manual` (topo da hierarquia, imune a sobrescrita).
- **v3.5.0** — busca padrão (Fase 19) com 8 categorias × 30 candidatos.
- **v3.5.1** — descarta produtos sem captura real (não polui DB com estimativa).

### Fase 19 — Buscas padrão
- `app/core/buscas_padrao.py` — lista hardcoded de buscas oficiais.
- `app/services/buscas_padrao_service.py` — dispara via tarefa.
- `app/web/templates/buscas.html` — seção topo "⭐ Buscas padrão" com cards.
- Primeira (e única até agora): **🛒 Mercado Livre — Mais vendidos por categoria**.

### Fase 20 — Barra de progresso e cancelamento
- **v3.6.0** — `tarefas.progresso_pct/mensagem/atualizado_em` (migration 0013). WS handler `_h_busca_progresso` persiste DB. Endpoint `GET /api/v1/tarefas/em-progresso`. Card flutuante no dashboard com polling 3s. Agente helper `ws_progresso.reportar(tarefa_id, pct, msg)`.
- **v3.6.1** — botão "✕ Cancelar" na barra. Cancelamento cooperativo via flag global thread-safe (`agent/cancelamento.py`). Dispatcher.cancelar envia WS + marca CANCELADA.
- **v3.7.0** — captura simplificada (URL canônica DIRETO, sem meli.la → /social/ — user reverteu orientação anterior). Tempo decorrido na barra (`⏱ Xmin Ys`). Tempo final salvo em `tarefas.duracao_seg` (migration 0014).
- **v3.7.1** — meli.la gerado em batches de 10 durante o loop (não no fim). Cancelamento granular dentro de `_processar_categoria`. Se cancelar no produto N de uma categoria, gera meli.la pros N capturados antes de parar.

---

## 🔑 Decisões arquiteturais importantes desta sessão

1. **Captura de comissão ML**: abrir **URL canônica direto** (não meli.la).
   Chrome do agente está logado como afiliado em `chrome_perfil_ml` → barra
   aparece automática. ~3x mais rápido que o fluxo via meli.la → /social/.

2. **Hierarquia de `comissao_fonte`** (alta → baixa):
   `manual > ml_barra_afiliados > ml_painel > shopee_api > amazon_tabela > categoria_ml_v2 > estimativa`
   Servidor NUNCA sobrescreve fonte alta com baixa em `_upsert_produto`.

3. **Buscas padrão HARDCODED** em `app/core/buscas_padrao.py` — não tabela
   DB. São oficiais, versionadas com código. UI em seção topo `/buscas`.

4. **Cancelamento cooperativo** — flag global em `agent/cancelamento.py`.
   Loops longos checam entre iterações. Granularidade: entre produtos
   (~1.5s, dentro de uma categoria).

5. **Tempo + estatísticas** — `tarefas.duracao_seg` calculado em
   `dispatcher._calcular_duracao_seg(tarefa)`. UI mostra `⏱ Xmin Ys`
   durante execução, `✓ Concluído em Xmin Ys` ao terminar.

6. **Filtro estrito de qualidade** — busca padrão descarta produtos sem
   `comissao_fonte=ml_barra_afiliados` (não polui DB com estimativa).

---

## 🔄 Fluxo end-to-end atual (busca padrão Fase 19+)

```
1. Admin clica "▶ Rodar agora" em /buscas (seção ⭐ Buscas padrão)
2. Servidor: buscas_padrao_service.disparar() cria Tarefa(BUSCAR_MERCADO_LIVRE)
   com payload {tipo_busca: "padrao_mais_vendidos_completo", ...}
3. Agente recebe via WS:
   - varrer_padrao_completo(cfg, candidatos_por_categoria=30, tarefa_id=...)
   - Pra cada uma das 8 categorias:
     - Abre /mais-vendidos/MLBxxx, extrai ~30 candidatos
     - LOOP de captura:
       - Check cancelamento (entre cada produto)
       - Abre URL canônica DIRETO de cada produto
       - Captura GANHOS EXTRAS X% + preço da barra preta
       - A cada 10 capturados: gera meli.la em batch
       - Continua
     - Fechamento: gera meli.la pros parciais (se cancelado OU < 10 sobrando)
     - Filtra top 10 por (preço × comissão_real)
   - ws_progresso.reportar a cada categoria (0% → 12.5% → 25% → ... → 100%)
4. Dashboard polling 3s renderiza barra com:
   - Tipo (🛒 Busca Mercado Livre #123)
   - Tempo decorrido (⏱ 04min 23s)
   - Percentual (47%)
   - Botão ✕ Cancelar
   - Mensagem ("Categoria 3/8: Beleza")
5. Ingest: produtos chegam com comissao_fonte=ml_barra_afiliados
6. Servidor _upsert_produto: hierarquia respeita (não sobrescreve nada melhor)
7. Tarefa CONCLUIDA: duracao_seg salvo, mensagem "✓ Concluído em Xmin Ys"
8. Card some do dashboard no próximo polling
9. Produtos aparecem em /curadoria/top com label ✅ ML barra
```

---

## 🛑 ARMADILHAS críticas (não repetir)

### 1. Captura ML — abrir URL canônica direto (não meli.la)
Documentado em CLAUDE.md armadilha "Comissão real do ML". Já reverti
3 vezes nesta sessão. **Pra próximas sessões**: se algo falhar, PERGUNTAR
ao user o fluxo correto. Ele opera o painel.

### 2. Hierarquia de comissao_fonte
Servidor `_upsert_produto` usa `_HIERARQUIA_FONTE_COMISSAO` em
`busca_service.py`. SÓ sobrescreve se nova fonte é >= confiável que a atual.

### 3. Pydantic IngestProdutoItem
`extra="allow"` configurado. Pra cada campo NOVO que o agente envia,
declarar EXPLICITAMENTE no schema (Pydantic ignora silenciosamente o
que não está declarado, mesmo com extra=allow em alguns casos).

### 4. Workflow de release do agente
Toda mudança em `agente/agent/*.py` requer:
- Bump 3 arquivos (local_server.py, pyproject.toml, installer.iss)
- Tag `agente-vX.Y.Z` push
- Aguardar build (~3min)
- Confirmar via download URL (HEAD HTTP 200):
  ```
  curl -sI -o /dev/null -w "%{http_code}" -L \
    "https://github.com/silviosvargas/achadinhosv3/releases/download/agente-vX.Y.Z/AchadinhosAgent-Setup-X.Y.Z.exe"
  ```

### 5. Comunicação OBRIGATÓRIA pra cada commit
SEMPRE dizer ao user:
- "servidor-only" → Railway redeploy automático (~3min, sem bump agente)
- "agente bump X.Y.Z" → precisa build + user instalar exe novo
- Status do build/deploy via HEAD do download URL (sem rate limit)

---

## 🎯 Próximos passos sugeridos (em ordem de prioridade)

### Imediato (próxima sessão)
1. **Validar v3.7.1 end-to-end**: user instala exe, roda busca padrão,
   testa cancelamento parcial (produto N de uma categoria), confirma
   que produtos parciais entram no DB com meli.la.
2. **Página /relatorios** (pedido anterior): histórico de tarefas
   com `duracao_seg`, média por tipo, gráfico simples.

### Curto prazo
3. **Mais buscas padrão**: adicionar entries em `app/core/buscas_padrao.py`
   - Shopee mais ofertas (já tem busca por categoria; transformar em padrão)
   - Amazon bestsellers (idem)
4. **Captura comissão Shopee/Amazon na revalidação**: hoje só ML usa
   barra preta. Adaptar o fluxo pra outras plataformas se possível.
5. **Magalu** — 4º marketplace (segue `docs/contrato_busca_marketplace.md`)

### Médio prazo
6. **AliExpress + TikTok** (após Magalu validar padrão)
7. **Configurar ANTHROPIC_API_KEY no Railway** pra ativar IA dos
   Personalizados (Fase 17)

### Bugs anotados mas não corrigidos
- `REDIS_URL_OVERRIDE` vs `REDIS_URL` em `app/core/config.py`
  (api funciona por sorte; ajustar pra aceitar ambos)
- `ADMIN_PASSWORD` env var no Railway desatualizada
  (user trocou via `/conta` na sessão antiga; é cosmético, só usado em
  CREATE inicial)

---

## 📦 Comandos úteis pra começar nova sessão

### Reconhecer estado
```powershell
git status --short
git log --oneline -10
# Atual: 91718bf (v3.7.1) é o último commit
```

### Validar versão atual do agente publicada
```bash
curl -sI -o /dev/null -w "%{http_code}\n" -L \
  "https://github.com/silviosvargas/achadinhosv3/releases/download/agente-v3.7.1/AchadinhosAgent-Setup-3.7.1.exe"
# Esperado: 200
```

### Servidor está no ar?
```bash
curl -s -o /dev/null -w "HTTP %{http_code} | %{time_total}s\n" \
  https://achadinhos.maisseguidores.ia.br/buscas
# Esperado: HTTP 307 (redirect login pra não autenticado)
```

### Migration atual
```bash
ls alembic/versions/ | tail -3
# Esperado: 0012_produtos_curadoria.py, 0013_tarefas_progresso.py, 0014_tarefas_duracao.py
```

### Memórias persistentes do user
Local: `C:\Users\silvi\.claude\projects\D--ACHADINHOSV3\memory\`
- `feedback_ml_captura_comissao.md` — URL canônica direto (atualizada)
- `feedback_hierarquia_fontes.md` — não sobrescrever fonte alta
- `feedback_user_fonte_verdade.md` — perguntar ao user, não inventar
- `feedback_release_agente_workflow.md` — comunicar tipo + status build
- `feedback_documentar_sempre.md` — atualizar docs ao final de sessão (NOVO)

---

## 🚨 LEITURA OBRIGATÓRIA antes de mexer em código

1. **CLAUDE.md** — overview + fases entregidas + armadilhas (9 armadilhas atuais)
2. **docs/contrato_handlers_ws.md** — handlers WS PRECISAM retornar `{"ok": True}`
3. **docs/contrato_busca_marketplace.md** — checklist pra adicionar marketplace
4. **Memórias persistentes do user** (acima)

---

## 📋 Tipo de commit — política de comunicação

Pra CADA commit, comunicar ao user EXPLICITAMENTE:

| Categoria | Sinal | Ação requerida |
|---|---|---|
| Servidor-only (app/, alembic/, docs/) | 🔧 SERVIDOR-ONLY | Railway redeploy auto (~3min). User NÃO precisa instalar exe. |
| Agente (agente/agent/*.py) | 🔧 AGENTE bump X.Y.Z | Tag agente-vX.Y.Z, build GH Actions (~3min), user instala exe |
| Agente + servidor juntos | 🔧 AGENTE + SERVIDOR | Ambos. SERVIDOR PRIMEIRO (Railway), agente DEPOIS (tag) |

Confirmar build agente via:
```bash
curl -sI -o /dev/null -w "%{http_code}" -L \
  "https://github.com/silviosvargas/achadinhosv3/releases/download/agente-vX.Y.Z/AchadinhosAgent-Setup-X.Y.Z.exe"
```
HTTP 200 = publicado, 404 = ainda buildando ou falhou.

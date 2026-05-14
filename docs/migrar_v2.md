# Migração V2 → V3

> **Status: planejado, ainda não implementado.**
> Este documento descreve a estratégia. O script entra na Fase 2.

---

## Mapeamento de tabelas

| V2 (SQLite) | V3 (Postgres) | Notas |
|---|---|---|
| `usuarios` | `usuarios` | + `org_id` (todos viram membros da org default) |
| `grupos` | `grupos` | + `org_id`, + `canal_id` (apontando pro canal default WhatsApp) |
| `produtos` | `produtos` | sem org (catálogo compartilhado) |
| `produto_nichos` | `produto_nichos` | sem org |
| `super_produtos` | `super_produtos` | sem org |
| `agendamentos` | `agendamentos` | + `org_id` |
| `produtos_personalizados` | `produtos_personalizados` | + `org_id` (do dono) |
| `templates_mensagem` | `templates_mensagem` | + `org_id` (NULL = global) |
| `postagens` | `postagens` | + `org_id`, + `canal_id`, + `canal_tipo='whatsapp_agente'` |
| `convites_afiliado` | `convites` | + `org_id` |
| `configuracoes` | dispersa em `configuracoes_org` ou env | depende da chave |
| `meta` | descartada | versão de schema vai pro Alembic |
| (não existia) | `organizacoes` | criar org default a partir do admin atual |
| (não existia) | `planos` | seed automático |
| (não existia) | `agentes` | criar 1 por usuário com `chrome_porta != NULL` |
| (não existia) | `canais` | criar 1 por usuário com WhatsApp configurado |

---

## Estratégia

1. **Backup completo da V2** antes de qualquer coisa
   (`cp data/achadinhos.db data/achadinhos.v2.bak.db`).
2. **Subir Postgres da V3 vazio + aplicar Alembic upgrade head.**
3. **Rodar `scripts/migrar_v2.py`** que:
   - Lê SQLite da V2 com sqlite3.
   - Cria 1 org default (`Achadinhos`) com plano Business.
   - Para cada usuário V2: cria `Usuario` na org default.
   - Para cada usuário com `chrome_porta`: cria `Agente` + `Canal whatsapp_agente`.
   - Migra grupos, mapeando-os pro canal default.
   - Migra produtos (sem org_id, vão pro pool compartilhado).
   - Migra postagens (com novo canal_id, canal_tipo).
   - Migra agendamentos, templates, super_produtos.
4. **Verificação:** roda smoke tests comparando contagens.
5. **Cutover:** redireciona afiliados pro novo site.
   Agentes recebem novo token e reconectam.

---

## Riscos e mitigações

| Risco | Mitigação |
|---|---|
| Imagens em disco (`data/imagens/`) | Tar e copiar; novo path: `media/produtos/<plataforma>/<id>` |
| Histórico de jobs (`data/logs_jobs/`) | Não migra — só auditoria recente |
| Sessões ativas | Todos deslogados — emite e-mail de aviso 24h antes |
| Senhas | hashes bcrypt da V2 são compatíveis (passlib) — copia direto |

---

## Não-objetivos

- Migrar logs do dia-a-dia da V2 (vão ficar arquivados, não importam).
- Migrar configurações em `.env` da V2 — admin reconfigura na nova UI.
- Migrar `chrome_perfil/` — afiliado faz login de novo no novo agente.

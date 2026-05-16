# Contrato dos handlers WS do agente

> **LEIA ISSO antes de adicionar qualquer handler novo em `agente/agent/main.py`.**

## Regra de ouro: o retorno do handler PRECISA ter `"ok": True` (ou `"ok": False`)

`agente/agent/ws_client.py:_executar_handler` decide o que enviar pro
servidor olhando o campo `ok` do dict retornado:

```python
async def _executar_handler(self, tipo, msg, handler):
    tarefa_id = msg.get("tarefa_id")
    try:
        resultado = await handler(msg)
        if tarefa_id and resultado is not None:
            if resultado.get("ok"):                          # ← AQUI
                await self.enviar({
                    "tipo":      "tarefa_concluida",
                    "tarefa_id": tarefa_id,
                    "resultado": resultado,
                })
            else:
                await self.enviar({
                    "tipo":      "tarefa_falhou",
                    "tarefa_id": tarefa_id,
                    "erro":      resultado.get("erro", "erro_desconhecido"),
                    "tentar_de_novo": resultado.get("tentar_de_novo", False),
                })
```

- `ok=True` → servidor recebe `tarefa_concluida` → `dispatcher.marcar_concluida` roda hooks de pós-conclusão (ex: `aplicar_mapping` pra GERAR_LINK).
- `ok=False` ou **`ok` ausente** → servidor recebe `tarefa_falhou` → marca tarefa como `FALHOU` ou retry (se `tentar_de_novo=True`). **Nenhum hook é chamado.**

## Histórico do bug que essa regra existe pra prevenir

Da Fase 15 (v3.5.0) até a v3.0.9, `handler_gerar_links_ml` retornava:

```python
return {"mapping": mapping, "total": len(mapping)}  # ← FALTA ok
```

Resultado em produção:
1. Agente capturava `meli.la/XXX` no painel ML corretamente (logs:
   `linkbuilder_ml.lote_capturado capturadas=10 enviadas=10`).
2. Handler retornava o mapping.
3. ws_client via `resultado.get("ok")` → `None` (falsy).
4. Enviava `tarefa_falhou` ao servidor.
5. Servidor marcava a tarefa como FALHOU; NÃO chamava `aplicar_mapping`.
6. `produtos.url_afiliado` ficava com o **fallback** `?matt_word=...`
   (que era preenchido no `_upsert_produto` antes do GERAR_LINK rodar).

Sintoma visível: produtos eram extraídos corretamente, ML gerava os
shortlinks, mas o DB ficava com URL crua + tag genérica. Tentamos 6
releases (v3.0.4 a v3.0.9) atacando sintomas (URL suja, regex MLBU, lock
de Chrome, match flexível, inline vs assíncrono) antes de achar isso.

**Fix em v3.0.10** ([commit 1a76992](https://github.com/silviosvargas/achadinhosv3/commit/1a76992)):

```python
return {
    "ok":      True,
    "mapping": mapping,
    "total":   len(mapping),
    "pedidos": len(urls),
}
```

## Template pra novos handlers

```python
async def handler_meu_comando(msg: dict) -> dict:
    """Descrição curta do que faz."""
    try:
        # 1. Valida payload
        if not msg.get("campo_obrigatorio"):
            return {
                "ok":    False,
                "erro":  "campo_obrigatorio_ausente",
                # tentar_de_novo=False: servidor NÃO re-enfileira automaticamente.
                # Use False pra erros estruturais que retry não resolve.
                "tentar_de_novo": False,
            }

        # 2. Faz o trabalho
        resultado_real = await fazer_a_coisa()

        # 3. Retorna sucesso com payload útil pra dispatcher.marcar_concluida
        return {
            "ok":         True,
            "resultado":  resultado_real,
            # campos extras que o hook do dispatcher consome ficam aqui
        }

    except Exception as e:
        log.exception("handler.meu_comando.crashou", erro=str(e))
        return {
            "ok":    False,
            "erro":  f"{type(e).__name__}: {str(e)[:200]}",
            "tentar_de_novo": True,  # erro transiente — vale tentar de novo
        }
```

## Handlers existentes (todos com `ok=True` desde v3.0.10)

| Handler | Comando WS | Retorno em sucesso |
|---|---|---|
| `handler_postar_whatsapp` | `postar_whatsapp` | `{"ok": True, "grupo_id", "ts_envio", ...}` |
| `handler_busca_ml` | `iniciar_busca_ml` | `{"ok": True, "encontrados", "ingest"}` |
| `handler_gerar_links_ml` | `gerar_links_afiliado_ml` | `{"ok": True, "mapping", "total", "pedidos"}` |

## Hooks pós-conclusão no servidor

`app/services/dispatcher.py:marcar_concluida` despacha por `tarefa.tipo`:

```python
if tarefa.tipo == TipoTarefa.GERAR_LINK:
    mapping = (resultado or {}).get("mapping") or {}
    if mapping:
        await afiliado_ml_writer.aplicar_mapping(
            db, org_id=tarefa.org_id, mapping=mapping,
        )
```

Pra adicionar side-effect pós-conclusão pra um tipo de tarefa novo, adicione
um branch aqui — só roda quando `ok=True` chega no callback.

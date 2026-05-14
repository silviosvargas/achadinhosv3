# Protocolo cloud ↔ agente local

> **Status: rascunho. A implementação real entra na Fase 2.**

Define como o **servidor cloud** conversa com o **agente local**
(`AchadinhosAgent.exe` rodando no PC do afiliado).

---

## Visão geral

```
┌──────────────┐     wss://achadinhos.app/ws/agente?token=X     ┌─────────────┐
│              │ ─────────────────────────────────────────────▶ │             │
│   SERVIDOR   │ ◀───────────────────────────────────────────── │   AGENTE    │
│              │     conexão WebSocket persistente, TLS         │   (PC)      │
└──────────────┘                                                 └─────────────┘
```

O agente conecta no WebSocket no boot e fica online. Reconecta
automaticamente em caso de queda (backoff exponencial).

---

## Autenticação

1. Admin gera token na dashboard. Token JWT de longa duração (1 ano),
   contendo `org_id`, `usuario_id`, `agente_id`, `tipo: "agente"`.
2. Token é gravado em arquivo cifrado no PC do afiliado
   (`%APPDATA%\Achadinhos\agent.token`).
3. Conexão WS envia o token como query string ou header `Authorization`.
4. Servidor valida e mapeia token → `agente_id`.

Revogação: admin marca `agentes.ativo = false`. Próxima reconexão é negada.

---

## Mensagens — formato

Todas em JSON, UTF-8. Campo obrigatório: `tipo`.

### 📥 Servidor → Agente (comandos)

#### `postar_whatsapp`
```json
{
  "tipo":          "postar_whatsapp",
  "tarefa_id":     12345,
  "grupo_nome":    "Achadinhos - Ofertas 01",
  "texto":         "🔥 Mouse Logitech G203\n💸 R$ 89,90\n👉 https://meli.la/abc",
  "imagem_url":    "https://cdn.achadinhos.app/img/abc.jpg",
  "timeout_seg":   60
}
```

#### `verificar_whatsapp`
```json
{ "tipo": "verificar_whatsapp" }
```

#### `atualizar_agente`
```json
{ "tipo": "atualizar_agente", "versao_alvo": "3.1.0",
  "url": "https://releases.achadinhos.app/agent/3.1.0/setup.exe" }
```

#### `desconectar`
```json
{ "tipo": "desconectar", "motivo": "manutencao" }
```

#### `iniciar_busca_ml` (Fase 4b)
Comando pra agente executar uma busca no Mercado Livre via Selenium.
```json
{
  "tipo":         "iniciar_busca_ml",
  "tarefa_id":    789,
  "busca_id":     12,
  "tipo_entrada": "url",
  "entrada":      "https://lista.mercadolivre.com.br/fone-bluetooth_FretGratis_yes",
  "max_paginas":  3,
  "max_produtos": 50,
  "disparado_por": 1
}
```
- `tipo_entrada`: `"termo"` ou `"url"`. Quando `termo`, agente monta a URL
  (`https://lista.mercadolivre.com.br/{termo-com-hifen}`).
- Agente envia produtos extraídos via REST: `POST /api/v1/produtos/ingest`
  (autenticado com token do agente), **não** via WS. WS fica reservado pra
  postagens em tempo real.

### 📤 Agente → Servidor (eventos)

#### `pong` (resposta a ping do servidor)
```json
{ "tipo": "pong", "ts": "2026-05-01T12:34:56Z" }
```

#### `tarefa_concluida`
```json
{
  "tipo":        "tarefa_concluida",
  "tarefa_id":   12345,
  "duracao_ms":  3200,
  "resultado":   { "mensagem_id_whatsapp": "...", "preview_url": null }
}
```

#### `tarefa_falhou`
```json
{
  "tipo":      "tarefa_falhou",
  "tarefa_id": 12345,
  "erro":      "grupo_nao_encontrado",
  "detalhes":  "Não achei aba do WhatsApp Web aberta",
  "tentar_de_novo": false
}
```

#### `qr_pendente`
WhatsApp deslogou — afiliado precisa escanear QR de novo.
Servidor mostra notificação na dashboard.
```json
{
  "tipo":        "qr_pendente",
  "qr_data_url": "data:image/png;base64,iVBORw0...",
  "expira_em":   "2026-05-01T12:35:30Z"
}
```

#### `busca_progresso` (Fase 4b — opcional)
Relato parcial enquanto a busca ML está em execução. Útil pra UI mostrar
"varrendo página 3 de 5". Servidor só loga por enquanto.
```json
{
  "tipo":      "busca_progresso",
  "tarefa_id": 789,
  "busca_id":  12,
  "pagina_atual":  3,
  "total_paginas": 5,
  "produtos_encontrados_ate_agora": 27
}
```

#### `metricas`
Enviado a cada 60 segundos.
```json
{
  "tipo":          "metricas",
  "ram_mb":        142,
  "cpu_percent":   3.2,
  "chrome_aberto": true,
  "whatsapp_ok":   true,
  "ultima_postagem_em": "2026-05-01T12:30:00Z"
}
```

---

## REST — `POST /api/v1/produtos/ingest` (Fase 4b)

Quando o agente termina uma busca ML, envia os produtos extraídos via REST
(não WS). Autenticação: header `Authorization: Bearer <token do agente>`.

Request:
```json
{
  "busca_id":  12,
  "tarefa_id": 789,
  "produtos": [
    {
      "plataforma":   "ml",
      "item_id":      "MLB1234567890",
      "nome":         "Fone Bluetooth XYZ TWS",
      "preco":        89.90,
      "preco_orig":   159.00,
      "desconto":     44,
      "frete_gratis": true,
      "categoria":    "Eletrônicos > Áudio > Fones de Ouvido",
      "url_canonica": "https://produto.mercadolivre.com.br/MLB-1234567890-fone-...",
      "foto_url":     "https://http2.mlstatic.com/.../IMG.jpg"
    }
  ]
}
```

Response:
```json
{
  "recebidos":   30,
  "criados":     22,
  "atualizados": 7,
  "ignorados":   1,
  "com_nicho":   18,
  "detalhes":    ["item_id=MLB99: item_id ausente"]
}
```

Por que REST e não WS?
- Lotes podem ter dezenas/centenas de produtos — não bloqueia o canal WS
  que é usado pra postagens em tempo real.
- Idempotente (upsert por chave) e fácil de re-enviar em caso de falha.
- Servidor marca a `Tarefa` correspondente como `concluida` automaticamente.

---

## Reconexão e fila

- Servidor mantém `tarefas.status = pendente` até receber `tarefa_concluida`.
- Se agente desconecta no meio de uma tarefa, servidor marca como
  `proxima_tentativa_em = agora + 30s` e tenta de novo quando voltar.
- Limite de tentativas: `tarefas.max_tentativas` (default 3).
- Após esgotar, status vira `falhou` e admin recebe notificação.

---

## Heartbeat

- Servidor envia `{"tipo":"ping"}` a cada 30s.
- Se agente não responde em 90s, considera offline. Marca `agentes.online = false`.
- Tarefas pendentes ficam aguardando reconexão.

---

## Versão do protocolo

Cabeçalho `X-Achadinhos-Protocol-Version: 1` no handshake WebSocket.
Mudanças incompatíveis incrementam o número. Servidor recusa versões muito antigas.

---

## Implementação prevista (Fase 2)

- `app/api/v1/endpoints/ws_agente.py` — endpoint WS no servidor.
- `app/services/dispatcher.py` — entrega tarefas pra agentes online.
- `achadinhos-agent/` — projeto separado (PyInstaller, tray icon).

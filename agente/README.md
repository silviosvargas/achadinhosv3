# Achadinhos Agent

> Agente local que roda no PC do afiliado. Executa duas famílias de tarefas:
> postagem no WhatsApp (Fase 2/3) e buscas no Mercado Livre (Fase 4b).

## O que ele faz

1. Conecta no servidor cloud via WebSocket (TLS)
2. Recebe e processa comandos:
   - `postar_whatsapp` — posta no grupo via Selenium + pyautogui
   - `iniciar_busca_ml` — varre páginas do ML, extrai produtos, envia
     batch pra cloud via `POST /api/v1/produtos/ingest`
3. Reporta sucesso/falha de volta pro servidor
4. Reconecta automaticamente em caso de queda (backoff exponencial)
5. Mostra status na barra de tarefas (tray icon)
6. **HTTP local em `127.0.0.1:5577`** (Fase 9.2) — endpoints `GET /ping`,
   `GET /status`, `POST /pair`, `POST /abrir-tudo`. Ponte com o dashboard
   pro botão "Conectar meu WhatsApp" (ADR-009).

## Como rodar (fluxo recomendado — Fase 6)

### Setup inicial (1ª vez, ~2 minutos)

```bash
cd D:\ACHADINHOSV3\agente
.venv\Scripts\activate

# 1) Setup interativo — pede email/senha da sua conta, registra este PC
#    automaticamente como agente, e grava token em %APPDATA%\Achadinhos\config.json
python -m agent.setup

# 2) Login no Mercado Livre (1ª vez — sessão fica salva por ~30 dias)
python -m agent.login_ml
# Chrome abre, você loga, fecha a janela.

# 3) Login no WhatsApp Web (1ª vez)
python -m agent.login_whatsapp
# Chrome abre, escaneia QR code com seu celular, fecha.
```

### Uso normal (depois do setup)

```bash
python -m agent.main --sem-tray
# Quando ver "ws.conectado", está pronto.
# Volta pro dashboard e use /buscas, /lote, etc.
```

### Setup alternativo (manual — sem precisar de credenciais via API)

```bash
# Se preferir, admin pode gerar token na UI (Agentes → criar) e passar via CLI:
python -m agent.main \
  --token "<COLE_O_TOKEN_AQUI>" \
  --servidor "ws://localhost:8000/api/v1/ws/agente" \
  --sem-tray
```

Se o ML voltar a pedir login (sessão expirou, mudou IP, etc.), você verá
no log do agente:
```
busca.bloqueada motivo='ML exige login (redirecionou pra .../login). Rode UMA vez: python -m agent.login_ml ...'
```
Aí basta rodar `python -m agent.login_ml` de novo.

## Sobre login automatizado

O servidor tem infra pronta pra armazenar `usuario_ml` + `senha_ml` cifrados
(coluna `senha_ml_cifrada` em `usuarios`, endpoint `GET /api/v1/agentes/me/credenciais`,
UI em `/usuarios/{id}/credenciais`). **Mas o agente NÃO automatiza o login.**

Motivo: o ML tem 2FA real ("Escolha um método de verificação") que o Selenium
puro não consegue resolver. Tentar automatizar quebra na primeira conta protegida
e ainda viola o TOS do ML.

**Caminho atual:** o usuário cadastra credenciais na UI se quiser (pra outras
automações futuras), mas pra logar no ML usa `python -m agent.login_ml` manual
uma vez. Sessão fica salva no perfil persistente do Chrome, reusada por dias
até expirar.

Se virar prioridade, podemos adicionar handlers específicos por plataforma
(Selenium customizado + integração com SMS/email pra 2FA) no futuro.

Variáveis úteis (config persistida em `%APPDATA%\Achadinhos\config.json`
após primeiro `--token`):
- `chrome_porta` (default 9222) — Chrome do WhatsApp
- `chrome_porta_ml` (default 9223) — Chrome dedicado pra buscas ML
- `ml_headless` (default false em dev) — esconde janela do Chrome ML

## Como instalar (afiliado final)

1. Baixa `AchadinhosAgent.exe` (gerado por `pyinstaller build.spec`)
2. Roda o instalador
3. No primeiro uso, cola o **token** que o admin enviou
4. O agente vai abrir Chrome em modo debug com perfil isolado
5. Faz login no WhatsApp Web (1ª vez)
6. Pronto — daí em diante é automático

## Estrutura

```
achadinhos-agent/
├── agent/
│   ├── main.py              # entrypoint (boot do app + tray)
│   ├── config.py            # carrega/salva config local cifrada
│   ├── ws_client.py         # cliente WebSocket persistente
│   ├── tray.py              # ícone na barra de tarefas
│   ├── chrome.py            # gerencia Chrome em modo debug + perfil
│   └── postador/
│       ├── __init__.py
│       └── whatsapp.py      # ⚠️ PORTAR DA V2 — src/postar/whatsapp.py
├── pyproject.toml           # dependências
├── build.spec               # config PyInstaller
└── README.md
```

## ⚠️ Trabalho pendente

O arquivo `agent/postador/whatsapp.py` está como **stub**. A lógica real
de postagem (selenium, pyautogui, win32clipboard) está na V2 em
`src/postar/whatsapp.py`. Você precisa portar adaptando:

- Substituir leituras do banco SQLite por argumentos recebidos via WebSocket
- Substituir `_postar_msg(produto, grupo)` por uma função que recebe
  `texto: str, imagem_path: str, grupo_nome: str` e retorna sucesso/erro
- Manter o Chrome em modo debug + perfil persistente (já funciona na V2)

A função-alvo no agente:

```python
def postar_whatsapp(*, grupo: str, texto: str, imagem_url: str | None) -> dict:
    """
    Retorna { "ok": True } em sucesso, ou
            { "ok": False, "erro": "...", "tentar_de_novo": bool }
    """
    ...
```

## Como rodar em dev (sem PyInstaller)

```bash
cd achadinhos-agent
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
python -m agent.main --token "<seu_token>" --servidor "ws://localhost:8000/ws/agente"
```

## Como gerar o .exe

```bash
pyinstaller build.spec
# Gera dist/AchadinhosAgent.exe
```

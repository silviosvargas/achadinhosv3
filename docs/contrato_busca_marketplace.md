# Contrato: scrapers de busca por marketplace

> **LEIA isso antes de adicionar suporte a um marketplace novo** (Magalu,
> AliExpress, TikTok Shop, etc) ou de implementar **busca personalizada**.

Atualmente suportados (todos validados em prod): **Mercado Livre · Shopee · Amazon**.

---

## Padrão arquitetural

Cada marketplace tem **seu próprio módulo** em `agente/agent/busca_<slug>.py`
seguindo o mesmo esqueleto. O orquestrador `agente/agent/busca_ml.py` registra
cada coletor num dict global:

```python
_COLETORES_POR_MARKETPLACE = {
    "ml":     _coletar_produtos_ml,
    "shopee": _coletar_produtos_shopee,
    "amazon": _coletar_produtos_amazon,
    # NOVO: "magalu": _coletar_produtos_magalu,
}
```

Quando `executar_busca` recebe `msg["marketplaces"] = ["ml", "shopee", "magalu"]`,
itera o dict e chama cada coletor em sequência (serializado por marketplace
porque cada um abre 1 Chrome).

---

## Checklist completo pra adicionar marketplace novo

### 1. Config: perfil Chrome dedicado

`agente/agent/config.py` — adicionar property:

```python
@property
def chrome_perfil_magalu(self) -> str:
    """Perfil Chrome dedicado pra sessão Magalu Parceiros."""
    return str(_config_dir() / "chrome_perfil_magalu")
```

> **Por que perfil dedicado**: cada marketplace mantém sua sessão logada num
> diretório próprio. Sem isso, login expira ao trocar de site e o `_LOCK_CHROME`
> de outros marketplaces pode bloquear.

### 2. Módulo do scraper: `agente/agent/busca_<slug>.py`

Estrutura **obrigatória** (espelhar `busca_shopee.py` ou `busca_amazon.py`):

```python
"""Scraper <Marketplace> — estratégia <API interna / scraping bestsellers / etc>"""
from __future__ import annotations
import asyncio, threading, time
from typing import Any
import structlog
import undetected_chromedriver as uc

from agent.config import Config

log = structlog.get_logger(__name__)

# Constantes do marketplace
URL_LOGIN      = "https://parceiros.magalu.com.br/login"
URL_PRINCIPAL  = "https://parceiros.magalu.com.br/produtos"
ESPERA_FIXA_SEG = 30
MAX_TENTATIVAS  = 3

# Lock — só 1 Chrome <marketplace> por vez (mesmo perfil)
_LOCK_CHROME = threading.Lock()


def _criar_driver(cfg: Config) -> uc.Chrome:
    """Chrome dedicado pra esse marketplace, perfil persistente."""
    opts = uc.ChromeOptions()
    opts.add_argument(f"--user-data-dir={cfg.chrome_perfil_magalu}")
    opts.add_argument("--profile-directory=Default")
    opts.add_argument("--no-first-run")
    opts.add_argument("--no-default-browser-check")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1366,900")
    return uc.Chrome(options=opts, use_subprocess=True)


def _detectou_bloqueio(driver: uc.Chrome) -> tuple[str, str] | None:
    """Retorna (motivo, instrucao_user) ou None se OK.

    motivo deve ser uma das chaves padrão:
    - "login_expirado"
    - "captcha"
    - "bloqueio" (genérico — rate limit, etc)
    """
    url = (driver.current_url or "").lower()
    if "/login" in url or "/signin" in url:
        return (
            "login_expirado",
            "🔐 Faça login no painel <Marketplace> aqui, depois volte. Vou esperar.",
        )
    if "captcha" in url:
        return (
            "captcha",
            "🤖 <Marketplace> pediu CAPTCHA. Resolva nesta janela do Chrome.",
        )
    return None


def _verificar_login(driver: uc.Chrome) -> tuple[str, str] | None:
    """OPCIONAL mas recomendado: acessa uma página protegida pra
    confirmar login. Mais confiável que o `_detectou_bloqueio` que
    só capta sintomas óbvios.

    Idealmente acessa uma URL que SÓ funciona logado (ex: /home,
    /dashboard) — se redirecionar pra /login, sabemos que falhou.
    """
    try:
        driver.get("https://parceiros.magalu.com.br/home")
        time.sleep(3)
    except Exception:
        return ("erro_rede", "⚠ Não consegui acessar <Marketplace>.")
    return _detectou_bloqueio(driver)
```

### 3. Modo interativo — banner + retry 30s × 3

**Padrão único pra captcha + login** (cf `busca_amazon.py`):

```python
_BANNER_JS = """..."""  # copia do busca_shopee.py / busca_amazon.py

def _mostrar_banner(driver, mensagem):
    try:
        driver.execute_script(_BANNER_JS, mensagem)
        driver.execute_script("window.focus();")
        driver.maximize_window()
    except Exception: pass

def _aguardar_com_retry(
    driver, *, tipo_aviso: str, mensagem: str,
    url_revalidacao: str = URL_PRINCIPAL,
) -> bool:
    """
    Estratégia ÚNICA pra captcha + login: 30s fixos × 3 tentativas.

    1. Mostra banner amarelo no Chrome
    2. Publica aviso no dashboard via `agent.avisos.publicar`
    3. Espera 30s FIXOS (não polling — cobre captcha em nova aba)
    4. Recarrega URL_principal e re-verifica
    5. Se desbloqueou (e, pra login, _verificar_login confirma) → True
    6. Senão, próxima tentativa
    """
    from agent import avisos
    try:
        for tentativa in range(1, MAX_TENTATIVAS + 1):
            msg_tent = f"{mensagem}\n\nTentativa {tentativa}/{MAX_TENTATIVAS} — aguardando {ESPERA_FIXA_SEG}s..."
            _mostrar_banner(driver, msg_tent)
            avisos.publicar(
                tipo_aviso, mensagem,
                detalhe=f"Tentativa {tentativa}/{MAX_TENTATIVAS} — "
                        f"aguardando {ESPERA_FIXA_SEG}s. "
                        "Após resolver, vou re-testar automaticamente.",
                marketplace="magalu", ttl_seg=ESPERA_FIXA_SEG + 30,
            )
            time.sleep(ESPERA_FIXA_SEG)
            try:
                driver.get(url_revalidacao); time.sleep(3)
            except Exception:
                continue
            if _detectou_bloqueio(driver) is None:
                # Pra login: confirma via _verificar_login (dupla checagem)
                if tipo_aviso == "login_expirado" and _verificar_login(driver):
                    continue
                return True
        return False
    finally:
        avisos.limpar(marketplace="magalu")
```

### 4. Loop principal `_varrer_sync` + async wrapper

```python
def _varrer_sync(cfg: Config, *, max_produtos: int) -> list[dict[str, Any]]:
    log.info("magalu.aguardando_lock", max_produtos=max_produtos)
    with _LOCK_CHROME:
        driver = _criar_driver(cfg)
        try:
            # PRIMEIRO: verifica login. Se não logado, banner + retry 30s × 3.
            problema = _verificar_login(driver)
            if problema:
                motivo, msg = problema
                if not _aguardar_com_retry(driver, tipo_aviso=motivo, mensagem=msg):
                    raise RuntimeError(
                        f"Magalu {motivo} — usuário não resolveu em {MAX_TENTATIVAS} "
                        f"tentativas. Rode `python -m agent.login_magalu`."
                    )
            # Resto: itera categorias / extrai cards / gera links de afiliado / etc
            ...
        finally:
            try: driver.quit()
            except Exception: pass
            time.sleep(1.0)


async def buscar_magalu(cfg: Config, *, max_produtos: int = 50) -> list[dict[str, Any]]:
    """Async wrapper — Selenium em thread separada."""
    return await asyncio.to_thread(_varrer_sync, cfg, max_produtos=max_produtos)
```

### 5. Login manual: `agente/agent/login_<slug>.py`

Análogo a `login_ml.py`, `login_shopee.py`, `login_amazon.py`. Abre Chrome
com perfil dedicado em URL de login, espera user logar e fechar a janela.

```python
"""Login manual em <Marketplace> — abre Chrome com perfil persistente."""
# Copia template de login_amazon.py, ajusta URL e perfil.
```

### 6. Formato de produto retornado

**Obrigatório** — cada produto deve ser dict com TODOS estes campos
(servidor `_upsert_produto` espera):

```python
{
    "plataforma":   "magalu",                 # slug do marketplace (lowercase)
    "item_id":      "MAG12345",               # ID único no marketplace
    "nome":         "Produto X 256GB",        # até 500 chars
    "preco":        199.90,                   # float > 0 obrigatório
    "preco_orig":   299.90,                   # float ou None
    "desconto":     33.0,                     # % ou None
    "comissao":     8.0,                      # % estimada por categoria
    "frete_gratis": False,                    # bool
    "categoria":    "Eletrônicos > Áudio",    # path tipo breadcrumb
    "url_canonica": "https://magalu.com.br/produto/X",  # SEM query/fragment
    "url_afiliado": "https://parceiros.magalu.com.br/r/abc",  # ou None se fallback
    "foto_url":     "https://cdn.magalu.com.br/...jpg",
}
```

**URL canônica DEVE estar limpa** (sem `?...` ou `#...`) — limpe na hora
da extração (cf `busca_ml.py:_achar_url`):

```python
href = (anchor.get_attribute("href") or "").split("?", 1)[0].split("#", 1)[0]
```

### 7. Integração no orquestrador `busca_ml.py`

Adicionar coletor + registrar no dict:

```python
async def _coletar_produtos_magalu(
    msg: dict[str, Any], cfg: Config,
) -> tuple[list[dict[str, Any]], str | None]:
    max_produtos = int(msg.get("max_produtos", 50))
    try:
        from agent.busca_magalu import buscar_magalu
        produtos = await buscar_magalu(cfg, max_produtos=max_produtos)
    except RuntimeError as e:
        return [], f"magalu_bloqueou: {str(e)[:300]}"
    except Exception as e:
        log.exception("busca_magalu.crash", erro=str(e))
        return [], f"magalu_crash: {type(e).__name__}: {str(e)[:200]}"
    return produtos, None


_COLETORES_POR_MARKETPLACE = {
    "ml":     _coletar_produtos_ml,
    "shopee": _coletar_produtos_shopee,
    "amazon": _coletar_produtos_amazon,
    "magalu": _coletar_produtos_magalu,   # ← novo
}
```

### 8. UI: habilita checkbox em `busca_form.html`

```jinja
{% set _marketplaces = [
  ('ml',     'Mercado Livre',  '🛒', true),
  ('shopee', 'Shopee',         '🛍️', true),
  ('amazon', 'Amazon',         '📦', true),
  ('magalu', 'Magazine Luiza', '🌟', true),    # ← troca false pra true
  ('aliexpress', 'AliExpress', '🌏', false),
  ('tiktok', 'TikTok Shop',    '🎵', false),
] %}
```

### 9. Servidor: aceita slug em `_MARKETPLACES_SUPORTADOS`

`app/web/routes.py`:

```python
_MARKETPLACES_SUPORTADOS = {"ml", "shopee", "amazon", "magalu"}
```

### 10. Validação de tag de afiliado no servidor

`app/services/busca_service.py:_upsert_produto` valida que `url_afiliado`
recebido do agente **contém a tag esperada** (cascata
`afiliado_service.tag_com_cascata`). Suporta:

- **Shorteners oficiais** (passa sem validar tag — redirect aplica):
  `meli.la/`, `s.shopee.com.br/`, `shp.ee/`, `amzn.to/`
- **URL com tag visível na query**: substring match com a tag do admin

**Pra adicionar shortener novo** (ex: Magalu tem `parceiros.magalu.com.br/r/...`):
edita a tupla em `_upsert_produto`:

```python
"meli.la/" in url_afiliado_agente
or "s.shopee.com.br/" in url_afiliado_agente
or "shp.ee/" in url_afiliado_agente
or "amzn.to/" in url_afiliado_agente
or "parceiros.magalu.com.br/r/" in url_afiliado_agente   # ← novo
```

### 11. Build PyInstaller `agente/build.spec`

```python
hiddenimports=[
    ...,
    'agent.busca_magalu',   # ← novo
    ...,
]
```

### 12. Bump versão + tag

Edita 3 arquivos:
- `agente/agent/local_server.py` → `VERSAO_AGENTE`
- `agente/pyproject.toml` → `version`
- `agente/installer.iss` → `MyAppVersion`

Versionamento: novo marketplace = **minor bump** (3.X.0 → 3.X+1.0).
Patch interno = patch bump (3.X.Y → 3.X.Y+1).

---

## Padrão pra busca personalizada (`tipo_busca` novo)

Quando o user pediu **"busca por URL/link"** (Fase 16.4), a abordagem foi:

1. **Roteamento dentro de `_coletar_produtos_ml`** (não num módulo separado),
   porque era um novo TIPO de busca dentro do mesmo marketplace.
2. **Novo handler síncrono** `_varrer_produto_unico_sync(cfg, url)`.
3. **UI** já tinha o dropdown `tipo_busca` com `por_url`.

**Receita pra novo `tipo_busca`** (ex: `por_categoria_id`, `melhor_avaliacao`,
`destaque_homepage`, etc):

1. Adiciona opção no `<select>` de `app/web/templates/busca_form.html`
2. Adiciona handler `_varrer_<tipo>_sync(cfg, ...)` em `busca_ml.py`
   (ou no módulo do marketplace alvo)
3. Adiciona branch em `_coletar_produtos_<marketplace>` que rotea por
   `msg.get("tipo_busca")`
4. Se for específico de UM marketplace (ex: "best reviewed amazon"),
   adiciona handler dentro do módulo dele

**Por que NÃO criar módulo separado por tipo de busca:** mantém afinidade
com o marketplace alvo. ML "mais vendidos" e ML "termo livre" usam o
MESMO driver Chrome, mesmo perfil, mesmas estratégias de extração de card.

---

## Lições registradas (NÃO repetir)

1. **Handler WS PRECISA retornar `{"ok": True}`** — cf `docs/contrato_handlers_ws.md`.
   Sem isso, ws_client envia `tarefa_falhou` e hooks pós-conclusão nunca rodam.

2. **NÃO criar Tarefa GERAR_LINK separada pra cada marketplace.** A V3 fazia
   isso pra ML (linkbuilder via tarefa). Levou 6 releases pra descobrir que
   o callback nunca chegava. **Gera o link de afiliado INLINE no mesmo
   driver Chrome da busca**, antes de enviar ingest. Servidor só salva
   o que recebe pronto.

3. **URL canônica DEVE ser limpa na extração** (V2-style `split('?')[0]`).
   Não confie em camadas posteriores normalizarem.

4. **Lock por marketplace** (`_LOCK_CHROME_<MKT>`) impede conflito de
   user-data-dir quando 2 buscas chegam próximas no agente.

5. **Modo interativo padronizado**: 30s fixos × 3 tentativas pra TUDO
   que precisa intervenção humana (captcha + login). Polling longo é
   ruim porque captcha em nova aba não dispara mudança de URL.

6. **Banner Chrome + aviso dashboard** SEMPRE — o user precisa ver de
   QUALQUER lugar (PC do agente OU dashboard mobile do celular).

7. **Confiança no agente pra `url_afiliado`**: servidor aceita o link
   recebido se contém um **shortener oficial conhecido** (lista em
   `_upsert_produto`) OU **substring com a tag do admin**. Senão, gera
   fallback genérico com a tag via `linkbuilder.gerar_url_afiliado`.

8. **Pra CAPTURAR comissão da barra de afiliados ML** (padrão Fase 18.3):
   o agente DEVE **abrir o shortener (`meli.la/XXX`) → ML redireciona pra
   `/social/<usuario>` → clicar no botão "Ir para produto" → CHEGAR na
   página completa do produto → captura barra preta** ("GANHOS X%" ou
   "GANHOS EXTRAS X%").

   Por quê? Abrir o `meli.la` carrega o contexto do afiliado correto
   (tag + tool + ref do shortlink). Abrir URL canônica direto pega
   comissão genérica do Chrome logado, NÃO a comissão real do programa
   daquele link específico.

   A página `/social/` mostra um card resumo + botão "Ir para produto"
   — a barra preta só aparece DEPOIS do clique nesse botão.

   Implementação ML: `agente/agent/busca_ml.py:_capturar_comissao_da_barra(driver, url_meli_la)`.

   Pra outros marketplaces com fluxo similar (Shopee `s.shopee.com.br`,
   Amazon `amzn.to`): adaptar o mesmo princípio — abre shortener, segue
   redirects/cliques até chegar onde a comissão real está exposta.

   **Regra de ouro**: o user OPERA o painel de afiliados — ele é a
   fonte de verdade sobre o fluxo real. Se a captura voltar vazia, NÃO
   inventar abordagem nova; perguntar ao user qual é o caminho correto.

---

## Marketplaces no roadmap

| Marketplace | Status | Estratégia | Notas |
|---|---|---|---|
| ✅ Mercado Livre | Produção | Scraping listagem/produto + linkbuilder painel oficial → `meli.la` | Validado em prod |
| ✅ Shopee | Produção | API interna `affiliate.shopee.com.br/api/v3/offer/product/list` retorna `long_link` pronto | Mais simples (API direta) |
| ✅ Amazon | Produção | Scraping `/gp/bestsellers/<cat>` + SiteStripe → `amzn.to` | Mais lento (SiteStripe um por um) |
| 🚧 Magazine Luiza | Pendente | Provavelmente Magalu Parceiros + API ou scraping | A pesquisar |
| 🚧 AliExpress | Pendente | API Portals (precisa credenciais) ou scraping | A pesquisar |
| 🚧 TikTok Shop | Pendente | Limbo. Sem painel afiliados BR consolidado em 2026 | Aguardar maturidade |

---

## Referências de código (espelhar quando criar novo)

- **busca_ml.py** — múltiplos tipos de busca (`mais_vendidos`, `termo_livre`,
  `por_url`, `melhor_comissao`, `em_alta`), linkbuilder inline
- **busca_shopee.py** — API interna via `fetch()` JS, banner + retry, lock
- **busca_amazon.py** — scraping + SiteStripe, verificação login no início,
  retry 30s × 3 padronizado (template mais completo)

Pra um marketplace novo: copia `busca_amazon.py` (estrutura mais robusta)
e adapta. Login manual → copia `login_amazon.py`.

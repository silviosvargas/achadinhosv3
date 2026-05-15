"""
Setup do agente (Fase 6).

Substitui o fluxo "admin gera token → copia → cola no .env" por um simples:

    python -m agent.setup

Pede:
  - URL do servidor (default http://localhost:8000)
  - Email/login da sua conta no Achadinhos
  - Senha
  - Nome do PC (default: hostname)

Faz:
  1. POST /api/v1/auth/login   → obtém JWT de acesso
  2. POST /api/v1/agentes/registrar-self → cria agente PRA VOCÊ + retorna token
  3. Grava config em %APPDATA%\\Achadinhos\\config.json (sem precisar reabrir)

Próximo passo após o setup: `python -m agent.main` (já lê a config salva).
"""
from __future__ import annotations

import getpass
import platform
import socket
import sys

import httpx

from agent.config import Config


DEFAULT_SERVIDOR = "http://localhost:8000"


def _ler(label: str, default: str = "") -> str:
    prompt = f"{label}"
    if default:
        prompt += f" [{default}]"
    prompt += ": "
    valor = input(prompt).strip()
    return valor or default


def _detectar_sistema_op() -> str:
    sis = platform.system()
    rel = platform.release()
    return f"{sis} {rel}".strip()


def main() -> int:
    print()
    print("=" * 70)
    print("SETUP DO ACHADINHOS AGENT")
    print("=" * 70)
    print()
    print("Vou cadastrar este PC como um agente seu no servidor.")
    print("Preciso do email/login e senha que voce usa pra entrar no dashboard.")
    print()

    servidor = _ler("URL do servidor Achadinhos", DEFAULT_SERVIDOR).rstrip("/")
    login    = _ler("Seu login (ou email)")
    senha    = getpass.getpass("Sua senha: ").strip()
    nome_pc  = _ler("Nome deste PC", socket.gethostname())

    if not login or not senha:
        print("ERRO: login e senha sao obrigatorios.", file=sys.stderr)
        return 1

    # Algumas contas têm o mesmo login em orgs diferentes; nesse caso o
    # servidor exige org_slug. Perguntamos só se a primeira tentativa falhar.
    print()
    print("Conectando no servidor...", servidor)

    try:
        with httpx.Client(timeout=15.0) as cli:
            # 1. Login → access_token
            r = cli.post(
                f"{servidor}/api/v1/auth/login",
                json={"login": login, "senha": senha},
            )
            if r.status_code == 400 and "org_slug" in (r.text or "").lower():
                # Login em mais de uma org — pede slug
                org_slug = _ler("Slug da sua organização (aparece na URL do dashboard)")
                r = cli.post(
                    f"{servidor}/api/v1/auth/login",
                    json={"login": login, "senha": senha, "org_slug": org_slug},
                )
            if r.status_code != 200:
                print(f"ERRO no login: HTTP {r.status_code} — {r.text[:200]}",
                      file=sys.stderr)
                return 2
            jwt_acesso = r.json()["access_token"]
            user_info = r.json().get("usuario", {})
            print(f"  [ok] Login OK como '{user_info.get('login')}' (org_id={user_info.get('org_id')})")

            # 2. Cria agente automaticamente
            r = cli.post(
                f"{servidor}/api/v1/agentes/registrar-self",
                headers={"Authorization": f"Bearer {jwt_acesso}"},
                json={"nome": nome_pc, "sistema_op": _detectar_sistema_op()},
            )
            if r.status_code not in (200, 201):
                print(f"ERRO no registro do agente: HTTP {r.status_code} — {r.text[:200]}",
                      file=sys.stderr)
                return 3
            data = r.json()
            agente_id = data["agente"]["id"]
            token = data["token"]
            ws_url = data["ws_url"]
            api_url = data["api_url"]
            print(f"  [ok] Agente #{agente_id} '{nome_pc}' criado")
    except httpx.HTTPError as e:
        print(f"ERRO de rede: {type(e).__name__}: {e}", file=sys.stderr)
        return 4

    # 3. Salva config local
    cfg = Config.from_args(
        token=token,
        servidor_ws=ws_url,
        chrome_porta=9222,
    )
    cfg.salvar()
    print(f"  [ok] Config salva em {cfg.config_dir / 'config.json'}")

    print()
    print("=" * 70)
    print("SETUP CONCLUIDO")
    print("=" * 70)
    print()
    print("Proximos passos:")
    print(f"  1. Logar no Mercado Livre (1x):")
    print(f"     python -m agent.login_ml")
    print(f"  2. Logar no WhatsApp Web (1x):")
    print(f"     python -m agent.login_whatsapp")
    print(f"  3. Rodar o agente:")
    print(f"     python -m agent.main --sem-tray")
    print()
    print(f"Dashboard: {api_url}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())

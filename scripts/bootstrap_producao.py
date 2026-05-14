"""
Bootstrap idempotente pra rodar antes do uvicorn em produção.

Encadeia:
1. alembic upgrade head  (migrations)
2. criar_admin           (cria org+admin inicial se não existir)

Uso (Railway / container prod):
    python -m scripts.bootstrap_producao && \
    uvicorn app.main:app --host 0.0.0.0 --port $PORT

É seguro re-rodar — todos os passos são idempotentes.
"""
from __future__ import annotations

import subprocess
import sys


def passo(nome: str, comando: list[str]) -> bool:
    print(f"\n=== {nome} ===")
    print(f"$ {' '.join(comando)}")
    try:
        result = subprocess.run(comando, check=False, capture_output=False)
        if result.returncode == 0:
            print(f"  [ok] {nome}")
            return True
        print(f"  [FAIL] {nome} retornou {result.returncode}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"  [FAIL] {nome}: {type(e).__name__}: {e}", file=sys.stderr)
        return False


def main() -> int:
    # 1. Migrations
    if not passo("Alembic upgrade head", ["alembic", "upgrade", "head"]):
        return 1

    # 2. Admin inicial (idempotente — não cria se já existir)
    if not passo("Criar admin inicial", [sys.executable, "-m", "scripts.criar_admin"]):
        # Falha aqui não é fatal — admin pode já existir, ou .env não ter
        # ADMIN_* configurado. Log e segue (uvicorn vai subir).
        print("  [WARN] criar_admin falhou — segue pra subir o servidor mesmo assim.")

    print("\n=== Bootstrap concluído ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())

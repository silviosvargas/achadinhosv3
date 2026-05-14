"""
Gera os ícones PWA (192x192 e 512x512) em app/web/static/icons/.

Roda 1x — depois fica versionado no repo. Re-rode se quiser mudar a aparência.

Uso (dentro do container):
    docker compose exec api python -m scripts.gerar_icones_pwa
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


# Cor do tema (verde, casando com theme-color do manifest)
COR_FUNDO   = (22, 163, 74)   # #16a34a
COR_TEXTO   = (255, 255, 255)
TEXTO_ICON  = "🛒"

# Diretório destino — app/web/static/icons relativo ao /app no container
DEST_DIR = Path(__file__).resolve().parent.parent / "app" / "web" / "static" / "icons"
TAMANHOS = [192, 512]


def carregar_fonte(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Tenta achar uma fonte com suporte a emoji; cai pra padrão se nenhuma."""
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Apple Color Emoji.ttc",
        "C:\\Windows\\Fonts\\seguiemj.ttf",
    ]
    for p in candidates:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size=size)
            except Exception:
                continue
    return ImageFont.load_default()


def gerar_icone(tamanho: int, destino: Path) -> None:
    img = Image.new("RGB", (tamanho, tamanho), COR_FUNDO)
    draw = ImageDraw.Draw(img)

    # Tenta usar emoji 🛒; se a fonte do ambiente não renderiza, faz letra "A"
    fonte_size = int(tamanho * 0.55)
    fonte = carregar_fonte(fonte_size)
    texto = TEXTO_ICON

    bbox = draw.textbbox((0, 0), texto, font=fonte)
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    # textbbox pode dar offset negativo; corrige
    x = (tamanho - w) // 2 - bbox[0]
    y = (tamanho - h) // 2 - bbox[1]

    # Algumas fontes não suportam emoji -> a box vem 0x0. Detecta e usa fallback.
    if w == 0 or h == 0:
        texto = "A"
        bbox = draw.textbbox((0, 0), texto, font=fonte)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        x = (tamanho - w) // 2 - bbox[0]
        y = (tamanho - h) // 2 - bbox[1]

    # Pillow >= 10 com `embedded_color=True` renderiza emoji colorido em
    # algumas fontes. Tenta — se der erro, cai pra texto monocromático.
    try:
        draw.text((x, y), texto, font=fonte, fill=COR_TEXTO, embedded_color=True)
    except (TypeError, OSError):
        draw.text((x, y), texto, font=fonte, fill=COR_TEXTO)

    img.save(destino, "PNG", optimize=True)
    print(f"  [ok] {destino.name}  ({tamanho}x{tamanho}, {destino.stat().st_size} bytes)")


def main() -> int:
    DEST_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Gerando ícones PWA em {DEST_DIR}")
    for t in TAMANHOS:
        gerar_icone(t, DEST_DIR / f"icon-{t}.png")
    print("Pronto.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Genera los iconos de la PWA (xrpbot_ui/static/icons). Requiere Pillow (solo para regenerarlos).

    python scripts/make_icons.py
"""
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "xrpbot_ui" / "static" / "icons"
BG, BLUE, ORANGE = (18, 18, 17), (57, 135, 229), (235, 130, 50)


def draw(size: int, safe: float) -> Image.Image:
    s = size * 4                                   # se dibuja a 4x y se reduce (bordes suaves)
    img = Image.new("RGB", (s, s), BG)
    d = ImageDraw.Draw(img)
    inner = s * safe                               # zona útil (maskable: 80 % central)
    x0 = (s - inner) / 2
    w = inner / 4                                  # 3 velas de ancho w separadas por w/2
    left0 = x0
    y = lambda f: (s - inner) / 2 + f * inner      # noqa: E731
    lw = max(4, int(s * 0.018))
    # (cuerpo arriba, cuerpo abajo, mecha arriba, mecha abajo, relleno) en fracciones de la zona útil
    candles = [(.45, .80, .32, .92, ORANGE), (.30, .62, .18, .74, None), (.10, .42, .02, .55, None)]
    for i, (top, bot, wt, wb, fill) in enumerate(candles):
        left = left0 + i * 1.5 * w
        cx = left + w / 2
        color = fill or BLUE
        d.line([(cx, y(wt)), (cx, y(top))], fill=color, width=lw)
        d.line([(cx, y(bot)), (cx, y(wb))], fill=color, width=lw)
        box = [left, y(top), left + w, y(bot)]
        if fill:
            d.rectangle(box, fill=fill)
        else:
            d.rectangle(box, outline=BLUE, width=lw)
    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    draw(180, 0.62).save(OUT / "apple-touch-icon.png")      # iOS: sin transparencia, iOS redondea las esquinas
    draw(192, 0.62).save(OUT / "icon-192.png")
    draw(512, 0.62).save(OUT / "icon-512.png")
    draw(512, 0.50).save(OUT / "icon-maskable-512.png")     # Android: el recorte puede comerse el 20 % exterior
    draw(32, 0.70).save(OUT / "favicon-32.png")
    print("Iconos en", OUT)


if __name__ == "__main__":
    main()

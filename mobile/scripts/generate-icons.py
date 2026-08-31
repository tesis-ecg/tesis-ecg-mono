"""Genera el ícono de la app y el logo del splash a partir de la marca.

La marca usa la geometría del ícono `Activity` de Lucide, el mismo componente
que dibuja `src/components/BrandMark.tsx`. La ruta se reproduce acá para que los
assets nativos no dependan de una fuente de íconos en tiempo de generación.

Correr desde `mobile/`:  python3 scripts/generate-icons.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets/images"

ACTIVITY_POINTS = ((22, 12), (18, 12), (15, 21), (9, 3), (6, 12), (2, 12))

# Los mismos tokens de `src/global.css` y `src/lib/gradients.ts`.
START = (26, 62, 223)  # primary-400 #1a3edf
MID = (11, 33, 133)  # primary-500 #0b2185
END = (8, 27, 115)  # primary-600 #081b73


def brand_gradient(size: int) -> Image.Image:
    """El gradiente de marca, en diagonal, igual que el de la app (160deg)."""
    image = Image.new("RGB", (size, size))
    pixels = image.load()
    for y in range(size):
        for x in range(size):
            # 160deg en CSS: mayormente hacia abajo, apenas hacia la izquierda.
            t = (y * 0.94 + (size - x) * 0.34) / (size * 1.28)
            t = min(max(t, 0.0), 1.0)
            if t < 0.55:
                k = t / 0.55
                base, target = START, MID
            else:
                k = (t - 0.55) / 0.45
                base, target = MID, END
            pixels[x, y] = tuple(round(base[i] + (target[i] - base[i]) * k) for i in range(3))
    return image


def activity_mark(size: int, color: tuple[int, int, int, int]) -> Image.Image:
    """El trazo `Activity` de Lucide sobre un lienzo transparente."""
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    scale = size / 24
    points = [(round(x * scale), round(y * scale)) for x, y in ACTIVITY_POINTS]
    width = max(1, round(2 * scale))
    draw.line(points, fill=color, width=width, joint="curve")
    radius = width / 2
    for x, y in points:
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color)
    return layer


def compose(size: int, glyph_ratio: float) -> Image.Image:
    """Ícono completo: gradiente + glifo blanco encima."""
    icon = brand_gradient(size).convert("RGBA")
    mark = activity_mark(round(size * glyph_ratio), (255, 255, 255, 255))
    offset = (size - mark.width) // 2
    icon.alpha_composite(mark, (offset, offset))
    return icon


def main() -> None:
    # iOS y el genérico. Sin transparencia: iOS aplica su propia máscara.
    compose(1024, 0.56).convert("RGB").save(OUT / "icon.png")

    # El splash corre sobre el navy sólido de `app.json`, así que va solo el
    # glifo en blanco y con fondo transparente.
    activity_mark(512, (255, 255, 255, 255)).save(OUT / "splash-icon.png")

    compose(96, 0.56).convert("RGB").save(OUT / "favicon.png")

    # Android adaptativo: el sistema recorta, así que el glifo va más chico para
    # quedar dentro de la zona segura (66% del lienzo).
    brand_gradient(432).save(OUT / "android-icon-background.png")
    fg = Image.new("RGBA", (432, 432), (0, 0, 0, 0))
    mark = activity_mark(round(432 * 0.42), (255, 255, 255, 255))
    fg.alpha_composite(mark, ((432 - mark.width) // 2, (432 - mark.width) // 2))
    fg.save(OUT / "android-icon-foreground.png")
    # El monocromo (Material You) es la silueta: mismo glifo, en negro.
    mono = Image.new("RGBA", (432, 432), (0, 0, 0, 0))
    black = activity_mark(round(432 * 0.42), (0, 0, 0, 255))
    mono.alpha_composite(black, ((432 - black.width) // 2, (432 - black.width) // 2))
    mono.save(OUT / "android-icon-monochrome.png")

    # Android exige un glifo blanco transparente separado para la bandeja de
    # notificaciones. Reusar el ícono a color lo convierte en un bloque opaco.
    notification = Image.new("RGBA", (96, 96), (0, 0, 0, 0))
    notification_mark = activity_mark(round(96 * 0.62), (255, 255, 255, 255))
    notification.alpha_composite(
        notification_mark,
        ((96 - notification_mark.width) // 2, (96 - notification_mark.width) // 2),
    )
    notification.save(OUT / "notification-icon.png")

    print("Escritos en", OUT)


if __name__ == "__main__":
    main()

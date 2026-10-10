"""Génère les icônes PNG (balle de tennis sur fond vert court). Usage: python tools/make_icons.py"""
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "app" / "static" / "icons"
GREEN, BALL, SEAM = (31, 77, 58), (214, 226, 74), (255, 255, 255)


def ball(size: int, pad: float, bg, color=BALL, seam=SEAM, radius=0.22, seam_w=0.035) -> Image.Image:
    s = size * 4  # suréchantillonnage pour l'anticrénelage
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    if bg:
        ImageDraw.Draw(img).rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * radius), fill=bg)
    p = int(s * pad)
    r = s - 2 * p
    # balle et coutures sur un calque, découpé ensuite par le cercle : rien ne déborde
    layer = Image.new("RGBA", (s, s), color)
    d = ImageDraw.Draw(layer)
    w = max(4, int(s * seam_w))
    top, bottom = p + int(r * 0.08), s - p - int(r * 0.08)
    d.arc([p - int(r * 0.62), top, p + int(r * 0.38), bottom], -70, 70, fill=seam, width=w)
    d.arc([s - p - int(r * 0.38), top, s - p + int(r * 0.62), bottom], 110, 250, fill=seam, width=w)
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).ellipse([p, p, s - p, s - p], fill=255)
    img.paste(layer, (0, 0), mask)
    return img.resize((size, size), Image.LANCZOS)


OUT.mkdir(parents=True, exist_ok=True)
ball(192, 0.16, GREEN).save(OUT / "icon-192.png")
# onglet du navigateur : balle plus grande et coutures plus épaisses pour rester lisible en 16-32 px
ball(32, 0.08, GREEN, seam_w=0.07).save(OUT / "favicon-32.png")
ball(512, 0.16, GREEN).save(OUT / "icon-512.png")
ball(512, 0.24, GREEN, radius=0).save(OUT / "icon-maskable-512.png")
ball(180, 0.16, GREEN, radius=0).convert("RGB").save(OUT / "apple-touch-icon.png")
# badge Android : seule la transparence compte
ball(96, 0.06, None, color=(255, 255, 255, 255), seam=(0, 0, 0, 0)).save(OUT / "badge-96.png")
print("ok", sorted(p.name for p in OUT.iterdir()))

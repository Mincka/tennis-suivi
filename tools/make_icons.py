"""Génère les icônes PNG : balle de tennis réaliste (rendu procédural).

- sphère éclairée (diffus + légère lumière ambiante), feutre bruité ;
- couture = vraie courbe de balle de tennis sur la sphère, projetée, avec son ombre ;
- suréchantillonnage puis réduction pour l'anticrénelage.

Usage : python tools/make_icons.py  (dépendances : pillow, numpy)
"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

OUT = Path(__file__).resolve().parent.parent / "app" / "static" / "icons"
GREEN = (31, 77, 58, 255)
FELT = np.array([214, 232, 62], dtype=np.float32)  # jaune optique
SEAM = np.array([246, 246, 236], dtype=np.float32)
LIGHT = np.array([-0.45, -0.6, 0.66])
LIGHT = LIGHT / np.linalg.norm(LIGHT)


def seam_curve(n=4000, b=0.29, tilt=(0.9, 0.2, 0.5)):
    """Courbe de la couture sur la sphère unité (forme classique « balle de tennis »), tournée."""
    t = np.linspace(0, 2 * np.pi, n)
    a = 1 - b
    c = 2 * np.sqrt(a * b)
    pts = np.stack([a * np.cos(t) + b * np.cos(3 * t), a * np.sin(t) - b * np.sin(3 * t), c * np.sin(2 * t)], 1)
    rx, ry, rz = tilt
    rot_x = np.array([[1, 0, 0], [0, np.cos(rx), -np.sin(rx)], [0, np.sin(rx), np.cos(rx)]])
    rot_y = np.array([[np.cos(ry), 0, np.sin(ry)], [0, 1, 0], [-np.sin(ry), 0, np.cos(ry)]])
    rot_z = np.array([[np.cos(rz), -np.sin(rz), 0], [np.sin(rz), np.cos(rz), 0], [0, 0, 1]])
    return pts @ (rot_z @ rot_y @ rot_x).T


def render_ball(px: int, seam_width=0.055, felt_noise=0.06, seed=7) -> Image.Image:
    """Balle seule, fond transparent, px×px."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:px, 0:px].astype(np.float32)
    x = (xx + 0.5) / px * 2 - 1
    y = (yy + 0.5) / px * 2 - 1
    r2 = x * x + y * y
    z = np.sqrt(np.clip(1 - r2, 0, 1))
    diffuse = np.clip(x * LIGHT[0] + y * LIGHT[1] + z * LIGHT[2], 0, 1)
    shade = 0.5 + 0.62 * diffuse ** 0.9
    # feutre : bruit fin + grain plus large, plus marqué au bord (duvet)
    fine = rng.normal(0, 1, (px, px)).astype(np.float32)
    small = ((rng.normal(0, 1, (px // 8 + 1, px // 8 + 1)) + 3) * 40).clip(0, 255).astype(np.uint8)
    coarse = np.asarray(Image.fromarray(small).resize((px, px), Image.BICUBIC), np.float32) / 40 - 3
    felt = 1 + felt_noise * (0.6 * fine + 0.4 * coarse) * (0.7 + 0.6 * (1 - z))
    rgb = FELT[None, None, :] * (shade * felt)[..., None]

    # couture : points visibles (z > 0) projetés, largeur réduite par le raccourci perspectif
    seam_mask = Image.new("L", (px, px), 0)
    shadow_mask = Image.new("L", (px, px), 0)
    dm, ds = ImageDraw.Draw(seam_mask), ImageDraw.Draw(shadow_mask)
    w = seam_width * px / 2
    for sx, sy, sz in seam_curve():
        if sz <= 0.02:
            continue
        cx, cy = (sx + 1) / 2 * px, (sy + 1) / 2 * px
        rad = w * (0.35 + 0.65 * sz)
        dm.ellipse([cx - rad, cy - rad, cx + rad, cy + rad], fill=255)
        off = w * 0.3
        ds.ellipse([cx - rad + off, cy - rad + off, cx + rad * 1.25 + off, cy + rad * 1.25 + off], fill=255)
    shadow = np.asarray(shadow_mask.filter(ImageFilter.GaussianBlur(w * 0.6)), np.float32) / 255
    seam = np.asarray(seam_mask.filter(ImageFilter.GaussianBlur(w * 0.12)), np.float32) / 255
    rgb = rgb * (1 - 0.13 * shadow[..., None])
    seam_rgb = SEAM[None, None, :] * (0.55 + 0.5 * diffuse)[..., None]
    rgb = rgb * (1 - seam[..., None]) + seam_rgb * seam[..., None]

    alpha = np.clip((1 - np.sqrt(r2)) * px / 2 + 0.5, 0, 1) * 255  # bord net mais anticrénelé
    out = np.dstack([np.clip(rgb, 0, 255), alpha]).astype(np.uint8)
    return Image.fromarray(out)


def icon(size: int, ball_frac: float, bg=GREEN, radius=0.22, shadow=True, **kw) -> Image.Image:
    ss = 4
    s = size * ss
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    if bg:
        ImageDraw.Draw(img).rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * radius), fill=bg)
    d = int(s * ball_frac)
    o = (s - d) // 2
    if shadow and bg:
        sh = Image.new("L", (s, s), 0)
        ImageDraw.Draw(sh).ellipse([o + d * 0.06, o + d * 0.1, o + d * 1.04, o + d * 1.08], fill=110)
        sh = sh.filter(ImageFilter.GaussianBlur(d * 0.05))
        black = Image.new("L", (s, s), 0)
        img.alpha_composite(Image.merge("RGBA", (black, black, black, sh)))
    img.alpha_composite(render_ball(d, **kw), (o, o))
    return img.resize((size, size), Image.LANCZOS)


def badge(size: int) -> Image.Image:
    """Badge Android monochrome : silhouette blanche, couture évidée."""
    a = np.asarray(render_ball(size * 4, seam_width=0.09, felt_noise=0.0), np.float32)
    seam = np.clip((a[..., :3].min(-1) - 95) / 30, 0, 1)  # la couture est la seule zone claire en bleu
    alpha = a[..., 3] * (1 - seam)
    white = np.full(alpha.shape, 255, np.float32)
    out = np.dstack([white, white, white, alpha]).astype(np.uint8)
    return Image.fromarray(out).resize((size, size), Image.LANCZOS)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    icon(512, 0.72).save(OUT / "icon-512.png")
    icon(192, 0.72).save(OUT / "icon-192.png")
    icon(512, 0.56, radius=0).save(OUT / "icon-maskable-512.png")
    icon(180, 0.72, radius=0).convert("RGB").save(OUT / "apple-touch-icon.png")
    # onglet et en-tête : balle seule, couture plus épaisse pour rester lisible en petit
    icon(32, 1.0, bg=None, seam_width=0.1, felt_noise=0.0).save(OUT / "favicon-32.png")
    icon(96, 1.0, bg=None, seam_width=0.07).save(OUT / "ball-96.png")
    badge(96).save(OUT / "badge-96.png")
    print("ok", sorted(p.name for p in OUT.iterdir()))

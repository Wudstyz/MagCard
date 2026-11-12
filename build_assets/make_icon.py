"""Génère icon.icns (icône de l'application) avec Pillow + iconutil."""
import os, subprocess, shutil
from PIL import Image, ImageDraw, ImageFilter

S = 2048  # dessiné en grand puis réduit (anti-crénelage)
here = os.path.dirname(os.path.abspath(__file__))


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def rounded_mask(size, radius):
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=255)
    return m


c1, c2 = (88, 60, 230), (0, 206, 201)  # violet -> turquoise

bg = Image.new("RGB", (S, S))
px = bg.load()
for y in range(S):
    for x in range(S):
        px[x, y] = lerp(c1, c2, (x * .42 + y * .78) / (S * 1.2))
mask = rounded_mask((S, S), int(S * .225))
icon = Image.new("RGBA", (S, S), (0, 0, 0, 0)); icon.paste(bg, (0, 0), mask)

gloss = Image.new("L", (S, S), 0)
gd = ImageDraw.Draw(gloss)
gd.ellipse((int(S * -.25), int(S * -.45), int(S * .95), int(S * .55)), fill=70)
gloss = gloss.filter(ImageFilter.GaussianBlur(S * .09))
gloss_rgba = Image.new("RGBA", (S, S), (255, 255, 255, 0))
gloss_rgba.putalpha(Image.composite(gloss, Image.new("L", (S, S), 0), mask))
icon = Image.alpha_composite(icon, gloss_rgba)

edge = Image.new("L", (S, S), 0)
ed = ImageDraw.Draw(edge)
ed.ellipse((int(S * .35), int(S * .45), int(S * 1.35), int(S * 1.45)), fill=60)
edge = edge.filter(ImageFilter.GaussianBlur(S * .12))
edge_rgba = Image.new("RGBA", (S, S), (0, 0, 0, 0))
edge_rgba.putalpha(Image.composite(edge, Image.new("L", (S, S), 0), mask))
icon = Image.alpha_composite(icon, edge_rgba)

cw, ch = int(S * .66), int(S * .42)
thick = int(ch * .05)
card = Image.new("RGBA", (cw, ch + thick), (0, 0, 0, 0))
d = ImageDraw.Draw(card)
r = int(ch * .12)
d.rounded_rectangle((0, thick, cw - 1, ch - 1 + thick), radius=r, fill=(214, 217, 230, 255))
d.rounded_rectangle((0, 0, cw - 1, ch - 1), radius=r, fill=(250, 251, 255, 255))

sy0, sy1 = int(ch * .20), int(ch * .50)
d.rectangle((0, sy0, cw, sy1), fill=(24, 26, 40, 255))
d.line((0, sy0 + int((sy1 - sy0) * .18), cw, sy0 + int((sy1 - sy0) * .18)), fill=(70, 74, 100, 255), width=max(2, int(ch * .012)))
for i, w in enumerate((.42, .28, .20)):
    y = int(ch * (.64 + i * .11))
    d.rounded_rectangle((int(cw * .08), y, int(cw * (.08 + w)), y + int(ch * .055)), radius=int(ch * .03), fill=(196, 201, 224, 255))

card = card.rotate(14, expand=True, resample=Image.BICUBIC)

shadow = Image.new("RGBA", icon.size, (0, 0, 0, 0))
sh = Image.new("RGBA", card.size, (0, 0, 0, 0)); sh.putalpha(card.split()[3].point(lambda v: int(v * .45)))
pos = ((S - card.width) // 2, (S - card.height) // 2 + int(S * .01))
shadow.paste(sh, (pos[0], pos[1] + int(S * .035)), sh)
shadow = shadow.filter(ImageFilter.GaussianBlur(S * .028))
icon = Image.alpha_composite(icon, shadow)
icon.alpha_composite(card, pos)

base = icon.resize((1024, 1024), Image.LANCZOS)
base.save(os.path.join(here, "icon_1024.png"))

iconset = os.path.join(here, "icon.iconset")
shutil.rmtree(iconset, ignore_errors=True); os.makedirs(iconset)
for size in (16, 32, 128, 256, 512):
    base.resize((size, size), Image.LANCZOS).save(f"{iconset}/icon_{size}x{size}.png")
    base.resize((size * 2, size * 2), Image.LANCZOS).save(f"{iconset}/icon_{size}x{size}@2x.png")
subprocess.run(["iconutil", "-c", "icns", iconset, "-o", os.path.join(here, "icon.icns")], check=True)
shutil.rmtree(iconset)
print("icon.icns OK")

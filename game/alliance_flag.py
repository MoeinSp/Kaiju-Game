"""Alliance flags are drawn, not uploaded.

Two templates, and every part of a template is a choice: cloth, metal, emblem
and pattern. The writing on the flag is always the alliance's own name.
Leader and deputy save the choices; the picture is rendered from them.
"""

from __future__ import annotations

import hashlib
import json
import math

from PIL import Image, ImageChops, ImageDraw

from game.media import ASSETS_DIR, CACHE_DIR, _load_ui_font, shape_persian_text

_PARTS = ASSETS_DIR / "flags" / "parts"
_LOADED: dict = {}

STYLES = {
    "hall": "تالار",
    "sea": "دریا",
}
CLOTHS = {
    "purple": ("بنفش", (92, 42, 148), (28, 12, 52)),
    "blue": ("آبی", (28, 72, 158), (8, 22, 58)),
    "red": ("سرخ", (148, 28, 36), (48, 8, 12)),
    "green": ("سبز", (22, 112, 72), (6, 36, 28)),
    "black": ("سیاه", (48, 48, 56), (12, 12, 16)),
    "gold": ("طلایی", (168, 112, 28), (64, 36, 8)),
    "white": ("سفید", (236, 232, 220), (150, 146, 136)),
    "teal": ("سبزآبی", (18, 118, 128), (6, 36, 48)),
}
METALS = {
    "gold": ("طلا", (232, 196, 92), (120, 84, 24)),
    "silver": ("نقره", (226, 230, 236), (96, 104, 116)),
    "bronze": ("برنز", (206, 132, 72), (96, 52, 28)),
    "iron": ("آهن", (168, 176, 188), (52, 58, 68)),
}
EMBLEMS = {
    "dragon": "اژدها",
    "lion": "شیر",
    "raven": "کلاغ",
    "wheel": "سکان",
    "leaf": "برگ",
    "crown": "تاج",
    "kaiju": "کایجو",
    "sword": "شمشیر",
}
PATTERNS = {
    "plain": "ساده",
    "rays": "پرتو",
    "scales": "فلس",
}

_RENDER = "6"
_NAME_MAX = 32


def catalog() -> dict:
    def rows(table, color=False):
        out = []
        for key, value in table.items():
            item = {"id": key, "label": value if isinstance(value, str) else value[0]}
            if color:
                item["color"] = "#%02x%02x%02x" % value[1]
            out.append(item)
        return out

    return {
        "styles": rows(STYLES),
        "cloths": rows(CLOTHS, color=True),
        "metals": rows(METALS, color=True),
        "emblems": rows(EMBLEMS),
        "patterns": rows(PATTERNS),
    }


def default_spec(name: str = "") -> dict:
    return {"style": "hall", "cloth": "purple", "metal": "gold", "emblem": "dragon",
            "pattern": "rays", "motto": _flag_name(name)}


def _flag_name(name: str) -> str:
    """The only text a flag may carry: the alliance name, one line."""
    text = " ".join(str(name or "").split())
    return (text or "اتحاد")[:_NAME_MAX]


def normalize(raw: dict | None, name: str = "") -> dict:
    raw = raw or {}
    spec = default_spec(name)
    if raw.get("style") in STYLES:
        spec["style"] = raw["style"]
    if raw.get("cloth") in CLOTHS:
        spec["cloth"] = raw["cloth"]
    if raw.get("metal") in METALS:
        spec["metal"] = raw["metal"]
    if raw.get("emblem") in EMBLEMS:
        spec["emblem"] = raw["emblem"]
    if raw.get("pattern") in PATTERNS:
        spec["pattern"] = raw["pattern"]
    # A motto typed by an officer is ignored. Renaming the alliance updates the flag.
    spec["motto"] = _flag_name(name)
    return spec


def spec_key(spec: dict) -> str:
    raw = _RENDER + json.dumps(spec, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def render_path(spec: dict):
    folder = CACHE_DIR / "flags"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{spec_key(spec)}.jpg"


def set_flag(user, raw: dict):
    """Leader or deputy only. Stores the choices and draws the picture."""
    from game.alliance import _assert_manager
    from game.creature import GameError

    alliance = getattr(user, "alliance", None)
    if alliance is None:
        raise GameError("اول باید عضو یه اتحاد باشی.")
    _assert_manager(user, alliance, "ساخت پرچم")
    spec = normalize(raw, alliance.name)
    alliance.flag = spec
    alliance.save(update_fields=["flag"])
    try:
        render_flag(spec)
    except OSError as exc:
        raise GameError("پرچم ساخته نشد.") from exc
    return alliance


def render_flag(spec: dict):
    """Draw the flag and return the cached JPEG path."""
    path = render_path(spec)
    if path.exists() and path.stat().st_size > 2000:
        return path
    image = _draw(spec)
    image.save(path, "JPEG", quality=90)
    return path


def _part(name: str) -> Image.Image:
    hit = _LOADED.get(name)
    if hit is None:
        hit = Image.open(_PARTS / name).convert("RGBA")
        _LOADED[name] = hit
    return hit


def _knock(image: Image.Image, cutoff: int = 12) -> Image.Image:
    """Turn a near-black backdrop into transparency, with a short soft edge."""
    image = image.convert("RGBA")
    red, green, blue, _alpha = image.split()
    peak = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    alpha = peak.point(lambda v: 0 if v <= cutoff else min(255, (v - cutoff) * 10))
    image.putalpha(alpha)
    return image


def _tint(image: Image.Image, color, gamma: float = 0.78, sheen: float = 0.22) -> Image.Image:
    """Recolor by brightness so velvet folds and metal highlights survive."""
    lum = image.convert("L")
    table = []
    for channel in color:
        curve = []
        for value in range(256):
            base = (value / 255) ** gamma
            gloss = ((value - 188) / 67) * sheen if value > 188 else 0
            curve.append(min(255, int(channel * base + 255 * gloss)))
        table.append(lum.point(curve))
    painted = Image.merge("RGB", tuple(table)).convert("RGBA")
    if image.mode == "RGBA":
        painted.putalpha(image.getchannel("A"))
    return painted


def _fit(image: Image.Image, size) -> Image.Image:
    return image.resize(size, Image.Resampling.LANCZOS)


def _draw(spec: dict) -> Image.Image:
    cloth_hi = CLOTHS[spec["cloth"]][1]
    metal_hi, metal_lo = METALS[spec["metal"]][1], METALS[spec["metal"]][2]
    style = "sea" if spec["style"] == "sea" else "hall"
    canvas = Image.new("RGBA", (900, 1500), (0, 0, 0, 255))
    cloth = _tint(_fit(_part(f"flag_cloth_{style}.jpg"), canvas.size), cloth_hi, gamma=0.7, sheen=0.18)
    canvas.alpha_composite(cloth)
    if spec["pattern"] != "plain":
        _pattern(canvas, spec["pattern"], cloth_hi, cloth)
    emblem = _knock(_part(f"flag_emblem_{spec['emblem']}.jpg"))
    emblem = _fit(emblem, (620, 620))
    if spec["metal"] != "silver":
        emblem = _tint(emblem, metal_hi, gamma=0.9, sheen=0.12)
    canvas.alpha_composite(emblem, (140, 250 if style == "hall" else 280))
    frame = _tint(_knock(_fit(_part(f"flag_frame_{style}.jpg"), canvas.size)), metal_hi, gamma=0.85, sheen=0.28)
    # keep the darkest metal from vanishing into the cloth
    shadow = _tint(_knock(_fit(_part(f"flag_frame_{style}.jpg"), canvas.size), cutoff=4), metal_lo, gamma=1.05, sheen=0)
    canvas.alpha_composite(shadow)
    canvas.alpha_composite(frame)
    plate_w, plate_h = 780, 280
    plate = _tint(_knock(_fit(_part("flag_nameplate.jpg"), (plate_w, plate_h))), metal_hi, gamma=0.9, sheen=0.2)
    plate_x, plate_y = 60, (980 if style == "hall" else 1020)
    canvas.alpha_composite(plate, (plate_x, plate_y))
    draw = ImageDraw.Draw(canvas)
    # The nameplate file is a plaque sitting on its own reflection. The open
    # band is the upper dark slot, between the two gold rails and inside the
    # side spikes. Centering on the whole bitmap drops the name onto the rail.
    _motto(
        draw, spec["motto"],
        (plate_x + 210, plate_y + int(plate_h * 0.33), plate_x + plate_w - 210, plate_y + int(plate_h * 0.53)),
        (236, 226, 206), 40,
    )
    return canvas.convert("RGB")


def _pattern(canvas: Image.Image, kind: str, color, cloth: Image.Image):
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    pen = ImageDraw.Draw(overlay)
    light = tuple(min(255, c + 80) for c in color) + (70,)
    if kind == "rays":
        cx, cy = canvas.size[0] // 2, 620
        for i in range(18):
            ang = math.tau * i / 18
            pen.line([(cx, cy), (cx + int(math.cos(ang) * 900), cy + int(math.sin(ang) * 1400))], fill=light, width=3)
    else:
        ink = (0, 0, 0, 90)
        for row, y in enumerate(range(180, 1300, 36)):
            x = 80 + (18 if row % 2 else 0)
            while x < canvas.size[0] - 80:
                pen.arc([x, y, x + 40, y + 34], 200, 340, fill=ink, width=2)
                x += 40
    red, green, blue, _alpha = cloth.split()
    mask = ImageChops.lighter(ImageChops.lighter(red, green), blue).point(lambda v: 255 if v > 18 else 0)
    overlay.putalpha(ImageChops.multiply(overlay.getchannel("A"), mask))
    canvas.alpha_composite(overlay)


def _mix(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _cloth(draw, box, hi, lo, pattern):
    x0, y0, x1, y1 = box
    for y in range(y0, y1):
        t = (y - y0) / max(1, y1 - y0)
        draw.line([(x0, y), (x1, y)], fill=_mix(hi, lo, t))
    if pattern == "rays":
        cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
        for i in range(16):
            ang = math.tau * i / 16
            draw.line([(cx, cy), (cx + int(math.cos(ang) * 800), cy + int(math.sin(ang) * 800))],
                      fill=_mix(hi, (255, 255, 255), 0.18), width=2)
    elif pattern == "scales":
        ink = _mix(lo, (0, 0, 0), 0.35)
        for row, y in enumerate(range(y0, y1, 28)):
            shift = 16 if row % 2 else 0
            x = x0 + shift
            while x < x1:
                draw.arc([x, y, x + 32, y + 28], 200, 340, fill=ink, width=2)
                x += 32


def _emblem(draw, kind, cx, cy, r, color):
    if kind == "lion":
        draw.ellipse([cx - r, cy - r + 6, cx + r, cy + r], outline=color, width=7)
        draw.polygon([(cx - r // 2, cy - r // 2), (cx - r // 5, cy - r), (cx, cy - r // 3)], outline=color)
        draw.polygon([(cx + r // 2, cy - r // 2), (cx + r // 5, cy - r), (cx, cy - r // 3)], outline=color)
        draw.ellipse([cx - r // 3, cy - 6, cx - 6, cy + r // 5], fill=color)
        draw.ellipse([cx + 6, cy - 6, cx + r // 3, cy + r // 5], fill=color)
        draw.polygon([(cx, cy + 4), (cx - 12, cy + r // 2), (cx + 12, cy + r // 2)], fill=color)
    elif kind == "raven":
        draw.polygon([(cx - r, cy), (cx, cy - r), (cx + r // 3, cy - 4), (cx - 8, cy + r // 2)], fill=color)
        draw.polygon([(cx, cy - 6), (cx + r, cy + 4), (cx + r // 5, cy + 8)], fill=color)
        draw.ellipse([cx - r // 5, cy - r // 3, cx, cy - 4], fill=(20, 8, 28))
    elif kind == "wheel":
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color, width=8)
        draw.ellipse([cx - r // 3, cy - r // 3, cx + r // 3, cy + r // 3], outline=color, width=6)
        for i in range(6):
            ang = math.tau * i / 6
            draw.line([(cx + int(math.cos(ang) * r // 3), cy + int(math.sin(ang) * r // 3)),
                       (cx + int(math.cos(ang) * r), cy + int(math.sin(ang) * r))], fill=color, width=6)
    elif kind == "leaf":
        draw.polygon([(cx, cy - r), (cx + r // 2, cy), (cx, cy + r), (cx - r // 2, cy)], outline=color)
        draw.ellipse([cx - r // 2, cy - r, cx + r // 2, cy + r // 3], outline=color, width=6)
        draw.line([(cx, cy - r), (cx, cy + r)], fill=color, width=4)
    elif kind == "crown":
        draw.polygon([
            (cx - r, cy + r // 3), (cx - r, cy - r // 5), (cx - r // 2, cy + r // 6),
            (cx, cy - r), (cx + r // 2, cy + r // 6), (cx + r, cy - r // 5), (cx + r, cy + r // 3),
        ], outline=color)
        draw.line([(cx - r, cy + r // 3), (cx + r, cy + r // 3)], fill=color, width=8)
    elif kind == "kaiju":
        draw.polygon([(cx - r, cy + r // 2), (cx - r // 2, cy - r), (cx, cy - r // 3),
                      (cx + r // 2, cy - r), (cx + r, cy + r // 2)], outline=color)
        draw.line([(cx - r // 3, cy), (cx - 8, cy + r // 4), (cx, cy), (cx + 8, cy + r // 4), (cx + r // 3, cy)], fill=color, width=5)
    elif kind == "sword":
        draw.polygon([(cx - 8, cy - r), (cx + 8, cy - r), (cx + 14, cy + r // 5), (cx - 14, cy + r // 5)], fill=color)
        draw.line([(cx, cy + r // 5), (cx, cy + r)], fill=color, width=8)
        draw.line([(cx - r // 2, cy + r // 5), (cx + r // 2, cy + r // 5)], fill=color, width=10)
    else:
        draw.arc([cx - r, cy - r // 2, cx + r, cy + r], 200, 340, fill=color, width=8)
        draw.polygon([(cx + r // 2, cy - r // 5), (cx + r, cy + r // 6), (cx + r // 3, cy + r // 8)], fill=color)
        draw.ellipse([cx - 8, cy - 8, cx + 10, cy + 10], fill=color)


def _motto(draw, text, box, fill, font_size=42):
    """Center the ink of the name in the slot, for Persian and Latin alike.

    Width-only centering leaves the left side-bearing on the wrong side of the
    box, and Persian letters sit lower in the em square than Latin ones. The
    anchor is the middle of the painted glyphs, then the size shrinks until
    the whole name stays inside the slot.
    """
    shaped = shape_persian_text(text)
    if shaped == text and any("\u0600" <= ch <= "\u06FF" for ch in text):
        shaped = text[::-1]
    x0, y0, x1, y1 = box
    max_w, max_h = (x1 - x0), (y1 - y0)
    size = font_size
    font = _load_ui_font(size, bold=True, persian=True)
    while size > 14:
        font = _load_ui_font(size, bold=True, persian=True)
        bbox = draw.textbbox((0, 0), shaped, font=font)
        if bbox[2] - bbox[0] <= max_w and bbox[3] - bbox[1] <= max_h:
            break
        size -= 2
    draw.text(((x0 + x1) / 2, (y0 + y1) / 2), shaped, font=font, fill=fill, anchor="mm")


def _hall(draw, image, spec, hi, lo, metal, metal_lo):
    draw.rectangle([250, 24, 390, 70], fill=metal_lo)
    draw.rectangle([236, 40, 404, 58], fill=metal)
    pts = [(96, 150), (544, 150), (560, 760), (320, 900), (80, 760)]
    draw.polygon(pts, fill=lo)
    mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(mask).polygon(pts, fill=255)
    cloth = Image.new("RGB", image.size, hi)
    cd = ImageDraw.Draw(cloth)
    _cloth(cd, (70, 140, 570, 910), hi, lo, spec["pattern"])
    image.paste(cloth, mask=mask)
    draw.line(pts + [pts[0]], fill=metal, width=10)
    cx, cy = 320, 390
    draw.polygon([(cx - 70, cy - 150), (cx - 34, cy - 118), (cx, cy - 176), (cx + 34, cy - 118), (cx + 70, cy - 150),
                  (cx + 78, cy - 96), (cx - 78, cy - 96)], fill=metal)
    draw.ellipse([cx - 108, cy - 108, cx + 108, cy + 108], fill=lo, outline=metal, width=10)
    _emblem(draw, spec["emblem"], cx, cy, 62, metal)
    draw.rounded_rectangle([120, 560, 520, 640], radius=12, fill=metal_lo, outline=metal, width=4)
    _motto(draw, spec["motto"], (120, 560, 520, 640), (24, 16, 12) if spec["metal"] != "iron" else (236, 232, 220))
    for x in range(110, 530, 18):
        draw.line([(x, 760), (x + 8, 820)], fill=metal, width=3)


def _sea(draw, image, spec, hi, lo, metal, metal_lo):
    draw.rectangle([70, 36, 570, 58], fill=metal_lo)
    for x in (110, 530):
        draw.line([(x, 58), (x, 150)], fill=metal, width=6)
    draw.rounded_rectangle([90, 120, 550, 860], radius=18, fill=lo, outline=metal, width=12)
    cloth = Image.new("RGB", image.size, hi)
    cd = ImageDraw.Draw(cloth)
    _cloth(cd, (100, 130, 540, 850), hi, lo, spec["pattern"])
    mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([102, 132, 538, 848], radius=12, fill=255)
    image.paste(cloth, mask=mask)
    cx, cy = 320, 390
    draw.ellipse([cx - 150, cy - 150, cx + 150, cy + 150], outline=metal, width=14)
    draw.ellipse([cx - 118, cy - 118, cx + 118, cy + 118], fill=lo, outline=metal, width=6)
    for i in range(8):
        ang = math.tau * i / 8
        draw.line([(cx + int(math.cos(ang) * 70), cy + int(math.sin(ang) * 70)),
                   (cx + int(math.cos(ang) * 140), cy + int(math.sin(ang) * 140))], fill=metal, width=8)
    _emblem(draw, spec["emblem"], cx, cy, 48, metal)
    draw.rounded_rectangle([130, 700, 510, 800], radius=10, fill=metal_lo, outline=metal, width=4)
    _motto(draw, spec["motto"], (130, 700, 510, 800), (24, 16, 12) if spec["metal"] != "iron" else (236, 232, 220), 40)
    draw.polygon([(150, 820), (170, 860), (130, 860)], fill=metal)
    draw.polygon([(490, 820), (470, 860), (510, 860)], fill=metal)


def flag_file(alliance):
    """Rendered flag of an alliance, or None when its officers have not built one."""
    raw = alliance.flag if isinstance(getattr(alliance, "flag", None), dict) else None
    if not raw or not raw.get("style"):
        return None
    return render_flag(normalize(raw, alliance.name))


def render_board(ranked) -> str:
    """One picture of the top alliances, each with its own flag. The path changes
    when the ranking or a flag changes, so Telegram does not keep an old file."""
    parts = []
    for row in ranked:
        alliance = row["alliance"]
        raw = alliance.flag if isinstance(alliance.flag, dict) else {}
        parts.append(f"{alliance.id}|{alliance.name}|{row['power']}|{row['member_count']}|{raw}")
    digest = hashlib.sha1((_RENDER + "\n".join(parts)).encode()).hexdigest()[:12]
    folder = CACHE_DIR / "flags"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"board_{digest}.jpg"
    if path.exists() and path.stat().st_size > 2000:
        return str(path)
    _draw_board(ranked).save(path, "JPEG", quality=90)
    return str(path)


def _draw_board(ranked) -> Image.Image:
    width, row_h, head = 1080, 150, 110
    image = Image.new("RGB", (width, head + row_h * max(1, len(ranked)) + 24), (10, 12, 18))
    draw = ImageDraw.Draw(image)
    title_font = _load_ui_font(42, bold=True, persian=True)
    name_font = _load_ui_font(36, bold=True, persian=True)
    sub_font = _load_ui_font(26, bold=False, persian=True)
    _right(draw, "برترین اتحادها", width - 48, 32, title_font, (236, 208, 120))
    for index, row in enumerate(ranked):
        top = head + index * row_h
        draw.rounded_rectangle([24, top, width - 24, top + row_h - 16], radius=16, fill=(22, 26, 36))
        thumb = _thumb(row["alliance"])
        image.paste(thumb, (width - 48 - thumb.width, top + 8))
        _right(draw, f"{index + 1}", width - 160, top + 18, title_font, (236, 208, 120) if index < 3 else (180, 186, 198))
        _right(draw, row["alliance"].name, width - 230, top + 22, name_font, (244, 244, 248))
        _right(draw, f"قدرت {row['power']:,}   ·   {row['member_count']} عضو", width - 230, top + 78, sub_font, (168, 176, 190))
    return image


def _thumb(alliance) -> Image.Image:
    path = flag_file(alliance)
    if path is not None:
        with Image.open(path) as src:
            return src.convert("RGB").resize((86, 118), Image.Resampling.LANCZOS)
    empty = Image.new("RGB", (86, 118), (28, 30, 38))
    pen = ImageDraw.Draw(empty)
    pen.polygon([(8, 10), (78, 10), (78, 90), (43, 110), (8, 90)], outline=(90, 96, 110))
    return empty


def _right(draw, text, right, top, font, fill):
    shaped = shape_persian_text(str(text))
    if shaped == str(text) and any("\u0600" <= ch <= "\u06FF" for ch in str(text)):
        shaped = str(text)[::-1]
    box = draw.textbbox((0, 0), shaped, font=font)
    draw.text((right - (box[2] - box[0]), top), shaped, font=font, fill=fill)

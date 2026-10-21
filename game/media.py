"""Asset and image resolution utilities for TelGame."""

import hashlib
import os
from pathlib import Path
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

# Base directory for assets
BASE_DIR = Path(__file__).resolve().parent.parent
ASSETS_DIR = BASE_DIR / "assets" / "images"
KAIJU_DIR = ASSETS_DIR / "kaijus"
BUILDINGS_DIR = ASSETS_DIR / "buildings"
ALLIANCE_DIR = ASSETS_DIR / "alliance"
EQUIPMENT_DIR = ASSETS_DIR / "equipment"
BANNERS_DIR = ASSETS_DIR / "banners"
CACHE_DIR = ASSETS_DIR / "cache"
FEATURES_DIR = ASSETS_DIR / "features"
DROPS_DIR = ASSETS_DIR / "drops"
CHESTS_DIR = ASSETS_DIR / "chests"
FONTS_DIR = BASE_DIR / "assets" / "fonts"

CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Composited cards live in CACHE_DIR, inside the container. Two kinds are made PER ITEM
# (an equipment card per item id, a collage per loot batch) and are almost never asked for
# twice — 5,600 of them (1 GB) appeared in 28 hours. They are dropped after a few hours;
# everything else (creature/building cards, shared by many players) stays until the
# folder passes CACHE_MAX_BYTES, then the oldest go first. A deleted card is simply drawn
# again the next time someone opens it.
CACHE_ONE_OFF_PREFIXES = ("equip_card_", "loot_batch_", "tour_bracket_")
CACHE_ONE_OFF_MAX_AGE = 3 * 3600
CACHE_MAX_BYTES = 300 * 1024 * 1024


def prune_cache() -> tuple[int, int]:
    """Trim CACHE_DIR (top-level files only — the Mini App's thumbs/ folder is separate).
    Returns (files removed, bytes freed). Safe to run at any time."""
    import time

    now = time.time()
    kept: list[tuple[float, int, Path]] = []
    removed = freed = 0
    for f in CACHE_DIR.iterdir():
        try:
            if not f.is_file():
                continue
            st = f.stat()
        except OSError:
            continue
        if f.name.startswith(CACHE_ONE_OFF_PREFIXES) and now - st.st_mtime > CACHE_ONE_OFF_MAX_AGE:
            try:
                f.unlink()
                removed += 1
                freed += st.st_size
            except OSError:
                pass
            continue
        kept.append((st.st_mtime, st.st_size, f))
    total = sum(size for _m, size, _f in kept)
    for _mtime, size, f in sorted(kept):          # oldest first
        if total <= CACHE_MAX_BYTES:
            break
        try:
            f.unlink()
            removed += 1
            freed += size
            total -= size
        except OSError:
            pass
    return removed, freed
FEATURES_DIR.mkdir(parents=True, exist_ok=True)
DROPS_DIR.mkdir(parents=True, exist_ok=True)
CHESTS_DIR.mkdir(parents=True, exist_ok=True)
FONTS_DIR.mkdir(parents=True, exist_ok=True)


def shape_persian_text(text: str) -> str:
    """Shapes Persian/Arabic text for correct right-to-left connected rendering in Pillow."""
    if not text:
        return ""
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        return get_display(arabic_reshaper.reshape(text))
    except Exception:
        return text


SPECIES_TO_SLUG = {
    # fire
    "سیمرغ": "simurgh",
    "اژدهاک": "azhdahak",
    "آذرگشسب": "azargashasp",
    "ضحاک": "zahhak",
    "فرنبغ": "farnbagh",
    # water
    "اپم‌نپات": "apam_napat",
    "آناهیتا": "anahita",
    "تیشتر": "tishtrya",
    "کرکس دریا": "karkas_darya",
    "ماهی‌ور": "mahivar",
    # earth
    "کرکدان": "karkadann",
    "البرزکوه": "alborzkuh",
    "اسپندارمذ": "spandarmad",
    "گاوبرمایه": "gav_barmayeh",
    "سنگ‌دیو": "sang_div",
    # electric
    "بهرام": "bahram",
    "وایو": "vayu",
    "هما": "homa",
    "رخش": "rakhsh",
    "شهباز": "shahbaz",
    # crystal
    "زبرجد": "zebarjad",
    "یشم‌تن": "yashmtan",
    "بلورزاد": "boloorzad",
    "درخشنده": "derakhshandeh",
    "الماس‌گون": "almasgoon",
    # plasma
    "اخگرزاد": "akhgarzad",
    "شهاب‌شکن": "shahabshekan",
    "تندرمهر": "tondarmehr",
    "آذرخش‌تن": "azarakhshtan",
    "شعله‌سای": "sholehsay",
}

RARITY_STYLES = {
    "common": {
        "label": "COMMON",
        "badge_bg": (15, 20, 30, 220),
        "border": (180, 190, 205),
        "glow": (110, 120, 135),
        "text_color": (220, 230, 245),
        "tint_color": (160, 175, 195),
        "tint_alpha": 12,
    },
    "rare": {
        "label": "● RARE ●",
        "badge_bg": (10, 25, 50, 225),
        "border": (45, 165, 255),
        "glow": (15, 105, 225),
        "text_color": (130, 215, 255),
        "tint_color": (20, 110, 240),
        "tint_alpha": 22,
    },
    "epic": {
        "label": "◆ EPIC ◆",
        "badge_bg": (30, 15, 55, 225),
        "border": (195, 75, 255),
        "glow": (145, 35, 225),
        "text_color": (235, 160, 255),
        "tint_color": (165, 35, 235),
        "tint_alpha": 25,
    },
    "legendary": {
        "label": "★ LEGENDARY ★",
        "badge_bg": (45, 30, 10, 230),
        "border": (255, 195, 35),
        "glow": (225, 135, 10),
        "text_color": (255, 230, 110),
        "tint_color": (245, 160, 10),
        "tint_alpha": 28,
    },
    "mythic": {
        "label": "★ MYTHIC ★",
        "badge_bg": (50, 10, 20, 235),
        "border": (255, 45, 85),
        "glow": (205, 10, 45),
        "text_color": (255, 130, 160),
        "tint_color": (230, 15, 50),
        "tint_alpha": 32,
    },
}


def get_creature_stage(level: int) -> int:
    """Calculates evolution stage (1-6) based on level."""
    if level >= 101:
        return 6
    elif level >= 81:
        return 5
    elif level >= 61:
        return 4
    elif level >= 41:
        return 3
    elif level >= 21:
        return 2
    else:
        return 1


def _load_ui_font(size: int, bold: bool = False, persian: bool = False):
    candidates = []
    if persian:
        candidates.extend([
            str(FONTS_DIR / ("Vazirmatn-Bold.ttf" if bold else "Vazirmatn-Regular.ttf")),
            "C:/Windows/Fonts/tahomabd.ttf" if bold else "C:/Windows/Fonts/tahoma.ttf",
            "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ])
    else:
        candidates.extend([
            # 1. Bundled DejaVu font (guaranteed full support for •, ★, ☆, and Latin)
            str(FONTS_DIR / ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")),
            # 2. Linux system fonts
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf" if bold else "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            # 3. Windows system fonts
            "C:/Windows/Fonts/seguisym.ttf",
            "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
            "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        ])

    for p in candidates:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    try:
        return ImageFont.load_default(size=size)
    except Exception:
        return ImageFont.load_default()


SPECIES_TITLES = {
    "simurgh": ("SIMURGH", "SOLAR FIRE EMPRESS"),
    "azhdahak": ("AZHDAHAK", "THREE-HEADED DRAKE"),
    "azargashasp": ("AZARGASHASP", "SACRED FIRE WARRIOR"),
    "zahhak": ("ZAHHAK", "MYTHIC DRAGON LORD"),
    "farnbagh": ("FARNBAGH", "DIVINE FLAME LION"),
    "apam_napat": ("APAM NAPAT", "PRIMORDIAL WATER DRAGON"),
    "anahita": ("ANAHITA", "CELESTIAL SEA LEVIATHAN"),
    "tishtrya": ("TISHTRYA", "DIVINE WATER STALLION"),
    "karkas_darya": ("KARKAS DARYA", "ABYSSAL SEA PREDATOR"),
    "mahivar": ("MAHIVAR", "COLOSSAL ISLAND WHALE"),
    "karkadann": ("KARKADANN", "ARMORED RHINO TITAN"),
    "alborzkuh": ("ALBORZKUH", "LIVING MOUNTAIN TITAN"),
    "spandarmad": ("SPANDARMAD", "SACRED EARTH GUARDIAN"),
    "gav_barmayeh": ("GAV BARMAYEH", "SACRED CELESTIAL BULL"),
    "sang_div": ("SANG DIV", "OBSIDIAN STONE DEMON"),
    "bahram": ("BAHRAM", "FIERCE THUNDER WARLORD"),
    "vayu": ("VAYU", "TEMPEST STORM GOD"),
    "homa": ("HOMA", "CELESTIAL THUNDERBIRD"),
    "rakhsh": ("RAKHSH", "SAFFRON LIGHTNING STEED"),
    "shahbaz": ("SHAHBAZ", "IMPERIAL THUNDER FALCON"),
    # crystal
    "zebarjad": ("ZEBARJAD", "PRISMATIC SERPENT TITAN"),
    "yashmtan": ("YASHMTAN", "ANCIENT JADE GOLEM"),
    "boloorzad": ("BOLOORZAD", "ASTRAL CRYSTAL DRAKE"),
    "derakhshandeh": ("DERAKHSHANDEH", "LUMINOUS PRISM BEAST"),
    "almasgoon": ("ALMASGOON", "APEX DIAMOND COLOSSUS"),
    # plasma
    "akhgarzad": ("AKHGARZAD", "SUPERHEATED PLASMA DRAKE"),
    "shahabshekan": ("SHAHABSHEKAN", "COSMIC METEOR BREAKER"),
    "tondarmehr": ("TONDARMEHR", "SOLAR CORONA TITAN"),
    "azarakhshtan": ("AZARAKHSHTAN", "IONIZED PARTICLE BEAST"),
    "sholehsay": ("SHOLEHSAY", "SUPERNOVA FLARE LORD"),
}


def composite_rarity_and_stage(
    base_img: Image.Image,
    rarity: str,
    stage_num: int,
    is_max: bool = False,
    en_name: str = "KAIJU",
    subtitle: str = "MYTHICAL CREATURE",
    star_level: int = 1,
    level: int = 1,
) -> Image.Image:
    cfg = RARITY_STYLES.get(str(rarity).lower(), RARITY_STYLES["common"])
    w, h = base_img.size

    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)

    # Smooth natural dark gradient at the bottom (NO horizontal line)
    grad_h = 160
    by = h - grad_h
    for i in range(grad_h):
        progress = i / grad_h
        alpha = int(245 * (progress ** 1.6))
        d.line([(0, by + i), (w, by + i)], fill=(6, 8, 12, alpha), width=1)

    comp = Image.alpha_composite(base_img.convert("RGBA"), overlay).convert("RGB")
    dt = ImageDraw.Draw(comp)

    font_title = _load_ui_font(34, bold=True)
    font_sub = _load_ui_font(22, bold=False)
    font_stars = _load_ui_font(28, bold=False)

    r_color = cfg["border"]
    clean_label = cfg["label"].replace("★", "").replace("◆", "").replace("●", "").strip()
    title_text = f"{en_name.upper()}   •   {clean_label}"

    y1 = h - 105
    y2 = h - 60

    # Title & subtitle on left
    dt.text((45 + 1, y1 + 1), title_text, font=font_title, fill=(0, 0, 0))
    dt.text((45, y1), title_text, font=font_title, fill=r_color)

    dt.text((45 + 1, y2 + 1), subtitle, font=font_sub, fill=(0, 0, 0))
    dt.text((45, y2), subtitle, font=font_sub, fill=(185, 195, 215))

    # Exactly 5 Stars on right
    clamped_stars = max(1, min(5, star_level))
    stars_str = ("★  " * clamped_stars) + ("☆  " * (5 - clamped_stars))
    stars_str = stars_str.strip()

    stage_text = "APEX MAX LEVEL 120" if is_max else f"STAGE {stage_num}   •   LVL {level}"
    st_color = (255, 215, 60) if is_max else (200, 215, 235)

    dt.text((w - 45 + 1, y1 + 1), stars_str, font=font_stars, fill=(0, 0, 0), anchor="ra")
    dt.text((w - 45, y1), stars_str, font=font_stars, fill=(255, 215, 50), anchor="ra")

    dt.text((w - 45 + 1, y2 + 1), stage_text, font=font_sub, fill=(0, 0, 0), anchor="ra")
    dt.text((w - 45, y2), stage_text, font=font_sub, fill=st_color, anchor="ra")

    return comp


_ROMAN = ("", "I", "II", "III", "IV", "V")


def apply_shadow_aura(img: Image.Image, level: int, seed: int = 0) -> Image.Image:
    """The «فرم سیاه» look on a creature card: a violet edge glow, dark smoke creeping in from
    the borders, glowing cracks (more of them per level) and a SHADOW sigil with the level.
    Everything thickens with the level, so a «سایه ۵» card is unmistakable."""
    import random as _random

    level = max(1, min(5, int(level)))
    rng = _random.Random(seed * 31 + level)
    base = img.convert("RGB")
    w, h = base.size

    # 1. a cold violet grade over the whole card
    tint = Image.new("RGB", (w, h), (120, 70, 210))
    base = Image.blend(base, ImageChops.multiply(base, tint), 0.18 + 0.06 * level)

    # 2. smoke from the borders (a soft dark frame)
    smoke = Image.new("L", (w, h), 0)
    sd = ImageDraw.Draw(smoke)
    thick = 26 + 10 * level
    for i in range(thick):
        a = int(230 * (1 - i / thick) ** 1.8)
        sd.rectangle((i, i, w - 1 - i, h - 1 - i), outline=a)
    smoke = smoke.filter(ImageFilter.GaussianBlur(10))
    base = Image.composite(Image.new("RGB", (w, h), (12, 4, 26)), base, smoke)

    # 3. glowing cracks reaching in from the edges
    cracks = Image.new("L", (w, h), 0)
    cd = ImageDraw.Draw(cracks)
    for _ in range(2 + 2 * level):
        side = rng.choice("tlr")  # never from the bottom: the name and stats live there
        x, y = {"t": (rng.uniform(0, w), 0), "b": (rng.uniform(0, w), h), "l": (0, rng.uniform(0, h)), "r": (w, rng.uniform(0, h))}[side]
        tx, ty = w * rng.uniform(0.3, 0.7), h * rng.uniform(0.25, 0.5)
        pts = [(x, y)]
        for step in range(1, 7):
            f = step / 7 * rng.uniform(0.35, 0.6)
            pts.append((x + (tx - x) * f + rng.uniform(-18, 18), y + (ty - y) * f + rng.uniform(-18, 18)))
        cd.line(pts, fill=255, width=3)
    glow = cracks.filter(ImageFilter.GaussianBlur(6))
    base = Image.composite(Image.new("RGB", (w, h), (176, 96, 255)), base, glow.point(lambda v: int(v * (0.55 + 0.08 * level))))
    base = Image.composite(Image.new("RGB", (w, h), (238, 214, 255)), base, cracks.point(lambda v: int(v * 0.85)))

    # 4. the violet edge glow
    edge = Image.new("L", (w, h), 0)
    ImageDraw.Draw(edge).rounded_rectangle((8, 8, w - 9, h - 9), radius=28, outline=255, width=4 + level)
    edge_glow = edge.filter(ImageFilter.GaussianBlur(9 + 2 * level))
    base = Image.composite(Image.new("RGB", (w, h), (168, 92, 255)), base, edge_glow)
    base = Image.composite(Image.new("RGB", (w, h), (226, 196, 255)), base, edge.point(lambda v: int(v * 0.7)))

    # 5. the sigil: «SHADOW · III» in the top corner
    d = ImageDraw.Draw(base)
    font = _load_ui_font(30, bold=True)
    label = f"SHADOW  {_ROMAN[level]}"
    tw = d.textlength(label, font=font)
    x0, y0 = 34, 30
    pill = Image.new("L", (w, h), 0)
    ImageDraw.Draw(pill).rounded_rectangle((x0 - 14, y0 - 8, x0 + tw + 14, y0 + 44), radius=22, fill=200)
    base = Image.composite(Image.new("RGB", (w, h), (24, 8, 46)), base, pill)
    d = ImageDraw.Draw(base)
    d.rounded_rectangle((x0 - 14, y0 - 8, x0 + tw + 14, y0 + 44), radius=22, outline=(190, 120, 255), width=2)
    d.text((x0, y0), label, font=font, fill=(232, 205, 255))
    return base


def get_creature_image_path(creature) -> str | None:
    """Returns the local image file path for a creature based on species, stage, and rarity."""
    if creature is None:
        return None

    name = getattr(creature, "name", None)
    element = getattr(creature, "element", "fire")
    level = getattr(creature, "level", 1)
    rarity = getattr(creature, "rarity", "common")
    star_level = getattr(creature, "star_level", 1)

    slug = SPECIES_TO_SLUG.get(name) or (name if name in SPECIES_TITLES else None)
    if not slug:
        sp = getattr(creature, "species", None)
        slug = SPECIES_TO_SLUG.get(sp) or (sp if sp in SPECIES_TITLES else None)

    stage = get_creature_stage(level)
    is_max = (level >= 120)
    shadow = int(getattr(creature, "shadow_level", 0) or 0)

    # 1. Species-based progression
    if slug:
        aura = f"_shadow{shadow}" if shadow else ""
        cache_file = CACHE_DIR / f"creature_{slug}_s{stage}_{rarity}_star{star_level}{aura}.jpg"
        if cache_file.exists() and cache_file.stat().st_size > 5000:
            return str(cache_file)

        stage_file = KAIJU_DIR / f"{slug}_star{star_level}.jpg"
        if not stage_file.exists():
            stage_file = KAIJU_DIR / f"{slug}_stage{stage}.jpg"
        if not stage_file.exists():
            stage_file = KAIJU_DIR / f"{slug}_base.jpg"

        if stage_file.exists():
            try:
                base_img = Image.open(stage_file).convert("RGB")
                en_name, sub_title = SPECIES_TITLES.get(slug, (slug.upper(), "MYTHICAL CREATURE"))
                comp = composite_rarity_and_stage(
                    base_img,
                    rarity=rarity,
                    stage_num=stage,
                    is_max=is_max,
                    en_name=en_name,
                    subtitle=sub_title,
                    star_level=star_level,
                    level=level,
                )
                if shadow:  # «فرم سیاه»: the dark aura (game/shadow.py)
                    comp = apply_shadow_aura(comp, shadow, seed=len(slug))
                comp.save(cache_file, "JPEG", quality=93, progressive=True)
                return str(cache_file)
            except Exception:
                return str(stage_file)

    # 2. Fallback to element tier
    if level <= 15:
        tier = "tier1"
    elif level <= 35:
        tier = "tier2"
    else:
        tier = "tier3"

    filename = f"{element}_{tier}.jpg"
    path = KAIJU_DIR / filename
    if path.exists():
        return str(path)

    fallback = KAIJU_DIR / f"{element}_tier1.jpg"
    if fallback.exists():
        return str(fallback)

    return None


BUILDING_TITLES = {
    "main_hall": ("TALAR-E MEHR", "🏛 تالار مِهر", 5),
    "gold_collector": ("GOLD FOUNDRY", "🏭 جمع‌کننده طلا", 5),
    "diamond_collector": ("DIAMOND SANCTUM", "💎 جمع‌کننده الماس", 5),
    "dna_lab": ("GENESIS BIOLAB", "🧬 آزمایشگاه DNA", 5),
    "blacksmith": ("KAVEH ARMORY", "⚒ آهنگری", 5),
    "fusion_lab": ("ASTRAL CRUCIBLE", "🔮 تالار ادغام", 5),
    "trade_hall": ("SILK ROAD EXCHANGE", "🤝 تالار تجارت", 5),
    "research_lab": ("JAMSHID ARCHIVES", "🔬 آزمایشگاه", 5),
    "dispatch_hq": ("EXPEDITION OUTPOST", "🧭 پایگاه اعزام", 5),
    # Alliance
    "hall": ("ALLIANCE BASTION", "🏰 تالار اتحاد", 4),
    "xp": ("WAR ACADEMY", "🎓 آکادمی", 5),
    "pass": ("SACRED TEMPLE", "⛩ معبد", 5),
    "fortress": ("MOUNTAIN CITADEL", "🏯 دژ", 5),
    "barracks": ("WAR GARRISON", "🪖 پادگان", 5),
    "vault": ("CYRUS TREASURY", "🏦 خزانه", 5),
}

EQUIPMENT_NAME_TO_SLUG = {
    "پنجه‌های فولادی": "steel_claws",
    "شمشیر لیزری": "laser_sword",
    "تبر استخوانی": "bone_axe",
    "زره تیتانیومی": "titanium_armor",
    "فلس‌های اژدها": "dragon_scales",
    "سپر انرژی": "energy_shield",
    "حلقه سم": "venom_ring",
    "گردنبند خون‌خوار": "blood_amulet",
    "غدد اسیدی": "acid_glands",
    "پرتاب‌کننده آتش": "flamethrower",
}

EQUIPMENT_INFO = {
    "steel_claws": {"en": "STEEL CLAWS", "fa": "پنجه‌های فولادی", "slot": "weapon", "slot_label": "⚔️ سلاح"},
    "laser_sword": {"en": "LASER SWORD", "fa": "شمشیر لیزری", "slot": "weapon", "slot_label": "⚔️ سلاح"},
    "bone_axe": {"en": "BONE AXE", "fa": "تبر استخوانی", "slot": "weapon", "slot_label": "⚔️ سلاح"},
    "titanium_armor": {"en": "TITANIUM ARMOR", "fa": "زره تیتانیومی", "slot": "armor", "slot_label": "🛡 زره"},
    "dragon_scales": {"en": "DRAGON SCALES", "fa": "فلس‌های اژدها", "slot": "armor", "slot_label": "🛡 زره"},
    "energy_shield": {"en": "ENERGY SHIELD", "fa": "سپر انرژی", "slot": "armor", "slot_label": "🛡 زره"},
    "venom_ring": {"en": "VENOM RING", "fa": "حلقه سم", "slot": "rune", "slot_label": "💍 طلسم"},
    "blood_amulet": {"en": "BLOOD AMULET", "fa": "گردنبند خون‌خوار", "slot": "rune", "slot_label": "💍 طلسم"},
    "acid_glands": {"en": "ACID GLANDS", "fa": "غدد اسیدی", "slot": "offhand", "slot_label": "🧪 غلاف"},
    "flamethrower": {"en": "FLAMETHROWER", "fa": "پرتاب‌کننده آتش", "slot": "offhand", "slot_label": "🧪 غلاف"},
}


def composite_building_card(
    base_img: Image.Image,
    building_key: str,
    level: int = 1,
    is_alliance: bool = False,
) -> Image.Image:
    title_info = BUILDING_TITLES.get(building_key, (building_key.upper(), building_key, 5))
    en_title, fa_label, max_lvl = title_info
    w, h = base_img.size

    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)

    grad_h = 160
    by = h - grad_h
    for i in range(grad_h):
        progress = i / grad_h
        alpha = int(245 * (progress ** 1.6))
        d.line([(0, by + i), (w, by + i)], fill=(6, 8, 12, alpha), width=1)

    comp = Image.alpha_composite(base_img.convert("RGBA"), overlay).convert("RGB")
    dt = ImageDraw.Draw(comp)

    font_title = _load_ui_font(32, bold=True)
    font_sub = _load_ui_font(21, bold=False, persian=True)

    category = "اتحاد • ALLIANCE" if is_alliance else "پایگاه کایجو • BASE"
    title_text = f"{en_title}   •   LEVEL {level}"
    sub_text = shape_persian_text(f"{fa_label}   •   {category}")

    y1 = h - 105
    y2 = h - 60

    dt.text((45 + 1, y1 + 1), title_text, font=font_title, fill=(0, 0, 0))
    dt.text((45, y1), title_text, font=font_title, fill=(255, 215, 60))

    dt.text((45 + 1, y2 + 1), sub_text, font=font_sub, fill=(0, 0, 0))
    dt.text((45, y2), sub_text, font=font_sub, fill=(185, 195, 215))

    is_max = (level >= max_lvl)
    lvl_badge = "MAX TIER ★" if is_max else f"TIER {level} / {max_lvl}"
    badge_col = (255, 215, 60) if is_max else (200, 220, 245)

    dt.text((w - 45 + 1, y1 + 1), lvl_badge, font=font_title, fill=(0, 0, 0), anchor="ra")
    dt.text((w - 45, y1), lvl_badge, font=font_title, fill=badge_col, anchor="ra")

    return comp


def get_building_image_path(building_type: str, level: int = 1) -> str | None:
    """Returns the local composited card file path for a personal building at a specific level."""
    if not building_type:
        return None

    lvl = max(1, min(5, level))
    cache_file = CACHE_DIR / f"bld_card_{building_type}_lvl{lvl}.jpg"
    if cache_file.exists() and cache_file.stat().st_size > 5000:
        return str(cache_file)

    base_file = BUILDINGS_DIR / f"bld_{building_type}_lvl{lvl}.jpg"
    if not base_file.exists():
        base_file = BUILDINGS_DIR / f"bld_{building_type}.jpg"
    if not base_file.exists():
        base_file = BUILDINGS_DIR / "bld_main_hall_lvl1.jpg"
    if not base_file.exists():
        base_file = BUILDINGS_DIR / "bld_main_hall.jpg"

    if base_file.exists():
        try:
            base_img = Image.open(base_file).convert("RGB")
            card = composite_building_card(base_img, building_key=building_type, level=lvl, is_alliance=False)
            card.save(cache_file, "JPEG", quality=93, progressive=True)
            return str(cache_file)
        except Exception:
            return str(base_file)

    return None


def get_alliance_building_image_path(perk_key: str, level: int = 1) -> str | None:
    """Returns the local composited card file path for an alliance building/perk at a specific level."""
    if not perk_key:
        return None

    lvl = max(1, min(5, level))
    cache_file = CACHE_DIR / f"ally_card_{perk_key}_lvl{lvl}.jpg"
    if cache_file.exists() and cache_file.stat().st_size > 5000:
        return str(cache_file)

    base_file = ALLIANCE_DIR / f"ally_{perk_key}_lvl{lvl}.jpg"
    if not base_file.exists():
        base_file = ALLIANCE_DIR / f"ally_{perk_key}.jpg"
    if not base_file.exists():
        base_file = ALLIANCE_DIR / "ally_hall_lvl1.jpg"
    if not base_file.exists():
        base_file = BUILDINGS_DIR / "bld_main_hall.jpg"

    if base_file.exists():
        try:
            base_img = Image.open(base_file).convert("RGB")
            card = composite_building_card(base_img, building_key=perk_key, level=lvl, is_alliance=True)
            card.save(cache_file, "JPEG", quality=93, progressive=True)
            return str(cache_file)
        except Exception:
            return str(base_file)

    return None


def composite_equipment_card(
    base_img: Image.Image,
    slug: str,
    rarity: str = "common",
    level: int = 1,
    bonus_str: str = "",
    power: int = 0,
) -> Image.Image:
    cfg = RARITY_STYLES.get(str(rarity).lower(), RARITY_STYLES["common"])
    info = EQUIPMENT_INFO.get(slug, {
        "en": slug.replace("_", " ").upper(),
        "fa": slug,
        "slot": "gear",
        "slot_label": "تجهیزات"
    })
    w, h = base_img.size

    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)

    grad_h = 160
    by = h - grad_h
    for i in range(grad_h):
        progress = i / grad_h
        alpha = int(245 * (progress ** 1.6))
        d.line([(0, by + i), (w, by + i)], fill=(6, 8, 12, alpha), width=1)

    comp = Image.alpha_composite(base_img.convert("RGBA"), overlay).convert("RGB")
    dt = ImageDraw.Draw(comp)

    font_title = _load_ui_font(32, bold=True)
    font_sub = _load_ui_font(21, bold=False, persian=True)
    font_spec = _load_ui_font(23, bold=True, persian=True)

    r_color = cfg["border"]
    clean_label = cfg["label"].replace("★", "").replace("◆", "").replace("●", "").strip()

    title_text = f"{info['en']}   •   {clean_label}"
    sub_text = shape_persian_text(f"{info['slot_label']}   •   {info['fa']}")

    y1 = h - 105
    y2 = h - 60

    # Title & Subtitle on left
    dt.text((45 + 1, y1 + 1), title_text, font=font_title, fill=(0, 0, 0))
    dt.text((45, y1), title_text, font=font_title, fill=r_color)

    dt.text((45 + 1, y2 + 1), sub_text, font=font_sub, fill=(0, 0, 0))
    dt.text((45, y2), sub_text, font=font_sub, fill=(185, 195, 215))

    # Right side: Level + Specifications / Stats
    lvl_text = f"+{level}" if level > 0 else "BASE"
    dt.text((w - 45 + 1, y1 + 1), lvl_text, font=font_title, fill=(0, 0, 0), anchor="ra")
    dt.text((w - 45, y1), lvl_text, font=font_title, fill=(255, 215, 50), anchor="ra")

    spec_raw = bonus_str if bonus_str else f"POWER {power}"
    spec_text = shape_persian_text(spec_raw)
    dt.text((w - 45 + 1, y2 + 1), spec_text, font=font_spec, fill=(0, 0, 0), anchor="ra")
    dt.text((w - 45, y2), spec_text, font=font_spec, fill=(180, 230, 255), anchor="ra")

    return comp


def get_equipment_image_path(item_or_slot, rarity: str = "common") -> str | None:
    """Returns the local composited card file path for an equipment piece."""
    if item_or_slot is None:
        return None

    name = None
    level = 1
    slot = "weapon"
    bonus_str = ""
    power = 0
    item_id = 0

    if hasattr(item_or_slot, "slot"):
        slot = getattr(item_or_slot, "slot", "weapon")
        rarity = getattr(item_or_slot, "rarity", "common")
        level = getattr(item_or_slot, "level", 1)
        name = getattr(item_or_slot, "name", None)
        item_id = getattr(item_or_slot, "id", 0)
        try:
            from game.equipment import bonus_text, equipment_power
            bonus_str = bonus_text(item_or_slot)
            power = equipment_power(item_or_slot)
        except Exception:
            pass
    elif isinstance(item_or_slot, str):
        if item_or_slot in EQUIPMENT_NAME_TO_SLUG:
            name = item_or_slot
        else:
            slot = item_or_slot

    slug = EQUIPMENT_NAME_TO_SLUG.get(name) or name or slot

    # Check cache first
    cache_file = CACHE_DIR / f"equip_card_{slug}_{rarity}_lvl{level}_{item_id}.jpg"
    if cache_file.exists() and cache_file.stat().st_size > 5000:
        return str(cache_file)

    # Base artwork file
    base_file = EQUIPMENT_DIR / f"equip_{slug}.jpg"
    if not base_file.exists():
        base_file = EQUIPMENT_DIR / f"equip_{slot}.jpg"
    if not base_file.exists():
        base_file = EQUIPMENT_DIR / f"equip_{slot}_{rarity}.jpg"
    if not base_file.exists():
        base_file = EQUIPMENT_DIR / "equip_steel_claws.jpg"

    if base_file.exists():
        try:
            base_img = Image.open(base_file).convert("RGB")
            card = composite_equipment_card(
                base_img,
                slug=slug,
                rarity=rarity,
                level=level,
                bonus_str=bonus_str,
                power=power
            )
            card.save(cache_file, "JPEG", quality=93, progressive=True)
            return str(cache_file)
        except Exception:
            return str(base_file)

    return None


def composite_lootbox_batch_image(rolls: list[dict], title: str = "باکس ژنتیکی") -> str | None:
    """Composites a single high-quality grid image displaying all items/creatures
    obtained from a multi-open lootbox (e.g. 10+1 or batch open)."""
    if not rolls:
        return None

    if len(rolls) == 1:
        r = rolls[0]
        if r.get("kind") == "creature" and r.get("creature"):
            return get_creature_image_path(r["creature"])
        elif r.get("kind") == "equipment" and r.get("item"):
            return get_equipment_image_path(r["item"])
        return None

    from game import constants

    # Deterministic cache key based on items
    key_parts = []
    for r in rolls:
        k = r.get("kind", "")
        obj = r.get("creature") or r.get("item")
        obj_id = getattr(obj, "id", "")
        rarity = r.get("rarity", "")
        key_parts.append(f"{k}_{obj_id}_{rarity}")
    batch_hash = hashlib.md5("_".join(key_parts).encode()).hexdigest()[:16]

    cache_file = CACHE_DIR / f"loot_batch_{batch_hash}.jpg"
    if cache_file.exists() and cache_file.stat().st_size > 10000:
        return str(cache_file)

    # Grid dimensions calculation
    N = len(rolls)
    if N <= 4:
        cols = N
        rows = 1
    elif N <= 8:
        cols = 4
        rows = (N + 3) // 4
    elif N <= 12:
        cols = 4
        rows = (N + 3) // 4
    else:
        cols = 5
        rows = (N + 4) // 5

    card_w = 270
    card_h = 202
    gap_x = 20
    gap_y = 20
    pad_x = 40
    pad_top = 105
    pad_bottom = 35

    canvas_w = pad_x * 2 + cols * card_w + (cols - 1) * gap_x
    canvas_h = pad_top + rows * card_h + (rows - 1) * gap_y + pad_bottom

    # Dark sci-fi gradient background
    canvas = Image.new("RGB", (canvas_w, canvas_h), (10, 13, 20))
    draw = ImageDraw.Draw(canvas)

    # Top-to-bottom atmospheric gradient
    for y in range(canvas_h):
        t = y / canvas_h
        r = int(18 * (1 - t) + 8 * t)
        g = int(24 * (1 - t) + 10 * t)
        b = int(38 * (1 - t) + 16 * t)
        draw.line([(0, y), (canvas_w, y)], fill=(r, g, b))

    # Ambient radial header glow
    header_glow = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(header_glow)
    glow_draw.ellipse(
        [(canvas_w // 2 - 350, -100), (canvas_w // 2 + 350, 180)],
        fill=(40, 80, 160, 45),
    )
    canvas = Image.alpha_composite(canvas.convert("RGBA"), header_glow).convert("RGB")
    draw = ImageDraw.Draw(canvas)

    # Header texts
    font_title = _load_ui_font(34, bold=True)
    font_sub = _load_ui_font(20, bold=False, persian=True)

    header_title = f"LOOT REVEAL   •   {N} REWARDS"
    draw.text((canvas_w // 2 + 1, 32 + 1), header_title, font=font_title, fill=(0, 0, 0), anchor="mt")
    draw.text((canvas_w // 2, 32), header_title, font=font_title, fill=(255, 215, 60), anchor="mt")

    sub_persian = shape_persian_text(f"نتایج گشایش {title}   •   کایجو لجندز")
    draw.text((canvas_w // 2 + 1, 72 + 1), sub_persian, font=font_sub, fill=(0, 0, 0), anchor="mt")
    draw.text((canvas_w // 2, 72), sub_persian, font=font_sub, fill=(175, 195, 225), anchor="mt")

    # Separator line under header
    draw.line([(pad_x + 80, 100), (canvas_w - pad_x - 80, 100)], fill=(45, 60, 85), width=1)

    # Pre-render item cards
    card_mask = Image.new("L", (card_w, card_h), 0)
    mask_draw = ImageDraw.Draw(card_mask)
    mask_draw.rounded_rectangle([(0, 0), (card_w, card_h)], radius=12, fill=255)

    order = {r: i for i, r in enumerate(constants.RARITY_ORDER)}
    best_item = max(rolls, key=lambda r: (order.get(r.get("rarity", "common"), 0), getattr(r.get("creature") or r.get("item"), "id", 0)))
    font_badge = _load_ui_font(15, bold=True)

    for idx, roll in enumerate(rolls):
        r_idx = idx // cols

        # Items in this specific row
        row_start = r_idx * cols
        row_end = min(N, (r_idx + 1) * cols)
        items_in_row = row_end - row_start

        row_w = items_in_row * card_w + (items_in_row - 1) * gap_x
        start_x = (canvas_w - row_w) // 2
        x = start_x + (idx - row_start) * (card_w + gap_x)
        y = pad_top + 15 + r_idx * (card_h + gap_y)

        kind = roll.get("kind")
        rarity = roll.get("rarity", "common")
        cfg = RARITY_STYLES.get(rarity, RARITY_STYLES["common"])
        border_col = cfg["border"]

        # Resolve card image
        img_path = None
        if kind == "creature" and roll.get("creature"):
            img_path = get_creature_image_path(roll["creature"])
        elif kind == "equipment" and roll.get("item"):
            img_path = get_equipment_image_path(roll["item"])

        if img_path and os.path.exists(img_path):
            try:
                card_src = Image.open(img_path).convert("RGBA")
                card_scaled = card_src.resize((card_w, card_h), Image.Resampling.LANCZOS)
            except Exception:
                card_scaled = Image.new("RGBA", (card_w, card_h), (25, 30, 45, 255))
        else:
            card_scaled = Image.new("RGBA", (card_w, card_h), (25, 30, 45, 255))

        # Paste with rounded corners
        canvas.paste(card_scaled.convert("RGB"), (x, y), card_mask)

        # Draw glowing rarity border
        is_best = (roll is best_item and order.get(rarity, 0) >= 2)
        border_width = 3 if is_best else 2

        # Outer glow for high rarities
        if order.get(rarity, 0) >= 2:
            glow_col = cfg.get("glow", border_col)
            draw.rounded_rectangle([(x - 2, y - 2), (x + card_w + 1, y + card_h + 1)], radius=14, outline=glow_col, width=1)

        draw.rounded_rectangle([(x, y), (x + card_w - 1, y + card_h - 1)], radius=12, outline=border_col, width=border_width)

        # Best item crown/star badge
        if is_best:
            badge_w, badge_h = 60, 22
            bx, by = x + card_w - badge_w - 8, y + 8
            draw.rounded_rectangle([(bx, by), (bx + badge_w, by + badge_h)], radius=6, fill=(20, 10, 5, 230), outline=(255, 215, 60), width=1)
            draw.text((bx + badge_w // 2, by + 2), "TOP ★", font=font_badge, fill=(255, 220, 80), anchor="mt")

    try:
        canvas.save(cache_file, "JPEG", quality=92, progressive=True)
        return str(cache_file)
    except Exception:
        return None


def get_banner_image_path(banner_name: str) -> str | None:
    """Returns the local image file path for a section banner."""
    if not banner_name:
        return None

    filename = f"banner_{banner_name}.jpg"
    path = BANNERS_DIR / filename
    if path.exists():
        return str(path)

    return None


def get_lab_overview_image_path(user, creature, buildings: list | None = None) -> str | None:
    """Composites an overview command screen showing the player's lab, active Kaiju, and buildings."""
    if user is None:
        return None

    creature_id = getattr(creature, "id", 0) if creature else 0
    c_lvl = getattr(creature, "level", 1) if creature else 1
    c_star = getattr(creature, "star_level", 1) if creature else 1

    from game.lab import lab_level

    user_lab_lvl = lab_level(user)

    # Cache file name based on state
    cache_file = CACHE_DIR / f"lab_ov_{user.id}_{creature_id}_{c_lvl}_{c_star}_{user_lab_lvl}.jpg"
    if cache_file.exists():
        return str(cache_file)

    try:
        # Base background: panoramic base image
        bg_path = BANNERS_DIR / "banner_base_panoramic.jpg"
        if not bg_path.exists():
            bg_path = BUILDINGS_DIR / "bld_main_hall.jpg"

        if bg_path.exists():
            base_img = Image.open(bg_path).convert("RGB")
        else:
            base_img = Image.new("RGB", (1024, 1024), (16, 22, 34))

        w, h = base_img.size

        # Overlay active Kaiju in the corner if present
        kaiju_path = get_creature_image_path(creature)
        if kaiju_path and os.path.exists(kaiju_path):
            k_img = Image.open(kaiju_path).convert("RGB")
            kw, kh = int(w * 0.44), int(h * 0.44)
            k_small = k_img.resize((kw, kh), Image.Resampling.LANCZOS)

            # Element-based border color
            elem = getattr(creature, "element", "fire")
            elem_colors = {
                "fire": (255, 120, 20),
                "water": (0, 200, 255),
                "earth": (160, 220, 40),
                "electric": (255, 215, 0),
                "crystal": (210, 80, 255),
                "plasma": (0, 240, 255),
            }
            border_col = elem_colors.get(elem, (255, 140, 0))

            mask = Image.new("L", (kw, kh), 0)
            draw_mask = ImageDraw.Draw(mask)
            draw_mask.rounded_rectangle([4, 4, kw - 4, kh - 4], radius=32, fill=255)
            mask = mask.filter(ImageFilter.GaussianBlur(6))

            pos_x = w - kw - 24
            pos_y = h - kh - 24

            draw_base = ImageDraw.Draw(base_img)
            draw_base.rounded_rectangle(
                [pos_x - 4, pos_y - 4, pos_x + kw + 4, pos_y + kh + 4],
                radius=34,
                fill=(12, 16, 24),
                outline=border_col,
                width=4,
            )
            base_img.paste(k_small, (pos_x, pos_y), mask)

        base_img.save(cache_file, quality=92)
        return str(cache_file)
    except Exception as exc:
        default_hall = BUILDINGS_DIR / "bld_main_hall.jpg"
        if default_hall.exists():
            return str(default_hall)
        return None


def get_cave_image_path() -> str | None:
    """Returns the path to the monster cave illustration."""
    path = FEATURES_DIR / "cave.jpg"
    if path.exists():
        return str(path)
    return None


def get_notify_image_path(kind: str) -> str | None:
    """Returns the notification artwork path for the given kind ('transfer', 'energy', 'war_win')."""
    mapping = {
        "transfer": "notify_transfer.jpg",
        "energy": "notify_energy.jpg",
        "energy_full": "notify_energy.jpg",
        "war_win": "notify_war_win.jpg",
        "war_reward": "notify_war_win.jpg",
    }
    filename = mapping.get(kind)
    if filename:
        path = FEATURES_DIR / filename
        if path.exists():
            return str(path)
    return None


def get_drop_image_path(kind: str, state: str = "locked") -> str | None:
    """Returns the artwork path for group flash drop of the given kind and state.

    `state`:
      - 'locked' / 'closed' / 'unclaimed': drop appeared in chat, box is locked / monster waiting.
      - 'open' / 'opened' / 'claimed' / 'won': drop was claimed, box opened / rewards revealed.
    """
    state_key = "open" if state in ("open", "opened", "claimed", "won") else "locked"

    # 1. State-specific drop image e.g. drop_chest_locked.jpg or drop_chest_open.jpg
    target = DROPS_DIR / f"drop_{kind}_{state_key}.jpg"
    if target.exists():
        return str(target)

    # 2. Base drop image e.g. drop_chest.jpg
    legacy = DROPS_DIR / f"drop_{kind}.jpg"
    if legacy.exists():
        return str(legacy)

    # 3. Fallbacks to chest
    fallback_target = DROPS_DIR / f"drop_chest_{state_key}.jpg"
    if fallback_target.exists():
        return str(fallback_target)
    fallback_legacy = DROPS_DIR / "drop_chest.jpg"
    if fallback_legacy.exists():
        return str(fallback_legacy)
    return None


def get_feature_image_path(feature_name: str) -> str | None:
    """Returns the artwork path for a bot feature/section (e.g. 'missions', 'hunt', 'arena', 'shop', etc.)."""
    if not feature_name:
        return None
    if feature_name in ("breeding", "cave"):
        p = FEATURES_DIR / "cave.jpg"
        if p.exists():
            return str(p)
    if feature_name in ("hub_shop", "cat_shop"):
        p = FEATURES_DIR / "feat_cat_shop.jpg"
        if p.exists():
            return str(p)
    if feature_name in ("hub_city", "cat_social", "cat_rewards"):
        p = FEATURES_DIR / "feat_cat_social.jpg"
        if p.exists():
            return str(p)
    filename = f"feat_{feature_name}.jpg"
    path = FEATURES_DIR / filename
    if path.exists():
        return str(path)
    return None


def get_arena_chest_image_path(tier: str, state: str = "locked") -> str | None:
    """Returns the artwork path for an arena chest in locked or open state."""
    state_key = "open" if state in ("open", "opened", "claimed", "ready") else "locked"
    target = CHESTS_DIR / f"{tier}_{state_key}.jpg"
    if target.exists():
        return str(target)
    # fallback to locked version if open doesn't exist
    fallback = CHESTS_DIR / f"{tier}_locked.jpg"
    if fallback.exists():
        return str(fallback)
    return get_feature_image_path("arena_chests")







# ── «🏟 جام آخر هفته»: the bracket of one group ────────────────────────────────
def render_tournament_bracket(br: dict, me_user_id: int | None = None) -> str | None:
    """A knockout bracket picture of one tournament group (game.tournament.group_bracket):
    quarter-final on the right, then semi-final, final and the champion on the left (Persian
    reads right to left). Winners are green, players who are out are dimmed, the viewer's own
    boxes are gold. Cached per (week, group, played state, viewer)."""
    import hashlib
    import json

    from PIL import Image, ImageDraw, ImageFont

    from game import tournament as T

    state = json.dumps([br["week"], br["group"], br["rounds"], br["champion"], me_user_id], sort_keys=True, default=str)
    key = hashlib.sha1(state.encode()).hexdigest()[:16]
    out = CACHE_DIR / f"tour_bracket_{key}.jpg"
    if out.exists():
        return str(out)
    try:
        W, H = 1280, 940
        img = Image.new("RGB", (W, H), (12, 15, 26))
        d = ImageDraw.Draw(img)
        for y in range(H):   # soft vertical gradient
            t = y / H
            d.line([(0, y), (W, y)], fill=(int(14 + 10 * t), int(18 + 6 * t), int(34 - 8 * t)))
        bold = str(FONTS_DIR / "Vazirmatn-Bold.ttf")
        reg = str(FONTS_DIR / "Vazirmatn-Regular.ttf")
        f_title = ImageFont.truetype(bold, 44)
        f_sub = ImageFont.truetype(reg, 24)
        f_head = ImageFont.truetype(bold, 26)
        f_name = ImageFont.truetype(bold, 23)
        f_small = ImageFont.truetype(reg, 18)

        def text_c(x, y, txt, font, fill):
            t = shape_persian_text(txt)
            w = d.textlength(t, font=font)
            d.text((x - w / 2, y), t, font=font, fill=fill)

        def text_r(x_right, y, txt, font, fill):
            t = shape_persian_text(txt)
            w = d.textlength(t, font=font)
            d.text((x_right - w, y), t, font=font, fill=fill)

        text_c(W / 2, 22, f"جام آخر هفته — گروه {br['group']}", f_title, (255, 216, 120))
        text_c(W / 2, 80, f"{len(br['members'])} بازیکن · برنده‌ی هر بازی صعود می‌کنه · ساعت ۲۰، ۲۱ و ۲۲", f_sub, (190, 200, 225))

        col_w, box_h = 270, 58
        cols = {1: 975, 2: 655, 3: 335}           # left x of each round's column
        champ_x = 30
        top, bottom = 170, H - 40
        heads = {1: T.ROUND_NAMES[1], 2: T.ROUND_NAMES[2], 3: T.ROUND_NAMES[3]}
        for r, x in cols.items():
            text_c(x + col_w / 2, top - 52, heads[r], f_head, (140, 210, 255))
        text_c(champ_x + col_w / 2, top - 52, "قهرمان", f_head, (255, 216, 120))

        # vertical centre of every match position, by round
        n1 = T.GROUP_SIZE // 2
        span = (bottom - top) / n1
        centres = {1: [top + span * (i + 0.5) for i in range(n1)]}
        for r in range(2, T.ROUNDS + 1):
            prev = centres[r - 1]
            centres[r] = [(prev[2 * i] + prev[2 * i + 1]) / 2 for i in range(len(prev) // 2)]

        GREEN, DIM, GOLD, LINE = (60, 200, 120), (95, 102, 125), (255, 200, 70), (70, 80, 110)

        def player_box(x, y, side, won, lost, label_if_empty="؟"):
            mine = side is not None and me_user_id is not None and side["user"] == me_user_id
            fill = (28, 34, 54)
            border = LINE
            name_col = (235, 240, 255)
            if won:
                fill, border = (24, 58, 44), GREEN
            if lost:
                fill, name_col = (24, 27, 40), DIM
            if mine:
                border = GOLD
            d.rounded_rectangle([x, y, x + col_w, y + box_h], radius=12, fill=fill, outline=border, width=3 if (mine or won) else 2)
            name = side["name"] if side else label_if_empty
            if len(name) > 18:
                name = name[:17] + "…"
            text_r(x + col_w - 14, y + 13, name, f_name, name_col if side else (120, 130, 155))

        def match_block(r, i, m):
            cx = cols[r]
            cy = centres[r][i]
            gap = 8 if r == 1 else 6
            ya, yb = cy - box_h - gap / 2, cy + gap / 2
            a = m.get("a") if m else None
            b = m.get("b") if m else None
            w = m.get("winner") if m else None
            played = w is not None
            player_box(cx, ya, a, played and a and w == a["entry"], played and a and w != a["entry"])
            if m is not None and b is None and a is not None:
                player_box(cx, yb, None, False, False, label_if_empty="استراحت")
            else:
                player_box(cx, yb, b, played and b and w == b["entry"], played and b and w != b["entry"])
            # elbow line to the next round
            nx = cols.get(r + 1, champ_x) + col_w
            ny = centres[r + 1][i // 2] if r < T.ROUNDS else centres[T.ROUNDS][0]
            col = GREEN if played else LINE
            d.line([(cx, cy), (cx - 18, cy)], fill=col, width=3)
            d.line([(cx - 18, cy), (cx - 18, ny)], fill=col, width=3)
            d.line([(cx - 18, ny), (nx, ny)], fill=col, width=3)

        for r in range(1, T.ROUNDS + 1):
            by_slot = {m["slot"]: m for m in br["rounds"].get(r, [])}
            for i in range(len(centres[r])):
                match_block(r, i, by_slot.get(i))

        # champion
        cy = centres[T.ROUNDS][0]
        champ = br.get("champion")
        mine = champ is not None and champ["user"] == me_user_id
        d.rounded_rectangle([champ_x, cy - 40, champ_x + col_w, cy + 40], radius=16,
                            fill=(60, 48, 20) if champ else (28, 34, 54), outline=GOLD, width=4 if mine else 3)
        text_c(champ_x + col_w / 2, cy - 22, (champ["name"][:17] if champ else "؟"), f_name, (255, 230, 160))
        text_c(champ_x + col_w / 2, cy + 52, "جعبه‌ی جادویی + ۴۰ الماس", f_small, (200, 190, 150))

        # legend
        ly = H - 32
        for i, (col, label) in enumerate([(GREEN, "صعود"), (DIM, "حذف"), (GOLD, "تو")]):
            x = W - 40 - i * 150
            d.rounded_rectangle([x - 22, ly, x, ly + 20], radius=5, fill=col)
            text_r(x - 30, ly - 3, label, f_small, (200, 205, 220))
        img.save(out, "JPEG", quality=90)
        return str(out)
    except Exception:  # noqa: BLE001 — a picture must never break the screen
        import logging

        logging.getLogger(__name__).exception("tournament bracket render failed")
        return None

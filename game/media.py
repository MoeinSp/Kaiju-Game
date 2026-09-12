"""Asset and image resolution utilities for TelGame."""

import os
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter, ImageFont

# Base directory for assets
BASE_DIR = Path(__file__).resolve().parent.parent
ASSETS_DIR = BASE_DIR / "assets" / "images"
KAIJU_DIR = ASSETS_DIR / "kaijus"
BUILDINGS_DIR = ASSETS_DIR / "buildings"
ALLIANCE_DIR = ASSETS_DIR / "alliance"
EQUIPMENT_DIR = ASSETS_DIR / "equipment"
BANNERS_DIR = ASSETS_DIR / "banners"
CACHE_DIR = ASSETS_DIR / "cache"

CACHE_DIR.mkdir(parents=True, exist_ok=True)


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


def _load_ui_font(size: int, bold: bool = False):
    candidates = [
        "C:/Windows/Fonts/seguisym.ttf",
        "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
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

    # 1. Species-based progression
    if slug:
        cache_file = CACHE_DIR / f"creature_{slug}_s{stage}_{rarity}_star{star_level}.jpg"
        if cache_file.exists() and cache_file.stat().st_size > 5000:
            return str(cache_file)

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
    font_sub = _load_ui_font(21, bold=False)

    category = "اتحاد • ALLIANCE" if is_alliance else "پایگاه کایجو • BASE"
    title_text = f"{en_title}   •   LEVEL {level}"
    sub_text = f"{fa_label}   •   {category}"

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
    font_sub = _load_ui_font(21, bold=False)
    font_spec = _load_ui_font(23, bold=True)

    r_color = cfg["border"]
    clean_label = cfg["label"].replace("★", "").replace("◆", "").replace("●", "").strip()

    title_text = f"{info['en']}   •   {clean_label}"
    sub_text = f"{info['slot_label']}   •   {info['fa']}"

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

    spec_text = bonus_str if bonus_str else f"POWER {power}"
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


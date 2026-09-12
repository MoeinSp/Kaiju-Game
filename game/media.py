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


def get_creature_image_path(creature) -> str | None:
    """Returns the local image file path for a creature based on element and level."""
    if creature is None:
        return None

    element = getattr(creature, "element", "fire")
    level = getattr(creature, "level", 1)

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

    # Fallback to tier1 if higher tier is missing
    fallback = KAIJU_DIR / f"{element}_tier1.jpg"
    if fallback.exists():
        return str(fallback)

    return None


def get_building_image_path(building_type: str, level: int = 1) -> str | None:
    """Returns the local image file path for a personal building at a specific level."""
    if not building_type:
        return None

    lvl = max(1, min(5, level))
    filename = f"bld_{building_type}_lvl{lvl}.jpg"
    path = BUILDINGS_DIR / filename
    if path.exists():
        return str(path)

    # Fallback to unversioned building image
    fallback_bld = BUILDINGS_DIR / f"bld_{building_type}.jpg"
    if fallback_bld.exists():
        return str(fallback_bld)

    # Fallback to main_hall if available
    default_hall = BUILDINGS_DIR / "bld_main_hall.jpg"
    if default_hall.exists():
        return str(default_hall)

    return None


def get_alliance_building_image_path(perk_key: str, level: int = 1) -> str | None:
    """Returns the local image file path for an alliance building/perk at a specific level."""
    if not perk_key:
        return None

    lvl = max(1, min(5, level))
    filename = f"ally_{perk_key}_lvl{lvl}.jpg"
    path = ALLIANCE_DIR / filename
    if path.exists():
        return str(path)

    fallback = ALLIANCE_DIR / f"ally_{perk_key}.jpg"
    if fallback.exists():
        return str(fallback)

    # Fallback to alliance hall level 1 or main hall
    hall_fallback = ALLIANCE_DIR / "ally_hall_lvl1.jpg"
    if hall_fallback.exists():
        return str(hall_fallback)

    default_hall = BUILDINGS_DIR / "bld_main_hall.jpg"
    if default_hall.exists():
        return str(default_hall)

    return None


def get_equipment_image_path(item_or_slot, rarity: str = "common") -> str | None:
    """Returns the local image file path for an equipment piece."""
    if item_or_slot is None:
        return None

    if hasattr(item_or_slot, "slot"):
        slot = getattr(item_or_slot, "slot", "weapon")
        rarity = getattr(item_or_slot, "rarity", "common")
    else:
        slot = str(item_or_slot)

    filename = f"equip_{slot}_{rarity}.jpg"
    path = EQUIPMENT_DIR / filename
    if path.exists():
        return str(path)

    fallback = EQUIPMENT_DIR / f"equip_{slot}.jpg"
    if fallback.exists():
        return str(fallback)

    # General fallback
    default_weapon = EQUIPMENT_DIR / "equip_weapon_common.jpg"
    if default_weapon.exists():
        return str(default_weapon)

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


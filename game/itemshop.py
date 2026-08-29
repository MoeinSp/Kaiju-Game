"""Owner-authored shop items and packs.

The owner builds offers from the admin panel by sending a short spec (see
parse_spec below); players buy them from «🛍 آیتم‌های ویژه» in the shop. An item's
`contents` is a list of reward components, so a single mythic creature and a big
multi-part «pack» use exactly the same machinery — a pack is just an item with more
than one component.
"""

from __future__ import annotations

import json
import random

from django.db import transaction

from bio_lab.models import Creature, Equipment, ShopItem, User
from game import constants
from game.creature import GameError, InsufficientGoldError

# ── rarity / element / slot vocab (accept English keys + common Persian aliases) ──
_RARITY_ALIASES = {
    "common": "common", "معمولی": "common",
    "rare": "rare", "نایاب": "rare",
    "epic": "epic", "حماسی": "epic",
    "legendary": "legendary", "افسانه‌ای": "legendary", "افسانه": "legendary",
    "mythic": "mythic", "اساطیری": "mythic",
}
_ELEMENT_ALIASES = {
    "fire": "fire", "آتش": "fire",
    "water": "water", "آب": "water",
    "earth": "earth", "خاک": "earth",
    "electric": "electric", "الکتریسیته": "electric", "برق": "electric",
}
_SLOT_ALIASES = {
    "weapon": "weapon", "سلاح": "weapon",
    "armor": "armor", "زره": "armor",
    "rune": "rune", "طلسم": "rune",
    "offhand": "offhand", "غلاف": "offhand",
}
_COIN_WORDS = {"سکه", "طلا", "coins", "coin", "gold"}
_DIAMOND_WORDS = {"جم", "الماس", "diamonds", "diamond", "gem", "gems"}
_DNA_WORDS = {"dna", "دی‌ان‌ای", "دی‌ان‌آی", "دیان‌ای"}


def _to_int(tok: str) -> int | None:
    # tolerate Persian digits
    trans = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
    tok = tok.translate(trans)
    return int(tok) if tok.lstrip("-").isdigit() else None


def parse_spec(text: str) -> dict:
    """Parse the owner's item spec into {title, emoji, price_coins, price_diamonds,
    contents}. Raises GameError with a helpful message on any bad line.

    Format (one item per message):
        <title>
        قیمت: 19000 جم            (or: 5000 سکه 50 جم — either/both, any order)
        سکه 10000                 (content lines, one component each)
        جم 25
        dna 200
        کارت 60 3                 (speedup: minutes [count])
        هیولا mythic fire         (creature: rarity [element])
        تجهیزات weapon legendary  (equipment: slot rarity)
    """
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    if len(lines) < 2:
        raise GameError(
            "قالب ناقصه. حداقل دو خط لازمه: خط اول عنوان، خط دوم قیمت. "
            "بعدش هر خط یه محتوا (مثل «سکه 10000» یا «هیولا mythic fire»)."
        )

    title = lines[0][:64]
    emoji = "🎁"
    # allow a leading emoji in the title line: "🐉 اژدهای اساطیری"
    if title and title.split()[0] and not title.split()[0].isascii() and len(title.split()[0]) <= 4:
        maybe = title.split()[0]
        if any(ord(ch) > 0x2600 for ch in maybe):
            emoji = maybe
            title = title[len(maybe):].strip() or title

    price_coins = 0
    price_diamonds = 0
    price_line = lines[1].replace("قیمت", " ").replace(":", " ").replace("price", " ")
    ptoks = price_line.split()
    i = 0
    while i < len(ptoks):
        n = _to_int(ptoks[i])
        if n is not None and i + 1 < len(ptoks):
            unit = ptoks[i + 1].lower()
            if unit in _COIN_WORDS:
                price_coins = n
            elif unit in _DIAMOND_WORDS:
                price_diamonds = n
            i += 2
            continue
        i += 1
    if price_coins <= 0 and price_diamonds <= 0:
        raise GameError(
            "خط قیمت رو نفهمیدم. مثال: «قیمت: 19000 جم» یا «قیمت: 5000 سکه 50 جم»."
        )

    contents = [_parse_content_line(ln) for ln in lines[2:]]
    if not contents:
        raise GameError("حداقل یه خط محتوا لازمه (مثل «سکه 10000» یا «تجهیزات weapon legendary»).")

    return {
        "title": title, "emoji": emoji,
        "price_coins": price_coins, "price_diamonds": price_diamonds,
        "contents": contents,
    }


def parse_price_line(text: str) -> tuple[int, int]:
    """Parse a free-form price like «5000 سکه 50 جم» → (coins, diamonds). Raises
    GameError if no valid price is found. Used by the inline admin builder."""
    toks = text.replace("قیمت", " ").replace(":", " ").replace("price", " ").split()
    coins = diamonds = 0
    i = 0
    while i < len(toks):
        n = _to_int(toks[i])
        if n is not None and i + 1 < len(toks):
            unit = toks[i + 1].lower()
            if unit in _COIN_WORDS:
                coins = max(0, n)
            elif unit in _DIAMOND_WORDS:
                diamonds = max(0, n)
            i += 2
            continue
        i += 1
    if coins <= 0 and diamonds <= 0:
        raise GameError("قیمت رو نفهمیدم. مثال: «5000 سکه» یا «19000 جم» یا «5000 سکه 50 جم».")
    return coins, diamonds


def create_item_from_draft(draft: dict) -> ShopItem:
    return ShopItem.objects.create(
        title=draft["title"][:64], emoji=draft.get("emoji", "🎁"),
        price_coins=draft.get("price_coins", 0), price_diamonds=draft.get("price_diamonds", 0),
        contents_json=json.dumps(draft["contents"], ensure_ascii=False),
        max_per_user=max(0, int(draft.get("max_per_user", 0))),
    )


def _parse_content_line(line: str) -> dict:
    toks = line.split()
    head = toks[0].lower()
    rest = toks[1:]

    if head in _COIN_WORDS:
        n = _to_int(rest[0]) if rest else None
        if not n or n <= 0:
            raise GameError(f"مقدار سکه توی «{line}» درست نیست.")
        return {"type": "coins", "amount": n}
    if head in _DIAMOND_WORDS:
        n = _to_int(rest[0]) if rest else None
        if not n or n <= 0:
            raise GameError(f"مقدار جم توی «{line}» درست نیست.")
        return {"type": "diamonds", "amount": n}
    if head in _DNA_WORDS:
        n = _to_int(rest[0]) if rest else None
        if not n or n <= 0:
            raise GameError(f"مقدار DNA توی «{line}» درست نیست.")
        return {"type": "dna", "amount": n}
    if head in ("کارت", "speedup", "سرعت"):
        minutes = _to_int(rest[0]) if rest else None
        if minutes not in constants.SPEEDUP_MINUTES:
            raise GameError(
                f"دقیقه‌ی کارت سرعت توی «{line}» نامعتبره. یکی از این‌ها: "
                + "، ".join(str(m) for m in constants.SPEEDUP_MINUTES)
            )
        count = _to_int(rest[1]) if len(rest) > 1 else 1
        return {"type": "speedup", "minutes": minutes, "count": max(1, count or 1)}
    if head in ("هیولا", "creature", "کایجو"):
        if not rest or rest[0] not in _RARITY_ALIASES:
            raise GameError(f"نایابیِ هیولا توی «{line}» نامعتبره (مثل mythic/اساطیری).")
        rarity = _RARITY_ALIASES[rest[0]]
        element = None
        name_toks = rest[1:]
        if name_toks and name_toks[0] in _ELEMENT_ALIASES:
            element = _ELEMENT_ALIASES[name_toks[0]]
            name_toks = name_toks[1:]
        # anything left over is a custom name: «هیولا mythic fire اژدهای آتش»
        name = " ".join(name_toks).strip() or None
        return {"type": "creature", "rarity": rarity, "element": element, "name": name}
    if head in ("تجهیزات", "equipment", "آیتم", "item"):
        if len(rest) < 2 or rest[0] not in _SLOT_ALIASES or rest[1] not in _RARITY_ALIASES:
            raise GameError(f"تجهیزات توی «{line}» باید به شکل «تجهیزات [اسلات] [نایابی]» باشه.")
        # anything after slot+rarity is a custom name: «تجهیزات weapon legendary شمشیر مرگ»
        name = " ".join(rest[2:]).strip() or None
        return {"type": "equipment", "slot": _SLOT_ALIASES[rest[0]], "rarity": _RARITY_ALIASES[rest[1]], "name": name}

    raise GameError(
        f"خط «{line}» رو نشناختم. کلمه‌های مجاز: سکه، جم، dna، کارت، هیولا، تجهیزات."
    )


# ── rendering ─────────────────────────────────────────────────────────────────
def content_summary(contents: list[dict]) -> str:
    parts = []
    for c in contents:
        t = c["type"]
        if t == "coins":
            parts.append(f"{c['amount']:,} طلا")
        elif t == "diamonds":
            parts.append(f"{c['amount']} 💎")
        elif t == "dna":
            parts.append(f"{c['amount']} DNA")
        elif t == "energy":
            parts.append("انرژی کامل")
        elif t == "speedup":
            parts.append(f"{c['count']}× کارت سرعت {c['minutes']}دقیقه")
        elif t == "creature":
            r = constants.RARITY_LABELS[c["rarity"]]
            el = f" {constants.ELEMENT_WORDS[c['element']]}" if c.get("element") else ""
            nm = f" «{c['name']}»" if c.get("name") else ""
            parts.append(f"هیولای {r}{el}{nm}")
        elif t == "equipment":
            nm = f" «{c['name']}»" if c.get("name") else ""
            parts.append(f"{constants.EQUIPMENT_SLOT_LABELS[c['slot']]} {constants.RARITY_LABELS[c['rarity']]}{nm}")
    return " + ".join(parts) or "—"


def price_text(item: ShopItem) -> str:
    bits = []
    if item.price_coins:
        bits.append(f"{item.price_coins:,} طلا")
    if item.price_diamonds:
        bits.append(f"{item.price_diamonds} 💎")
    return " + ".join(bits) or "رایگان"


# ── CRUD ──────────────────────────────────────────────────────────────────────
def create_item(spec: dict) -> ShopItem:
    return ShopItem.objects.create(
        title=spec["title"], emoji=spec["emoji"], description=spec.get("description", ""),
        price_coins=spec["price_coins"], price_diamonds=spec["price_diamonds"],
        contents_json=json.dumps(spec["contents"], ensure_ascii=False),
    )


def list_items(active_only: bool = True) -> list[ShopItem]:
    qs = ShopItem.objects.all()
    if active_only:
        qs = qs.filter(is_active=True)
    return list(qs.order_by("sort_order", "id"))


def get_item(item_id: int) -> ShopItem | None:
    return ShopItem.objects.filter(id=item_id).first()


def delete_item(item_id: int) -> None:
    ShopItem.objects.filter(id=item_id).delete()


def toggle_item(item_id: int) -> bool:
    item = ShopItem.objects.filter(id=item_id).first()
    if item is None:
        raise GameError("این آیتم پیدا نشد.")
    item.is_active = not item.is_active
    item.save(update_fields=["is_active"])
    return item.is_active


# ── granting ──────────────────────────────────────────────────────────────────
def _make_creature(user: User, rarity: str, element: str | None, name: str | None = None) -> Creature:
    element = element or constants.random_element()
    mult = constants.RARITY_STAT_MULTIPLIER[rarity]
    return Creature.objects.create(
        owner=user, name=(name or constants.random_species_name(element))[:64], element=element, rarity=rarity,
        base_hp=round(constants.STARTER_BASE_HP * mult),
        base_atk=round(constants.STARTER_BASE_ATK * mult),
        base_def=round(constants.STARTER_BASE_DEF * mult),
        base_spd=round(constants.STARTER_BASE_SPD * mult),
        is_active=False,
    )


def _make_equipment(user: User, slot: str, rarity: str, name: str | None = None) -> Equipment:
    template = random.choice(constants.EQUIPMENT_TEMPLATES[slot])
    return Equipment.objects.create(
        owner=user, slot=slot, template_key=template, name=(name or template)[:48], rarity=rarity
    )


def grant_contents(user: User, contents: list[dict]) -> list[str]:
    """Apply every component to `user` and return human-readable notes. Coins/dna/
    diamonds are batched into one save; creatures/equipment are created as rows."""
    notes = []
    money_fields = set()
    for c in contents:
        t = c["type"]
        if t == "coins":
            user.coins += c["amount"]; money_fields.add("coins"); notes.append(f"{c['amount']:,} طلا")
        elif t == "diamonds":
            user.diamonds += c["amount"]; money_fields.add("diamonds"); notes.append(f"{c['amount']} 💎")
        elif t == "dna":
            user.dna_fragments += c["amount"]; money_fields.add("dna_fragments"); notes.append(f"{c['amount']} DNA")
        elif t == "energy":
            from django.utils import timezone
            user.energy = constants.MAX_ENERGY
            user.energy_updated_at = timezone.now()
            money_fields.update({"energy", "energy_updated_at"})
            notes.append("انرژی کامل")
        elif t == "speedup":
            from game.buildings import grant_speedup_card
            grant_speedup_card(user, c["minutes"], count=c["count"])
            notes.append(f"{c['count']}× کارت سرعت {c['minutes']}دقیقه")
        elif t == "creature":
            cr = _make_creature(user, c["rarity"], c.get("element"), c.get("name"))
            notes.append(f"هیولای {constants.RARITY_LABELS[c['rarity']]} «{cr.name}»")
        elif t == "equipment":
            it = _make_equipment(user, c["slot"], c["rarity"], c.get("name"))
            notes.append(f"{constants.EQUIPMENT_SLOT_LABELS[c['slot']]} «{it.name}» {constants.RARITY_LABELS[c['rarity']]}")
    if money_fields:
        user.save(update_fields=list(money_fields))
    return notes


@transaction.atomic
def buy(user: User, item_id: int) -> dict:
    from bio_lab.models import ShopItemPurchase

    item = ShopItem.objects.select_for_update().filter(id=item_id, is_active=True).first()
    if item is None:
        raise GameError("این آیتم دیگه در دسترس نیست.")
    # per-user purchase limit
    purchase = None
    if item.max_per_user and item.max_per_user > 0:
        purchase, _ = ShopItemPurchase.objects.select_for_update().get_or_create(user=user, item=item)
        if purchase.count >= item.max_per_user:
            raise GameError(
                f"این آیتم محدوده — هر نفر حداکثر {item.max_per_user} بار می‌تونه بخره و تو سقفت رو زدی."
            )
    user = User.objects.select_for_update().get(id=user.id)
    if user.coins < item.price_coins:
        raise InsufficientGoldError(
            f"طلا کافی نداری! این آیتم <b>{item.price_coins:,}</b> طلا می‌خواد (الان {user.coins:,} داری).",
            need=item.price_coins, have=user.coins,
        )
    if user.diamonds < item.price_diamonds:
        raise GameError(f"الماس کافی نداری! این آیتم {item.price_diamonds} الماس می‌خواد.")
    if item.price_coins:
        user.coins -= item.price_coins
    if item.price_diamonds:
        user.diamonds -= item.price_diamonds
    user.save(update_fields=["coins", "diamonds"])

    contents = json.loads(item.contents_json)
    notes = grant_contents(user, contents)
    if purchase is not None:
        purchase.count += 1
        purchase.save(update_fields=["count"])
    return {"title": item.title, "emoji": item.emoji, "notes": notes,
            "coins": user.coins, "diamonds": user.diamonds}

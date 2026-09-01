"""Rotating daily shop — a currency sink with a daily "check what's on today" hook.

Four offers are shown per day, drawn from a fixed pool by day-of-year, and the
first is the day's featured deal at a discount. Offers are buyable repeatedly
(they're utility conversions — gold→speedup cards, diamonds→resource packs, an
energy refill), which drains excess currency and gives diamonds somewhere to go
between the gacha. The daily rotation is the recurring reason to look.
"""

from __future__ import annotations

from django.utils import timezone

from bio_lab.models import User
from game import constants
from game.creature import GameError

FEATURED_DISCOUNT = 0.25  # the day's featured offer is this much off
OFFERS_PER_DAY = 4

_VALID_CURRENCIES = ("coins", "diamonds")

# The 7 built-in offers, seeded into the DailyShopItem catalog the first time the shop
# is read. After seeding, the catalog is fully owner-managed (add / delete / edit) — so
# these defaults are only the starting point, not a fixed list. Grants use the itemshop
# reward-component format so a custom offer (a kaiju, equipment, a pack) uses the exact
# same machinery.
BUILTIN_OFFERS = [
    {"key": "speedup30", "emoji": "⏱", "title": "کارت سرعت 30 دقیقه", "cost": 800, "currency": "coins", "contents": [{"type": "speedup", "minutes": 30, "count": 1}]},
    {"key": "speedup60", "emoji": "⏱", "title": "کارت سرعت 1 ساعت", "cost": 1500, "currency": "coins", "contents": [{"type": "speedup", "minutes": 60, "count": 1}]},
    {"key": "speedup720", "emoji": "⏱", "title": "کارت سرعت 12 ساعت", "cost": 30, "currency": "diamonds", "contents": [{"type": "speedup", "minutes": 720, "count": 1}]},
    {"key": "dna50", "emoji": "🧬", "title": "بسته‌ی 50 DNA", "cost": 15, "currency": "diamonds", "contents": [{"type": "dna", "amount": 50}]},
    {"key": "dna150", "emoji": "🧬", "title": "بسته‌ی 150 DNA", "cost": 40, "currency": "diamonds", "contents": [{"type": "dna", "amount": 150}]},
    {"key": "gold3000", "emoji": "💰", "title": "بسته‌ی 3000 طلا", "cost": 20, "currency": "diamonds", "contents": [{"type": "coins", "amount": 3000}]},
    {"key": "energy", "emoji": "⚡", "title": "شارژ کامل انرژی", "cost": 10, "currency": "diamonds", "contents": [{"type": "energy"}]},
]


def _ensure_catalog() -> None:
    """Seed the built-in offers into the DailyShopItem catalog exactly once — when the
    table is still empty. After that the catalog is whatever the owner has made it, so
    a deleted built-in stays deleted (we never re-seed per-key)."""
    import json

    from bio_lab.models import DailyShopItem

    if DailyShopItem.objects.exists():
        return
    for i, o in enumerate(BUILTIN_OFFERS):
        DailyShopItem.objects.get_or_create(
            key=o["key"],
            defaults={"emoji": o["emoji"], "title": o["title"],
                      "contents_json": json.dumps(o["contents"], ensure_ascii=False),
                      "cost": o["cost"], "currency": o["currency"], "sort_order": i},
        )


def catalog_items() -> list[dict]:
    """The full daily-shop catalog from the DB (seeded on first use). Each entry:
    {key, emoji, title, cost, currency, contents}. Sync-only (runs inside run_db)."""
    import json

    from bio_lab.models import DailyShopItem

    _ensure_catalog()
    out = []
    for r in DailyShopItem.objects.order_by("sort_order", "id"):
        try:
            contents = json.loads(r.contents_json)
        except (ValueError, TypeError):
            contents = []
        out.append({"key": r.key, "emoji": r.emoji, "title": r.title, "cost": max(0, r.cost),
                    "currency": r.currency if r.currency in _VALID_CURRENCIES else "coins",
                    "contents": contents})
    return out


def catalog_by_key() -> dict[str, dict]:
    return {o["key"]: o for o in catalog_items()}


def _day() -> int:
    return timezone.localtime(timezone.now()).timetuple().tm_yday


def _price(offer: dict, featured: bool) -> int:
    if featured:
        return max(1, round(offer["cost"] * (1 - FEATURED_DISCOUNT)))
    return offer["cost"]


# ── per-day schedule (repeating 3-day cycle) ─────────────────────────────────
# The daily shop can be scheduled per day. `slot = date.toordinal() % DAILY_CYCLE`
# so each of the 3 slots recurs every 3 days: configuring "today / tomorrow / day
# after" fills the whole cycle, and a slot keeps showing until it's reconfigured —
# which is exactly the "if a day isn't set, repeat what it was 3 days ago" rule.
DAILY_CYCLE = 3
DAY_LABELS = {0: "امروز", 1: "فردا", 2: "پس‌فردا"}  # OFFSET (not slot) → label


def _today_ordinal() -> int:
    return timezone.localtime(timezone.now()).date().toordinal()


def slot_for_offset(offset: int) -> int:
    """The cycle slot for `offset` days from today (0=today, 1=tomorrow, 2=day after)."""
    return (_today_ordinal() + offset) % DAILY_CYCLE


def _configured_offers_for_slot(slot: int) -> list[dict] | None:
    """The owner-scheduled offers for a slot, merged with each offer's fixed code
    definition (title/emoji/grant), or None if that slot isn't configured."""
    import json

    from bio_lab.models import DailyShopDay

    row = DailyShopDay.objects.filter(slot=slot, configured=True).first()
    if row is None:
        return None
    try:
        entries = json.loads(row.offers_json)
    except (ValueError, TypeError):
        entries = []
    by_key = catalog_by_key()
    out = []
    for e in entries:
        base = by_key.get(e.get("key"))
        if base is None:  # offer was deleted from the catalog since it was scheduled
            continue
        currency = e.get("currency") if e.get("currency") in _VALID_CURRENCIES else base["currency"]
        out.append({**base, "cost": max(0, int(e.get("cost", base["cost"]))), "currency": currency,
                    "limit": max(0, int(e.get("limit", 0)))})
    return out or None


def today_offers() -> list[dict]:
    """Today's shop. If today's cycle slot is scheduled by the owner, those exact
    offers/prices are shown (no featured discount — the set price is the price).
    Otherwise it falls back to the default rotation over the active pool, whose first
    entry is a discounted featured deal."""
    scheduled = _configured_offers_for_slot(slot_for_offset(0))
    if scheduled is not None:
        return [{**o, "featured": False, "price": o["cost"], "limit": o.get("limit", 0)} for o in scheduled]

    pool = catalog_items()
    if not pool:
        return []
    day = _day()
    n = len(pool)
    count = min(OFFERS_PER_DAY, n)
    picks = [pool[(day + i) % n] for i in range(count)]
    out = []
    for i, o in enumerate(picks):
        featured = i == 0
        out.append({**o, "featured": featured, "price": _price(o, featured), "limit": 0})
    return out


# ── owner admin: per-day scheduling ──────────────────────────────────────────
DIAMOND_PRICE_PRESETS = [5, 10, 15, 20, 30, 50, 100]
COIN_PRICE_PRESETS = [500, 800, 1000, 1500, 2000, 3000, 5000]


def day_offer_states(slot: int) -> list[dict]:
    """The full editable state for a slot: every pool offer with its cost/currency and
    whether it's active for that day. Seeds from the slot's saved schedule if it has
    one, else from the default prices with every offer active. Drives the admin editor."""
    import json

    from bio_lab.models import DailyShopDay

    by_key = catalog_by_key()
    saved = {}
    order = []
    row = DailyShopDay.objects.filter(slot=slot).first()
    if row is not None and row.configured:
        try:
            for e in json.loads(row.offers_json):
                if e.get("key") in by_key:
                    saved[e["key"]] = e
                    order.append(e["key"])
        except (ValueError, TypeError):
            pass

    states = []
    # saved-and-active offers first (in their saved order), then the rest as inactive
    for key in order + [k for k in by_key if k not in saved]:
        base = by_key[key]
        e = saved.get(key)
        if e is not None:
            currency = e.get("currency") if e.get("currency") in _VALID_CURRENCIES else base["currency"]
            states.append({"key": key, "emoji": base["emoji"], "title": base["title"],
                           "cost": max(0, int(e.get("cost", base["cost"]))), "currency": currency,
                           "limit": max(0, int(e.get("limit", 0))), "active": True})
        else:
            # if the slot was NEVER configured, default all offers ON; if it WAS
            # configured, offers not in the saved list are OFF
            active = row is None or not row.configured
            states.append({"key": key, "emoji": base["emoji"], "title": base["title"],
                           "cost": base["cost"], "currency": base["currency"],
                           "limit": 0, "active": active})
    return states


def save_day(slot: int, states: list[dict]) -> None:
    """Persist a slot's schedule from the editor draft. Only ACTIVE offers are stored
    (in their given order); the first shown becomes the day's lead offer."""
    import json

    from bio_lab.models import DailyShopDay

    entries = [
        {"key": s["key"], "cost": max(0, int(s["cost"])),
         "currency": s["currency"] if s["currency"] in _VALID_CURRENCIES else "coins",
         "limit": max(0, int(s.get("limit", 0)))}
        for s in states if s.get("active")
    ]
    DailyShopDay.objects.update_or_create(
        slot=slot, defaults={"offers_json": json.dumps(entries, ensure_ascii=False), "configured": True}
    )


def clear_day(slot: int) -> None:
    """Un-schedule a slot so it falls back to the default rotation again."""
    from bio_lab.models import DailyShopDay

    DailyShopDay.objects.filter(slot=slot).update(configured=False)


def day_is_configured(slot: int) -> bool:
    from bio_lab.models import DailyShopDay

    return DailyShopDay.objects.filter(slot=slot, configured=True).exists()


# ── owner admin: catalog add / delete ────────────────────────────────────────
def add_catalog_item(title: str, emoji: str, contents: list[dict], cost: int, currency: str) -> "object":
    """Add a brand-new offer to the daily-shop catalog (a kaiju, equipment, a pack —
    any itemshop contents). Returns the created DailyShopItem."""
    import json
    import time

    from bio_lab.models import DailyShopItem

    _ensure_catalog()
    if not contents:
        raise GameError("آیتم باید حداقل یه محتوا داشته باشه.")
    key = f"custom_{int(time.time())}_{DailyShopItem.objects.count() + 1}"
    order = (DailyShopItem.objects.count() + 1)
    return DailyShopItem.objects.create(
        key=key, emoji=(emoji or "🎁")[:8], title=(title or "آیتم")[:64],
        contents_json=json.dumps(contents, ensure_ascii=False),
        cost=max(0, int(cost)), currency=currency if currency in _VALID_CURRENCIES else "coins",
        sort_order=order,
    )


def delete_catalog_item(key: str) -> None:
    """Permanently remove an offer from the catalog and drop it from every day's
    schedule so it can never resurface."""
    import json

    from bio_lab.models import DailyShopDay, DailyShopItem

    DailyShopItem.objects.filter(key=key).delete()
    # scrub the key out of any saved day schedules
    for row in DailyShopDay.objects.all():
        try:
            entries = json.loads(row.offers_json)
        except (ValueError, TypeError):
            continue
        kept = [e for e in entries if e.get("key") != key]
        if len(kept) != len(entries):
            row.offers_json = json.dumps(kept, ensure_ascii=False)
            row.save(update_fields=["offers_json"])


def catalog_item_count() -> int:
    from bio_lab.models import DailyShopItem

    _ensure_catalog()
    return DailyShopItem.objects.count()


def _balance(user: User, currency: str) -> int:
    return user.diamonds if currency == "diamonds" else user.coins


def _daily_purchase_count(user: User, key: str) -> int:
    from bio_lab.models import DailyShopPurchase
    from game.daily import today_str

    row = DailyShopPurchase.objects.filter(user=user, key=key, day=today_str()).first()
    return row.count if row else 0


def remaining_for(user: User, offer: dict) -> int | None:
    """How many of this offer the player may still buy TODAY, or None when the offer
    is unlimited (no per-day limit). Drives the «N عدد مانده» line in the shop."""
    limit = int(offer.get("limit", 0) or 0)
    if limit <= 0:
        return None
    return max(0, limit - _daily_purchase_count(user, offer["key"]))


def offers_with_remaining(user: User) -> list[dict]:
    """Today's offers, each annotated with `remaining` (per-user, per-day)."""
    offers = today_offers()
    for o in offers:
        o["remaining"] = remaining_for(user, o)
    return offers


def buy(user: User, key: str, shown_price: int | None = None, shown_currency: str | None = None) -> dict:
    """Buy an offer that's in TODAY's shop. Repeatable unless the offer carries a
    per-day purchase limit (owner-set: 1 / 2 / unlimited).

    `shown_price`/`shown_currency` are what the player was actually shown (remembered
    server-side at render time). The charge is clamped so the player is NEVER billed
    more than the price they saw — the fix for "خریدم و پول بیشتری کم شد" reports, which
    happened when the offer's price/rotation changed between viewing and buying (featured
    discount, midnight rotation, an owner edit, or a deploy). The GOODS always come from
    the offer's stable key→contents mapping, so what they get matches the offer title."""
    from django.db import transaction

    offers = {o["key"]: o for o in today_offers()}
    offer = offers.get(key)
    if offer is None:
        raise GameError("این آفر دیگه توی شاپ امروز نیست — دوباره شاپ رو باز کن.")
    limit = int(offer.get("limit", 0) or 0)
    currency = offer["currency"]
    price = offer["price"]
    # never charge more than what was displayed to the player
    if shown_price is not None and shown_currency == currency:
        price = min(price, max(0, int(shown_price)))

    with transaction.atomic():
        user = User.objects.select_for_update().get(id=user.id)
        if limit > 0:
            from bio_lab.models import DailyShopPurchase
            from game.daily import today_str

            purchase, _ = DailyShopPurchase.objects.select_for_update().get_or_create(
                user=user, key=key, day=today_str()
            )
            if purchase.count >= limit:
                raise GameError(
                    f"این آفر محدوده — امروز فقط {limit} بار می‌شه خریدش و سقفت رو زدی. فردا دوباره سر بزن."
                )
        if _balance(user, currency) < price:
            unit = "الماس" if currency == "diamonds" else "طلا"
            raise GameError(f"{unit} کافی نداری! این آفر {price} {unit} می‌خواد.")

        if currency == "diamonds":
            user.diamonds -= price
        else:
            user.coins -= price
        user.save(update_fields=["diamonds", "coins"])

        # grant the offer's contents (coins/dna/diamonds/energy/speedup/creature/
        # equipment) through the shared itemshop machinery — the same one the special
        # items and the custom daily-shop offers use.
        from game import itemshop

        notes = itemshop.grant_contents(user, offer.get("contents", []))

        if limit > 0:
            purchase.count += 1
            purchase.save(update_fields=["count"])
    return {**offer, "notes": notes}


# ── Always-on gold exchange (diamonds → gold) ────────────────────────────────
# Unlike the rotating daily offers, these are ALWAYS available, so any "not enough
# gold" dead-end can send the player straight here. Rates get slightly better in
# bulk. Kept deliberately un-cheap so gold stays meaningful.
GOLD_PACKS = [
    {"gold": 1_000, "diamonds": 8},
    {"gold": 3_000, "diamonds": 20},
    {"gold": 8_000, "diamonds": 45},
    {"gold": 20_000, "diamonds": 100},
    {"gold": 50_000, "diamonds": 220},
]


def buy_gold_pack(user: User, idx: int) -> dict:
    """Spend diamonds for a fixed gold pack. Always available (not day-gated)."""
    if idx < 0 or idx >= len(GOLD_PACKS):
        raise GameError("این بسته‌ی طلا وجود نداره.")
    pack = GOLD_PACKS[idx]
    if user.diamonds < pack["diamonds"]:
        raise GameError(
            f"الماس کافی نداری! این بسته {pack['diamonds']} الماس می‌خواد "
            f"(الان {user.diamonds} داری)."
        )
    user.diamonds -= pack["diamonds"]
    user.coins += pack["gold"]
    user.save(update_fields=["diamonds", "coins"])
    from game.ledger import record_gain

    record_gain(user, "shop", coins=pack["gold"])
    return pack


def offer_reward_text(offer: dict) -> str:
    """A short summary of what an offer grants — from the granted notes when available
    (after a buy), else from the offer's contents."""
    notes = offer.get("notes")
    if notes:
        return " + ".join(notes)
    from game import itemshop

    return itemshop.content_summary(offer.get("contents", []))

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

# The canonical offer definitions: title / emoji / grant, plus the DEFAULT price and
# currency. What each offer GRANTS is fixed in code (so the admin panel can't create a
# broken grant); the owner tunes only price / currency / on-off, stored per-key in the
# DailyShopOffer table (see _effective_pool below).
POOL_DEFAULTS = [
    {"key": "speedup30", "emoji": "⏱", "title": "کارت سرعت ۳۰ دقیقه", "cost": 800, "currency": "coins", "grant": {"speedup": 30}},
    {"key": "speedup60", "emoji": "⏱", "title": "کارت سرعت ۱ ساعت", "cost": 1500, "currency": "coins", "grant": {"speedup": 60}},
    {"key": "speedup720", "emoji": "⏱", "title": "کارت سرعت ۱۲ ساعت", "cost": 30, "currency": "diamonds", "grant": {"speedup": 720}},
    {"key": "dna50", "emoji": "🧬", "title": "بسته‌ی ۵۰ DNA", "cost": 15, "currency": "diamonds", "grant": {"dna": 50}},
    {"key": "dna150", "emoji": "🧬", "title": "بسته‌ی ۱۵۰ DNA", "cost": 40, "currency": "diamonds", "grant": {"dna": 150}},
    {"key": "gold3000", "emoji": "💰", "title": "بسته‌ی ۳۰۰۰ طلا", "cost": 20, "currency": "diamonds", "grant": {"coins": 3000}},
    {"key": "energy", "emoji": "⚡", "title": "شارژ کامل انرژی", "cost": 10, "currency": "diamonds", "grant": {"energy": "full"}},
]
POOL_DEFAULTS_BY_KEY = {o["key"]: o for o in POOL_DEFAULTS}
# back-compat alias for any old import site
POOL = POOL_DEFAULTS
POOL_BY_KEY = POOL_DEFAULTS_BY_KEY

_VALID_CURRENCIES = ("coins", "diamonds")


def _effective_pool(include_inactive: bool = False) -> list[dict]:
    """Every offer merged with its owner override (price / currency / on-off) from the
    DailyShopOffer table. Sync-only (the shop panel/buy run in run_db). Missing rows
    are seeded from the code defaults so the admin panel always shows the full list."""
    from bio_lab.models import DailyShopOffer

    rows = {r.key: r for r in DailyShopOffer.objects.all()}
    out = []
    for base in POOL_DEFAULTS:
        row = rows.get(base["key"])
        if row is None:
            # seed a row from the default the first time we see this key
            row = DailyShopOffer.objects.create(
                key=base["key"], cost=base["cost"], currency=base["currency"], is_active=True
            )
        currency = row.currency if row.currency in _VALID_CURRENCIES else base["currency"]
        merged = {**base, "cost": max(0, row.cost), "currency": currency, "is_active": row.is_active}
        if include_inactive or row.is_active:
            out.append(merged)
    return out


def _day() -> int:
    return timezone.localtime(timezone.now()).timetuple().tm_yday


def _price(offer: dict, featured: bool) -> int:
    if featured:
        return max(1, round(offer["cost"] * (1 - FEATURED_DISCOUNT)))
    return offer["cost"]


def today_offers() -> list[dict]:
    """The rotating selection for today; the first entry is the featured deal. Rotates
    over only the ACTIVE offers, so an owner-disabled offer never shows or sells."""
    pool = _effective_pool()
    if not pool:
        return []
    day = _day()
    n = len(pool)
    count = min(OFFERS_PER_DAY, n)
    picks = [pool[(day + i) % n] for i in range(count)]
    out = []
    for i, o in enumerate(picks):
        featured = i == 0
        out.append({**o, "featured": featured, "price": _price(o, featured)})
    return out


# ── owner admin: tune daily-shop pricing ─────────────────────────────────────
def admin_offer_list() -> list[dict]:
    """Every offer (active or not) with its current price/currency, for the panel."""
    return _effective_pool(include_inactive=True)


def set_offer_price(key: str, cost: int, currency: str | None = None) -> None:
    from bio_lab.models import DailyShopOffer

    base = POOL_DEFAULTS_BY_KEY.get(key)
    if base is None:
        raise GameError("این آفر وجود نداره.")
    row, _ = DailyShopOffer.objects.get_or_create(
        key=key, defaults={"cost": base["cost"], "currency": base["currency"], "is_active": True}
    )
    row.cost = max(0, int(cost))
    fields = ["cost"]
    if currency in _VALID_CURRENCIES:
        row.currency = currency
        fields.append("currency")
    row.save(update_fields=fields)


def toggle_offer(key: str) -> bool:
    from bio_lab.models import DailyShopOffer

    base = POOL_DEFAULTS_BY_KEY.get(key)
    if base is None:
        raise GameError("این آفر وجود نداره.")
    row, _ = DailyShopOffer.objects.get_or_create(
        key=key, defaults={"cost": base["cost"], "currency": base["currency"], "is_active": True}
    )
    row.is_active = not row.is_active
    row.save(update_fields=["is_active"])
    return row.is_active


def _balance(user: User, currency: str) -> int:
    return user.diamonds if currency == "diamonds" else user.coins


def buy(user: User, key: str) -> dict:
    """Buy an offer that's in TODAY's shop. Repeatable."""
    offers = {o["key"]: o for o in today_offers()}
    offer = offers.get(key)
    if offer is None:
        raise GameError("این آفر امروز توی شاپ نیست.")
    price = offer["price"]
    currency = offer["currency"]
    if _balance(user, currency) < price:
        unit = "الماس" if currency == "diamonds" else "طلا"
        raise GameError(f"{unit} کافی نداری! این آفر {price} {unit} می‌خواد.")

    if currency == "diamonds":
        user.diamonds -= price
    else:
        user.coins -= price
    fields = ["diamonds", "coins"]

    grant = offer["grant"]
    if grant.get("coins"):
        user.coins += grant["coins"]
    if grant.get("dna"):
        user.dna_fragments += grant["dna"]; fields.append("dna_fragments")
    if grant.get("energy") == "full":
        user.energy = constants.MAX_ENERGY
        user.energy_updated_at = timezone.now()
        fields += ["energy", "energy_updated_at"]
    user.save(update_fields=list(set(fields)))

    if grant.get("speedup"):
        from game.buildings import grant_speedup_card

        grant_speedup_card(user, grant["speedup"], count=1)
    return offer


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
    return pack


def offer_reward_text(offer: dict) -> str:
    g = offer["grant"]
    if g.get("energy") == "full":
        return "انرژی کامل"
    if g.get("speedup"):
        return f"کارت سرعت {g['speedup']}د"
    if g.get("dna"):
        return f"{g['dna']} DNA"
    if g.get("coins"):
        return f"{g['coins']} طلا"
    return "—"

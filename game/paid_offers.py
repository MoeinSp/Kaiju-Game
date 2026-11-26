"""Rotating real-money deals for the buy store — daily + weekly.

Catalog rows are ordinary ``ShopItem``s with ``offer_cadence`` set and a Toman price.
Each Tehran day / ISO week a deterministic subset is shown (so every player sees the
same deals). Per-period purchase caps use ``PaidOfferClaim``, separate from the
lifetime ``ShopItemPurchase`` counter.
"""

from __future__ import annotations

import json
import random
from datetime import datetime, time, timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from bio_lab.models import PaidOfferClaim, PurchaseRequest, ShopItem, User
from game.creature import GameError

CADENCE_DAILY = "daily"
CADENCE_WEEKLY = "weekly"
CADENCES = (CADENCE_DAILY, CADENCE_WEEKLY)

DAILY_SLOTS = 2
WEEKLY_SLOTS = 1

CADENCE_LABEL = {CADENCE_DAILY: "افر روزانه", CADENCE_WEEKLY: "افر هفتگی"}
CADENCE_EMOJI = {CADENCE_DAILY: "🔥", CADENCE_WEEKLY: "📅"}


def period_key(cadence: str) -> str:
    from game.daily import today_str, week_key

    if cadence == CADENCE_DAILY:
        return f"d:{today_str()}"
    if cadence == CADENCE_WEEKLY:
        return f"w:{week_key()}"
    raise ValueError(cadence)


def seconds_left(cadence: str) -> int:
    """Seconds until the current Tehran day / week rolls over."""
    now = timezone.localtime()
    if cadence == CADENCE_DAILY:
        end = timezone.make_aware(datetime.combine(now.date() + timedelta(days=1), time.min))
    else:
        # ISO week ends Monday 00:00 Tehran (week_key uses ISO weeks)
        days_to_monday = (7 - now.weekday()) % 7
        if days_to_monday == 0:
            days_to_monday = 7
        end = timezone.make_aware(datetime.combine(now.date() + timedelta(days=days_to_monday), time.min))
    return max(0, int((end - now).total_seconds()))


def _pool(cadence: str) -> list[ShopItem]:
    return list(
        ShopItem.objects.filter(
            is_active=True, offer_cadence=cadence, price_toman__gt=0,
        ).order_by("sort_order", "id")
    )


def live_items(cadence: str) -> list[ShopItem]:
    """The deals currently on sale for this cadence (deterministic for the period)."""
    pool = _pool(cadence)
    if not pool:
        return []
    n = DAILY_SLOTS if cadence == CADENCE_DAILY else WEEKLY_SLOTS
    if len(pool) <= n:
        return pool
    rng = random.Random(period_key(cadence))
    return sorted(rng.sample(pool, n), key=lambda it: (it.sort_order, it.id))


def any_live() -> bool:
    return bool(live_items(CADENCE_DAILY) or live_items(CADENCE_WEEKLY))


def _was_price(price: int, discount: int) -> int:
    d = max(0, min(95, int(discount or 0)))
    if d <= 0 or price <= 0:
        return int(price)
    return int(round(price / (1 - d / 100)))


def _bought_this_period(user: User, item: ShopItem) -> int:
    row = PaidOfferClaim.objects.filter(
        user=user, item=item, period_key=period_key(item.offer_cadence),
    ).first()
    return int(row.count) if row else 0


def _waiting(user: User, item: ShopItem) -> int:
    return PurchaseRequest.objects.filter(
        user=user, shop_item=item, status__in=("awaiting_receipt", "pending"),
    ).count()


def _deal_dict(user: User, item: ShopItem) -> dict:
    from game import itemshop

    try:
        contents = json.loads(item.contents_json)
    except (TypeError, ValueError):
        contents = []
    limit = int(item.offer_period_limit or 1)
    bought = _bought_this_period(user, item)
    waiting = _waiting(user, item)
    left = max(0, limit - bought - waiting) if limit else None
    from game import globalsale

    # list_for_user warms the cache once; per-deal view then reads it.
    v = globalsale.view(int(item.price_toman), int(item.offer_discount or 0), sale=globalsale.active())
    return {
        "id": item.id,
        "title": item.title,
        "emoji": item.emoji or "🎁",
        "cadence": item.offer_cadence,
        "label": CADENCE_LABEL.get(item.offer_cadence, "افر"),
        "gets": itemshop.content_summary(contents),
        "contents": contents,
        "price": v["price"],
        "discount": v["discount"],
        "original": v["original"],
        "sale_percent": v["sale_percent"],
        "sale_reason": v["sale_reason"],
        "list_price": v["list_price"],
        "ends_in": seconds_left(item.offer_cadence),
        "limit": limit,
        "bought": bought,
        "left": left,
        "art": f"img/{item.offer_art or 'hub_shop'}.jpg",
        "sold_out": left == 0,
    }


def list_for_user(user: User) -> dict:
    """``{"daily": [...], "weekly": [...]}`` for the buy store."""
    from game import globalsale

    globalsale.active_sync()
    return {
        CADENCE_DAILY: [_deal_dict(user, it) for it in live_items(CADENCE_DAILY)],
        CADENCE_WEEKLY: [_deal_dict(user, it) for it in live_items(CADENCE_WEEKLY)],
    }


def assert_buyable(user: User, item: ShopItem) -> None:
    """Raise if this cadence deal isn't live right now or the period cap is hit."""
    cadence = (item.offer_cadence or "").strip()
    if not cadence:
        return
    if cadence not in CADENCES:
        raise GameError("این افر نامعتبره.")
    if item.id not in {it.id for it in live_items(cadence)}:
        raise GameError("این افر الان فعال نیست — فردا/هفته‌ی بعد برگرد.")
    limit = int(item.offer_period_limit or 1)
    if not limit:
        return
    bought = _bought_this_period(user, item)
    waiting = _waiting(user, item)
    if bought + waiting >= limit:
        unit = "امروز" if cadence == CADENCE_DAILY else "این هفته"
        raise GameError(
            f"سقف خرید {unit} این افر پر شده"
            + (" (یکی هم در انتظار تأییده)." if waiting else ".")
        )


@transaction.atomic
def record_purchase(user: User, item: ShopItem) -> None:
    """Bump the period claim after a receipt is approved."""
    cadence = (item.offer_cadence or "").strip()
    if cadence not in CADENCES:
        return
    pk = period_key(cadence)
    row, _ = PaidOfferClaim.objects.select_for_update().get_or_create(
        user=user, item=item, period_key=pk, defaults={"count": 0},
    )
    row.count += 1
    row.save(update_fields=["count"])


def seed_demo_catalog() -> list[ShopItem]:
    """Upsert a small demo catalog for local / first-run testing. Idempotent by title."""
    specs = [
        {
            "title": "افر هفتگی افسانه‌ای",
            "emoji": "🐉",
            "cadence": CADENCE_WEEKLY,
            "discount": 50,
            "price": 500_000,
            "art": "hub_chests",
            "sort": 0,
            "contents": [
                {"type": "coins", "amount": 2_000_000},
                {"type": "chest", "tier": "mega", "count": 2},
                {"type": "shadow_cores", "amount": 10},
                {"type": "dna", "amount": 100_000},
                {"type": "creature_pick", "rarity": "mythic", "count": 2},
            ],
        },
        {
            "title": "افر روزانه طلایی",
            "emoji": "🪙",
            "cadence": CADENCE_DAILY,
            "discount": 40,
            "price": 79_000,
            "art": "gold_5",
            "sort": 1,
            "contents": [
                {"type": "coins", "amount": 500_000},
                {"type": "dna", "amount": 16_000},
                {"type": "diamonds", "amount": 80},
                {"type": "chest", "tier": "golden", "count": 1},
            ],
        },
        {
            "title": "افر روزانه سایه",
            "emoji": "🌑",
            "cadence": CADENCE_DAILY,
            "discount": 35,
            "price": 99_000,
            "art": "hub_dungeon",
            "sort": 2,
            "contents": [
                {"type": "shadow_cores", "amount": 5},
                {"type": "dungeon_keys", "amount": 10},
                {"type": "coins", "amount": 300_000},
                {"type": "creature_pick", "rarity": "legendary", "count": 1},
            ],
        },
        {
            "title": "افر روزانه انرژی",
            "emoji": "⚡",
            "cadence": CADENCE_DAILY,
            "discount": 30,
            "price": 49_000,
            "art": "hub_energy",
            "sort": 3,
            "contents": [
                {"type": "diamonds", "amount": 150},
                {"type": "speedup", "minutes": 60, "count": 3},
                {"type": "coins", "amount": 200_000},
            ],
        },
    ]
    out = []
    for i, s in enumerate(specs):
        item, _ = ShopItem.objects.update_or_create(
            title=s["title"],
            defaults={
                "emoji": s["emoji"],
                "price_coins": 0,
                "price_diamonds": 0,
                "price_toman": s["price"],
                "contents_json": json.dumps(s["contents"], ensure_ascii=False),
                "is_active": True,
                "is_offer": True,
                "offer_ends_at": None,
                "offer_discount": s["discount"],
                "offer_art": s["art"],
                "offer_cadence": s["cadence"],
                "offer_period_limit": 1,
                "max_per_user": 0,
                "sort_order": s["sort"],
            },
        )
        out.append(item)
    # deactivate any leftover demo titles we renamed away — nothing else
    return out


def double_cadence_gold_dna() -> int:
    """×2 every ``coins`` / ``dna`` amount on active daily+weekly paid deals.
    Idempotent only if run once — prefer ``seed_demo_catalog`` for the known titles."""
    n = 0
    for item in ShopItem.objects.filter(offer_cadence__in=CADENCES, is_active=True):
        try:
            contents = json.loads(item.contents_json or "[]")
        except json.JSONDecodeError:
            continue
        changed = False
        for part in contents:
            if part.get("type") in ("coins", "dna") and isinstance(part.get("amount"), (int, float)):
                part["amount"] = int(part["amount"]) * 2
                changed = True
        if changed:
            item.contents_json = json.dumps(contents, ensure_ascii=False)
            item.save(update_fields=["contents_json"])
            n += 1
    return n

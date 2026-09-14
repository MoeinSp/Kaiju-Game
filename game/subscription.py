"""VIP Subscriptions — 30-day Silver & Gold tiers with exclusive gameplay perks.

Silver (۲۵۰,۰۰۰ تومان):
- Special premium badge 🥈 next to name in reports, attacks, and groups
- Max energy capped at 100 (refills up to 100)
- Ability to queue a 2nd arena chest (like Clash Royale)
- +25% income from auto-hunt (automated only, manual unchanged)

Gold (۵۰۰,۰۰۰ تومان):
- All Silver perks + special premium badge 👑
- Concurrent cave breeding limit increased to 2
- +50% income from auto-hunt (automated only, manual unchanged)
"""

from __future__ import annotations

import datetime
from django.utils import timezone
from bio_lab.models import User

SUBSCRIPTION_TIERS = {
    "silver": {
        "key": "silver",
        "name": "اشتراک نقره‌ای",
        "badge": "🥈",
        "price_toman": 250_000,
        "energy_cap": 100,
        "cave_limit": 1,
        "auto_hunt_bonus": 0.25,
        "queue_chest": True,
        "duration_days": 30,
        "perks": [
            "🥈 نشان اختصاصی پرمیوم در کنار نام",
            "⚡ سقف انرژی ۱۰۰ (به جای ۵۰)",
            "📋 امکان در صف گذاشتن یک جعبه آرنا (مانند کلش رویال)",
            "🎯 افزایش ۲۵ درصدی درآمد شکار خودکار",
        ],
    },
    "gold": {
        "key": "gold",
        "name": "اشتراک طلایی",
        "badge": "👑",
        "price_toman": 500_000,
        "energy_cap": 100,
        "cave_limit": 2,
        "auto_hunt_bonus": 0.50,
        "queue_chest": True,
        "duration_days": 30,
        "perks": [
            "👑 نشان اختصاصی پرمیوم طلایی در کنار نام",
            "⚡ سقف انرژی ۱۰۰ (به جای ۵۰)",
            "🕳 افزایش ظرفیت همزمانی غار هیولا به ۲ جفت",
            "📋 امکان در صف گذاشتن یک جعبه آرنا (مانند کلش رویال)",
            "🎯 افزایش ۵۰ درصدی درآمد شکار خودکار",
        ],
    },
}


def is_subscription_active(user: User) -> bool:
    if not getattr(user, "subscription_tier", ""):
        return False
    until = getattr(user, "subscription_until", None)
    return bool(until and until > timezone.now())


def get_subscription_tier(user: User) -> str | None:
    if is_subscription_active(user):
        return user.subscription_tier
    return None


def subscription_badge(user: User) -> str:
    tier = get_subscription_tier(user)
    if tier == "gold":
        return " 👑"
    if tier == "silver":
        return " 🥈"
    return ""


def get_subscription_info(user: User) -> dict:
    active = is_subscription_active(user)
    tier = get_subscription_tier(user)
    cfg = SUBSCRIPTION_TIERS.get(tier) if tier else None
    until = getattr(user, "subscription_until", None)
    days_left = 0
    hours_left = 0
    if active and until:
        diff = until - timezone.now()
        days_left = diff.days
        hours_left = int(diff.seconds // 3600)

    return {
        "is_active": active,
        "tier": tier,
        "tier_name": cfg["name"] if cfg else "عادی",
        "badge": cfg["badge"] if cfg else "",
        "until": until,
        "days_left": days_left,
        "hours_left": hours_left,
        "energy_cap": cfg["energy_cap"] if cfg else 50,
        "cave_limit": cfg["cave_limit"] if cfg else 1,
        "auto_hunt_bonus": cfg["auto_hunt_bonus"] if cfg else 0.0,
        "can_queue_chest": cfg["queue_chest"] if cfg else False,
    }


def activate_subscription(user: User, tier: str, days: int = 30) -> User:
    if tier not in SUBSCRIPTION_TIERS:
        raise ValueError(f"Unknown subscription tier: {tier}")

    now = timezone.now()
    active = is_subscription_active(user)

    if active and user.subscription_until:
        # If already active, extend from current expiration
        user.subscription_until += datetime.timedelta(days=days)
        # If upgrading to gold, update tier
        if tier == "gold" or user.subscription_tier != "gold":
            user.subscription_tier = tier
    else:
        user.subscription_tier = tier
        user.subscription_until = now + datetime.timedelta(days=days)

    # Bump energy if needed
    if user.energy < 100:
        user.energy = max(user.energy, 100)

    user.save(update_fields=["subscription_tier", "subscription_until", "energy"])
    return user


def extend_subscription(user: User, days: int) -> User:
    """Admin helper to extend a subscription, or activate silver if currently inactive."""
    if not is_subscription_active(user):
        return activate_subscription(user, "silver", days=days)
    user.subscription_until += datetime.timedelta(days=days)
    user.save(update_fields=["subscription_until"])
    return user


def cancel_subscription(user: User) -> User:
    """Admin helper to immediately cancel/remove a user's subscription."""
    user.subscription_tier = ""
    user.subscription_until = None
    user.save(update_fields=["subscription_tier", "subscription_until"])
    return user

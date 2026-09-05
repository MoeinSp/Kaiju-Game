"""Player-chosen creature nicknames (نام‌گذاری کایجو).

A creature's `name` field is its species/breed (نژاد) and is never player-set — the
whole game (fusion pairing, cave/breeding, codex) keys off it. This module manages a
SEPARATE, purely cosmetic `custom_name` (نام) the player may set from the collection
or the upgrade panel.

Pricing: the first rename is FREE, the second costs 100 diamonds, and every rename
after that costs 100 more than the last (100, 200, 300, …). `name_changes` on the
creature records how many renames have happened, so the next price is simply
`name_changes × RENAME_STEP` (0 the first time).
"""

from __future__ import annotations

from django.db import transaction

from bio_lab.models import Creature, User
from game.creature import GameError

RENAME_STEP = 100  # diamonds added per rename after the free first one
NAME_MAX_LEN = 24
# characters we refuse: HTML metacharacters (so the name is always HTML-safe to render)
# and control/formatting whitespace beyond a plain space.
_FORBIDDEN = set("<>&\n\r\t")


def rename_cost(creature: Creature) -> int:
    """Diamonds the NEXT rename of this creature will cost: 0 for the first, then
    100, 200, 300, … (RENAME_STEP × how many renames already happened)."""
    return max(0, int(getattr(creature, "name_changes", 0) or 0)) * RENAME_STEP


def validate_name(raw: str) -> str:
    """Clean and validate a proposed nickname; returns the trimmed name or raises
    GameError. Keeps names short, single-line, and free of HTML metacharacters so
    they're safe to interpolate into every card without escaping."""
    name = (raw or "").strip()
    if not name:
        raise GameError("یه اسم بفرست (خالی نباشه).")
    if len(name) > NAME_MAX_LEN:
        raise GameError(f"اسم باید حداکثر {NAME_MAX_LEN} حرف باشه — یه اسم کوتاه‌تر بفرست.")
    if any(ch in _FORBIDDEN for ch in name):
        raise GameError("اسم نباید شامل کاراکترهای «< > &» یا خط جدید باشه.")
    return name


def rename_creature(user: User, creature_id: int, raw_name: str) -> dict:
    """Set (or change) a creature's nickname, charging the rising diamond price. The
    first rename of a given creature is free; each later one costs RENAME_STEP more.
    Returns {creature, name, cost, next_cost}. Raises GameError on bad name / funds /
    ownership."""
    name = validate_name(raw_name)
    with transaction.atomic():
        user = User.objects.select_for_update().get(id=user.id)
        creature = Creature.objects.select_for_update().filter(id=creature_id, owner=user).first()
        if creature is None:
            raise GameError("همچین کایجویی توی کلکسیونت نیست.")
        cost = rename_cost(creature)
        if cost > 0 and user.diamonds < cost:
            raise GameError(
                f"برای این نام‌گذاری {cost} 💎 الماس لازمه ولی فقط {user.diamonds} تا داری."
            )
        if cost > 0:
            user.diamonds -= cost
            user.save(update_fields=["diamonds"])
        creature.custom_name = name
        creature.name_changes = int(getattr(creature, "name_changes", 0) or 0) + 1
        creature.save(update_fields=["custom_name", "name_changes"])
    return {
        "creature": creature,
        "name": name,
        "breed": creature.name,
        "cost": cost,
        "next_cost": rename_cost(creature),
    }

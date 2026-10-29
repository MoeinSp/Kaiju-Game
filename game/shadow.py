"""«فرم سیاه» — the dungeon's endgame for a kaiju already at the power cap, and the dungeon badges.

A 5★ mythic whose raw power has reached MAX_KAIJU_POWER can be raised through SHADOW_MAX_LEVEL
levels with «هسته‌ی سایه» (hard / nightmare dungeon bosses, or the dungeon shop) and gold. Each
level adds constants.SHADOW_POWER_PER_LEVEL power and lifts that creature's cap by the same
amount (constants.power_cap / shadow_bonus, applied in creature_power and in combat).

The look lives elsewhere: the bot card art (game.media, a dark aura that thickens per level),
the name mark (bio_lab.repository.creature_name) and the Mini App's animated aura.

Badges (User.dungeon_badge) sit next to the lab name everywhere lab_display / lab_mention is
used. A player shows the highest one earned.
"""

from __future__ import annotations

from django.db import transaction

from bio_lab.models import Creature, DungeonProfile, GroupMembership, User
from bio_lab.repository import lock_row
from game import constants
from game.creature import GameError, raw_power

# cores and gold to go from level L to L+1 (index L)
CORE_COST = (8, 12, 16, 22, 30)   # 88 in all (was 68): ~3-4 weeks for a first black form, simulated
GOLD_COST = (200_000, 400_000, 700_000, 1_000_000, 1_500_000)
LEVEL_NAMES = ("", "سایه ۱", "سایه ۲", "سایه ۳", "سایه ۴", "سایه ۵")

# badge key → (emoji, title); ordered low → high
BADGES = {
    "explorer": ("🕳", "کاوشگر سیاه‌چال"),
    "hard": ("⚔️", "فاتح سخت"),
    "nightmare": ("💀", "فاتح کابوس"),
    "shadow_lord": ("🌑", "ارباب سایه"),
}
_BADGE_RANK = {k: i for i, k in enumerate(BADGES)}


def badge_mark(user) -> str:
    """' 🌑' — the badge after a lab name, or ''. Reads only the user row (no query)."""
    key = getattr(user, "dungeon_badge", "") or ""
    return f" {BADGES[key][0]}" if key in BADGES else ""


def award_badge(user: User, key: str) -> bool:
    """Raise the player's badge to `key` if it's higher than the one they have. True if it changed."""
    current = user.dungeon_badge or ""
    if _BADGE_RANK.get(key, -1) <= _BADGE_RANK.get(current, -1):
        return False
    User.objects.filter(id=user.id).update(dungeon_badge=key)
    user.dungeon_badge = key
    return True


def eligible(user: User, creature: Creature) -> tuple[bool, str]:
    """Can this creature take (another) black-form level? (ok, reason-if-not)"""
    from game import research
    from game.equipment import get_equipped_items

    if creature.shadow_level >= constants.SHADOW_MAX_LEVEL:
        return False, "به آخرین سطح فرم سیاه رسیده."
    if creature.rarity != "mythic" or creature.star_level < constants.STAR_MAX:
        return False, "فقط هیولای اساطیری ۵ ستاره فرم سیاه می‌گیره."
    research.attach_research(user, creature)
    if raw_power(creature, get_equipped_items(creature)) < constants.MAX_KAIJU_POWER:
        return False, f"اول باید قدرتش به سقف ({constants.MAX_KAIJU_POWER:,}) برسه."
    return True, ""


def cost(level: int) -> dict:
    """What the NEXT level costs from `level`."""
    return {"cores": CORE_COST[level], "coins": GOLD_COST[level]}


def candidates(user: User) -> list[dict]:
    """The player's 5★ mythics with their black-form state — for the «فرم سیاه» screen."""
    from game.creature import creature_power
    from game.equipment import equipped_items_map
    from game import research

    rows = list(Creature.objects.filter(owner=user, rarity="mythic", star_level__gte=constants.STAR_MAX))
    research.attach_research(user, rows)
    gear = equipped_items_map(rows)
    out = []
    for c in rows:
        ok, why = eligible(user, c)
        nxt = cost(c.shadow_level) if c.shadow_level < constants.SHADOW_MAX_LEVEL else None
        out.append({"creature": c, "power": creature_power(c, gear[c.id]), "level": c.shadow_level,
                    "ok": ok, "why": why, "next": nxt})
    out.sort(key=lambda r: (-r["level"], not r["ok"], -r["power"]))
    return out


@transaction.atomic
def ascend(user: User, creature_id: int) -> dict:
    """One black-form level up. Returns {creature, level, lord(bool: just reached the top)}."""
    lock_row(user)
    creature = Creature.objects.select_for_update().filter(id=creature_id, owner=user).first()
    if creature is None:
        raise GameError("این هیولا مال تو نیست.")
    ok, why = eligible(user, creature)
    if not ok:
        raise GameError(why)
    need = cost(creature.shadow_level)
    prof = DungeonProfile.objects.select_for_update().filter(user=user).first()
    have = prof.shadow_cores if prof else 0
    if have < need["cores"]:
        raise GameError(f"هسته‌ی سایه کافی نداری ({need['cores']} لازمه، {have} داری).")
    if user.coins < need["coins"]:
        raise GameError(f"طلا کافی نداری ({need['coins']:,} لازمه، {user.coins:,} داری).")
    prof.shadow_cores -= need["cores"]
    prof.save(update_fields=["shadow_cores"])
    user.coins -= need["coins"]
    user.save(update_fields=["coins"])
    creature.shadow_level += 1
    creature.save(update_fields=["shadow_level"])
    lord = creature.shadow_level >= constants.SHADOW_MAX_LEVEL
    if lord:
        award_badge(user, "shadow_lord")
        _announce_lord(user, creature)
    return {"creature": creature, "level": creature.shadow_level, "lord": lord}


def _announce_lord(user: User, creature: Creature) -> None:
    """«ارباب سایه»: tell the groups this player plays in (through the bot's outbox, so it works
    from the Mini App too)."""
    import html

    from bio_lab.repository import lab_mention
    from game import outbox

    text = (
        "🌑 <b>یه ارباب سایه متولد شد!</b>\n━━━━━━━━━━━━━━━━━━━━\n"
        f"{lab_mention(user)} هیولای <b>{html.escape(creature.name)}</b> رو به "
        f"<b>فرم سیاه {constants.SHADOW_MAX_LEVEL}</b> رسوند — قدرتی فراتر از سقف بازی 👑\n"
        "<i>سیاه‌چال منتظر شجاع‌های بعدیه…</i>"
    )
    groups = (GroupMembership.objects.filter(user=user, left_at__isnull=True)
              .order_by("-id").values_list("group_id", flat=True)[:20])
    for gid in groups:
        outbox.push("group_post", {"chat_id": gid, "text": text})

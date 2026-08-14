"""Lab level — the player's overall progress number.

Creatures have their own levels and stars, but until now nothing summarised *the
player*. Two people could both own a level-20 creature while one had ground out
a thousand raids and the other had opened one lucky crate, and the game had no
way to say so. Lab level is that summary: XP accrues from every meaningful
action, and the level is a pure function of the total.

**Deliberately grants no mechanical bonus.** It's tempting to hang gold or energy
multipliers off it, but the build-time and loot economy is tuned to specific
per-day income figures (see game/constants.py's building tables), and a
compounding multiplier on top of those would quietly invalidate that tuning.
Lab level is progression *feedback* and the sort key for the leaderboard — no
more. If it ever gains a bonus, the economy needs re-tuning in the same change.

The curve is quadratic: `xp_for_level(L) = LAB_XP_COEFFICIENT * (L - 1)²`. Early
levels come fast enough to feel like progress in the first session, and the
distance between levels widens without ever hard-stopping. An active player
tracks roughly level 20 by the time they've maxed their buildings, which is the
same 1–2 week horizon the build times target.
"""

from __future__ import annotations

import math

from bio_lab.models import User

LAB_XP_COEFFICIENT = 25
LAB_MAX_LEVEL = 50

# What each action is worth. Ratios matter more than absolute values: a raid is
# worth about twice a hunt because it costs the same energy but can fail, and a
# finished building upgrade is worth a lot because it represents hours of real
# time rather than one tap.
LAB_XP_AWARDS = {
    "hunt_win": 6,
    "hunt_loss": 2,
    "arena_win": 12,
    "arena_loss": 4,
    "duel_win": 15,
    "fusion": 40,
    "breeding": 35,
    "mission": 30,
    "box": 10,
}
# building upgrades pay per level reached, so the level-5 grind is worth the wait
LAB_XP_PER_BUILDING_LEVEL = 40


def xp_for_level(level: int) -> int:
    """Total XP needed to *be* this level."""
    return LAB_XP_COEFFICIENT * max(0, level - 1) ** 2


def level_for_xp(xp: int) -> int:
    """Inverse of xp_for_level, clamped to LAB_MAX_LEVEL."""
    if xp <= 0:
        return 1
    level = int(math.isqrt(int(xp) // LAB_XP_COEFFICIENT)) + 1
    return min(level, LAB_MAX_LEVEL)


def lab_level(user: User) -> int:
    return level_for_xp(user.lab_xp)


def lab_progress(user: User) -> dict:
    """Everything a progress bar needs. `into`/`span` are the XP earned and
    needed *within* the current level, so callers never re-derive the curve."""
    level = lab_level(user)
    if level >= LAB_MAX_LEVEL:
        return {
            "level": level,
            "xp": user.lab_xp,
            "into": 0,
            "span": 0,
            "next_at": None,
            "ratio": 1.0,
            "is_max": True,
        }
    floor_xp = xp_for_level(level)
    next_xp = xp_for_level(level + 1)
    span = next_xp - floor_xp
    into = user.lab_xp - floor_xp
    return {
        "level": level,
        "xp": user.lab_xp,
        "into": into,
        "span": span,
        "next_at": next_xp,
        "ratio": (into / span) if span else 1.0,
        "is_max": False,
    }


def add_lab_xp(user: User, amount: int) -> dict | None:
    """Credit XP and report a level-up, or None when the level didn't change.

    Returns {"from": int, "to": int} so callers can congratulate the player
    without having to remember the level themselves before the call.
    """
    amount = int(amount)
    if amount <= 0:
        return None
    before = lab_level(user)
    user.lab_xp = max(0, user.lab_xp + amount)
    user.save(update_fields=["lab_xp"])
    after = lab_level(user)
    return {"from": before, "to": after} if after > before else None


def award(user: User, key: str) -> dict | None:
    """Credit the XP for a named action from LAB_XP_AWARDS.

    Unknown keys are ignored rather than raising: a typo in a call site should
    cost a player some XP, not crash the action they were performing."""
    return add_lab_xp(user, LAB_XP_AWARDS.get(key, 0))


def award_building_level(user: User, target_level: int) -> dict | None:
    return add_lab_xp(user, LAB_XP_PER_BUILDING_LEVEL * max(1, target_level))


def lab_bar(user: User, width: int = 10) -> str:
    """Textual progress bar for the lab level, for message bodies."""
    progress = lab_progress(user)
    if progress["is_max"]:
        return "█" * width
    filled = max(0, min(width, round(progress["ratio"] * width)))
    return "█" * filled + "░" * (width - filled)

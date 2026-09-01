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

The curve is **super-quadratic**: `xp_for_level(L) = COEFFICIENT * (L - 1) ^ EXPONENT`
with the exponent above 2. A plain square made the late levels too easy to
inflate — the gap from 40 to 41 was only about 1.7x the gap from 10 to 11, so a
player who kept playing at the same rate climbed at nearly a constant speed and
a high number stopped meaning much. At 2.6 the gap widens roughly 8x over the
same span, so every tier of the ladder costs visibly more than the one below it.

Early levels are deliberately still cheap: the first few come inside one session
so a new player sees the number move, and only then does it start to bite.
"""

from __future__ import annotations

import math

from bio_lab.models import User

# Was 18; cut 30% (→ 12.6) so lab levels cost 30% less XP and progression is easier.
# Existing players had their stored lab_xp scaled ×0.7 in the same change (migration
# 0057), so both the curve and the stored XP moved together and nobody's level changed —
# only the absolute XP numbers dropped, and future levels come 30% sooner.
LAB_XP_COEFFICIENT = 12.6
LAB_XP_EXPONENT = 2.6
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
    "campaign_win": 14,
}
# building upgrades pay per level reached, so the level-5 grind is worth the wait
# A finished building upgrade takes hours of real time, so it's the single biggest
# lab-XP source — bumped so raising buildings visibly moves the lab level (was 40).
LAB_XP_PER_BUILDING_LEVEL = 120


def xp_for_level(level: int) -> int:
    """Total XP needed to *be* this level."""
    return round(LAB_XP_COEFFICIENT * max(0, level - 1) ** LAB_XP_EXPONENT)


def level_for_xp(xp: int) -> int:
    """Inverse of xp_for_level, clamped to LAB_MAX_LEVEL.

    Computed by inverting the power, then corrected by comparing against
    xp_for_level: the float root can land a hair either side of a boundary once
    the numbers get large, and a level that flickers at the threshold would make
    the progress bar jump backwards.
    """
    if xp <= 0:
        return 1
    level = int((int(xp) / LAB_XP_COEFFICIENT) ** (1 / LAB_XP_EXPONENT)) + 1
    while level > 1 and xp_for_level(level) > xp:
        level -= 1
    while level < LAB_MAX_LEVEL and xp_for_level(level + 1) <= xp:
        level += 1
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
    # limited-time event may multiply XP (e.g. "Double-XP week") — single hook so
    # every XP source is covered. Guarded so an event bug can't break progression.
    try:
        from game import events

        amount = int(amount * events.xp_multiplier())
    except Exception:  # pragma: no cover
        pass
    # alliance XP perk — a member of an alliance that invested treasury in the perk
    # earns more XP from everything.
    try:
        from game import alliance

        amount = int(amount * alliance.xp_perk_multiplier(user))
    except Exception:  # pragma: no cover
        pass
    before = lab_level(user)
    user.lab_xp = max(0, user.lab_xp + amount)
    user.save(update_fields=["lab_xp"])
    after = lab_level(user)
    return {"from": before, "to": after} if after > before else None


def _award_pass_points(user: User, points: int) -> None:
    """Mirror an XP award into Battle Pass points. Lazy import + swallow errors so
    a pass problem can never break the underlying game action."""
    try:
        from game import battlepass

        battlepass.award(user, points)
    except Exception:  # pragma: no cover - defensive; pass points are non-critical
        pass


def award(user: User, key: str) -> dict | None:
    """Credit the XP for a named action from LAB_XP_AWARDS.

    Unknown keys are ignored rather than raising: a typo in a call site should
    cost a player some XP, not crash the action they were performing. The same
    amount feeds the Battle Pass, so every rewarding action also moves the pass."""
    amount = LAB_XP_AWARDS.get(key, 0)
    _award_pass_points(user, amount)
    # the same activity feeds the alliance's weekly war score
    try:
        from game import alliance

        alliance.add_war_points(user, amount)
    except Exception:  # pragma: no cover
        pass
    return add_lab_xp(user, amount)


def award_building_level(user: User, target_level: int) -> dict | None:
    amount = LAB_XP_PER_BUILDING_LEVEL * max(1, target_level)
    _award_pass_points(user, amount)
    return add_lab_xp(user, amount)


def lab_bar(user: User, width: int = 10) -> str:
    """Textual progress bar for the lab level, for message bodies."""
    progress = lab_progress(user)
    if progress["is_max"]:
        return "█" * width
    filled = max(0, min(width, round(progress["ratio"] * width)))
    return "█" * filled + "░" * (width - filled)

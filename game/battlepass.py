"""Battle Pass (پاس فصلی) — a monthly reward track that turns daily play into a
visible, claimable ladder.

Why it retains: every session moves a bar toward the next tier, and there's always
a "just one more tier" reward in sight. The premium track adds a paid goal (buy it
with diamonds) whose value only pays off if you keep playing the season — so it
buys commitment, not power.

Design mirrors the rest of the codebase: no cron, no season table. The active
season is derived from the date; points accrue as a side effect of the existing
XP awards (game/lab.award hooks in here); and rewards are *claimed* on demand,
granting everything between the last-claimed tier and the current one.
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from bio_lab.models import PassProgress, User

POINTS_PER_TIER = 150
MAX_TIER = 30
PREMIUM_COST_DIAMONDS = 150


def season_key() -> str:
    """The current season id — one per calendar month in the game timezone."""
    return timezone.localtime(timezone.now()).strftime("%Y-%m")


def tier_for_points(points: int) -> int:
    return min(MAX_TIER, max(0, points) // POINTS_PER_TIER)


def points_into_tier(points: int) -> tuple[int, int]:
    """(points into the current tier, points a tier costs) — for the progress bar.
    At max tier returns (POINTS_PER_TIER, POINTS_PER_TIER) so the bar reads full."""
    if tier_for_points(points) >= MAX_TIER:
        return POINTS_PER_TIER, POINTS_PER_TIER
    return points % POINTS_PER_TIER, POINTS_PER_TIER


def free_reward(tier: int) -> dict:
    """Free-track reward for a tier (1..MAX_TIER)."""
    if tier % 10 == 0:
        return {"diamonds": 15}
    if tier % 5 == 0:
        return {"speedup": 30}
    if tier % 3 == 0:
        return {"dna": 12}
    return {"coins": 120 + tier * 25}


def premium_reward(tier: int) -> dict:
    """Premium-track reward — diamonds every tier, so it's clearly the better track."""
    if tier % 10 == 0:
        return {"diamonds": 60}
    if tier % 5 == 0:
        return {"diamonds": 25, "speedup": 60}
    return {"diamonds": 6, "coins": 250 + tier * 35}


def _grant(user: User, reward: dict) -> None:
    fields = []
    if reward.get("coins"):
        user.coins += reward["coins"]; fields.append("coins")
    if reward.get("dna"):
        user.dna_fragments += reward["dna"]; fields.append("dna_fragments")
    if reward.get("diamonds"):
        user.diamonds += reward["diamonds"]; fields.append("diamonds")
    if fields:
        user.save(update_fields=fields)
    if reward.get("speedup"):
        from game.buildings import grant_speedup_card

        grant_speedup_card(user, reward["speedup"], count=1)


def _get_progress(user: User) -> PassProgress:
    progress, _ = PassProgress.objects.get_or_create(user=user, season_key=season_key())
    return progress


def award(user: User, points: int) -> None:
    """Add pass points for the current season. Called as a side effect of the
    normal XP awards, so any activity that grants lab XP also moves the pass."""
    points = int(points)
    if points <= 0:
        return
    # a limited-time event may double pass points ("Double-Pass week")
    try:
        from game import events

        points = int(points * events.pass_multiplier())
    except Exception:  # pragma: no cover
        pass
    progress = _get_progress(user)
    progress.points += points
    progress.save(update_fields=["points", "updated_at"])


def status(user: User) -> dict:
    """Everything the panel needs."""
    progress = _get_progress(user)
    tier = tier_for_points(progress.points)
    into, span = points_into_tier(progress.points)
    free_claimable = tier > progress.free_claimed
    premium_claimable = progress.premium and tier > progress.premium_claimed
    return {
        "season": progress.season_key,
        "points": progress.points,
        "tier": tier,
        "max_tier": MAX_TIER,
        "into": into,
        "span": span,
        "premium": progress.premium,
        "free_claimed": progress.free_claimed,
        "premium_claimed": progress.premium_claimed,
        "has_claimable": free_claimable or premium_claimable,
        "premium_cost": PREMIUM_COST_DIAMONDS,
    }


def reward_text(reward: dict) -> str:
    parts = []
    if reward.get("coins"):
        parts.append(f"{reward['coins']} طلا")
    if reward.get("dna"):
        parts.append(f"{reward['dna']} DNA")
    if reward.get("diamonds"):
        parts.append(f"{reward['diamonds']} 💎")
    if reward.get("speedup"):
        parts.append(f"کارت {reward['speedup']}د")
    return " + ".join(parts) or "—"


@transaction.atomic
def claim(user: User) -> dict:
    """Claim every reached-but-unclaimed tier on both tracks the player owns.
    Returns {'tiers': int, 'reward': {...totals...}}."""
    progress = _get_progress(user)
    tier = tier_for_points(progress.points)
    totals: dict[str, int] = {}
    claimed_tiers = 0

    def add(reward: dict) -> None:
        nonlocal claimed_tiers
        _grant(user, reward)
        for k, v in reward.items():
            totals[k] = totals.get(k, 0) + v

    for t in range(progress.free_claimed + 1, tier + 1):
        add(free_reward(t))
        claimed_tiers += 1
    if progress.premium:
        for t in range(progress.premium_claimed + 1, tier + 1):
            add(premium_reward(t))
            claimed_tiers += 1

    progress.free_claimed = max(progress.free_claimed, tier)
    if progress.premium:
        progress.premium_claimed = max(progress.premium_claimed, tier)
    progress.save(update_fields=["free_claimed", "premium_claimed", "updated_at"])
    return {"tiers": claimed_tiers, "reward": totals}


@transaction.atomic
def buy_premium(user: User) -> None:
    """Unlock the premium track for the current season. Retroactive — you can then
    claim premium rewards for every tier you've already reached."""
    from game.creature import GameError

    progress = _get_progress(user)
    if progress.premium:
        raise GameError("پاس ویژه‌ی این فصل رو قبلاً گرفتی.")
    if user.diamonds < PREMIUM_COST_DIAMONDS:
        raise GameError(f"الماس کافی نداری! پاس ویژه {PREMIUM_COST_DIAMONDS} الماس می‌خواد.")
    user.diamonds -= PREMIUM_COST_DIAMONDS
    user.save(update_fields=["diamonds"])
    progress.premium = True
    progress.save(update_fields=["premium", "updated_at"])

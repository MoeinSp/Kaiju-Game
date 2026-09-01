"""Achievements — one-time milestone goals with rewards.

Design choice that keeps this cheap and safe to add to a live game: **every
achievement's progress is derived from current state**, computed at read time,
not from per-action counters sprinkled through the codebase. "Own 5 creatures"
counts the creatures; "reach lab level 10" reads lab_xp; "win 10 arena raids"
counts AttackLog rows (which already persist). So there's nothing to instrument
and nothing that can drift — the only thing stored is which rewards have been
*claimed* (AchievementClaim), so they can't be taken twice.

A milestone counts as *earned* the moment its progress meets the target; the
player then claims it (once) for the reward. Progress functions run in sync
context (called via run_db), so they may query the ORM freely.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from django.db import transaction

from bio_lab.models import AchievementClaim, AttackLog, Building, Creature, User
from game import constants, lab


@dataclass(frozen=True)
class Achievement:
    key: str
    title: str
    emoji: str
    desc: str
    reward: dict  # any of: coins, dna, diamonds, speedup (minutes)
    progress: Callable[["_Snapshot"], tuple[int, int]]  # (current, target)


class _Snapshot:
    """One player's stats gathered once, so evaluating the whole list is a handful
    of queries rather than a query per achievement."""

    def __init__(self, user: User):
        self.user = user
        creatures = list(Creature.objects.filter(owner=user))
        self.creature_count = len(creatures)
        self.max_level = max((c.level for c in creatures), default=0)
        self.max_star = max((c.star_level for c in creatures), default=0)
        self.best_rarity_idx = max(
            (constants.RARITY_ORDER.index(c.rarity) for c in creatures if c.rarity in constants.RARITY_ORDER),
            default=-1,
        )
        self.hall_level = (
            Building.objects.filter(owner=user, building_type=constants.MAIN_BUILDING)
            .values_list("level", flat=True)
            .first()
            or 0
        )
        self.maxed_buildings = Building.objects.filter(owner=user, level=constants.BUILDING_MAX_LEVEL).count()
        self.arena_wins = AttackLog.objects.filter(attacker=user, attacker_won=True).count()
        self.lab_level = lab.level_for_xp(user.lab_xp)
        self.cup = user.cup
        self.streak = user.login_streak


def _rarity_at_least(tier: str):
    idx = constants.RARITY_ORDER.index(tier)
    return lambda s: (1 if s.best_rarity_idx >= idx else 0, 1)


# Rewards are modest but meaningful — a nudge, never a shortcut past the economy.
ACHIEVEMENTS: list[Achievement] = [
    Achievement("first_raid", "🩸 اولین شکار موفق", "🩸",
                "اولین برد آرنا را ثبت کن", {"coins": 100},
                lambda s: (min(s.arena_wins, 1), 1)),
    Achievement("collector_5", "🗃 کلکسیونر", "🗃",
                "۵ هیولا جمع‌آوری کن", {"dna": 20},
                lambda s: (min(s.creature_count, 5), 5)),
    Achievement("collector_15", "📚 کلکسیونر بزرگ", "📚",
                "۱۵ هیولا جمع‌آوری کن", {"diamonds": 15},
                lambda s: (min(s.creature_count, 15), 15)),
    Achievement("epic_owner", "🟣 نایاب‌گرد", "🟣",
                "یک هیولای حماسی یا بالاتر داشته باش", {"coins": 250},
                _rarity_at_least("epic")),
    Achievement("legendary_owner", "🟡 افسانه‌ساز", "🟡",
                "یک هیولای افسانه‌ای یا اساطیری داشته باش", {"diamonds": 40},
                _rarity_at_least("legendary")),
    Achievement("star_3", "⭐ نخبه‌پرور", "⭐",
                "یک هیولای ۳ ستاره بساز", {"dna": 40},
                lambda s: (min(s.max_star, 3), 3)),
    Achievement("creature_lv20", "📈 پرورش‌دهنده", "📈",
                "سطح یک هیولا را به ۲۰ برسان", {"coins": 600},
                lambda s: (min(s.max_level, 20), 20)),
    Achievement("lab_10", "🔬 دانشمند", "🔬",
                "سطح آزمایشگاه را به ۱۰ برسان", {"coins": 500},
                lambda s: (min(s.lab_level, 10), 10)),
    Achievement("lab_25", "🧪 استاد آزمایشگاه", "🧪",
                "سطح آزمایشگاه را به ۲۵ برسان", {"diamonds": 50},
                lambda s: (min(s.lab_level, 25), 25)),
    Achievement("hall_max", "🏛 معمار", "🏛",
                "تالار مِهر را به آخرین سطح برسان", {"diamonds": 30},
                lambda s: (min(s.hall_level, constants.BUILDING_MAX_LEVEL), constants.BUILDING_MAX_LEVEL)),
    Achievement("all_buildings_max", "🏗 شهرساز", "🏗",
                "تمام ۶ ساختمان بازی را Max کن", {"diamonds": 80, "speedup": 720},
                lambda s: (min(s.maxed_buildings, len(constants.BUILDING_TYPES)), len(constants.BUILDING_TYPES))),
    Achievement("arena_wins_10", "⚔️ جنگجو", "⚔️",
                "۱۰ پیروزی در آرنا کسب کن", {"coins": 400},
                lambda s: (min(s.arena_wins, 10), 10)),
    Achievement("arena_wins_50", "🛡 قهرمان آرنا", "🛡",
                "۵۰ پیروزی در آرنا کسب کن", {"diamonds": 60},
                lambda s: (min(s.arena_wins, 50), 50)),
    Achievement("cup_300", "🏆 صعود", "🏆",
                "به ۳۰۰ کاپ برس", {"diamonds": 35},
                lambda s: (min(s.cup, 300), 300)),
    Achievement("streak_7", "🔥 وفادار", "🔥",
                "۷ روز پیاپی وارد بازی شو", {"diamonds": 25},
                lambda s: (min(s.streak, 7), 7)),
]

ACHIEVEMENTS_BY_KEY = {a.key: a for a in ACHIEVEMENTS}


def _reward_text(reward: dict) -> str:
    parts = []
    if reward.get("coins"):
        parts.append(f"{reward['coins']} طلا")
    if reward.get("dna"):
        parts.append(f"{reward['dna']} DNA")
    if reward.get("diamonds"):
        parts.append(f"{reward['diamonds']} الماس")
    if reward.get("speedup"):
        parts.append(f"کارت سرعت {reward['speedup']} دقیقه‌ای")
    return " + ".join(parts)


def evaluate(user: User) -> dict:
    """Everything the panel needs: per-achievement status plus counts.

    status per item: {'ach', 'current', 'target', 'earned', 'claimed'}.
    'earned' means the target is met; 'claimed' means the reward is already taken.
    """
    snap = _Snapshot(user)
    claimed_keys = set(
        AchievementClaim.objects.filter(user=user).values_list("key", flat=True)
    )
    items = []
    claimable = 0
    for ach in ACHIEVEMENTS:
        current, target = ach.progress(snap)
        earned = current >= target
        claimed = ach.key in claimed_keys
        if earned and not claimed:
            claimable += 1
        items.append(
            {"ach": ach, "current": current, "target": target, "earned": earned, "claimed": claimed}
        )
    done = sum(1 for i in items if i["claimed"])
    return {"items": items, "claimable": claimable, "done": done, "total": len(items)}


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


@transaction.atomic
def claim_all(user: User) -> dict:
    """Claim every earned-but-unclaimed achievement at once. Returns a summary:
    {'claimed': [Achievement, ...], 'reward': {coins, dna, diamonds, speedup}}."""
    snap = _Snapshot(user)
    already = set(AchievementClaim.objects.filter(user=user).values_list("key", flat=True))
    newly: list[Achievement] = []
    totals: dict[str, int] = {}
    for ach in ACHIEVEMENTS:
        if ach.key in already:
            continue
        current, target = ach.progress(snap)
        if current < target:
            continue
        AchievementClaim.objects.create(user=user, key=ach.key)
        _grant(user, ach.reward)
        newly.append(ach)
        for k, v in ach.reward.items():
            totals[k] = totals.get(k, 0) + v
    return {"claimed": newly, "reward": totals}

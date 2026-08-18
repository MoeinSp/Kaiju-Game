"""Titles / لقب‌ها — cosmetic prestige shown on the profile and leaderboards.

Every title's unlock is derived from existing progress (lab level, campaign depth,
cup, codex), so there's nothing new to track — a title simply becomes available
once its condition is met, the player equips one, and it shows next to their lab
name. Pure status; no mechanical effect (that's the point — it's a flex).
"""

from __future__ import annotations

from bio_lab.models import CodexEntry, User
from game import constants, lab


def _codex_count(user: User) -> int:
    return CodexEntry.objects.filter(user=user).count()


# each: key, emoji, title, and a predicate over the player's state
TITLES = [
    {"key": "rookie", "emoji": "🔰", "title": "تازه‌کار", "check": lambda u: True},
    {"key": "hunter", "emoji": "🏹", "title": "شکارچی", "check": lambda u: lab.lab_level(u) >= 10},
    {"key": "conqueror", "emoji": "🗺", "title": "فاتح", "check": lambda u: u.campaign_stage >= 10},
    {"key": "champion", "emoji": "🏆", "title": "قهرمان", "check": lambda u: u.cup >= 350},
    {"key": "veteran", "emoji": "🎖", "title": "کهنه‌کار", "check": lambda u: lab.lab_level(u) >= 25},
    {"key": "warlord", "emoji": "👑", "title": "سردار", "check": lambda u: u.campaign_stage >= 25},
    {"key": "scholar", "emoji": "📖", "title": "دانشنامه‌دار", "check": lambda u: _codex_count(u) >= len(constants.SPECIES)},
    {"key": "master", "emoji": "🧪", "title": "استادِ آزمایشگاه", "check": lambda u: lab.lab_level(u) >= 40},
    {"key": "legend", "emoji": "💎", "title": "اسطوره", "check": lambda u: u.cup >= 600 or u.campaign_stage >= 50},
]
TITLES_BY_KEY = {t["key"]: t for t in TITLES}


def available(user: User) -> list[dict]:
    """Titles the player has unlocked, each tagged with whether it's equipped."""
    return [
        {**t, "equipped": user.title == t["key"]}
        for t in TITLES
        if t["check"](user)
    ]


def label(user: User) -> str:
    """The equipped title as ' 🏆 قهرمان', or '' — for appending after a lab name.
    Falls back to nothing if the equipped title is no longer valid."""
    if not user.title:
        return ""
    t = TITLES_BY_KEY.get(user.title)
    if t is None or not t["check"](user):
        return ""
    return f" {t['emoji']}<i>{t['title']}</i>"


def equip(user: User, key: str) -> None:
    from game.creature import GameError

    if key == "none":
        user.title = None
        user.save(update_fields=["title"])
        return
    t = TITLES_BY_KEY.get(key)
    if t is None:
        raise GameError("این لقب وجود نداره.")
    if not t["check"](user):
        raise GameError("این لقب رو هنوز باز نکردی.")
    user.title = key
    user.save(update_fields=["title"])

"""Ranked league — divisions + end-of-season rewards on top of the weekly cup.

The arena already tracks a cup rating and resets it weekly (game/season.py). This
layers a visible competitive ladder on it: your cup places you in a division
(Wood → Diamond), and when the weekly season closes you get a reward scaled to the
division you finished in. Divisions turn a bare number into a ladder people climb
and a rank they defend.

Divisions are derived from the cup value (no state), and the reward is granted
inside the existing season close (game/season.close_due_season), so there's no new
timer.
"""

from __future__ import annotations

from bio_lab.models import User

# ordered high → low; a cup lands in the first division it meets the floor for
DIVISIONS = [
    {"key": "diamond", "emoji": "💎", "title": "الماس", "min_cup": 600},
    {"key": "gold", "emoji": "🥇", "title": "طلا", "min_cup": 350},
    {"key": "silver", "emoji": "🥈", "title": "نقره", "min_cup": 180},
    {"key": "bronze", "emoji": "🥉", "title": "برنز", "min_cup": 60},
    {"key": "wood", "emoji": "🪵", "title": "چوب", "min_cup": 0},
]

DIVISION_REWARD = {
    "diamond": {"diamonds": 50, "coins": 2000},
    "gold": {"diamonds": 25, "coins": 1200},
    "silver": {"diamonds": 12, "coins": 600},
    "bronze": {"diamonds": 5, "coins": 300},
    "wood": {"coins": 100},
}


def division_for(cup: int) -> dict:
    for d in DIVISIONS:
        if cup >= d["min_cup"]:
            return d
    return DIVISIONS[-1]


def next_division(cup: int) -> dict | None:
    """The division just above the current one, or None at the top."""
    current = division_for(cup)
    idx = DIVISIONS.index(current)
    return DIVISIONS[idx - 1] if idx > 0 else None


def season_reward(cup: int) -> dict:
    return DIVISION_REWARD[division_for(cup)["key"]]


def grant_season_reward(user: User, cup: int) -> dict:
    """Grant the division reward for finishing a season at `cup`. Called from the
    season close for each ranked player."""
    reward = season_reward(cup)
    fields = []
    if reward.get("coins"):
        user.coins += reward["coins"]; fields.append("coins")
    if reward.get("diamonds"):
        user.diamonds += reward["diamonds"]; fields.append("diamonds")
    if reward.get("dna"):
        user.dna_fragments += reward["dna"]; fields.append("dna_fragments")
    if fields:
        user.save(update_fields=fields)
    return reward


def reward_text(reward: dict) -> str:
    parts = []
    if reward.get("coins"):
        parts.append(f"{reward['coins']} طلا")
    if reward.get("diamonds"):
        parts.append(f"{reward['diamonds']} 💎")
    if reward.get("dna"):
        parts.append(f"{reward['dna']} DNA")
    return " + ".join(parts) or "—"

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

from game import constants

# The weekly ladder IS the 16 arena leagues (constants.LEAGUES) — the same names and cup
# floors the arena shows. It used to be a separate 5-step ladder that topped out at 600
# cups, so a 600-cup and a 3,700-cup player got the same «top» reward and almost one in
# ten players sat in the highest division.
# ordered high → low; a cup lands in the first division it meets the floor for
DIVISIONS = [
    {"key": lg["key"], "emoji": lg["emoji"], "title": lg["name"], "min_cup": lg["min_cup"]}
    for lg in reversed(constants.LEAGUES)
]

# End-of-week reward for the league a player FINISHES in. Sized against the arena itself
# (a won raid at the top pays roughly 5–15k gold): a top finish is worth a good day of
# raiding, a low one a small thank-you. Diamonds stay modest at the bottom, where most
# accounts sit, and grow steeply only where few players reach.
DIVISION_REWARD = {
    "bronze_1":  {"coins": 500,    "dna": 20},
    "bronze_2":  {"coins": 1_000,  "dna": 40,    "diamonds": 3},
    "bronze_3":  {"coins": 1_800,  "dna": 70,    "diamonds": 6},
    "silver_1":  {"coins": 3_000,  "dna": 110,   "diamonds": 10},
    "silver_2":  {"coins": 4_500,  "dna": 160,   "diamonds": 15},
    "silver_3":  {"coins": 6_500,  "dna": 220,   "diamonds": 20},
    "gold_1":    {"coins": 9_000,  "dna": 300,   "diamonds": 28},
    "gold_2":    {"coins": 12_000, "dna": 400,   "diamonds": 36},
    "gold_3":    {"coins": 16_000, "dna": 520,   "diamonds": 45},
    "plat_1":    {"coins": 21_000, "dna": 660,   "diamonds": 55},
    "plat_2":    {"coins": 27_000, "dna": 820,   "diamonds": 68},
    "diamond_1": {"coins": 34_000, "dna": 1_000, "diamonds": 82},
    "diamond_2": {"coins": 42_000, "dna": 1_200, "diamonds": 98},
    "master":    {"coins": 52_000, "dna": 1_450, "diamonds": 120},
    "legend":    {"coins": 65_000, "dna": 1_750, "diamonds": 150},
    "champion":  {"coins": 80_000, "dna": 2_100, "diamonds": 200},
}

# Extra reward for the week's final RANK, on top of the league reward — what actually
# makes the top of the table worth fighting over. (max rank, reward), best first.
RANK_REWARDS = [
    (1,  {"coins": 100_000, "dna": 2_500, "diamonds": 300}),
    (3,  {"coins": 60_000,  "dna": 1_500, "diamonds": 180}),
    (10, {"coins": 30_000,  "dna": 800,   "diamonds": 90}),
    (25, {"coins": 15_000,  "dna": 400,   "diamonds": 40}),
    (50, {"coins": 7_000,   "dna": 200,   "diamonds": 20}),
]


def rank_reward(rank: int) -> dict | None:
    for max_rank, reward in RANK_REWARDS:
        if rank <= max_rank:
            return reward
    return None


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
    _grant(user, reward)
    return reward


def grant_rank_reward(user: User, rank: int) -> dict | None:
    """Grant the bonus for finishing the week at `rank` (None outside the rewarded ranks)."""
    reward = rank_reward(rank)
    if reward:
        _grant(user, reward)
    return reward


def _grant(user: User, reward: dict) -> None:
    # F() updates: the season close walks every ranked player in one long transaction,
    # so saving a balance read before the loop would undo whatever they spent meanwhile
    from django.db.models import F

    updates = {}
    for key, field in (("coins", "coins"), ("diamonds", "diamonds"), ("dna", "dna_fragments")):
        if reward.get(key):
            updates[field] = F(field) + reward[key]
    if updates:
        User.objects.filter(pk=user.pk).update(**updates)
    from game.ledger import record_gain

    record_gain(user, "league", coins=reward.get("coins", 0), dna=reward.get("dna", 0),
                diamonds=reward.get("diamonds", 0))


def reward_text(reward: dict) -> str:
    parts = []
    if reward.get("coins"):
        parts.append(f"{reward['coins']:,} طلا")
    if reward.get("diamonds"):
        parts.append(f"{reward['diamonds']} 💎")
    if reward.get("dna"):
        parts.append(f"{reward['dna']} DNA")
    return " + ".join(parts) or "—"

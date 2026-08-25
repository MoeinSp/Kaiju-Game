"""The periodic reward that group trigger words hand out.

Several words («جایزه», «کایجو», …) all lead here and share **one GLOBAL** cooldown
per player (stored on User.reward_ready_at, not per-group) — so adding the bot to
many groups no longer multiplies the payout, and cycling synonyms doesn't help
either. The cooldown is a fresh random 4–6 minutes chosen after each claim. Every
off-cooldown claim always pays out a (small) prize — no empty/blank results.

Prizes are small on purpose. This is a reason to keep the group alive, not an
income stream; the numbers sit well under what a few minutes of hunting pays, so
nobody is better off spamming a word than playing the game.
"""

from __future__ import annotations

import datetime
import random

from django.db import transaction
from django.utils import timezone

from bio_lab.models import Group, User
from game import lab
from game.daily import record_action

# The cooldown is a fresh RANDOM value in this window, chosen after every claim, and
# it's GLOBAL per player (stored on User, not per-group) — so being in 30 groups no
# longer means 30× the reward. Every off-cooldown claim always pays a prize.
COOLDOWN_MIN_SECONDS = 4 * 60
COOLDOWN_MAX_SECONDS = 6 * 60

# (weight, kind, low, high) — `kind` names the resource so the caller can render
# the right emoji without re-deriving it from the amount.
PRIZES: tuple[tuple[int, str, int, int], ...] = (
    (46, "coins", 60, 220),
    (26, "dna", 3, 12),
    (10, "diamonds", 1, 2),
    (12, "speedup", 0, 0),  # minutes chosen from SPEEDUP_CHOICES below
    (6, "jackpot", 400, 900),  # gold, but announced as a jackpot
)
SPEEDUP_CHOICES: tuple[tuple[int, int], ...] = ((1, 55), (5, 33), (30, 12))  # (minutes, weight)

PRIZE_LABELS = {
    "coins": "طلا",
    "dna": "DNA",
    "diamonds": "الماس",
    "speedup": "کارت سرعت",
    "jackpot": "جکپات طلا",
}


def seconds_left(user: User) -> int:
    """0 when the player may claim right now (global, across every group)."""
    if user.reward_ready_at is None:
        return 0
    return max(0, int((user.reward_ready_at - timezone.now()).total_seconds()))


def _roll_prize(user: User) -> tuple[str, int, int]:
    kind = random.choices([p[1] for p in PRIZES], weights=[p[0] for p in PRIZES], k=1)[0]
    low, high = next((lo, hi) for _w, k, lo, hi in PRIZES if k == kind)
    amount = random.randint(low, high) if high else 0
    minutes = 0
    if kind in ("coins", "jackpot"):
        user.coins += amount
        user.save(update_fields=["coins"])
    elif kind == "dna":
        user.dna_fragments += amount
        user.save(update_fields=["dna_fragments"])
    elif kind == "diamonds":
        user.diamonds += amount
        user.save(update_fields=["diamonds"])
    elif kind == "speedup":
        from game.buildings import grant_speedup_card

        minutes = random.choices(
            [m for m, _w in SPEEDUP_CHOICES], weights=[w for _m, w in SPEEDUP_CHOICES], k=1
        )[0]
        grant_speedup_card(user, minutes, count=1)
    return kind, amount, minutes


@transaction.atomic
def claim(user: User, group: Group | None = None) -> dict:
    """Try to claim the word reward.

    Returns {"ok": False, "seconds_left": int} while on cooldown. Otherwise starts a
    fresh random cooldown and returns {"ok": True, "won": bool, "next_wait": int, ...};
    on a win it also carries kind/amount/minutes/count/lab_up. `group` is accepted
    for call-site compatibility but no longer affects the (now global) cooldown.
    """
    # lock the row so two near-simultaneous messages can't both pass the cooldown
    user = User.objects.select_for_update().get(id=user.id)
    remaining = seconds_left(user)
    if remaining > 0:
        return {"ok": False, "seconds_left": remaining}

    next_wait = random.randint(COOLDOWN_MIN_SECONDS, COOLDOWN_MAX_SECONDS)
    user.reward_ready_at = timezone.now() + datetime.timedelta(seconds=next_wait)

    record_action(user, "word_reward")  # for the admin cheat-finder's daily counts

    # every off-cooldown claim pays out — no empty results
    kind, amount, minutes = _roll_prize(user)
    user.reward_total_claims += 1
    user.save(update_fields=["reward_ready_at", "reward_total_claims"])
    lab_up = lab.add_lab_xp(user, 3)
    return {
        "ok": True,
        "won": True,
        "kind": kind,
        "amount": amount,
        "minutes": minutes,
        "count": user.reward_total_claims,
        "lab_up": lab_up,
        "next_wait": next_wait,
    }

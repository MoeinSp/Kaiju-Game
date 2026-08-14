"""The periodic reward that group trigger words hand out.

Several words («جایزه», «شانس», «گنج», …) all lead here, and they deliberately
share **one** cooldown per player per group — see WordRewardClaim. Giving each
word its own timer would just teach people to cycle the synonyms.

Prizes are small on purpose. This is a reason to keep the group alive, not an
income stream; the numbers sit well under what a few minutes of hunting pays, so
nobody is better off spamming a word than playing the game.
"""

from __future__ import annotations

import random

from django.db import transaction
from django.utils import timezone

from bio_lab.models import Group, User, WordRewardClaim
from game import lab

COOLDOWN_MINUTES = 5

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


def seconds_left(user: User, group: Group) -> int:
    """0 when the player may claim right now."""
    claim = WordRewardClaim.objects.filter(user=user, group=group).first()
    if claim is None:
        return 0
    elapsed = (timezone.now() - claim.last_claimed_at).total_seconds()
    return max(0, int(COOLDOWN_MINUTES * 60 - elapsed))


@transaction.atomic
def claim(user: User, group: Group) -> dict:
    """Award a prize, or report the wait.

    Returns {"ok": False, "seconds_left": int} when the cooldown hasn't elapsed,
    otherwise {"ok": True, "kind", "amount", "minutes", "count", "lab_up"}.
    Reporting rather than raising because "come back in 4 minutes" is a normal
    outcome of typing the word, not an error.
    """
    remaining = seconds_left(user, group)
    if remaining > 0:
        return {"ok": False, "seconds_left": remaining}

    kind = random.choices(
        [p[1] for p in PRIZES], weights=[p[0] for p in PRIZES], k=1
    )[0]
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

    claim_row, _ = WordRewardClaim.objects.get_or_create(
        user=user, group=group, defaults={"last_claimed_at": timezone.now()}
    )
    claim_row.last_claimed_at = timezone.now()
    claim_row.total_claims += 1
    claim_row.save(update_fields=["last_claimed_at", "total_claims"])

    lab_up = lab.add_lab_xp(user, 3)
    return {
        "ok": True,
        "kind": kind,
        "amount": amount,
        "minutes": minutes,
        "count": claim_row.total_claims,
        "lab_up": lab_up,
    }

import random

from django.db import transaction

from bio_lab.models import User
from game import constants
from game.buildings import grant_speedup_card
from game.daily import consume_daily


@transaction.atomic
def spin(user: User) -> dict:
    # consume the daily spin ATOMICALLY before granting, so a rapid double-tap can't
    # spin twice off one day's allowance (was check-then-record, which was spammable).
    consume_daily(user, "wheel_spin")
    prize = _roll_prize()
    _apply_prize(user, prize)
    return prize


def _roll_prize() -> dict:
    prizes = constants.WHEEL_PRIZES
    weights = [p["weight"] for p in prizes]
    return random.choices(prizes, weights=weights, k=1)[0]


def _apply_prize(user: User, prize: dict) -> None:
    kind, amount = prize["kind"], prize["amount"]
    if kind == "coins":
        user.coins += amount
        user.save(update_fields=["coins"])
        _ledger(user, coins=amount)
    elif kind == "dna":
        user.dna_fragments += amount
        user.save(update_fields=["dna_fragments"])
        _ledger(user, dna=amount)
    elif kind == "diamonds":
        user.diamonds += amount
        user.save(update_fields=["diamonds"])
        _ledger(user, diamonds=amount)
    elif kind == "speedup":
        grant_speedup_card(user, amount, count=1)


def _ledger(user: User, **kw) -> None:
    from game.ledger import record_gain

    record_gain(user, "wheel", **kw)

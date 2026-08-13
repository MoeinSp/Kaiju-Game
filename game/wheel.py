import random

from bio_lab.models import User
from game import constants
from game.buildings import grant_speedup_card
from game.daily import assert_energy_available, record_action


def spin(user: User) -> dict:
    assert_energy_available(user, "wheel_spin")
    prize = _roll_prize()
    _apply_prize(user, prize)
    record_action(user, "wheel_spin")
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
    elif kind == "dna":
        user.dna_fragments += amount
        user.save(update_fields=["dna_fragments"])
    elif kind == "diamonds":
        user.diamonds += amount
        user.save(update_fields=["diamonds"])
    elif kind == "speedup":
        grant_speedup_card(user, amount, count=1)

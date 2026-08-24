import random

from django.db import transaction

from bio_lab.models import User
from game import constants
from game.buildings import grant_speedup_card
from game.creature import GameError


def tier_list() -> list[dict]:
    """The four tables for the casino menu, in cheap→expensive order (free first)."""
    out = []
    for key in constants.CASINO_TIER_ORDER:
        cfg = constants.CASINO_TIERS[key]
        out.append({
            "key": key,
            "label": cfg["label"],
            "cost": cfg["cost"],
            "currency": cfg["currency"],
            "daily": cfg.get("daily", False),
            "desc": cfg["desc"],
        })
    return out


def _roll(prizes: list[dict]) -> dict:
    return random.choices(prizes, weights=[p["weight"] for p in prizes], k=1)[0]


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
    # "nothing" pays out nothing — the house edge


@transaction.atomic
def play(user: User, tier: str) -> dict:
    """Charge the tier's cost, roll its table and apply the prize. The free tier IS
    the daily wheel (قرعه‌کشی) — same once-a-day limit and prize table — so a player
    can't claim both. Returns the winning prize dict."""
    cfg = constants.CASINO_TIERS.get(tier)
    if cfg is None:
        raise GameError("این میز وجود نداره.")

    if cfg.get("daily"):
        # the free casino option is literally the daily wheel
        from game.wheel import spin

        return spin(user)

    user = User.objects.select_for_update().get(id=user.id)
    currency, cost = cfg["currency"], cfg["cost"]
    if currency == "coins":
        if user.coins < cost:
            raise GameError(f"طلا کافی نداری! این میز {cost} طلا شرط می‌خواد.")
        user.coins -= cost
        user.save(update_fields=["coins"])
    elif currency == "diamonds":
        if user.diamonds < cost:
            raise GameError(f"الماس کافی نداری! این میز {cost} الماس شرط می‌خواد.")
        user.diamonds -= cost
        user.save(update_fields=["diamonds"])

    prize = _roll(cfg["prizes"])
    _apply_prize(user, prize)
    return prize

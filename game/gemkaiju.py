"""💎 Daily gem-kaiju offers in the special-items shop.

1) «کایجوی الماسی» — same species+rarity as one of the player's three strongest
   epic+/legendary/mythic kaiju (a fusion partner). Price by rarity.
2) «کایجوی باستانی» — same species+rarity as one of the three strongest
   crystal/plasma kaiju the player owns. Fixed 1000 diamonds. Crystal/plasma are
   harder to grow, so they rarely win the regular gem roll; this is their lane.
"""

from __future__ import annotations

import random

from django.db import transaction
from django.utils import timezone

from bio_lab.models import Creature, User
from game import constants
from game.creature import GameError

GEM_PRICE = {"mythic": 750, "legendary": 400, "epic": 200}
_GEM_RARITIES = ("mythic", "legendary", "epic")

ANCIENT_PRICE = 1000
ANCIENT_ELEMENTS = constants.PREMIUM_ELEMENTS  # crystal, plasma
ANCIENT_TITLE = "کایجوی باستانی"


def _today():
    return timezone.localdate()


def _pick(user: User, pool: list[Creature], salt: int) -> Creature:
    pool = pool[:3]
    seed = user.id * 1_000_003 + _today().toordinal() + salt
    return random.Random(seed).choice(pool)


def gem_offer(user: User) -> dict | None:
    """Today's regular gem-kaiju offer, or None when they own no epic+ kaiju.
    Deterministic per (user, day): the pick among the top-3 is stable through the day."""
    from game.creature import creature_power

    pool = list(Creature.objects.filter(owner=user, rarity__in=_GEM_RARITIES))
    if not pool:
        return None
    pool.sort(key=creature_power, reverse=True)
    chosen = _pick(user, pool, salt=0)
    return {
        "kind": "gem",
        "name": chosen.name,
        "element": chosen.element,
        "rarity": chosen.rarity,
        "price": GEM_PRICE[chosen.rarity],
        "claimed": user.gem_kaiju_claimed_on == _today(),
        "title": "کایجوی الماسی",
    }


def ancient_offer(user: User) -> dict | None:
    """Today's ancient (crystal/plasma) gem offer, or None with no premium-element kaiju."""
    from game.creature import creature_power

    pool = list(Creature.objects.filter(owner=user, element__in=ANCIENT_ELEMENTS))
    if not pool:
        return None
    pool.sort(key=creature_power, reverse=True)
    chosen = _pick(user, pool, salt=17)  # different salt so it isn't locked to the gem pick
    return {
        "kind": "ancient",
        "name": chosen.name,
        "element": chosen.element,
        "rarity": chosen.rarity,
        "price": ANCIENT_PRICE,
        "claimed": user.ancient_kaiju_claimed_on == _today(),
        "title": ANCIENT_TITLE,
    }


def _buy(user: User, offer: dict, claim_field: str) -> dict:
    price = int(offer["price"])
    if user.diamonds < price:
        raise GameError(
            f"الماس کافی نداری! این کایجو {price} الماس می‌خواد (الان {user.diamonds} داری)."
        )
    from game.itemshop import _make_creature

    creature = _make_creature(user, offer["rarity"], offer["element"], offer["name"])
    user.diamonds -= price
    setattr(user, claim_field, _today())
    user.save(update_fields=["diamonds", claim_field])
    return {
        "creature": creature,
        "price": price,
        "coins": user.coins,
        "diamonds": user.diamonds,
        "offer": offer,
    }


@transaction.atomic
def buy_gem_kaiju(user: User) -> dict:
    """Buy today's regular gem kaiju (once per day)."""
    user = User.objects.select_for_update().get(id=user.id)
    if user.gem_kaiju_claimed_on == _today():
        raise GameError("کایجوی الماسیِ امروز رو قبلاً خریدی — فردا دوباره بیا.")
    offer = gem_offer(user)
    if offer is None:
        raise GameError(
            "الان کایجوی الماسی برات موجود نیست — باید حداقل یه کایجوی حماسی، افسانه‌ای یا اساطیری داشته باشی."
        )
    return _buy(user, offer, "gem_kaiju_claimed_on")


@transaction.atomic
def buy_ancient_kaiju(user: User) -> dict:
    """Buy today's ancient crystal/plasma kaiju (once per day, separate from the gem offer)."""
    user = User.objects.select_for_update().get(id=user.id)
    if user.ancient_kaiju_claimed_on == _today():
        raise GameError(f"{ANCIENT_TITLE} امروز رو قبلاً خریدی — فردا دوباره بیا.")
    offer = ancient_offer(user)
    if offer is None:
        raise GameError(
            f"الان {ANCIENT_TITLE} برات موجود نیست — باید حداقل یه کایجوی کریستال یا پلاسما داشته باشی."
        )
    return _buy(user, offer, "ancient_kaiju_claimed_on")

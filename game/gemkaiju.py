"""💎 Daily «کایجوی جمی» — a once-a-day gem-kaiju offer in the special-items shop.

Each day the shop offers ONE kaiju that is the same species AND rarity as one of the
player's three strongest high-rarity kaiju (mythic / legendary / epic) — a perfect
fusion partner. Priced purely by rarity, buyable once per day.
"""

import random

from django.db import transaction
from django.utils import timezone

from bio_lab.models import Creature, User
from game import constants
from game.creature import GameError

GEM_PRICE = {"mythic": 700, "legendary": 400, "epic": 200}
_GEM_RARITIES = ("mythic", "legendary", "epic")


def _today():
    return timezone.localdate()


def gem_offer(user: User) -> dict | None:
    """Today's gem-kaiju offer for `user`, or None when they own no epic+ kaiju.
    Deterministic per (user, day): the pick among the top-3 is stable through the day and
    only reshuffles at the daily rollover, so a refresh never rerolls it."""
    from game.creature import creature_power

    pool = list(Creature.objects.filter(owner=user, rarity__in=_GEM_RARITIES))
    if not pool:
        return None
    pool.sort(key=creature_power, reverse=True)
    top = pool[:3]
    seed = user.id * 1_000_003 + _today().toordinal()
    chosen = random.Random(seed).choice(top)
    return {
        "name": chosen.name,
        "element": chosen.element,
        "rarity": chosen.rarity,
        "price": GEM_PRICE[chosen.rarity],
        "claimed": user.gem_kaiju_claimed_on == _today(),
    }


@transaction.atomic
def buy_gem_kaiju(user: User) -> dict:
    """Buy today's gem kaiju (once per day). Charges diamonds by rarity and mints a fresh
    same-species/same-rarity kaiju (1★, level 1). Returns the created creature + balances."""
    user = User.objects.select_for_update().get(id=user.id)
    if user.gem_kaiju_claimed_on == _today():
        raise GameError("کایجوی جمیِ امروز رو قبلاً خریدی — فردا دوباره بیا.")
    offer = gem_offer(user)
    if offer is None:
        raise GameError(
            "الان کایجوی جمی برات موجود نیست — باید حداقل یه کایجوی حماسی، افسانه‌ای یا اساطیری داشته باشی."
        )
    price = offer["price"]
    if user.diamonds < price:
        raise GameError(f"الماس کافی نداری! این کایجو {price} الماس می‌خواد (الان {user.diamonds} داری).")

    from game.itemshop import _make_creature

    creature = _make_creature(user, offer["rarity"], offer["element"], offer["name"])
    user.diamonds -= price
    user.gem_kaiju_claimed_on = _today()
    user.save(update_fields=["diamonds", "gem_kaiju_claimed_on"])
    return {"creature": creature, "price": price, "coins": user.coins,
            "diamonds": user.diamonds, "offer": offer}

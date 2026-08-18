"""Featured banner + pity — the deep end of the gacha chase.

A single **featured creature** rotates every week (picked from the species list by
ISO week), giving a recurring "pull for THIS one before it's gone" hook. Pulling on
the banner costs diamonds and has two mercy/steer mechanics on top of the base
rarity roll:

* **Pity** — a counter of pulls since your last legendary-or-better. When it hits
  the threshold, the next pull is a guaranteed legendary; any legendary+ resets it.
  So a dry streak can never last forever, which is exactly what keeps people
  pulling.
* **Rate-up** — when a pull lands epic or above, it's very likely to be *this
  week's* featured species rather than a random one.

Always yields a creature. Discovery flows into the Codex automatically (it reads
owned creatures on view), so no extra hook is needed here.
"""

from __future__ import annotations

import random

from django.db import transaction
from django.utils import timezone

from bio_lab.models import Creature, User
from game import constants
from game.creature import GameError

PULL_COST_DIAMONDS = 30
PITY_THRESHOLD = 20  # a guaranteed legendary at least this often
FEATURED_CHANCE = 0.6  # chance an epic+ pull becomes the featured species
# slightly kinder than the gold diamond box, since the banner is the premium chase
WEIGHTS = {"common": 40, "rare": 35, "epic": 18, "legendary": 6, "mythic": 1}

_LEGENDARY_IDX = constants.RARITY_ORDER.index("legendary")
_EPIC_IDX = constants.RARITY_ORDER.index("epic")


def featured() -> dict:
    """This week's featured species (name + element), rotating by ISO week."""
    names = list(constants.SPECIES)
    week = timezone.localtime(timezone.now()).isocalendar().week
    name = names[week % len(names)]
    return {"name": name, "element": constants.SPECIES[name]}


def _roll_rarity(rng: random.Random) -> str:
    keys = list(WEIGHTS)
    return rng.choices(keys, weights=[WEIGHTS[k] for k in keys], k=1)[0]


def _make_creature(user: User, name: str, element: str, rarity: str) -> Creature:
    mult = constants.RARITY_STAT_MULTIPLIER[rarity]
    return Creature.objects.create(
        owner=user,
        name=name,
        element=element,
        rarity=rarity,
        base_hp=round(constants.STARTER_BASE_HP * mult),
        base_atk=round(constants.STARTER_BASE_ATK * mult),
        base_def=round(constants.STARTER_BASE_DEF * mult),
        base_spd=round(constants.STARTER_BASE_SPD * mult),
        is_active=False,
    )


def status(user: User) -> dict:
    feat = featured()
    return {
        "featured": feat,
        "featured_label": f"{constants.element_label(feat['element'])} {feat['name']}",
        "pity": user.banner_pity,
        "pity_threshold": PITY_THRESHOLD,
        "cost": PULL_COST_DIAMONDS,
        "diamonds": user.diamonds,
    }


@transaction.atomic
def pull(user: User) -> dict:
    """One banner pull. Deducts diamonds, applies pity + rate-up, yields a creature."""
    if user.diamonds < PULL_COST_DIAMONDS:
        raise GameError(f"الماس کافی نداری! هر کشش بنر {PULL_COST_DIAMONDS} الماس می‌خواد.")
    user.diamonds -= PULL_COST_DIAMONDS

    rng = random.Random()
    rarity = _roll_rarity(rng)
    guaranteed = False
    # pity: at the threshold, force at least legendary
    if constants.RARITY_ORDER.index(rarity) < _LEGENDARY_IDX and user.banner_pity + 1 >= PITY_THRESHOLD:
        rarity = "legendary"
        guaranteed = True

    if constants.RARITY_ORDER.index(rarity) >= _LEGENDARY_IDX:
        user.banner_pity = 0
    else:
        user.banner_pity += 1

    feat = featured()
    is_featured = False
    if constants.RARITY_ORDER.index(rarity) >= _EPIC_IDX and rng.random() < FEATURED_CHANCE:
        name, element = feat["name"], feat["element"]
        is_featured = True
    else:
        element = constants.random_element()
        name = constants.random_species_name(element)

    creature = _make_creature(user, name, element, rarity)
    user.save(update_fields=["diamonds", "banner_pity"])
    return {
        "creature": creature,
        "rarity": rarity,
        "is_featured": is_featured,
        "guaranteed": guaranteed,
        "pity": user.banner_pity,
    }

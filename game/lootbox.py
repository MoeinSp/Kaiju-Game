import random

from django.db import transaction

from bio_lab.models import Creature, User
from game import constants
from game.creature import GameError
from game.equipment import roll_equipment


def roll_rarity() -> str:
    rarities = list(constants.LOOTBOX_RARITY_WEIGHTS)
    weights = list(constants.LOOTBOX_RARITY_WEIGHTS.values())
    return random.choices(rarities, weights=weights, k=1)[0]


def _roll_creature(user: User, rarity: str) -> Creature:
    """Dropped inactive — get_active_creature() assumes exactly one is_active=True
    row per owner, so a biocrate creature must never silently steal that slot from
    whatever the player already has active. Players activate it themselves via
    /select once they see it in /collection."""
    mult = constants.RARITY_STAT_MULTIPLIER[rarity]
    element = constants.random_element()
    return Creature.objects.create(
        owner=user,
        name=constants.random_species_name(element),
        element=element,
        rarity=rarity,
        base_hp=round(constants.STARTER_BASE_HP * mult),
        base_atk=round(constants.STARTER_BASE_ATK * mult),
        base_def=round(constants.STARTER_BASE_DEF * mult),
        base_spd=round(constants.STARTER_BASE_SPD * mult),
        is_active=False,
    )


@transaction.atomic
def open_biocrate(user: User) -> dict:
    if user.coins < constants.BIOCRATE_GOLD_COST:
        raise GameError(f"طلا کافی نداری! باز کردن باکس ژنتیکی {constants.BIOCRATE_GOLD_COST} طلا هزینه داره.")
    user.coins -= constants.BIOCRATE_GOLD_COST
    user.save(update_fields=["coins"])

    rarity = roll_rarity()
    if random.random() < constants.BIOCRATE_CREATURE_CHANCE:
        creature = _roll_creature(user, rarity)
        return {"kind": "creature", "rarity": rarity, "creature": creature}
    item = roll_equipment(user, rarity)
    return {"kind": "equipment", "rarity": rarity, "item": item}

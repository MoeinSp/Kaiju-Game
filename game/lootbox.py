import random

from django.db import transaction

from bio_lab.models import Creature, User
from game import constants
from game.creature import GameError
from game.equipment import roll_equipment


def roll_rarity(weights: dict[str, float] | None = None) -> str:
    weights = weights or constants.LOOTBOX_RARITY_WEIGHTS
    rarities = list(weights)
    return random.choices(rarities, weights=list(weights.values()), k=1)[0]


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

    # Decide creature-vs-equipment FIRST, then roll rarity from the table that
    # belongs to that outcome — a creature uses its own steep table (so the crate's
    # absolute creature odds are 8% common + a thin tail), equipment keeps the
    # standard loot weights.
    if random.random() < constants.BIOCRATE_CREATURE_CHANCE:
        rarity = roll_rarity(constants.BIOCRATE_CREATURE_RARITY_WEIGHTS)
        creature = _roll_creature(user, rarity)
        return {"kind": "creature", "rarity": rarity, "creature": creature}
    rarity = roll_rarity()
    item = roll_equipment(user, rarity)
    return {"kind": "equipment", "rarity": rarity, "item": item}


@transaction.atomic
def open_diamond_box(user: User, tier: str) -> dict:
    """Diamond boxes always yield a creature (never equipment) — this is the "open
    a new monster with diamonds" path the gold Bio-Crate doesn't guarantee."""
    if tier not in constants.DIAMOND_BOX_TIERS:
        raise GameError("این نوع جعبه‌ی الماسی وجود نداره.")
    cfg = constants.DIAMOND_BOX_TIERS[tier]
    if user.diamonds < cfg["cost_diamonds"]:
        raise GameError(f"الماس کافی نداری! این جعبه {cfg['cost_diamonds']} الماس هزینه داره.")

    user.diamonds -= cfg["cost_diamonds"]
    user.save(update_fields=["diamonds"])

    rarity = roll_rarity(cfg["weights"])
    creature = _roll_creature(user, rarity)
    return {"kind": "creature", "rarity": rarity, "creature": creature, "tier": tier}

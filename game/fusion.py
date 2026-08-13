import random

from django.db import transaction

from bio_lab.models import Creature, User
from game import constants
from game.creature import GameError
from game.equipment import get_equipped_items


@transaction.atomic
def fuse(user: User, parent_a: Creature, parent_b: Creature) -> tuple[Creature, object | None]:
    """Burns both parents (gold cost, permanent deletion) and forges one new creature.
    Returns (child, inherited_item) — inherited_item is the Equipment moved onto the
    child if the FUSION_INHERIT_CHANCE roll hit and either parent had gear equipped,
    else None."""
    if parent_a.owner_id != user.id or parent_b.owner_id != user.id:
        raise GameError("هر دو موجود باید مال خودت باشن.")
    if parent_a.id == parent_b.id:
        raise GameError("نمی‌تونی یه موجود رو با خودش ترکیب کنی.")
    if user.coins < constants.FUSION_GOLD_COST:
        raise GameError(f"طلا کافی نداری! فیوژن {constants.FUSION_GOLD_COST} طلا هزینه داره.")

    user.coins -= constants.FUSION_GOLD_COST
    user.save(update_fields=["coins"])

    base_rarity = constants.higher_rarity(parent_a.rarity, parent_b.rarity)
    new_rarity = base_rarity
    if random.random() < constants.RARITY_UPGRADE_CHANCE.get(base_rarity, 0.0):
        new_rarity = constants.next_rarity(base_rarity)

    mult = constants.RARITY_STAT_MULTIPLIER[new_rarity]
    avg_level = (parent_a.level + parent_b.level) / 2
    star_level = min(constants.STAR_MAX, max(parent_a.star_level, parent_b.star_level) + 1)

    child = Creature.objects.create(
        owner=user,
        name=_fuse_name(parent_a.name, parent_b.name),
        element=random.choice([parent_a.element, parent_b.element]),
        rarity=new_rarity,
        star_level=star_level,
        base_hp=round((constants.STARTER_BASE_HP + avg_level * 4) * mult),
        base_atk=round((constants.STARTER_BASE_ATK + avg_level * 1.0) * mult),
        base_def=round((constants.STARTER_BASE_DEF + avg_level * 1.0) * mult),
        base_spd=round((constants.STARTER_BASE_SPD + avg_level * 0.6) * mult),
        is_active=True,
    )

    inherited_item = None
    if random.random() < constants.FUSION_INHERIT_CHANCE:
        parent_items = get_equipped_items(parent_a) + get_equipped_items(parent_b)
        if parent_items:
            inherited_item = random.choice(parent_items)
            inherited_item.equipped_on = child
            inherited_item.save(update_fields=["equipped_on"])

    parent_a.delete()
    parent_b.delete()
    # child is the new active creature — get_active_creature() assumes exactly one
    # is_active=True row per owner, so every other creature must yield the slot
    Creature.objects.filter(owner=user).exclude(id=child.id).update(is_active=False)

    return child, inherited_item


def _fuse_name(name_a: str, name_b: str) -> str:
    half_a = name_a[: max(2, len(name_a) // 2)]
    half_b = name_b[max(2, len(name_b) // 2) :]
    return (half_a + half_b).capitalize()

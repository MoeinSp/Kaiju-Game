import random

from bio_lab.models import Creature, Equipment, User
from game import constants
from game.creature import GameError


def get_equipped_items(creature: Creature) -> list[Equipment]:
    """Returns [] for an unsaved (pk-less) creature — e.g. hunt.py's ephemeral wild
    opponents, which only ever exist in memory for combat math and never own gear."""
    if creature.pk is None:
        return []
    return list(Equipment.objects.filter(equipped_on=creature))


def list_inventory(user: User) -> list[Equipment]:
    return list(Equipment.objects.filter(owner=user).order_by("slot", "-rarity", "-level"))


def equipment_bonus(item: Equipment) -> dict[str, float]:
    base = constants.EQUIPMENT_BASE_BONUS[item.slot]
    rarity_mult = constants.RARITY_STAT_MULTIPLIER[item.rarity]
    level_mult = 1 + (item.level - 1) * constants.EQUIPMENT_UPGRADE_BONUS_PCT
    return {stat: value * rarity_mult * level_mult for stat, value in base.items()}


def creature_equipment_bonus(equipped_items: list[Equipment]) -> dict[str, float]:
    total: dict[str, float] = {}
    for item in equipped_items:
        for stat, value in equipment_bonus(item).items():
            total[stat] = total.get(stat, 0) + value
    return total


def roll_equipment(owner: User, rarity: str) -> Equipment:
    """Creates a brand-new equipment piece of a given rarity with a random slot/template.
    Used by the Bio-Crate lootbox and by Fusion's equipment-inheritance roll."""
    slot = random.choice(constants.EQUIPMENT_SLOTS)
    template = random.choice(constants.EQUIPMENT_TEMPLATES[slot])
    return Equipment.objects.create(owner=owner, slot=slot, template_key=template, name=template, rarity=rarity)


def equip_item(user: User, creature: Creature, item_id: int) -> Equipment:
    try:
        item = Equipment.objects.get(id=item_id)
    except Equipment.DoesNotExist:
        raise GameError("این تجهیزات پیدا نشد.")
    if item.owner_id != user.id:
        raise GameError("این تجهیزات مال تو نیست.")
    if creature.owner_id != user.id:
        raise GameError("این موجود مال تو نیست.")
    Equipment.objects.filter(equipped_on=creature, slot=item.slot).update(equipped_on=None)
    item.equipped_on = creature
    item.save(update_fields=["equipped_on"])
    return item


def unequip_item(user: User, item_id: int) -> Equipment:
    try:
        item = Equipment.objects.get(id=item_id)
    except Equipment.DoesNotExist:
        raise GameError("این تجهیزات پیدا نشد.")
    if item.owner_id != user.id:
        raise GameError("این تجهیزات مال تو نیست.")
    item.equipped_on = None
    item.save(update_fields=["equipped_on"])
    return item


def upgrade_item(user: User, item_id: int, dupe_item_id: int) -> Equipment:
    try:
        item = Equipment.objects.get(id=item_id)
        dupe = Equipment.objects.get(id=dupe_item_id)
    except Equipment.DoesNotExist:
        raise GameError("این تجهیزات پیدا نشد.")
    if item.owner_id != user.id or dupe.owner_id != user.id:
        raise GameError("این تجهیزات مال تو نیست.")
    if item.id == dupe.id:
        raise GameError("باید یه تجهیزات تکراری متفاوت انتخاب کنی.")
    if item.level >= constants.EQUIPMENT_MAX_LEVEL:
        raise GameError(f"این تجهیزات به سقف +{constants.EQUIPMENT_MAX_LEVEL} رسیده.")
    if dupe.slot != item.slot or dupe.template_key != item.template_key or dupe.rarity != item.rarity:
        raise GameError("تجهیزات دوم باید هم‌نوع (اسلات/مدل/نایابی یکسان) باشه.")
    cost = constants.EQUIPMENT_UPGRADE_GOLD_COST * item.level
    if user.coins < cost:
        raise GameError(f"طلا کافی نداری! ارتقا {cost} طلا هزینه داره.")
    user.coins -= cost
    user.save(update_fields=["coins"])
    dupe.delete()
    item.level += 1
    item.save(update_fields=["level"])
    return item

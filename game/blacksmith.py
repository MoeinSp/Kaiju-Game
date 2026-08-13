import random

from django.db import transaction

from bio_lab.models import Equipment, User
from game import constants
from game.creature import GameError


def forge_preview(item: Equipment) -> dict:
    """Cost/risk of the next level, shown before the player commits."""
    target = item.level + 1
    return {
        "target_level": target,
        "cost": constants.forge_cost(item.level, item.rarity),
        "fail_chance": constants.forge_fail_chance(target),
        "at_max": item.level >= constants.EQUIPMENT_MAX_LEVEL,
    }


@transaction.atomic
def forge(user: User, item_id: int) -> dict:
    """Levels an item with gold alone — no duplicate required (that's what
    game.equipment.upgrade_item is for). The tradeoff is risk: past
    FORGE_SAFE_LEVEL an attempt can fail and burn the gold without a level, so
    feeding duplicates stays the safe-but-slow path and forging is the fast-but-
    risky one."""
    try:
        item = Equipment.objects.get(id=item_id)
    except Equipment.DoesNotExist:
        raise GameError("این تجهیزات پیدا نشد.")
    if item.owner_id != user.id:
        raise GameError("این تجهیزات مال تو نیست.")
    if item.level >= constants.EQUIPMENT_MAX_LEVEL:
        raise GameError(f"این تجهیزات به سقف +{constants.EQUIPMENT_MAX_LEVEL} رسیده.")

    preview = forge_preview(item)
    cost = preview["cost"]
    if user.coins < cost:
        raise GameError(f"طلا کافی نداری! این آهنگری {cost} طلا هزینه داره.")

    user.coins -= cost
    user.save(update_fields=["coins"])

    if random.random() < preview["fail_chance"]:
        # gold is spent either way — that's the whole risk of skipping duplicates
        return {"success": False, "item": item, "cost": cost, "fail_chance": preview["fail_chance"]}

    item.level += 1
    item.save(update_fields=["level"])
    return {"success": True, "item": item, "cost": cost, "fail_chance": preview["fail_chance"]}


def forgeable_items(user: User) -> list[Equipment]:
    return list(
        Equipment.objects.filter(owner=user, level__lt=constants.EQUIPMENT_MAX_LEVEL).order_by(
            "slot", "-rarity", "-level"
        )
    )

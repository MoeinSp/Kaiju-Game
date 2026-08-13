import random

from django.db import transaction

from bio_lab.models import Equipment, User
from game import constants
from game.buildings import building_level, is_built
from game.creature import GameError


def equipment_cap(user: User) -> int:
    """How high this player can level equipment right now. Each blacksmith level
    adds EQUIPMENT_LEVELS_PER_BLACKSMITH_LEVEL, so a level-1 forge caps items at +5
    and a maxed level-5 forge at +25. Returns 0 when the forge isn't built."""
    forge_level = building_level(user, "blacksmith")
    return min(
        constants.EQUIPMENT_MAX_LEVEL,
        forge_level * constants.EQUIPMENT_LEVELS_PER_BLACKSMITH_LEVEL,
    )


def assert_forge_available(user: User) -> None:
    if not is_built(user, "blacksmith"):
        raise GameError("اول باید ⚒ آهنگری رو از «🏗 ساختمون‌ها» بسازی.")


def forge_preview(item: Equipment, user: User | None = None) -> dict:
    """Cost/risk of the next level, shown before the player commits. `user` is
    optional only so older call sites keep working; pass it to get the real
    blacksmith-gated cap."""
    target = item.level + 1
    cap = equipment_cap(user) if user is not None else constants.EQUIPMENT_MAX_LEVEL
    return {
        "target_level": target,
        "cost": constants.forge_cost(item.level, item.rarity),
        "fail_chance": constants.forge_fail_chance(target),
        "cap": cap,
        "at_max": item.level >= cap,
    }


@transaction.atomic
def forge(user: User, item_id: int) -> dict:
    """Levels an item with gold alone — no duplicate required (that's what
    game.equipment.upgrade_item is for). The tradeoff is risk: past
    FORGE_SAFE_LEVEL an attempt can fail and burn the gold without a level, so
    feeding duplicates stays the safe-but-slow path and forging is the fast-but-
    risky one."""
    assert_forge_available(user)
    try:
        item = Equipment.objects.get(id=item_id)
    except Equipment.DoesNotExist:
        raise GameError("این تجهیزات پیدا نشد.")
    if item.owner_id != user.id:
        raise GameError("این تجهیزات مال تو نیست.")

    preview = forge_preview(item, user)
    if preview["at_max"]:
        raise GameError(
            f"این تجهیزات به سقف فعلی (+{preview['cap']}) رسیده — "
            "برای بالاتر رفتن باید ⚒ آهنگری رو ارتقا بدی."
        )
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
    """Only items below the player's current blacksmith-gated ceiling."""
    return list(
        Equipment.objects.filter(owner=user, level__lt=equipment_cap(user)).order_by(
            "slot", "-rarity", "-level"
        )
    )

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


def slot_loadout(user: User, creature: Creature) -> list[dict]:
    """Every equipment slot for this creature: what's in it, and what could be.

    One row per slot in constants.EQUIPMENT_SLOTS — **including empty ones**.
    Listing only equipped gear meant an empty slot was invisible, so a player
    with a spare weapon in their bag had no way to learn the slot existed. The
    empty rows are the point of this function.

    `candidates` excludes anything already equipped on *this* creature in this
    slot (that's `item`), but does include gear equipped on a *different*
    creature — equip_item moves it, which is usually what a player means when
    they pick it, and hiding it would look like the item had vanished.
    """
    if creature.pk is None:
        return []

    equipped = {i.slot: i for i in Equipment.objects.filter(equipped_on=creature)}
    pool: dict[str, list[Equipment]] = {}
    for item in (
        Equipment.objects.filter(owner=user)
        .exclude(equipped_on=creature)
        .select_related("equipped_on")
        .order_by("slot", "-level")
    ):
        pool.setdefault(item.slot, []).append(item)

    rows = []
    for slot in constants.EQUIPMENT_SLOTS:
        item = equipped.get(slot)
        rows.append(
            {
                "slot": slot,
                "label": constants.EQUIPMENT_SLOT_LABELS[slot],
                "item": item,
                "is_empty": item is None,
                "candidates": pool.get(slot, []),
            }
        )
    return rows


def equipment_bonus(item: Equipment) -> dict[str, float]:
    base = constants.EQUIPMENT_BASE_BONUS[item.slot]
    rarity_mult = constants.RARITY_STAT_MULTIPLIER[item.rarity]
    level_mult = 1 + (item.level - 1) * constants.EQUIPMENT_UPGRADE_BONUS_PCT
    return {stat: value * rarity_mult * level_mult for stat, value in base.items()}


def bonus_text(item: Equipment) -> str:
    """An item's bonuses as one readable clause, e.g. "حمله +۵، کریتیکال +۳٪".

    Lives here next to equipment_bonus() so every screen that shows gear renders
    it identically — and so the percent-vs-flat distinction is decided once."""
    parts = []
    for stat, value in equipment_bonus(item).items():
        if not value:
            continue
        label, is_percent = constants.EQUIPMENT_BONUS_LABELS.get(stat, (stat, False))
        parts.append(f"{label} +{value * 100:.0f}٪" if is_percent else f"{label} +{value:.0f}")
    return "، ".join(parts)


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

    # the blacksmith gates BOTH upgrade paths — this one (consume a duplicate) and
    # game.blacksmith.forge (pay gold) — so a forge-less player can't sidestep it
    from game.blacksmith import assert_forge_available, equipment_cap

    assert_forge_available(user)
    cap = equipment_cap(user)
    if item.level >= cap:
        raise GameError(f"این تجهیزات به سقف فعلی (+{cap}) رسیده — اول ⚒ آهنگری رو ارتقا بده.")
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


def _fuse_fail_chance(target: Equipment, sacrifice: Equipment) -> float:
    """Same-slot fusion odds: a sacrifice of equal-or-higher rarity, or a higher
    level, makes success more likely."""
    chance = constants.EQUIPMENT_FUSE_FAIL_CHANCE
    order = constants.RARITY_ORDER
    if order.index(sacrifice.rarity) >= order.index(target.rarity):
        chance -= 0.15
    chance -= 0.03 * max(0, sacrifice.level - 1)
    return max(0.05, min(0.6, chance))


def fuse_equipment(user: User, target_id: int, sacrifice_id: int) -> dict:
    """Sacrifice a SAME-SLOT item (sword into sword) to try to raise another by one
    level. Flexible (no exact-duplicate needed) but risky: on failure the sacrifice
    is still consumed and the level doesn't go up. Returns the outcome."""
    try:
        target = Equipment.objects.get(id=target_id)
        sacrifice = Equipment.objects.get(id=sacrifice_id)
    except Equipment.DoesNotExist:
        raise GameError("این تجهیزات پیدا نشد.")
    if target.owner_id != user.id or sacrifice.owner_id != user.id:
        raise GameError("این تجهیزات مال تو نیست.")
    if target.id == sacrifice.id:
        raise GameError("باید دو تجهیزات متفاوت انتخاب کنی.")
    if sacrifice.slot != target.slot:
        raise GameError("باید هم‌نوع باشن — مثلاً شمشیر با شمشیر.")

    from game.blacksmith import assert_forge_available, equipment_cap

    assert_forge_available(user)
    cap = equipment_cap(user)
    if target.level >= cap:
        raise GameError(f"این تجهیزات به سقف فعلی (+{cap}) رسیده — اول ⚒ آهنگری رو ارتقا بده.")

    fail_chance = _fuse_fail_chance(target, sacrifice)
    sacrifice.delete()  # the sacrifice is consumed whether it works or not
    success = random.random() >= fail_chance
    if success:
        target.level += 1
        target.save(update_fields=["level"])
    return {"success": success, "target": target, "fail_chance": fail_chance, "new_level": target.level}


def same_slot_candidates(user: User, target_id: int) -> list[Equipment]:
    """Other unequipped-preferred items in the same slot as `target`, usable as a
    fusion sacrifice (any rarity/model — just the slot must match)."""
    target = Equipment.objects.filter(id=target_id, owner=user).first()
    if target is None:
        return []
    return list(
        Equipment.objects.filter(owner=user, slot=target.slot)
        .exclude(id=target.id)
        .order_by("equipped_on", "rarity", "level")
    )

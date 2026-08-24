import datetime
import random

from django.db import transaction
from django.utils import timezone

from bio_lab.models import Creature, User
from game import constants


class GameError(Exception):
    """Raised for expected game-rule violations (not enough coins, cooldown, etc.)."""


def effective_stats(creature: Creature, equipped_items: list | None = None) -> dict[str, float]:
    """`equipped_items` must be pre-fetched by the caller (e.g. via
    game.equipment.get_equipped_items) — this function never queries the DB itself,
    so it stays safe to call from async Telegram-handler code as long as callers
    that need equipment bonuses fetch the list beforehand in sync context."""
    from game.equipment import creature_equipment_bonus

    bonus = creature_equipment_bonus(equipped_items) if equipped_items else {}
    # star_level is a fusion-generation prestige counter (never player-set — see
    # game.fusion.fuse) that scales the creature's own stats, not its gear's bonus
    star_mult = 1 + (creature.star_level - 1) * constants.STAR_STAT_BONUS_PCT
    return {
        "hp": round(creature.base_hp * star_mult) + round(bonus.get("hp", 0)),
        "atk": round((creature.base_atk + creature.fangs_lvl * constants.BODY_PARTS["fangs"]["bonus"]) * star_mult)
        + round(bonus.get("atk", 0)),
        "def": round((creature.base_def + creature.armor_lvl * constants.BODY_PARTS["armor"]["bonus"]) * star_mult)
        + round(bonus.get("def", 0)),
        "spd": round((creature.base_spd + creature.wings_lvl * constants.BODY_PARTS["wings"]["bonus"]) * star_mult)
        + round(bonus.get("spd", 0)),
        "poison": creature.poison_lvl * constants.BODY_PARTS["poison"]["bonus"] + round(bonus.get("poison", 0)),
        "crit_rate": constants.BASE_CRIT_CHANCE + bonus.get("crit_rate", 0),
        "lifesteal": constants.BASE_LIFESTEAL + bonus.get("lifesteal", 0),
    }


def create_starter_creature(owner: User) -> Creature:
    element = constants.random_element()
    creature = Creature.objects.create(
        owner=owner,
        name=constants.random_species_name(element),
        element=element,
    )
    # self-healing: callers only reach here when get_active_creature() found no
    # active row, but if that's because of a stale multi-active state rather than
    # zero creatures, this keeps the invariant (exactly one is_active=True per owner)
    Creature.objects.filter(owner=owner).exclude(id=creature.id).update(is_active=False)
    return creature


def add_xp(creature: Creature, amount: int) -> int:
    """Adds xp and applies level-ups in place. Caller is responsible for saving `creature`."""
    creature.xp += amount
    levels_gained = 0
    while creature.xp >= constants.xp_for_creature_level(creature.level):
        creature.xp -= constants.xp_for_creature_level(creature.level)
        creature.level += 1
        creature.base_hp += constants.LEVEL_UP_HP
        creature.base_atk += constants.LEVEL_UP_ATK
        creature.base_def += constants.LEVEL_UP_DEF
        creature.base_spd += constants.LEVEL_UP_SPD
        levels_gained += 1
    return levels_gained


def _devour_xp(sacrifice: Creature) -> int:
    """XP a target gains from devouring `sacrifice` — scales with the sacrifice's
    level, rarity and stars, so feeding a strong creature is worth more."""
    rarity_idx = constants.RARITY_ORDER.index(sacrifice.rarity) if sacrifice.rarity in constants.RARITY_ORDER else 0
    per_level = constants.CREATURE_XP_BASE + constants.CREATURE_XP_LINEAR
    mult = (1 + 0.3 * rarity_idx) * (1 + 0.5 * max(0, sacrifice.star_level - 1))
    return max(per_level, round(per_level * max(1, sacrifice.level) * mult))


def devour_candidates(user: User, target_id: int) -> list[Creature]:
    """Creatures that may be fed to `target_id`: any of the player's OTHER creatures
    that aren't currently active or busy (mining / breeding)."""
    from game.workers import creature_status

    out = []
    for c in Creature.objects.filter(owner=user).exclude(id=target_id).order_by("rarity", "level"):
        if c.is_active:
            continue
        if creature_status(user, c) is not None:
            continue
        out.append(c)
    return out


@transaction.atomic
def devour_creature(user: User, target_id: int, sacrifice_id: int) -> dict:
    """Feed one creature to another (fusion-without-stars): the sacrifice is consumed
    and the target gains XP (possibly levelling up). No same-name/same-star rule."""
    target = Creature.objects.filter(id=target_id, owner=user).first()
    sacrifice = Creature.objects.filter(id=sacrifice_id, owner=user).first()
    if target is None or sacrifice is None:
        raise GameError("این موجود توی کلکسیون تو نیست.")
    if target.id == sacrifice.id:
        raise GameError("نمی‌تونی یه موجود رو به خودش بدی.")
    if sacrifice.is_active:
        raise GameError("موجود فعال رو نمی‌شه قربانی کرد — اول یکی دیگه رو فعال کن.")
    from game.workers import creature_status

    status = creature_status(user, sacrifice)
    if status is not None:
        raise GameError(f"«{sacrifice.name}» الان مشغوله ({status}) — اول آزادش کن.")

    xp = _devour_xp(sacrifice)
    sac_name = sacrifice.name
    levels = add_xp(target, xp)
    target.save()
    sacrifice.delete()
    return {"xp": xp, "levels": levels, "sac_name": sac_name, "target": target, "new_level": target.level}


def feed(user: User, creature: Creature) -> int:
    if user.coins < constants.FEED_COST_COINS:
        raise GameError(f"طلا کافی نداری! هزینه تغذیه {constants.FEED_COST_COINS} طلا است.")
    user.coins -= constants.FEED_COST_COINS
    levels_gained = add_xp(creature, constants.FEED_XP_GAIN)
    user.save(update_fields=["coins"])
    creature.save()
    return levels_gained


def train(creature: Creature) -> int:
    now = timezone.now()
    if creature.last_trained_at is not None:
        elapsed = now - creature.last_trained_at
        cooldown = datetime.timedelta(hours=constants.TRAIN_COOLDOWN_HOURS)
        if elapsed < cooldown:
            remaining = cooldown - elapsed
            hours, remainder = divmod(int(remaining.total_seconds()), 3600)
            minutes = remainder // 60
            raise GameError(f"هیولات هنوز خسته‌ست، {hours} ساعت و {minutes} دقیقه دیگه صبر کن.")
    creature.last_trained_at = now
    levels_gained = add_xp(creature, constants.TRAIN_XP_GAIN)
    creature.save()
    return levels_gained


def apply_random_mutation(creature: Creature) -> tuple[str, int]:
    """Applies one free random stat bump (e.g. from a group mutation event). Returns (stat, bonus)."""
    stat = random.choice(list(constants.MUTATION_EVENT_STAT_LABELS))
    lo, hi = (
        constants.MUTATION_EVENT_HP_BONUS if stat == "base_hp" else constants.MUTATION_EVENT_OTHER_BONUS
    )
    bonus = random.randint(lo, hi)
    setattr(creature, stat, getattr(creature, stat) + bonus)
    creature.save(update_fields=[stat])
    return stat, bonus


def list_creatures(user: User) -> list[Creature]:
    return list(Creature.objects.filter(owner=user).order_by("id"))


def set_active_creature(user: User, creature_id: int) -> Creature:
    try:
        target = Creature.objects.get(id=creature_id)
    except Creature.DoesNotExist:
        raise GameError("این موجود توی کلکسیون تو نیست.")
    if target.owner_id != user.id:
        raise GameError("این موجود توی کلکسیون تو نیست.")
    # The other half of the "one job at a time" rule (game/workers.py owns the
    # first half). Without this a player could station a creature in a mine and
    # then make it active, and it would be both mining and fighting.
    # Imported lazily: game.workers imports this module.
    from game.workers import creature_status

    status = creature_status(user, target)
    if status is not None and not target.is_active:
        raise GameError(
            f"«{target.name}» الان مشغوله ({status}) — اول آزادش کن تا بتونی فعالش کنی."
        )
    Creature.objects.filter(owner=user).exclude(id=creature_id).update(is_active=False)
    target.is_active = True
    target.save(update_fields=["is_active"])
    return target


def upgrade_part(user: User, creature: Creature, part: str) -> int:
    if part not in constants.BODY_PARTS:
        raise GameError("این عضو وجود نداره.")
    current_level = getattr(creature, f"{part}_lvl")
    cost = constants.upgrade_cost(current_level)
    if user.coins < cost:
        raise GameError(f"طلا کافی نداری! ارتقای این عضو {cost} طلا هزینه داره.")
    user.coins -= cost
    setattr(creature, f"{part}_lvl", current_level + 1)
    user.save(update_fields=["coins"])
    creature.save()
    return current_level + 1

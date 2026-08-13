import datetime
import random

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Creature, User
from game import constants


class GameError(Exception):
    """Raised for expected game-rule violations (not enough coins, cooldown, etc.)."""


def effective_stats(creature: Creature) -> dict[str, int]:
    return {
        "hp": creature.base_hp,
        "atk": creature.base_atk + creature.fangs_lvl * constants.BODY_PARTS["fangs"]["bonus"],
        "def": creature.base_def + creature.armor_lvl * constants.BODY_PARTS["armor"]["bonus"],
        "spd": creature.base_spd + creature.wings_lvl * constants.BODY_PARTS["wings"]["bonus"],
        "poison": creature.poison_lvl * constants.BODY_PARTS["poison"]["bonus"],
    }


def create_starter_creature(session: Session, owner: User) -> Creature:
    element = constants.random_element()
    creature = Creature(
        owner_id=owner.id,
        name=constants.random_species_name(element),
        element=element,
    )
    session.add(creature)
    session.flush()
    return creature


def add_xp(creature: Creature, amount: int) -> int:
    """Adds xp and applies level-ups in place. Returns number of levels gained."""
    creature.xp += amount
    levels_gained = 0
    while creature.xp >= constants.XP_PER_LEVEL:
        creature.xp -= constants.XP_PER_LEVEL
        creature.level += 1
        creature.base_hp += constants.LEVEL_UP_HP
        creature.base_atk += constants.LEVEL_UP_ATK
        creature.base_def += constants.LEVEL_UP_DEF
        creature.base_spd += constants.LEVEL_UP_SPD
        levels_gained += 1
    return levels_gained


def feed(session: Session, user: User, creature: Creature) -> int:
    if user.coins < constants.FEED_COST_COINS:
        raise GameError(f"سکه کافی نداری! هزینه تغذیه {constants.FEED_COST_COINS} سکه است.")
    user.coins -= constants.FEED_COST_COINS
    levels_gained = add_xp(creature, constants.FEED_XP_GAIN)
    session.commit()
    return levels_gained


def train(session: Session, creature: Creature) -> int:
    now = datetime.datetime.utcnow()
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
    session.commit()
    return levels_gained


def apply_random_mutation(creature: Creature) -> tuple[str, int]:
    """Applies one free random stat bump (e.g. from a group mutation event). Returns (stat, bonus)."""
    stat = random.choice(list(constants.MUTATION_EVENT_STAT_LABELS))
    lo, hi = (
        constants.MUTATION_EVENT_HP_BONUS if stat == "base_hp" else constants.MUTATION_EVENT_OTHER_BONUS
    )
    bonus = random.randint(lo, hi)
    setattr(creature, stat, getattr(creature, stat) + bonus)
    return stat, bonus


def list_creatures(session: Session, user: User) -> list[Creature]:
    stmt = select(Creature).where(Creature.owner_id == user.id).order_by(Creature.id)
    return list(session.execute(stmt).scalars().all())


def set_active_creature(session: Session, user: User, creature_id: int) -> Creature:
    target = session.get(Creature, creature_id)
    if target is None or target.owner_id != user.id:
        raise GameError("این موجود توی کلکسیون تو نیست.")
    for creature in list_creatures(session, user):
        creature.is_active = creature.id == creature_id
    session.commit()
    return target


def upgrade_part(session: Session, user: User, creature: Creature, part: str) -> int:
    if part not in constants.BODY_PARTS:
        raise GameError("این عضو وجود نداره.")
    current_level = getattr(creature, f"{part}_lvl")
    cost = constants.upgrade_cost(current_level)
    if user.coins < cost:
        raise GameError(f"سکه کافی نداری! ارتقای این عضو {cost} سکه هزینه داره.")
    user.coins -= cost
    setattr(creature, f"{part}_lvl", current_level + 1)
    session.commit()
    return current_level + 1

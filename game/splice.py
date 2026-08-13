import random

from sqlalchemy.orm import Session

from db.models import Creature, User
from game import constants
from game.creature import GameError


def splice(session: Session, user: User, parent_a: Creature, parent_b: Creature) -> Creature:
    if parent_a.owner_id != user.id or parent_b.owner_id != user.id:
        raise GameError("هر دو موجود باید مال خودت باشن.")
    if parent_a.id == parent_b.id:
        raise GameError("نمی‌تونی یه موجود رو با خودش ترکیب کنی.")
    if user.dna_fragments < constants.SPLICE_DNA_COST:
        raise GameError(f"DNA کافی نداری! ترکیب {constants.SPLICE_DNA_COST} DNA Fragment هزینه داره.")

    user.dna_fragments -= constants.SPLICE_DNA_COST

    base_rarity = constants.higher_rarity(parent_a.rarity, parent_b.rarity)
    new_rarity = base_rarity
    if random.random() < constants.RARITY_UPGRADE_CHANCE.get(base_rarity, 0.0):
        new_rarity = constants.next_rarity(base_rarity)

    mult = constants.RARITY_STAT_MULTIPLIER[new_rarity]
    avg_level = (parent_a.level + parent_b.level) / 2

    child = Creature(
        owner_id=user.id,
        name=_fuse_name(parent_a.name, parent_b.name),
        element=random.choice([parent_a.element, parent_b.element]),
        rarity=new_rarity,
        base_hp=round((constants.STARTER_BASE_HP + avg_level * 4) * mult),
        base_atk=round((constants.STARTER_BASE_ATK + avg_level * 1.0) * mult),
        base_def=round((constants.STARTER_BASE_DEF + avg_level * 1.0) * mult),
        base_spd=round((constants.STARTER_BASE_SPD + avg_level * 0.6) * mult),
        is_active=False,
    )
    session.add(child)

    parent_a.is_active = False
    parent_b.is_active = False

    session.flush()
    child.is_active = True
    session.commit()
    return child


def _fuse_name(name_a: str, name_b: str) -> str:
    half_a = name_a[: max(2, len(name_a) // 2)]
    half_b = name_b[max(2, len(name_b) // 2) :]
    return (half_a + half_b).capitalize()

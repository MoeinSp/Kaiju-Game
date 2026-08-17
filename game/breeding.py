"""غار هیولا — the Monster Cave: two creatures lay an egg that hatches over time.

Deliberately shaped as the opposite trade to fusion (game/fusion.py):

* **fusion** burns both parents instantly for a guaranteed star-up. Costly,
  immediate, and it shrinks your collection.
* **propagation** keeps both parents but locks them up for hours, and pays out a
  *new* creature whose rarity is a roll. Cheap in creatures, expensive in time.

Because creatures are the scarce resource, the timers are long — the rarer the
parents, the longer the wait, so mass-producing legendaries isn't a thing you can
grind in an afternoon.

Offspring rarity starts at the better parent's tier and can climb from there. The
bonuses reward *matching* — same element, same species — and raw power, so a
considered pairing beats throwing two random creatures together.

Settlement is lazy, like every other timer in this codebase (see
game/buildings.py, game/energy.py): there's no background job. `collect()` is
what finishes a job, and the panel calls `ready()` to know whether to offer it.
"""

from __future__ import annotations

import datetime
import random

from django.db import transaction
from django.utils import timezone

from bio_lab.models import BreedingJob, Creature, User
from game import constants, lab
from game.buildings import is_built
from game.creature import GameError
from game.equipment import get_equipped_items
from game.workers import assert_free

# Propagation runs out of the same genetics hall that fusion uses. Deliberately
# not a new building type: the build-out pacing in constants.py is tuned to a
# specific 1-2 week total, and adding a seventh building would silently stretch
# it past the target.
BREEDING_BUILDING = "fusion_lab"


def assert_available(user: User) -> None:
    if not is_built(user, BREEDING_BUILDING):
        label = constants.BUILDING_LABELS[BREEDING_BUILDING]
        raise GameError(f"اول باید {label} رو از «🏗 ساختمون‌ها» بسازی.")


def active_job(user: User) -> BreedingJob | None:
    return BreedingJob.objects.filter(owner=user).select_related("parent_a", "parent_b").first()


def duration_minutes(parent_a: Creature, parent_b: Creature) -> int:
    """Keyed to the *better* parent's rarity — pairing a legendary with a common
    still costs legendary time, so rarity can't be laundered through a cheap
    partner."""
    rarity = constants.higher_rarity(parent_a.rarity, parent_b.rarity)
    return constants.BREEDING_MINUTES[rarity]


def dna_cost(parent_a: Creature, parent_b: Creature) -> int:
    rarity = constants.higher_rarity(parent_a.rarity, parent_b.rarity)
    return constants.BREEDING_DNA_COST[rarity]


def _power(creature: Creature) -> int:
    return creature.base_hp + creature.base_atk + creature.base_def + creature.base_spd


def upgrade_chance(parent_a: Creature, parent_b: Creature) -> float:
    """Probability the offspring lands one rarity tier above its better parent.

    Starts from the shared RARITY_UPGRADE_CHANCE table and adds bonuses for a
    deliberate pairing. Capped, so even a perfect match is a roll, not a
    guarantee — otherwise the best strategy collapses to one pairing repeated."""
    base_rarity = constants.higher_rarity(parent_a.rarity, parent_b.rarity)
    chance = constants.RARITY_UPGRADE_CHANCE.get(base_rarity, 0.0)
    if chance <= 0:
        return 0.0  # mythic has nowhere to climb
    if parent_a.element == parent_b.element:
        chance += constants.BREEDING_SAME_ELEMENT_BONUS
    if parent_a.name == parent_b.name:
        chance += constants.BREEDING_SAME_SPECIES_BONUS
    combined = _power(parent_a) + _power(parent_b)
    chance += min(
        constants.BREEDING_POWER_BONUS_CAP,
        combined / constants.BREEDING_POWER_PER_BONUS_POINT * 0.01,
    )
    return min(constants.BREEDING_MAX_UPGRADE_CHANCE, chance)


def preview(user: User, parent_a: Creature, parent_b: Creature) -> dict:
    """Everything the confirmation screen needs, without starting anything."""
    return {
        "minutes": duration_minutes(parent_a, parent_b),
        "dna": dna_cost(parent_a, parent_b),
        "base_rarity": constants.higher_rarity(parent_a.rarity, parent_b.rarity),
        "upgrade_chance": upgrade_chance(parent_a, parent_b),
        "same_element": parent_a.element == parent_b.element,
        "same_species": parent_a.name == parent_b.name,
    }


@transaction.atomic
def start(user: User, parent_a: Creature, parent_b: Creature) -> BreedingJob:
    assert_available(user)
    if parent_a.id == parent_b.id:
        raise GameError("یه موجود نمی‌تونه با خودش جفت بشه — دو تای متفاوت انتخاب کن.")
    if BreedingJob.objects.filter(owner=user).exists():
        raise GameError("همین الان یه تخم توی غاره — صبر کن سر باز کنه.")

    # both parents must be idle: not active, not mining, not already in the cave
    assert_free(user, parent_a, for_action="بفرستی توی غار هیولا")
    assert_free(user, parent_b, for_action="بفرستی توی غار هیولا")

    cost = dna_cost(parent_a, parent_b)
    if user.dna_fragments < cost:
        raise GameError(f"DNA کافی نداری! این تخم {cost} DNA لازم داره.")
    user.dna_fragments -= cost
    user.save(update_fields=["dna_fragments"])

    minutes = duration_minutes(parent_a, parent_b)
    return BreedingJob.objects.create(
        owner=user,
        parent_a=parent_a,
        parent_b=parent_b,
        finishes_at=timezone.now() + datetime.timedelta(minutes=minutes),
    )


def ready(job: BreedingJob) -> bool:
    return timezone.now() >= job.finishes_at


def seconds_left(job: BreedingJob) -> int:
    return max(0, int((job.finishes_at - timezone.now()).total_seconds()))


@transaction.atomic
def collect(user: User) -> tuple[Creature, dict]:
    """Finish a completed job and hatch the offspring.

    Returns (child, info) where info explains the roll, so the result screen can
    tell the player *why* they got what they got rather than just showing it."""
    job = active_job(user)
    if job is None:
        raise GameError("هیچ تخمی توی غار نیست.")
    if not ready(job):
        raise GameError("تخم هنوز سر باز نکرده — صبر کن تایمرش تموم بشه.")

    parent_a, parent_b = job.parent_a, job.parent_b
    base_rarity = constants.higher_rarity(parent_a.rarity, parent_b.rarity)
    chance = upgrade_chance(parent_a, parent_b)
    upgraded = random.random() < chance
    rarity = constants.next_rarity(base_rarity) if upgraded else base_rarity

    # the child is one of the two species, never a blend — `name` is the fusion
    # identity key, so inventing a hybrid name would create a species that can
    # never find a fusion partner
    parent = random.choice([parent_a, parent_b])
    mult = constants.RARITY_STAT_MULTIPLIER[rarity]
    level = max(1, round((parent_a.level + parent_b.level) / 2 * constants.BREEDING_LEVEL_INHERIT))

    child = Creature.objects.create(
        owner=user,
        name=parent.name,
        element=parent.element,
        rarity=rarity,
        star_level=1,  # stars come only from fusion — propagation never grants them
        level=level,
        xp=0,
        base_hp=round((constants.STARTER_BASE_HP + level * 4) * mult),
        base_atk=round((constants.STARTER_BASE_ATK + level * 1.0) * mult),
        base_def=round((constants.STARTER_BASE_DEF + level * 1.0) * mult),
        base_spd=round((constants.STARTER_BASE_SPD + level * 0.6) * mult),
        is_active=False,  # single-active-creature rule
    )

    job.delete()
    lab.award(user, "breeding")
    return child, {
        "base_rarity": base_rarity,
        "rarity": rarity,
        "upgraded": upgraded,
        "chance": chance,
        "level": level,
        "parents": (parent_a.name, parent_b.name),
    }


@transaction.atomic
def cancel(user: User) -> BreedingJob:
    """Abandon a job. The DNA is not refunded — otherwise a player could park two
    creatures whenever they weren't using them and cancel for free."""
    job = active_job(user)
    if job is None:
        raise GameError("هیچ تخمی توی غار نیست.")
    job.delete()
    return job


def parent_candidates(user: User, exclude_id: int | None = None) -> list[Creature]:
    """Idle creatures eligible as a parent. Equipment is irrelevant here, but the
    caller often wants power, so sort by level like every other picker."""
    from game.workers import free_creatures

    return [c for c in free_creatures(user) if c.id != exclude_id]


def creature_power(creature: Creature) -> int:
    """Public helper so screens can rank candidates the same way the roll does."""
    from game.creature import effective_stats

    stats = effective_stats(creature, get_equipped_items(creature))
    return round(stats["hp"] + stats["atk"] + stats["def"] + stats["spd"])

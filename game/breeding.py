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

Two phases, decoupled so the parents aren't hostage to the whole wait:

* **mating** (BreedingJob) — the two parents are busy in the cave. `start()`
  begins it; when `ready()`, `lay_egg()` (or `finish_cave_with_diamonds()`) frees
  the parents and lays an egg.
* **hatching** (Egg) — the laid egg incubates on its own. Because the parents are
  already free, the player can send a new pair into the cave immediately, so
  several eggs can incubate at once. `hatch()` (or `finish_egg_with_diamonds()`)
  turns a ready egg into a creature.

Settlement is lazy, like every other timer in this codebase (see
game/buildings.py, game/energy.py): there's no background job — the panel polls
`ready()` / `egg_ready()` and the collect actions are what finish things.
"""

from __future__ import annotations

import datetime
import random

from django.db import transaction
from django.utils import timezone

from bio_lab.models import BreedingJob, Creature, Egg, User
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


def mating_minutes(parent_a: Creature, parent_b: Creature) -> int:
    """Phase-1 (mating) duration, keyed to the *better* parent's rarity — pairing a
    legendary with a common still costs legendary time, so rarity can't be
    laundered through a cheap partner."""
    rarity = constants.higher_rarity(parent_a.rarity, parent_b.rarity)
    return constants.CAVE_MATING_MINUTES[rarity]


def hatch_minutes(rarity: str) -> int:
    """Phase-2 (egg incubation) duration for an egg of the given base rarity."""
    return constants.EGG_HATCH_MINUTES[rarity]


def dna_cost(parent_a: Creature, parent_b: Creature) -> int:
    rarity = constants.higher_rarity(parent_a.rarity, parent_b.rarity)
    return constants.BREEDING_DNA_COST[rarity]


def _power(creature: Creature) -> int:
    from game.creature import creature_power

    return creature_power(creature)


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


def active_eggs(user: User) -> list[Egg]:
    """Every egg this player has incubating right now (ordered soonest-first)."""
    return list(Egg.objects.filter(owner=user))


def preview(user: User, parent_a: Creature, parent_b: Creature) -> dict:
    """Everything the confirmation screen needs, without starting anything."""
    return {
        "mating_minutes": mating_minutes(parent_a, parent_b),
        "hatch_minutes": hatch_minutes(constants.higher_rarity(parent_a.rarity, parent_b.rarity)),
        "dna": dna_cost(parent_a, parent_b),
        "base_rarity": constants.higher_rarity(parent_a.rarity, parent_b.rarity),
        "upgrade_chance": upgrade_chance(parent_a, parent_b),
        "same_element": parent_a.element == parent_b.element,
        "same_species": parent_a.name == parent_b.name,
    }


@transaction.atomic
def start(user: User, parent_a: Creature, parent_b: Creature) -> BreedingJob:
    """Phase 1: send two parents into the cave to mate. They're locked until the
    mating timer finishes, at which point lay_egg() frees them and lays an egg."""
    assert_available(user)
    if parent_a.id == parent_b.id:
        raise GameError("یه موجود نمی‌تونه با خودش جفت بشه — دو تای متفاوت انتخاب کن.")
    if BreedingJob.objects.filter(owner=user).exists():
        raise GameError("همین الان یه جفت توی غارن — صبر کن تخم بذارن، بعد جفت بعدی رو بفرست.")

    # both parents must be idle: not active, not mining, not already in the cave
    assert_free(user, parent_a, for_action="بفرستی توی غار هیولا")
    assert_free(user, parent_b, for_action="بفرستی توی غار هیولا")

    cost = dna_cost(parent_a, parent_b)
    if user.dna_fragments < cost:
        raise GameError(f"DNA کافی نداری! این جفت‌گیری {cost} DNA لازم داره.")
    user.dna_fragments -= cost
    user.save(update_fields=["dna_fragments"])

    minutes = mating_minutes(parent_a, parent_b)
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


def _lay_egg_from(user: User, job: BreedingJob) -> Egg:
    """Turn a finished mating job into an incubating egg and free the parents.

    The recipe (base rarity, upgrade odds, both parents, inherited level) is
    frozen onto the Egg here, so hatching later doesn't care what happens to the
    parents in the meantime."""
    parent_a, parent_b = job.parent_a, job.parent_b
    base_rarity = constants.higher_rarity(parent_a.rarity, parent_b.rarity)
    egg = Egg.objects.create(
        owner=user,
        base_rarity=base_rarity,
        upgrade_chance=upgrade_chance(parent_a, parent_b),
        parent_a_name=parent_a.name,
        parent_a_element=parent_a.element,
        parent_b_name=parent_b.name,
        parent_b_element=parent_b.element,
        inherit_level=max(1, round((parent_a.level + parent_b.level) / 2 * constants.BREEDING_LEVEL_INHERIT)),
        finishes_at=timezone.now() + datetime.timedelta(minutes=hatch_minutes(base_rarity)),
    )
    job.delete()  # frees both parents — the cave is open again
    return egg


@transaction.atomic
def lay_egg(user: User) -> Egg:
    """Phase 1 → 2: the mating is done, so lay the egg and free the parents."""
    job = active_job(user)
    if job is None:
        raise GameError("هیچ جفتی توی غار نیست.")
    if not ready(job):
        raise GameError("هنوز جفت‌گیری تموم نشده — صبر کن تایمرش تموم بشه.")
    return _lay_egg_from(user, job)


def egg_ready(egg: Egg) -> bool:
    return timezone.now() >= egg.finishes_at


def egg_seconds_left(egg: Egg) -> int:
    return max(0, int((egg.finishes_at - timezone.now()).total_seconds()))


@transaction.atomic
def hatch(user: User, egg_id: int) -> tuple[Creature, dict]:
    """Phase 2 → done: hatch a ready egg into a creature. The rarity/species roll
    happens HERE, from the recipe frozen on the egg — that's what keeps the egg's
    contents a genuine mystery until this moment."""
    egg = Egg.objects.filter(id=egg_id, owner=user).first()
    if egg is None:
        raise GameError("این تخم پیدا نشد.")
    if not egg_ready(egg):
        raise GameError("تخم هنوز سر باز نکرده — صبر کن تایمرش تموم بشه.")

    upgraded = random.random() < egg.upgrade_chance
    rarity = constants.next_rarity(egg.base_rarity) if upgraded else egg.base_rarity

    # the child is one of the two parent species, never a blend — `name` is the
    # fusion identity key, so a hybrid name would create an unfuseable species
    if random.random() < 0.5:
        name, element = egg.parent_a_name, egg.parent_a_element
    else:
        name, element = egg.parent_b_name, egg.parent_b_element
    level = egg.inherit_level
    mult = constants.RARITY_STAT_MULTIPLIER[rarity]

    child = Creature.objects.create(
        owner=user,
        name=name,
        element=element,
        rarity=rarity,
        star_level=1,  # stars come only from fusion — the cave never grants them
        level=level,
        xp=0,
        base_hp=round((constants.STARTER_BASE_HP + level * 4) * mult),
        base_atk=round((constants.STARTER_BASE_ATK + level * 1.0) * mult),
        base_def=round((constants.STARTER_BASE_DEF + level * 1.0) * mult),
        base_spd=round((constants.STARTER_BASE_SPD + level * 0.6) * mult),
        is_active=False,  # single-active-creature rule
    )

    parents = (egg.parent_a_name, egg.parent_b_name)
    egg.delete()
    lab.award(user, "breeding")
    return child, {
        "base_rarity": egg.base_rarity,
        "rarity": rarity,
        "upgraded": upgraded,
        "level": level,
        "parents": parents,
    }


def cave_finish_price(job: BreedingJob) -> int:
    return constants.diamond_finish_cost((job.finishes_at - timezone.now()).total_seconds())


def egg_finish_price(egg: Egg) -> int:
    return constants.diamond_finish_cost((egg.finishes_at - timezone.now()).total_seconds())


@transaction.atomic
def finish_cave_with_diamonds(user: User) -> Egg:
    """Pay diamonds to end the mating right now and lay the egg. Priced from the
    time still left, like every other diamond-finish in the game."""
    job = active_job(user)
    if job is None:
        raise GameError("هیچ جفتی توی غار نیست.")
    if ready(job):
        return _lay_egg_from(user, job)  # already done — just lay it, no charge
    cost = cave_finish_price(job)
    if user.diamonds < cost:
        raise GameError(f"الماس کافی نداری! فوری‌کردن جفت‌گیری {cost} الماس می‌خواد.")
    user.diamonds -= cost
    user.save(update_fields=["diamonds"])
    return _lay_egg_from(user, job)


@transaction.atomic
def finish_egg_with_diamonds(user: User, egg_id: int) -> Egg:
    """Pay diamonds to make an egg ready to hatch right now."""
    egg = Egg.objects.filter(id=egg_id, owner=user).first()
    if egg is None:
        raise GameError("این تخم پیدا نشد.")
    if egg_ready(egg):
        return egg
    cost = egg_finish_price(egg)
    if user.diamonds < cost:
        raise GameError(f"الماس کافی نداری! فوری‌کردن این تخم {cost} الماس می‌خواد.")
    user.diamonds -= cost
    egg.finishes_at = timezone.now()
    user.save(update_fields=["diamonds"])
    egg.save(update_fields=["finishes_at"])
    return egg


@transaction.atomic
def cancel(user: User) -> BreedingJob:
    """Abandon the mating in progress. The DNA is not refunded — otherwise a
    player could park two creatures whenever they weren't using them and cancel
    for free. (Laid eggs can't be cancelled; they only hatch.)"""
    job = active_job(user)
    if job is None:
        raise GameError("هیچ جفتی توی غار نیست.")
    job.delete()
    return job


def parent_candidates(user: User, exclude_id: int | None = None) -> list[Creature]:
    """Idle creatures eligible as a parent. Equipment is irrelevant here, but the
    caller often wants power, so sort by level like every other picker."""
    from game.workers import free_creatures

    return [c for c in free_creatures(user) if c.id != exclude_id]


def creature_power(creature: Creature) -> int:
    """Public helper so screens can rank candidates the same way the roll does."""
    from game.creature import creature_power as _cp

    return _cp(creature, get_equipped_items(creature))

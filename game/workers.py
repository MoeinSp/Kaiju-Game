"""Stationing creatures in production buildings, and the one rule that keeps
every "is this creature busy?" question answerable in one place.

A creature can be doing exactly one thing at a time:

* being the **active** creature (it fights hunts, arena raids and duels)
* **working** a production building (game/buildings.py reads the bonus)
* **breeding** (game/breeding.py)

`assert_free()` is the single gate. Without it each feature would grow its own
half of the check and they'd disagree — a creature could end up mining while
also being the one sent into the arena, which is exactly the overlap the design
forbids. Both directions are enforced: a busy creature can't be made active, and
the active creature can't be put to work.
"""

from __future__ import annotations

from bio_lab.models import BreedingJob, Building, Creature, CreatureAssignment, User
from game import constants
from game.creature import GameError


def worker_slots(building: Building) -> int:
    """How many creatures a building can host: its level.

    A level-0 (unbuilt) building holds nobody, and only producers take workers —
    the main hall, forge and fusion lab are gates, not mines."""
    if building.level <= 0 or building.building_type not in constants.BUILDING_PRODUCTION:
        return 0
    return building.level


def assigned_creatures(building: Building) -> list[Creature]:
    return [
        a.creature
        for a in CreatureAssignment.objects.filter(building=building)
        .select_related("creature")
        .order_by("-creature__level")
    ]


def worker_bonus(building: Building) -> float:
    """Extra production from the creatures stationed here, as a multiplier
    addend: 0.0 means "no help", 0.4 means "+40% output".

    Scales with creature *level*, so stationing a well-raised creature is
    meaningfully better than parking a fresh one — the point of the feature.
    Capped so a late-game player can't turn a mine into their whole economy."""
    # each worker contributes level × its rarity multiplier, so a rare/mythic miner
    # is worth several commons of the same level
    total = sum(
        c.level * constants.WORKER_RARITY_MULT.get(c.rarity, 1.0)
        for c in assigned_creatures(building)
    )
    return min(
        constants.WORKER_BONUS_CAP,
        total * constants.WORKER_BONUS_PER_CREATURE_LEVEL,
    )


def breeding_job(user: User) -> BreedingJob | None:
    return BreedingJob.objects.filter(owner=user).select_related("parent_a", "parent_b").first()


def busy_creature_ids(user: User) -> set[int]:
    """Every creature that can't be reassigned right now — working or breeding."""
    ids = set(
        CreatureAssignment.objects.filter(creature__owner=user).values_list("creature_id", flat=True)
    )
    job = BreedingJob.objects.filter(owner=user).first()
    if job is not None:
        ids.update({job.parent_a_id, job.parent_b_id})
    return ids


def creature_status(user: User, creature: Creature) -> str | None:
    """A short Persian label for why this creature is unavailable, or None when
    it's free. Used by pickers so an unavailable creature is explained rather
    than silently missing."""
    if creature.is_active:
        return "🟢 موجود فعال"
    assignment = CreatureAssignment.objects.filter(creature=creature).select_related("building").first()
    if assignment is not None:
        return f"⛏ در {constants.BUILDING_LABELS[assignment.building.building_type]}"
    job = BreedingJob.objects.filter(owner=user).first()
    if job is not None and creature.id in (job.parent_a_id, job.parent_b_id):
        return "🥚 توی غار هیولا"
    return None


def assert_free(user: User, creature: Creature, *, for_action: str) -> None:
    """Raise unless `creature` is idle. `for_action` only shapes the message."""
    if creature.owner_id != user.id:
        raise GameError("این موجود مال تو نیست.")
    status = creature_status(user, creature)
    if status is None:
        return
    if creature.is_active:
        raise GameError(
            f"موجود فعالت رو نمی‌تونی {for_action}. اول یکی دیگه رو پیش‌فرض کن."
        )
    raise GameError(f"«{creature.name}» الان مشغوله ({status}) — نمی‌تونی {for_action}.")


def assign(user: User, building: Building, creature: Creature) -> int:
    if building.owner_id != user.id:
        raise GameError("این ساختمون مال تو نیست.")
    slots = worker_slots(building)
    if slots <= 0:
        raise GameError("این ساختمون کارگر قبول نمی‌کنه — فقط معدن‌ها و آزمایشگاه‌ها کارگر می‌گیرن.")
    if CreatureAssignment.objects.filter(building=building).count() >= slots:
        label = constants.BUILDING_LABELS[building.building_type]
        raise GameError(
            f"{label} پره ({slots} جایگاه). برای جای بیشتر باید ساختمون رو ارتقا بدی."
        )
    assert_free(user, creature, for_action="بفرستی سر کار")
    # bank whatever's accrued so far at the CURRENT bonus before the new worker's
    # bonus takes effect — stops a strong worker retroactively boosting past hours.
    # Returns the banked amount so the UI can tell the player it was collected (not
    # lost) — the "moving a worker in zeroed my mine" confusion.
    from game.buildings import bank_pending

    banked = bank_pending(user, building)
    CreatureAssignment.objects.create(building=building, creature=creature)
    return banked


def unassign(user: User, creature: Creature) -> tuple[Building, int]:
    assignment = (
        CreatureAssignment.objects.filter(creature=creature, creature__owner=user)
        .select_related("building")
        .first()
    )
    if assignment is None:
        raise GameError("این موجود جایی مشغول کار نیست.")
    building = assignment.building
    from game.buildings import bank_pending

    banked = bank_pending(user, building)  # bank at the with-worker bonus before it drops
    assignment.delete()
    return building, banked


def free_creatures(user: User) -> list[Creature]:
    """Idle creatures, RAREST-then-strongest first — what any "pick a creature" list
    should offer, so the best options sit on the first page. Excludes the active one
    and anything already working or breeding."""
    busy = busy_creature_ids(user)
    rank = {r: i for i, r in enumerate(constants.RARITY_ORDER)}
    free = [c for c in Creature.objects.filter(owner=user, is_active=False) if c.id not in busy]
    return sorted(free, key=lambda c: (rank.get(c.rarity, 0), c.star_level, c.level, c.id), reverse=True)

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


def creature_mine_influence(creature: Creature, building: Building | None = None) -> float:
    """One stationed kaiju's production bonus (a multiplier addend). Scales with the
    kaiju's power (gear included) from its per-rarity floor up to +1000% for a maxed
    mythic — see constants.mine_influence. When `building` is given, its per-building
    `influence_mult` is applied (the diamond collector scales every kaiju WAY down so
    the mine stays tightly bounded); gold/DNA use 1.0."""
    from game.creature import creature_power
    from game.equipment import get_equipped_items

    power = creature_power(creature, get_equipped_items(creature))
    base = constants.mine_influence(creature.rarity, power)
    if building is not None:
        mult = constants.BUILDING_PRODUCTION.get(building.building_type, {}).get("influence_mult", 1.0)
        base *= mult
    return base


def worker_bonus(building: Building) -> float:
    """Extra production from the creatures stationed here, as a multiplier
    addend: 0.0 means "no help", 0.4 means "+40% output".

    Each stationed kaiju contributes creature_mine_influence() — a per-rarity floor
    that climbs with the kaiju's power. The mine's total is the sum across every
    stationed kaiju, so stronger AND more kaiju both raise output. A mine with an
    explicit `worker_bonus_cap` (the diamond collector) is clamped to that ceiling —
    gold/DNA stay uncapped."""
    cfg = constants.BUILDING_PRODUCTION.get(building.building_type, {})
    total = sum(creature_mine_influence(c, building) for c in assigned_creatures(building))
    cap = cfg.get("worker_bonus_cap")  # None for gold/DNA → no ceiling
    return total if cap is None else min(cap, total)


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


def assign(user: User, building: Building, creature: Creature) -> None:
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
    # LOCK the accrual-so-far at the current rate (keeps the pending IN the mine, never
    # collected/lost on a worker swap) before the new worker's bonus takes effect — so a
    # strong worker can't retro-boost hours already earned.
    from game.buildings import lock_pending

    lock_pending(building)
    CreatureAssignment.objects.create(building=building, creature=creature)


def unassign(user: User, creature: Creature) -> Building:
    assignment = (
        CreatureAssignment.objects.filter(creature=creature, creature__owner=user)
        .select_related("building")
        .first()
    )
    if assignment is None:
        raise GameError("این موجود جایی مشغول کار نیست.")
    building = assignment.building
    from game.buildings import lock_pending

    lock_pending(building)  # keep the pending in the mine at the with-worker rate before it drops
    assignment.delete()
    return building


def free_creatures(user: User) -> list[Creature]:
    """Idle creatures, RAREST-then-strongest first — what any "pick a creature" list
    should offer, so the best options sit on the first page. Excludes the active one
    and anything already working or breeding."""
    busy = busy_creature_ids(user)
    rank = {r: i for i, r in enumerate(constants.RARITY_ORDER)}
    free = [c for c in Creature.objects.filter(owner=user, is_active=False) if c.id not in busy]
    return sorted(free, key=lambda c: (rank.get(c.rarity, 0), c.star_level, c.level, c.id), reverse=True)

import datetime
import math
import random

from django.db import IntegrityError, transaction
from django.utils import timezone

from bio_lab.models import Building, BuildingUpgrade, SpeedupCard, User
from game import constants, lab
from game.creature import GameError, InsufficientGoldError

# small bonus chance (separate from the daily wheel's guaranteed prize) attached to
# a handful of natural "win" moments — duel wins, raid kills, guardian defenses.
# Deliberately rare and small-denomination: building upgrades are the game's main
# time-gate, so speed-up cards must stay scarce or the whole build pacing collapses.
ACTIVITY_SPEEDUP_CHANCE = 0.006  # much rarer (was 0.02) — building time is the core gate
ACTIVITY_SPEEDUP_CHOICES = [(1, 85), (5, 14), (30, 1)]  # (minutes, weight) — big cards now very rare


def maybe_award_speedup_card(user: User) -> int | None:
    """Returns the minutes value awarded, or None if the roll missed."""
    if random.random() >= ACTIVITY_SPEEDUP_CHANCE:
        return None
    minutes = random.choices(
        [m for m, _ in ACTIVITY_SPEEDUP_CHOICES], weights=[w for _, w in ACTIVITY_SPEEDUP_CHOICES], k=1
    )[0]
    grant_speedup_card(user, minutes, count=1)
    return minutes


def get_or_create_buildings(user: User) -> list[Building]:
    """Seeds one row per building type. Everything starts at level 0 ("not built")
    except the main hall, which a player always owns — otherwise there'd be nothing
    to gate the very first construction against."""
    buildings = []
    for building_type in constants.BUILDING_TYPES:
        default_level = 1 if building_type == constants.MAIN_BUILDING else 0
        try:
            with transaction.atomic():
                building, _ = Building.objects.get_or_create(
                    owner=user,
                    building_type=building_type,
                    defaults={"level": default_level},
                )
        except IntegrityError:
            # PK sequence can lag after restores/imports; unique (owner, type) may
            # already exist from a concurrent request. Re-fetch or retry once.
            building = Building.objects.filter(
                owner=user, building_type=building_type
            ).first()
            if building is None:
                with transaction.atomic():
                    building, _ = Building.objects.get_or_create(
                        owner=user,
                        building_type=building_type,
                        defaults={"level": default_level},
                    )
        buildings.append(building)
    enforce_building_level_caps(user)
    by_type = {
        b.building_type: b
        for b in Building.objects.filter(owner=user, building_type__in=constants.BUILDING_TYPES)
    }
    return [by_type[t] for t in constants.BUILDING_TYPES if t in by_type]


def building_level(user: User, building_type: str) -> int:
    """0 when the building doesn't exist yet or hasn't been constructed."""
    building = Building.objects.filter(owner=user, building_type=building_type).first()
    return building.level if building is not None else 0


def main_hall_level(user: User) -> int:
    return building_level(user, constants.MAIN_BUILDING)


def max_level_for(user: User, building_type: str) -> int:
    """سقف سطح هر ساختمون.

    - تالار مِهر: تا BUILDING_MAX_LEVEL
    - بقیه: حداکثر برابر سطح فعلی تالار مِهر (جلوتر از تالار نمی‌روند؛
      پس لول آخر مطلق فقط وقتی در دسترس است که خود تالار به سقف رسیده باشد)
    """
    if building_type == constants.MAIN_BUILDING:
        return constants.BUILDING_MAX_LEVEL
    hall = main_hall_level(user)
    return min(constants.BUILDING_MAX_LEVEL, hall)


def enforce_building_level_caps(user: User) -> int:
    """اگر ساختمونی از سقف تالار جلو زده (دیتای قدیمی/باگ)، به سقف برش می‌دهد."""
    fixed = 0
    hall = main_hall_level(user)
    for building in Building.objects.filter(owner=user).exclude(
        building_type=constants.MAIN_BUILDING
    ):
        cap = min(constants.BUILDING_MAX_LEVEL, hall)
        if building.level > cap:
            building.level = cap
            building.save(update_fields=["level"])
            fixed += 1
    return fixed


def unlock_level_for(building_type: str) -> int:
    """Main-hall level needed before this building can be constructed at all."""
    return constants.BUILDING_UNLOCK_HALL_LEVEL.get(building_type, 1)


def is_unlocked(user: User, building_type: str) -> bool:
    if building_type == constants.MAIN_BUILDING:
        return True
    return main_hall_level(user) >= unlock_level_for(building_type)


def star_cap(user: User) -> int:
    """The highest star a player can fuse a creature to = their 🔮 تالار ادغام level.
    Fusing to N★ needs the fusion lab at level N (2★ → level 2, 3★ → level 3, …).
    Returns 1 when the lab isn't built (level 0), so a lone 1★ creature can't fuse
    until the lab is raised to level 2. The lab's own level is already capped by the
    main hall, so this stays within the overall progression."""
    return min(constants.STAR_MAX, max(1, building_level(user, "fusion_lab")))


def is_built(user: User, building_type: str) -> bool:
    return building_level(user, building_type) > 0


def produces(building_type: str) -> bool:
    return building_type in constants.BUILDING_PRODUCTION


def production_rate(building: Building) -> float:
    """Current output per hour (base × level × worker bonus), 0 for non-producers."""
    cfg = constants.BUILDING_PRODUCTION.get(building.building_type)
    if cfg is None or building.level <= 0:
        return 0.0
    from game.workers import worker_bonus

    return cfg["rate_per_hour"] * building.level * (1 + worker_bonus(building))


def storage_cap(building: Building) -> int:
    cfg = constants.BUILDING_PRODUCTION.get(building.building_type)
    if cfg is None or building.level <= 0:
        return 0
    from game.workers import worker_bonus

    return int(cfg["cap_base"] * building.level * (1 + worker_bonus(building)))


def _accrued_since_collect(building: Building) -> float:
    """Raw (uncapped) production earned since last_collected_at at the CURRENT rate."""
    if production_rate(building) <= 0:
        return 0.0
    elapsed_hours = (timezone.now() - building.last_collected_at).total_seconds() / 3600
    return production_rate(building) * max(elapsed_hours, 0)


def pending_amount(building: Building) -> int:
    """Total collectable now = previously-locked pending + accrual since, capped by
    the building's storage. The lock (banked_pending) is what lets a worker swap keep
    the pending instead of dumping it."""
    cfg = constants.BUILDING_PRODUCTION.get(building.building_type)
    if cfg is None or building.level <= 0:
        return 0
    total = (building.banked_pending or 0.0) + _accrued_since_collect(building)
    return int(min(storage_cap(building), math.floor(total)))


def lock_pending(building: Building) -> None:
    """Fold the accrual-so-far into banked_pending at the CURRENT rate and reset the
    clock — WITHOUT collecting it. Called before a worker is added/removed, so the
    pending is preserved (never lost on a worker swap) yet a newly-added strong worker
    still can't retro-multiply hours already earned (the 'الماس زیاد' abuse)."""
    if not produces(building.building_type) or building.level <= 0:
        return
    locked = min(float(storage_cap(building)), (building.banked_pending or 0.0) + _accrued_since_collect(building))
    building.banked_pending = locked
    building.last_collected_at = timezone.now()
    building.save(update_fields=["banked_pending", "last_collected_at"])


def bank_pending(user: User, building: Building) -> int:
    """Back-compat shim — worker assign/unassign now call lock_pending() instead (which
    keeps the pending in the mine). Kept so any other caller still collects safely."""
    amount = pending_amount(building)
    if amount > 0:
        resource_field = constants.BUILDING_PRODUCTION[building.building_type]["resource"]
        setattr(user, resource_field, getattr(user, resource_field) + amount)
        user.save(update_fields=[resource_field])
    building.banked_pending = 0.0
    building.last_collected_at = timezone.now()
    building.save(update_fields=["banked_pending", "last_collected_at"])
    return amount


@transaction.atomic
def collect(user: User, building: Building) -> tuple[int, str]:
    """Returns (amount, resource_field) collected.

    Locks the building row and re-reads the accrual INSIDE the lock, so a rapid
    double-tap on «جمع‌آوری» can't collect the same production twice — the second
    tap sees the reset clock and gets nothing.

    The CALLER's `building` instance is updated in place (its last_collected_at is
    reset), so the screen it re-renders right after shows 0 pending — without this
    the caller kept a stale copy and the collect button 'did nothing' until you left
    and came back."""
    locked = Building.objects.select_for_update().get(id=building.id)
    if locked.owner_id != user.id:
        raise GameError("این ساختمون مال تو نیست.")
    if not produces(locked.building_type):
        raise GameError("این ساختمون چیزی تولید نمی‌کنه.")
    amount = pending_amount(locked)
    if amount <= 0:
        raise GameError("چیزی برای جمع‌آوری نیست، بعداً دوباره سر بزن.")

    resource_field = constants.BUILDING_PRODUCTION[locked.building_type]["resource"]
    setattr(user, resource_field, getattr(user, resource_field) + amount)
    user.save(update_fields=[resource_field])
    from game.ledger import record_gain

    _res_key = {"coins": "coins", "diamonds": "diamonds", "dna_fragments": "dna"}.get(resource_field)
    if _res_key:
        record_gain(user, "collect", **{_res_key: amount})
    now = timezone.now()
    locked.banked_pending = 0.0
    locked.last_collected_at = now
    locked.save(update_fields=["banked_pending", "last_collected_at"])
    # keep the caller's instance in sync for its re-render
    building.banked_pending = 0.0
    building.last_collected_at = now
    return amount, resource_field


def _finish_upgrade_row(user: User, upgrade: BuildingUpgrade) -> Building | None:
    """Apply one due upgrade: bump the building's level (clamped to its current cap),
    award lab XP, and delete the job. Returns the building, or None if the cap moved
    below the target in the meantime (then the job is just cancelled)."""
    building = upgrade.building
    cap = max_level_for(user, building.building_type)
    target = min(upgrade.target_level, cap)
    if target < upgrade.target_level:
        # تالار عقب‌تر از هدف ارتقا — ارتقا را بدون اعمال لول غیرمجاز لغو کن
        upgrade.delete()
        return None
    building.level = target
    building.save(update_fields=["level"])
    # a finished upgrade is worth real lab XP because it represents hours of real
    # time, not one tap — awarded here, at the single point where an upgrade can
    # complete, so it can't be double-credited by the callers that poll this
    lab.award_building_level(user, target)
    upgrade.delete()
    return building


def check_and_apply_upgrade(user: User) -> Building | None:
    """Lazily finishes EVERY upgrade whose timer already passed — same style as
    game/energy.py's stamina regen: computed at read time, no background job. A
    player may run up to builder_slots upgrades at once, so more than one can be due.
    Returns one finished building (back-compat) — callers that need all of them use
    the return of the loop; the notification job reports each separately."""
    now = timezone.now()
    finished = None
    for upgrade in list(BuildingUpgrade.objects.filter(owner=user, finishes_at__lte=now).select_related("building")):
        b = _finish_upgrade_row(user, upgrade)
        finished = finished or b
    return finished


def active_upgrades(user: User) -> list[BuildingUpgrade]:
    """All of the player's in-progress upgrades (after finishing any that are due)."""
    check_and_apply_upgrade(user)
    return list(BuildingUpgrade.objects.filter(owner=user).select_related("building").order_by("finishes_at"))


def upgrade_for_building(user: User, building: Building) -> BuildingUpgrade | None:
    """The in-progress upgrade for THIS building, or None. Used by the per-building
    detail screen so speed-up / diamond-finish act on the building being viewed."""
    return BuildingUpgrade.objects.filter(owner=user, building=building).first()


def active_upgrade(user: User) -> BuildingUpgrade | None:
    """Back-compat single-upgrade accessor: the soonest-finishing active upgrade."""
    check_and_apply_upgrade(user)
    return BuildingUpgrade.objects.filter(owner=user).order_by("finishes_at").first()


def builder_slots(user: User) -> int:
    return max(1, user.builder_slots or 1)


def active_upgrade_count(user: User) -> int:
    return BuildingUpgrade.objects.filter(owner=user).count()


@transaction.atomic
def buy_second_builder(user: User) -> User:
    """One-time diamond purchase that unlocks a second parallel building upgrade."""
    user = User.objects.select_for_update().get(id=user.id)
    if user.builder_slots >= constants.MAX_BUILDER_SLOTS:
        raise GameError("کارگر دوم رو قبلاً گرفتی — بیشتر از این نمی‌شه.")
    cost = constants.SECOND_BUILDER_DIAMONDS
    if user.diamonds < cost:
        raise GameError(f"الماس کافی نداری! کارگر دوم {cost} الماس می‌خواد (الان {user.diamonds} داری).")
    user.diamonds -= cost
    user.builder_slots = constants.MAX_BUILDER_SLOTS
    user.save(update_fields=["diamonds", "builder_slots"])
    return user


def upgrade_cost_and_minutes(building: Building) -> tuple[int, int]:
    """Cost and build time for this building's next level.

    Keyed on the level being *reached* — level 0 -> 1 is construction, which is
    the cheapest and quickest entry in the table rather than free."""
    target = min(building.level + 1, constants.BUILDING_MAX_LEVEL)
    return (
        constants.BUILDING_UPGRADE_GOLD[target],
        constants.BUILDING_UPGRADE_MINUTES[target],
    )


def full_buildout_estimate() -> tuple[int, int]:
    """(total gold, total minutes) to take every building from scratch to max.

    Only meaningful because a player has exactly one worker, so build times add
    up instead of overlapping. Used by the tests that guard the 1–2 week target,
    and by the buildings screen to show players what they're signing up for."""
    gold = minutes = 0
    for building_type in constants.BUILDING_TYPES:
        start = 1 if building_type == constants.MAIN_BUILDING else 0
        for target in range(start + 1, constants.BUILDING_MAX_LEVEL + 1):
            gold += constants.BUILDING_UPGRADE_GOLD[target]
            minutes += constants.BUILDING_UPGRADE_MINUTES[target]
    return gold, minutes


def start_upgrade(user: User, building: Building) -> BuildingUpgrade:
    if building.owner_id != user.id:
        raise GameError("این ساختمون مال تو نیست.")
    check_and_apply_upgrade(user)
    if BuildingUpgrade.objects.filter(owner=user, building=building).exists():
        raise GameError("این ساختمون همین الان در حال ارتقاست.")
    slots = builder_slots(user)
    if BuildingUpgrade.objects.filter(owner=user).count() >= slots:
        if slots >= constants.MAX_BUILDER_SLOTS:
            raise GameError(
                "هر دو کارگرت مشغولن — صبر کن یکیشون تموم بشه یا با کارت سرعت/الماس زودتر تمومش کن."
            )
        raise GameError(
            "همین الان یه ارتقا در حال انجامه و فقط یه کارگر داری. می‌تونی از فروشگاه کارگر دوم رو بخری "
            "تا هم‌زمان دوتا ارتقا بزنی، یا صبر کن تموم بشه."
        )

    if building.level == 0 and not is_unlocked(user, building.building_type):
        hall = constants.BUILDING_LABELS[constants.MAIN_BUILDING]
        needed = unlock_level_for(building.building_type)
        raise GameError(f"این ساختمون از سطح {needed} {hall} باز می‌شه.")

    cap = max_level_for(user, building.building_type)
    target = building.level + 1
    if building.level >= constants.BUILDING_MAX_LEVEL:
        raise GameError("این ساختمون به سقف سطح رسیده.")
    if target > cap or building.level >= cap:
        hall = constants.BUILDING_LABELS[constants.MAIN_BUILDING]
        raise GameError(
            f"سقف این ساختمون الان سطح {cap} است — "
            f"اول {hall} رو ارتقا بده تا بشه بالاتر رفت "
            f"(هیچ ساختمونی از سطح تالار و لول آخر جلو نمی‌زنه)."
        )

    # lab-level gate: reaching a building level needs enough lab level, which only
    # real play earns — so a building (esp. the main hall) can't be speed-maxed early
    req = constants.BUILDING_LEVEL_LAB_REQ.get(target, 0)
    if req > 0:
        from game import lab

        lvl = lab.lab_level(user)
        if lvl < req:
            raise GameError(
                f"برای رسوندن این ساختمون به سطح {target} باید سطح آزمایشگاهت حداقل {req} باشه "
                f"(الان {lvl}). با بازی‌کردن و فعالیت، سطح آزمایشگاه بالا می‌ره."
            )

    cost, minutes = upgrade_cost_and_minutes(building)
    if user.coins < cost:
        verb = "ساخت" if building.level == 0 else "ارتقا"
        raise InsufficientGoldError(
            f"طلا کافی نداری! {verb} <b>{cost:,}</b> طلا می‌خواد (الان {user.coins:,} داری).",
            need=cost, have=user.coins,
        )
    user.coins -= cost
    user.save(update_fields=["coins"])

    finishes_at = timezone.now() + datetime.timedelta(minutes=minutes)
    return BuildingUpgrade.objects.create(
        owner=user, building=building, target_level=target, finishes_at=finishes_at
    )


def _pick_upgrade(user: User, building_id: int | None):
    """The upgrade a speed-up / finish action targets: the one for `building_id` when
    given (the per-building detail screen always passes it), else the soonest-finishing
    active upgrade (back-compat)."""
    qs = BuildingUpgrade.objects.filter(owner=user)
    if building_id is not None:
        return qs.filter(building_id=building_id).first()
    return qs.order_by("finishes_at").first()


def apply_speedup(user: User, minutes: int, building_id: int | None = None) -> tuple[BuildingUpgrade | None, bool]:
    """Use ONE card. Kept for back-compat; delegates to the bulk path."""
    upgrade, completed, _used = apply_speedup_bulk(user, minutes, 1, building_id)
    return upgrade, completed


def apply_speedup_bulk(user: User, minutes: int, count: int, building_id: int | None = None) -> tuple[BuildingUpgrade | None, bool, int]:
    """Use up to `count` cards of one denomination at once — but never more than are
    needed to finish the upgrade, so cards aren't wasted past completion. Returns
    (remaining_upgrade_or_None, completed, cards_actually_used)."""
    import math

    if minutes not in constants.SPEEDUP_MINUTES:
        raise GameError("این کارت سرعت معتبر نیست.")
    card = SpeedupCard.objects.filter(owner=user, minutes=minutes).first()
    if card is None or card.count <= 0:
        raise GameError("این کارت سرعت رو نداری.")
    upgrade = _pick_upgrade(user, building_id)
    if upgrade is None:
        raise GameError("هیچ ارتقایی در حال انجام نیست که سرعتش بدی.")

    remaining = (upgrade.finishes_at - timezone.now()).total_seconds()
    needed = max(1, math.ceil(remaining / (minutes * 60))) if remaining > 0 else 1
    use = max(1, min(int(count), card.count, needed))

    card.count -= use
    if card.count <= 0:
        card.delete()
    else:
        card.save(update_fields=["count"])

    upgrade.finishes_at -= datetime.timedelta(minutes=minutes * use)
    if upgrade.finishes_at <= timezone.now():
        building = upgrade.building
        cap = max_level_for(user, building.building_type)
        target = min(upgrade.target_level, cap)
        if target < upgrade.target_level:
            upgrade.delete()
            return None, True, use
        building.level = target
        building.save(update_fields=["level"])
        lab.award_building_level(user, target)
        upgrade.delete()
        return None, True, use

    upgrade.save(update_fields=["finishes_at"])
    return upgrade, False, use


def diamond_finish_price(upgrade: BuildingUpgrade) -> int:
    """Diamonds to finish this upgrade right now, from the time still remaining."""
    remaining = (upgrade.finishes_at - timezone.now()).total_seconds()
    return constants.diamond_finish_cost(remaining)


def finish_with_diamonds(user: User, building_id: int | None = None) -> tuple[Building, int]:
    """Instantly completes an active upgrade for diamonds. Returns (building, cost).
    Priced from the *remaining* time, so paying after waiting a while is cheaper —
    otherwise the sensible play would always be to pay immediately. `building_id`
    targets a specific upgrade when the player runs more than one at once."""
    upgrade = _pick_upgrade(user, building_id)
    if upgrade is None:
        raise GameError("هیچ ارتقایی در حال انجام نیست.")

    cost = diamond_finish_price(upgrade)
    if user.diamonds < cost:
        raise GameError(f"الماس کافی نداری! تموم کردن این ارتقا {cost} الماس می‌خواد.")

    user.diamonds -= cost
    user.save(update_fields=["diamonds"])

    building = upgrade.building
    cap = max_level_for(user, building.building_type)
    target = min(upgrade.target_level, cap)
    if target < upgrade.target_level:
        upgrade.delete()
        raise GameError(
            f"سقف این ساختمون الان سطح {cap} است — اول تالار مِهر رو ارتقا بده."
        )
    building.level = target
    building.save(update_fields=["level"])
    # a finished upgrade is worth real lab XP because it represents hours of real
    # time, not one tap — awarded here, at the single point where an upgrade can
    # complete, so it can't be double-credited by the callers that poll this
    lab.award_building_level(user, target)
    upgrade.delete()
    return building, cost


def grant_speedup_card(user: User, minutes: int, count: int = 1) -> SpeedupCard:
    card, _ = SpeedupCard.objects.get_or_create(owner=user, minutes=minutes)
    card.count += count
    card.save(update_fields=["count"])
    return card


def list_speedup_cards(user: User) -> list[SpeedupCard]:
    return list(SpeedupCard.objects.filter(owner=user, count__gt=0).order_by("minutes"))

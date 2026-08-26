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
    """A player can only raise creatures to as many stars as their main hall level."""
    return min(constants.STAR_MAX, max(1, main_hall_level(user)))


def is_built(user: User, building_type: str) -> bool:
    return building_level(user, building_type) > 0


def produces(building_type: str) -> bool:
    return building_type in constants.BUILDING_PRODUCTION


def pending_amount(building: Building) -> int:
    cfg = constants.BUILDING_PRODUCTION.get(building.building_type)
    if cfg is None or building.level <= 0:
        return 0  # pure-gate buildings (hall/forge/fusion lab) and unbuilt ones make nothing
    # imported here rather than at module level: game.workers imports game.creature,
    # which imports back into this module's dependency chain
    from game.workers import worker_bonus

    # Stationed creatures raise the rate *and* the storage cap. Raising only the
    # rate looked tidier, but the cap is small enough that it binds within a few
    # hours, so a staffed mine and a bare one paid identically to anyone who
    # doesn't collect constantly — the bonus was invisible exactly where idle
    # income matters. WORKER_BONUS_CAP is what keeps this bounded instead.
    bonus = 1 + worker_bonus(building)
    rate = cfg["rate_per_hour"] * building.level * bonus
    cap = cfg["cap_base"] * building.level * bonus
    elapsed_hours = (timezone.now() - building.last_collected_at).total_seconds() / 3600
    return int(min(cap, math.floor(rate * max(elapsed_hours, 0))))


@transaction.atomic
def collect(user: User, building: Building) -> tuple[int, str]:
    """Returns (amount, resource_field) collected.

    Locks the building row and re-reads the accrual INSIDE the lock, so a rapid
    double-tap on «جمع‌آوری» can't collect the same production twice — the second
    tap sees the reset clock and gets nothing."""
    building = Building.objects.select_for_update().get(id=building.id)
    if building.owner_id != user.id:
        raise GameError("این ساختمون مال تو نیست.")
    if not produces(building.building_type):
        raise GameError("این ساختمون چیزی تولید نمی‌کنه.")
    amount = pending_amount(building)
    if amount <= 0:
        raise GameError("چیزی برای جمع‌آوری نیست، بعداً دوباره سر بزن.")

    resource_field = constants.BUILDING_PRODUCTION[building.building_type]["resource"]
    setattr(user, resource_field, getattr(user, resource_field) + amount)
    user.save(update_fields=[resource_field])
    building.last_collected_at = timezone.now()
    building.save(update_fields=["last_collected_at"])
    return amount, resource_field


def check_and_apply_upgrade(user: User) -> Building | None:
    """Lazily finishes the active upgrade if its timer already passed — same style
    as game/energy.py's stamina regen: computed at read time, no background job."""
    upgrade = BuildingUpgrade.objects.filter(owner=user).first()
    if upgrade is None or timezone.now() < upgrade.finishes_at:
        return None
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


def active_upgrade(user: User) -> BuildingUpgrade | None:
    check_and_apply_upgrade(user)
    return BuildingUpgrade.objects.filter(owner=user).first()


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
    if BuildingUpgrade.objects.filter(owner=user).exists():
        raise GameError(
            "همین الان یه ارتقا در حال انجامه — فقط یه کارگر داری، صبر کن تموم بشه یا با کارت سرعت بدش."
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


def apply_speedup(user: User, minutes: int) -> tuple[BuildingUpgrade | None, bool]:
    """Use ONE card. Kept for back-compat; delegates to the bulk path."""
    upgrade, completed, _used = apply_speedup_bulk(user, minutes, 1)
    return upgrade, completed


def apply_speedup_bulk(user: User, minutes: int, count: int) -> tuple[BuildingUpgrade | None, bool, int]:
    """Use up to `count` cards of one denomination at once — but never more than are
    needed to finish the upgrade, so cards aren't wasted past completion. Returns
    (remaining_upgrade_or_None, completed, cards_actually_used)."""
    import math

    if minutes not in constants.SPEEDUP_MINUTES:
        raise GameError("این کارت سرعت معتبر نیست.")
    card = SpeedupCard.objects.filter(owner=user, minutes=minutes).first()
    if card is None or card.count <= 0:
        raise GameError("این کارت سرعت رو نداری.")
    upgrade = BuildingUpgrade.objects.filter(owner=user).first()
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


def finish_with_diamonds(user: User) -> tuple[Building, int]:
    """Instantly completes the active upgrade for diamonds. Returns (building, cost).
    Priced from the *remaining* time, so paying after waiting a while is cheaper —
    otherwise the sensible play would always be to pay immediately."""
    upgrade = BuildingUpgrade.objects.filter(owner=user).first()
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

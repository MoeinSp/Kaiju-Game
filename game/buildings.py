import datetime
import math
import random

from django.utils import timezone

from bio_lab.models import Building, BuildingUpgrade, SpeedupCard, User
from game import constants
from game.creature import GameError

# small bonus chance (separate from the daily wheel's guaranteed prize) attached to
# a handful of natural "win" moments — duel wins, raid kills, guardian defenses
ACTIVITY_SPEEDUP_CHANCE = 0.08
ACTIVITY_SPEEDUP_CHOICES = [(1, 50), (5, 35), (30, 15)]  # (minutes, weight) — small denominations only


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
        building, _ = Building.objects.get_or_create(
            owner=user, building_type=building_type, defaults={"level": default_level}
        )
        buildings.append(building)
    return buildings


def building_level(user: User, building_type: str) -> int:
    """0 when the building doesn't exist yet or hasn't been constructed."""
    building = Building.objects.filter(owner=user, building_type=building_type).first()
    return building.level if building is not None else 0


def main_hall_level(user: User) -> int:
    return building_level(user, constants.MAIN_BUILDING)


def max_level_for(user: User, building_type: str) -> int:
    """The main hall is capped by BUILDING_MAX_LEVEL; every other building is
    additionally capped by the hall's current level. That single rule is what makes
    the hall the deliberate progression bottleneck."""
    if building_type == constants.MAIN_BUILDING:
        return constants.BUILDING_MAX_LEVEL
    return min(constants.BUILDING_MAX_LEVEL, main_hall_level(user))


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
    rate = cfg["rate_per_hour"] * building.level
    cap = cfg["cap_base"] * building.level
    elapsed_hours = (timezone.now() - building.last_collected_at).total_seconds() / 3600
    return min(cap, math.floor(rate * max(elapsed_hours, 0)))


def collect(user: User, building: Building) -> tuple[int, str]:
    """Returns (amount, resource_field) collected."""
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
    building.level = upgrade.target_level
    building.save(update_fields=["level"])
    upgrade.delete()
    return building


def active_upgrade(user: User) -> BuildingUpgrade | None:
    check_and_apply_upgrade(user)
    return BuildingUpgrade.objects.filter(owner=user).first()


def upgrade_cost_and_minutes(building: Building) -> tuple[int, int]:
    """Construction (level 0 -> 1) is priced as if it were a level-1 upgrade, so a
    brand-new building isn't free."""
    steps = max(1, building.level)
    return (
        constants.BUILDING_UPGRADE_BASE_GOLD_COST * steps,
        constants.BUILDING_UPGRADE_BASE_MINUTES * steps,
    )


def start_upgrade(user: User, building: Building) -> BuildingUpgrade:
    if building.owner_id != user.id:
        raise GameError("این ساختمون مال تو نیست.")
    check_and_apply_upgrade(user)
    if BuildingUpgrade.objects.filter(owner=user).exists():
        raise GameError(
            "همین الان یه ارتقا در حال انجامه — فقط یه کارگر داری، صبر کن تموم بشه یا با کارت سرعت بدش."
        )

    cap = max_level_for(user, building.building_type)
    if building.level >= constants.BUILDING_MAX_LEVEL:
        raise GameError("این ساختمون به سقف سطح رسیده.")
    if building.level >= cap:
        hall = constants.BUILDING_LABELS[constants.MAIN_BUILDING]
        raise GameError(f"اول باید {hall} رو ارتقا بدی — هیچ ساختمونی نمی‌تونه از سطح اون جلو بزنه.")

    cost, minutes = upgrade_cost_and_minutes(building)
    if user.coins < cost:
        verb = "ساخت" if building.level == 0 else "ارتقا"
        raise GameError(f"طلا کافی نداری! {verb} {cost} طلا هزینه داره.")
    user.coins -= cost
    user.save(update_fields=["coins"])

    finishes_at = timezone.now() + datetime.timedelta(minutes=minutes)
    return BuildingUpgrade.objects.create(
        owner=user, building=building, target_level=building.level + 1, finishes_at=finishes_at
    )


def apply_speedup(user: User, minutes: int) -> tuple[BuildingUpgrade | None, bool]:
    """Returns (remaining_upgrade_or_None, completed). `completed=True` means the
    card finished the upgrade outright rather than just shortening it."""
    if minutes not in constants.SPEEDUP_MINUTES:
        raise GameError("این کارت سرعت معتبر نیست.")
    card = SpeedupCard.objects.filter(owner=user, minutes=minutes).first()
    if card is None or card.count <= 0:
        raise GameError("این کارت سرعت رو نداری.")
    upgrade = BuildingUpgrade.objects.filter(owner=user).first()
    if upgrade is None:
        raise GameError("هیچ ارتقایی در حال انجام نیست که سرعتش بدی.")

    card.count -= 1
    if card.count <= 0:
        card.delete()
    else:
        card.save(update_fields=["count"])

    upgrade.finishes_at -= datetime.timedelta(minutes=minutes)
    if upgrade.finishes_at <= timezone.now():
        building = upgrade.building
        building.level = upgrade.target_level
        building.save(update_fields=["level"])
        upgrade.delete()
        return None, True

    upgrade.save(update_fields=["finishes_at"])
    return upgrade, False


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
    building.level = upgrade.target_level
    building.save(update_fields=["level"])
    upgrade.delete()
    return building, cost


def grant_speedup_card(user: User, minutes: int, count: int = 1) -> SpeedupCard:
    card, _ = SpeedupCard.objects.get_or_create(owner=user, minutes=minutes)
    card.count += count
    card.save(update_fields=["count"])
    return card


def list_speedup_cards(user: User) -> list[SpeedupCard]:
    return list(SpeedupCard.objects.filter(owner=user, count__gt=0).order_by("minutes"))

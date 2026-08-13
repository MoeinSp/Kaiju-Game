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
    buildings = []
    for building_type in constants.BUILDING_TYPES:
        building, _ = Building.objects.get_or_create(owner=user, building_type=building_type)
        buildings.append(building)
    return buildings


def pending_amount(building: Building) -> int:
    cfg = constants.BUILDING_PRODUCTION[building.building_type]
    rate = cfg["rate_per_hour"] * building.level
    cap = cfg["cap_base"] * building.level
    elapsed_hours = (timezone.now() - building.last_collected_at).total_seconds() / 3600
    return min(cap, math.floor(rate * max(elapsed_hours, 0)))


def collect(user: User, building: Building) -> tuple[int, str]:
    """Returns (amount, resource_field) collected."""
    if building.owner_id != user.id:
        raise GameError("این ساختمون مال تو نیست.")
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


def start_upgrade(user: User, building: Building) -> BuildingUpgrade:
    if building.owner_id != user.id:
        raise GameError("این ساختمون مال تو نیست.")
    check_and_apply_upgrade(user)
    if BuildingUpgrade.objects.filter(owner=user).exists():
        raise GameError(
            "همین الان یه ارتقا در حال انجامه — فقط یه کارگر داری، صبر کن تموم بشه یا با کارت سرعت بدش."
        )
    if building.level >= constants.BUILDING_MAX_LEVEL:
        raise GameError("این ساختمون به سقف سطح رسیده.")

    cost = constants.BUILDING_UPGRADE_BASE_GOLD_COST * building.level
    if user.coins < cost:
        raise GameError(f"طلا کافی نداری! ارتقا {cost} طلا هزینه داره.")
    user.coins -= cost
    user.save(update_fields=["coins"])

    minutes = constants.BUILDING_UPGRADE_BASE_MINUTES * building.level
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


def grant_speedup_card(user: User, minutes: int, count: int = 1) -> SpeedupCard:
    card, _ = SpeedupCard.objects.get_or_create(owner=user, minutes=minutes)
    card.count += count
    card.save(update_fields=["count"])
    return card


def list_speedup_cards(user: User) -> list[SpeedupCard]:
    return list(SpeedupCard.objects.filter(owner=user, count__gt=0).order_by("minutes"))

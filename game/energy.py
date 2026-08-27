import datetime

from django.utils import timezone

from bio_lab.models import User
from game import constants
from game.creature import GameError


class EnergyError(GameError):
    """Raised specifically when an action can't run for lack of energy, so callers can
    tell it apart from other GameErrors and offer the diamond-refill button."""


def _synced_energy_and_anchor(user: User) -> tuple[int, datetime.datetime]:
    """Computes energy regenerated since energy_updated_at without mutating `user`."""
    if user.energy >= constants.MAX_ENERGY:
        return constants.MAX_ENERGY, user.energy_updated_at

    elapsed_seconds = (timezone.now() - user.energy_updated_at).total_seconds()
    ticks = int(elapsed_seconds // (constants.ENERGY_REGEN_MINUTES * 60))
    if ticks <= 0:
        return user.energy, user.energy_updated_at

    new_energy = min(constants.MAX_ENERGY, user.energy + ticks)
    if new_energy >= constants.MAX_ENERGY:
        return constants.MAX_ENERGY, timezone.now()

    # advance the anchor by exactly the ticks consumed, so partial progress toward
    # the next point isn't lost (e.g. 7 of 12 minutes elapsed keeps counting)
    new_anchor = user.energy_updated_at + datetime.timedelta(
        minutes=ticks * constants.ENERGY_REGEN_MINUTES
    )
    return new_energy, new_anchor


def sync_energy(user: User) -> int:
    """Applies any regenerated energy onto `user` in place. Caller must still .save() it
    if the caller doesn't otherwise save `user` afterward. Returns the current energy."""
    current, anchor = _synced_energy_and_anchor(user)
    if current != user.energy:
        user.energy = current
        user.energy_updated_at = anchor
    return current


def minutes_until_next_point(user: User) -> int:
    if user.energy >= constants.MAX_ENERGY:
        return 0
    elapsed_seconds = (timezone.now() - user.energy_updated_at).total_seconds()
    remaining = constants.ENERGY_REGEN_MINUTES * 60 - (elapsed_seconds % (constants.ENERGY_REGEN_MINUTES * 60))
    return max(1, round(remaining / 60))


def spend_energy(user: User, amount: int, action_label: str) -> None:
    """Raises GameError if not enough energy. Otherwise deducts it. Caller must .save() `user`."""
    sync_energy(user)
    if user.energy < amount:
        raise EnergyError(
            f"⚡ انرژیت برای {action_label} کافی نیست ({user.energy}/{constants.MAX_ENERGY}). "
            f"تا انرژی بعدی حدود {minutes_until_next_point(user)} دقیقه مونده."
        )
    # When spending from a FULL bar, restart the regen clock NOW. At max, sync leaves
    # energy_updated_at on its old (possibly hours-stale) value; without this reset the
    # next read would regenerate the point we're about to spend — the "اولین اتک/شکار
    # انرژی کم نمی‌کنه" bug (the first action from full looked free).
    if user.energy >= constants.MAX_ENERGY:
        user.energy_updated_at = timezone.now()
    user.energy -= amount


def refill_energy(user: User) -> dict:
    """Pay diamonds to top energy back up to full. Returns {"cost", "energy"}."""
    from django.db import transaction

    with transaction.atomic():
        user = User.objects.select_for_update().get(id=user.id)
        sync_energy(user)
        if user.energy >= constants.MAX_ENERGY:
            raise GameError("انرژیت پره، نیازی به شارژ نیست.")
        cost = constants.ENERGY_REFILL_DIAMOND_COST
        if user.diamonds < cost:
            raise GameError(f"الماس کافی نداری! شارژ کامل انرژی {cost} الماس می‌خواد.")
        user.diamonds -= cost
        user.energy = constants.MAX_ENERGY
        user.energy_updated_at = timezone.now()
        user.save(update_fields=["diamonds", "energy", "energy_updated_at"])
    return {"cost": cost, "energy": user.energy}

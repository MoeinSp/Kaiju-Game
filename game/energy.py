import datetime

from django.utils import timezone

from bio_lab.models import User
from game import constants
from game.creature import GameError


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
        raise GameError(
            f"⚡ انرژیت برای {action_label} کافی نیست ({user.energy}/{constants.MAX_ENERGY}). "
            f"تا انرژی بعدی حدود {minutes_until_next_point(user)} دقیقه مونده."
        )
    user.energy -= amount

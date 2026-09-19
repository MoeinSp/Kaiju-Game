import datetime

from django.utils import timezone

from bio_lab.models import User
from game import constants
from game.creature import GameError


class EnergyError(GameError):
    """Raised specifically when an action can't run for lack of energy, so callers can
    tell it apart from other GameErrors and offer the diamond-refill button."""


def get_max_energy(user: User) -> int:
    """Subscribed users (Silver / Gold) have a 100 energy cap; default is MAX_ENERGY (50)."""
    from game.subscription import is_subscription_active

    return 100 if is_subscription_active(user) else constants.MAX_ENERGY


def get_energy_regen_interval_seconds(user: User) -> float:
    """Computes seconds per energy point tick.
    Normal: base (constants.ENERGY_REGEN_MINUTES * 60)
    Silver: +25% faster regen (interval / 1.25)
    Gold: +50% faster regen (interval / 1.50)
    """
    from game.subscription import get_subscription_tier

    base_sec = float(constants.ENERGY_REGEN_MINUTES * 60)
    tier = get_subscription_tier(user)
    if tier == "gold":
        return base_sec / 1.50  # 50% faster regen rate
    elif tier == "silver":
        return base_sec / 1.25  # 25% faster regen rate
    return base_sec


def _synced_energy_and_anchor(user: User) -> tuple[int, datetime.datetime]:
    """Computes energy regenerated since energy_updated_at without mutating `user`."""
    max_en = get_max_energy(user)
    if user.energy >= max_en:
        return max_en, user.energy_updated_at

    elapsed_seconds = (timezone.now() - user.energy_updated_at).total_seconds()
    interval_sec = get_energy_regen_interval_seconds(user)
    ticks = int(elapsed_seconds // interval_sec)
    if ticks <= 0:
        return user.energy, user.energy_updated_at

    new_energy = min(max_en, user.energy + ticks)
    if new_energy >= max_en:
        return max_en, timezone.now()

    # advance the anchor by exactly the ticks consumed, so partial progress toward
    # the next point isn't lost
    new_anchor = user.energy_updated_at + datetime.timedelta(
        seconds=ticks * interval_sec
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
    sec = seconds_until_next_point(user)
    return max(1, round(sec / 60))


def seconds_until_next_point(user: User) -> int:
    """Seconds until the next energy point regenerates (0 when full) — like
    minutes_until_next_point but precise, for a MM:SS countdown."""
    max_en = get_max_energy(user)
    if user.energy >= max_en:
        return 0
    elapsed_seconds = (timezone.now() - user.energy_updated_at).total_seconds()
    interval_sec = get_energy_regen_interval_seconds(user)
    remaining = interval_sec - (elapsed_seconds % interval_sec)
    return max(1, int(remaining))


def spend_energy(user: User, amount: int, action_label: str) -> None:
    """Raises GameError if not enough energy. Otherwise deducts it. Caller must .save() `user`."""
    sync_energy(user)
    max_en = get_max_energy(user)
    if user.energy < amount:
        raise EnergyError(
            f"⚡ انرژیت برای {action_label} کافی نیست ({user.energy}/{max_en}). "
            f"تا انرژی بعدی حدود {minutes_until_next_point(user)} دقیقه مونده."
        )
    # When spending from a FULL bar, restart the regen clock NOW. At max, sync leaves
    # energy_updated_at on its old (possibly hours-stale) value; without this reset the
    # next read would regenerate the point we're about to spend — the "اولین اتک/شکار
    # انرژی کم نمی‌کنه" bug (the first action from full looked free).
    if user.energy >= max_en:
        user.energy_updated_at = timezone.now()
    user.energy -= amount


def refill_energy(user: User) -> dict:
    """Pay diamonds to top energy back up to full. Returns {"cost", "energy"}."""
    from django.db import transaction

    with transaction.atomic():
        user = User.objects.select_for_update().get(id=user.id)
        sync_energy(user)
        max_en = get_max_energy(user)
        if user.energy >= max_en:
            raise GameError("انرژیت پره، نیازی به شارژ نیست.")
        from game import botconfig

        cost = botconfig.get_energy_refill_cost()
        if user.diamonds < cost:
            raise GameError(f"الماس کافی نداری! شارژ کامل انرژی {cost} الماس می‌خواد.")
        user.diamonds -= cost
        user.energy = max_en
        user.energy_updated_at = timezone.now()
        user.save(update_fields=["diamonds", "energy", "energy_updated_at"])
    return {"cost": cost, "energy": user.energy}


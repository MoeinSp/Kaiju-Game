from django.db import IntegrityError
from django.db.models import Q
from django.utils import timezone

from bio_lab.models import ChannelJoinClaim, RequiredChannel, User
from game.creature import GameError

NOT_JOINED_STATUSES = {"left", "kicked"}


def active_channels() -> list[RequiredChannel]:
    now = timezone.now()
    return list(RequiredChannel.objects.filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now)))


def list_channels() -> list[RequiredChannel]:
    return list(RequiredChannel.objects.order_by("-created_at"))


def add_channel(chat_id: int, username: str | None, title: str | None) -> RequiredChannel:
    channel, _ = RequiredChannel.objects.update_or_create(
        chat_id=chat_id, defaults={"username": username, "title": title}
    )
    return channel


def remove_channel(channel_id: int) -> None:
    RequiredChannel.objects.filter(id=channel_id).delete()


def set_duration(channel_id: int, hours: int | None) -> RequiredChannel:
    try:
        channel = RequiredChannel.objects.get(id=channel_id)
    except RequiredChannel.DoesNotExist:
        raise GameError("این کانال دیگه پیدا نشد.")
    channel.expires_at = None if hours is None else timezone.now() + timezone.timedelta(hours=hours)
    channel.save(update_fields=["expires_at"])
    return channel


def set_reward(channel_id: int, coins: int, dna: int, diamonds: int = 0) -> RequiredChannel:
    try:
        channel = RequiredChannel.objects.get(id=channel_id)
    except RequiredChannel.DoesNotExist:
        raise GameError("این کانال دیگه پیدا نشد.")
    channel.reward_coins = max(0, coins)
    channel.reward_dna = max(0, dna)
    channel.reward_diamonds = max(0, diamonds)
    channel.save(update_fields=["reward_coins", "reward_dna", "reward_diamonds"])
    return channel


def has_reward(channel: RequiredChannel) -> bool:
    return channel.reward_coins > 0 or channel.reward_dna > 0 or channel.reward_diamonds > 0


def grant_reward_if_unclaimed(user: User, channel: RequiredChannel) -> bool:
    """Idempotent: returns True if this call actually granted the reward (first
    time), False if already claimed before. Relies on a DB-level unique
    constraint to stay correct even under concurrent callback taps."""
    if not has_reward(channel):
        return False
    try:
        ChannelJoinClaim.objects.create(user=user, channel=channel)
    except IntegrityError:
        return False
    user.coins += channel.reward_coins
    user.dna_fragments += channel.reward_dna
    user.diamonds += channel.reward_diamonds
    user.save(update_fields=["coins", "dna_fragments", "diamonds"])
    return True

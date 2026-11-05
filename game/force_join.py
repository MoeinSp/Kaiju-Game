import time

from django.db import IntegrityError
from django.db.models import Q
from django.utils import timezone

from bio_lab.models import ChannelJoinClaim, RequiredChannel, User
from game.creature import GameError

NOT_JOINED_STATUSES = {"left", "kicked"}

_CHANNELS_CACHE = None
_CHANNELS_CACHE_TIME = 0.0


def invalidate_channel_cache():
    global _CHANNELS_CACHE
    _CHANNELS_CACHE = None


def active_channels() -> list[RequiredChannel]:
    global _CHANNELS_CACHE, _CHANNELS_CACHE_TIME
    now_ts = time.monotonic()
    if _CHANNELS_CACHE is not None and (now_ts - _CHANNELS_CACHE_TIME) < 30.0:
        return _CHANNELS_CACHE
    now = timezone.now()
    channels = list(RequiredChannel.objects.filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now)))
    _CHANNELS_CACHE = channels
    _CHANNELS_CACHE_TIME = now_ts
    return channels


def list_channels() -> list[RequiredChannel]:
    return list(RequiredChannel.objects.order_by("-created_at"))


def add_channel(chat_id: int, username: str | None, title: str | None,
                kind: str = "channel", invite_link: str | None = None) -> RequiredChannel:
    defaults = {"username": username, "title": title, "kind": kind}
    if invite_link:
        defaults["invite_link"] = invite_link
    channel, _ = RequiredChannel.objects.update_or_create(chat_id=chat_id, defaults=defaults)
    invalidate_channel_cache()
    return channel


def set_invite_link(channel_id: int, link: str | None) -> RequiredChannel:
    try:
        channel = RequiredChannel.objects.get(id=channel_id)
    except RequiredChannel.DoesNotExist:
        raise GameError("این مورد دیگه پیدا نشد.")
    channel.invite_link = (link or "").strip() or None
    channel.save(update_fields=["invite_link"])
    invalidate_channel_cache()
    return channel


# ── «استارت اجباری ربات»: another bot's own API says whether a player started it ──
BOT_CHECK_TIMEOUT = 6.0


def _bot_row_id(username: str) -> int:
    """A stable, unique chat_id for a «bot» row (it has no chat to check): well below any real
    Telegram chat id, derived from the bot's username so re-adding it updates the same row."""
    import zlib

    return -8_000_000_000_000 - zlib.crc32(username.lower().encode())


def add_bot(username: str, title: str | None, api_url: str, api_key: str) -> RequiredChannel:
    username = username.lstrip("@")
    channel, _ = RequiredChannel.objects.update_or_create(
        chat_id=_bot_row_id(username),
        defaults={"kind": "bot", "username": username, "title": title or f"@{username}",
                  "invite_link": f"https://t.me/{username}", "api_url": api_url.strip(), "api_key": api_key.strip()},
    )
    invalidate_channel_cache()
    return channel


async def bot_started(channel: RequiredChannel, user_id: int) -> bool | None:
    """Ask the other bot's API whether `user_id` started it (and has not blocked it).
    True / False, or None when it can't tell right now (timeout, bad key, server error) —
    the caller then lets the player through, like a channel whose admin rights are missing."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=BOT_CHECK_TIMEOUT) as client:
            r = await client.get(channel.api_url, params={"user_id": user_id},
                                 headers={"X-API-Key": channel.api_key or ""})
        data = r.json()
    except Exception:  # noqa: BLE001 — a flaky partner API must never lock players out
        return None
    if r.status_code != 200 or not data.get("ok"):
        return None
    return bool(data.get("started")) and not data.get("blocked")


async def test_bot_api(api_url: str, api_key: str, user_id: int) -> str | None:
    """The owner panel's check before saving: None if the API answers with this key, else why not."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=BOT_CHECK_TIMEOUT) as client:
            r = await client.get(api_url, params={"user_id": user_id}, headers={"X-API-Key": api_key})
        data = r.json()
    except Exception as exc:  # noqa: BLE001
        return f"به API وصل نشدم ({type(exc).__name__})"
    if r.status_code != 200 or not data.get("ok"):
        return data.get("message") or data.get("code") or f"HTTP {r.status_code}"
    if "started" not in data:
        return "جواب API فیلد «started» نداره"
    return None


def remove_channel(channel_id: int) -> None:
    RequiredChannel.objects.filter(id=channel_id).delete()
    invalidate_channel_cache()


def set_duration(channel_id: int, hours: int | None) -> RequiredChannel:
    try:
        channel = RequiredChannel.objects.get(id=channel_id)
    except RequiredChannel.DoesNotExist:
        raise GameError("این کانال دیگه پیدا نشد.")
    channel.expires_at = None if hours is None else timezone.now() + timezone.timedelta(hours=hours)
    channel.save(update_fields=["expires_at"])
    invalidate_channel_cache()
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
    invalidate_channel_cache()
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

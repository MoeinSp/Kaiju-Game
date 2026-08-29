"""Owner-tunable global settings, read from an in-memory cache.

Only one row ever exists (id=1). The cache exists for the same reason
game.button_emoji's does: the "join the game group" button is built inside
async handler code, so a lazy DB read would raise Django's
SynchronousOnlyOperation. bot.main warms the cache once at startup, and every
write refreshes it, so reads are always pure in-memory and handler-safe.
"""

from __future__ import annotations

from bio_lab.models import BotConfig

DEFAULT_GROUP_TITLE = "🎮 ورود به گروه بازی"
DEFAULT_ENERGY_REFILL_DIAMONDS = 25

# Starts at defaults; never lazily populated on read.
_cache: dict[str, object] = {
    "group_game_url": "",
    "group_game_title": "",
    "energy_refill_diamonds": DEFAULT_ENERGY_REFILL_DIAMONDS,
}


def refresh_cache() -> None:
    """Reload from the DB. Sync context only (startup or right after a write)."""
    global _cache
    row = BotConfig.objects.filter(id=1).first()
    if row is None:
        _cache = {
            "group_game_url": "",
            "group_game_title": "",
            "energy_refill_diamonds": DEFAULT_ENERGY_REFILL_DIAMONDS,
        }
    else:
        _cache = {
            "group_game_url": row.group_game_url or "",
            "group_game_title": row.group_game_title or "",
            "energy_refill_diamonds": row.energy_refill_diamonds or DEFAULT_ENERGY_REFILL_DIAMONDS,
        }


def get_energy_refill_cost() -> int:
    """Diamonds for an instant full energy refill. Pure in-memory read — safe from
    async handler code (the refill button is built there)."""
    val = _cache.get("energy_refill_diamonds") or DEFAULT_ENERGY_REFILL_DIAMONDS
    return int(val)


def set_energy_refill_cost(diamonds: int) -> None:
    BotConfig.objects.update_or_create(id=1, defaults={"energy_refill_diamonds": max(1, int(diamonds))})
    refresh_cache()


def get_group_link() -> tuple[str, str] | None:
    """(url, button_title) for the game-group button, or None if unset. Pure
    in-memory read — safe from async handler code."""
    url = _cache.get("group_game_url") or ""
    if not url:
        return None
    title = _cache.get("group_game_title") or DEFAULT_GROUP_TITLE
    return url, title


def set_group_link(url: str, title: str = "") -> None:
    """Persist the game-group link (and optional button label) and refresh the
    cache. Pass an empty url to clear the button."""
    url = (url or "").strip()
    title = (title or "").strip()[:48]
    BotConfig.objects.update_or_create(
        id=1, defaults={"group_game_url": url[:256], "group_game_title": title}
    )
    refresh_cache()


def get_backup_interval() -> int:
    """Auto-backup interval in hours (0 = off)."""
    row = BotConfig.objects.filter(id=1).first()
    return row.backup_interval_hours if row else 0


def set_backup_interval(hours: int) -> None:
    BotConfig.objects.update_or_create(id=1, defaults={"backup_interval_hours": max(0, int(hours))})


def get_backup_chat_id() -> int | None:
    """Chat the auto-backup file is sent to, or None to use the owner's own DM."""
    row = BotConfig.objects.filter(id=1).first()
    return row.backup_chat_id if row else None


def set_backup_chat_id(chat_id: int | None) -> None:
    BotConfig.objects.update_or_create(id=1, defaults={"backup_chat_id": chat_id})


def due_backup() -> bool:
    """True if auto-backup is on and its interval has elapsed since the last one."""
    import datetime

    from django.utils import timezone

    row = BotConfig.objects.filter(id=1).first()
    if row is None or row.backup_interval_hours <= 0:
        return False
    if row.backup_last_at is None:
        return True
    return timezone.now() - row.backup_last_at >= datetime.timedelta(hours=row.backup_interval_hours)


def mark_backup_done() -> None:
    from django.utils import timezone

    BotConfig.objects.filter(id=1).update(backup_last_at=timezone.now())

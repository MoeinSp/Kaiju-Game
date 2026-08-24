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

# Starts as "no group configured"; never lazily populated on read.
_cache: dict[str, str] = {"group_game_url": "", "group_game_title": ""}


def refresh_cache() -> None:
    """Reload from the DB. Sync context only (startup or right after a write)."""
    global _cache
    row = BotConfig.objects.filter(id=1).first()
    if row is None:
        _cache = {"group_game_url": "", "group_game_title": ""}
    else:
        _cache = {
            "group_game_url": row.group_game_url or "",
            "group_game_title": row.group_game_title or "",
        }


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

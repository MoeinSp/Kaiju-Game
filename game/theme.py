"""Theme snapshots — «لودآوت»: the look of the bot, saved and switched as a unit.

A theme here means everything that changes how the bot *looks and reads* without
changing what players own:

* message-body Premium emoji (game/emoji.py)
* button icon Premium emoji (game/button_emoji.py)
* the button colour palette (game/button_style.py)

Deliberately **not** included: players, creatures, buildings, currencies,
alliances, seasons — any game state at all. Switching a loadout must be a safe,
reversible, cosmetic act; if it could also roll back someone's gold it would be
a restore, not a theme switch, and nobody would dare press it. Whole-database
backup and restore is a separate operation with a separate confirmation
(game/backup.py).

Snapshots are plain JSON so they can be exported from one deployment and
imported into another — the schema is versioned for exactly that reason.
"""

from __future__ import annotations

import json
from typing import Any

from django.db import transaction
from django.utils import timezone

from bio_lab.models import (
    ButtonEmojiOverride,
    ButtonStyleOverride,
    EmojiOverride,
    ThemeLoadout,
)
from game import button_emoji, button_style, emoji

SNAPSHOT_VERSION = 1


def refresh_theme_caches() -> None:
    """Reload all three in-memory caches from the DB.

    Must run from sync context. Every write path that touches a theme table has
    to call this, and bot.main calls it once at startup — the caches are never
    lazily populated because they're read from async handler code, where a
    Django query raises SynchronousOnlyOperation.
    """
    emoji.refresh_cache()
    button_emoji.refresh_cache()
    button_style.refresh_cache()


def capture_snapshot() -> dict[str, Any]:
    """The current look of the bot, as a serialisable dict."""
    return {
        "version": SNAPSHOT_VERSION,
        "captured_at": timezone.now().isoformat(),
        "emoji": [
            {"key": o.key, "custom_emoji_id": o.custom_emoji_id, "placeholder": o.placeholder}
            for o in EmojiOverride.objects.order_by("key")
        ],
        "button_emoji": [
            {"key": o.key, "custom_emoji_id": o.custom_emoji_id, "placeholder": o.placeholder}
            for o in ButtonEmojiOverride.objects.order_by("key")
        ],
        "button_styles": button_style.current_palette(),
    }


def snapshot_summary(snapshot: dict[str, Any]) -> dict[str, int]:
    """Counts for the panel's loadout cards, so a theme can be sized up without
    expanding it."""
    palette = snapshot.get("button_styles") or {}
    return {
        "emoji": len(snapshot.get("emoji") or []),
        "button_emoji": len(snapshot.get("button_emoji") or []),
        "button_styles": sum(
            1
            for role, style in palette.items()
            if role in button_style.ROLE_DEFS and style != button_style.ROLE_DEFAULTS[role]
        ),
    }


class ThemeError(Exception):
    """Bad snapshot payload — surfaced to the panel as a form error."""


def validate_snapshot(snapshot: Any) -> dict[str, Any]:
    """Reject anything that isn't a theme snapshot *before* it touches the DB.

    Import accepts a file the operator picked, so this is the trust boundary:
    unknown keys are dropped rather than stored, and a wrong-shaped payload
    fails loudly instead of half-applying.
    """
    if not isinstance(snapshot, dict):
        raise ThemeError("فایل لودآوت باید یک آبجکت JSON باشه.")
    version = snapshot.get("version")
    if version != SNAPSHOT_VERSION:
        raise ThemeError(
            f"نسخه‌ی این لودآوت ({version}) با نسخه‌ی فعلی ({SNAPSHOT_VERSION}) نمی‌خونه."
        )

    def _emoji_rows(raw: Any, field: str) -> list[dict[str, str]]:
        if raw is None:
            return []
        if not isinstance(raw, list):
            raise ThemeError(f"بخش «{field}» باید یک لیست باشه.")
        rows = []
        for item in raw:
            if not isinstance(item, dict):
                raise ThemeError(f"هر آیتم «{field}» باید یک آبجکت باشه.")
            key = str(item.get("key", ""))[:48]
            custom_emoji_id = str(item.get("custom_emoji_id", ""))[:64]
            placeholder = str(item.get("placeholder", ""))[:16]
            if not key or not custom_emoji_id:
                continue
            rows.append(
                {"key": key, "custom_emoji_id": custom_emoji_id, "placeholder": placeholder}
            )
        return rows

    palette_raw = snapshot.get("button_styles") or {}
    if not isinstance(palette_raw, dict):
        raise ThemeError("بخش «button_styles» باید یک آبجکت باشه.")
    palette = {
        role: style
        for role, style in palette_raw.items()
        if role in button_style.ROLE_DEFS and style in button_style.STYLE_CHOICES
    }

    return {
        "version": SNAPSHOT_VERSION,
        "captured_at": snapshot.get("captured_at"),
        "emoji": _emoji_rows(snapshot.get("emoji"), "emoji"),
        "button_emoji": _emoji_rows(snapshot.get("button_emoji"), "button_emoji"),
        "button_styles": palette,
    }


@transaction.atomic
def apply_snapshot(snapshot: dict[str, Any]) -> dict[str, int]:
    """Make `snapshot` the live look of the bot.

    A full replace, not a merge: overrides absent from the snapshot are deleted.
    Merging would make loadouts accumulate — switch to a minimal theme and you'd
    still be carrying icons from the previous one, which is the opposite of what
    "switch theme" means.
    """
    clean = validate_snapshot(snapshot)

    EmojiOverride.objects.all().delete()
    EmojiOverride.objects.bulk_create(
        [EmojiOverride(**row) for row in clean["emoji"] if row["key"] in emoji.EMOJI_DEFS]
    )

    ButtonEmojiOverride.objects.all().delete()
    ButtonEmojiOverride.objects.bulk_create(
        [
            ButtonEmojiOverride(**row)
            for row in clean["button_emoji"]
            if row["key"] in button_emoji.BUTTON_EMOJI_DEFS
        ]
    )

    button_style.apply_palette(clean["button_styles"])
    refresh_theme_caches()
    return snapshot_summary(clean)


# --- stored loadouts -------------------------------------------------------


def list_loadouts() -> list[ThemeLoadout]:
    return list(ThemeLoadout.objects.order_by("-is_active", "name"))


def save_loadout(name: str, note: str = "", loadout_id: int | None = None) -> ThemeLoadout:
    """Snapshot the current look under `name`. With `loadout_id`, overwrites that
    slot instead (the "update this loadout from what's live now" action)."""
    name = (name or "").strip()[:48]
    if not name:
        raise ThemeError("اسم لودآوت نمی‌تونه خالی باشه.")
    payload = capture_snapshot()
    if loadout_id is not None:
        loadout = ThemeLoadout.objects.filter(id=loadout_id).first()
        if loadout is None:
            raise ThemeError("این لودآوت پیدا نشد.")
        loadout.name = name
        loadout.note = note[:200]
        loadout.payload = json.dumps(payload, ensure_ascii=False)
        loadout.save(update_fields=["name", "note", "payload", "updated_at"])
        return loadout
    if ThemeLoadout.objects.filter(name=name).exists():
        raise ThemeError(f"لودآوتی با اسم «{name}» از قبل هست.")
    return ThemeLoadout.objects.create(
        name=name, note=note[:200], payload=json.dumps(payload, ensure_ascii=False)
    )


def loadout_snapshot(loadout: ThemeLoadout) -> dict[str, Any]:
    try:
        return json.loads(loadout.payload)
    except (TypeError, ValueError) as exc:
        raise ThemeError(f"محتوای لودآوت «{loadout.name}» خرابه.") from exc


@transaction.atomic
def activate_loadout(loadout_id: int) -> tuple[ThemeLoadout, dict[str, int]]:
    loadout = ThemeLoadout.objects.filter(id=loadout_id).first()
    if loadout is None:
        raise ThemeError("این لودآوت پیدا نشد.")
    summary = apply_snapshot(loadout_snapshot(loadout))
    ThemeLoadout.objects.update(is_active=False)
    ThemeLoadout.objects.filter(id=loadout.id).update(is_active=True, applied_at=timezone.now())
    loadout.refresh_from_db()
    return loadout, summary


def duplicate_loadout(loadout_id: int) -> ThemeLoadout:
    loadout = ThemeLoadout.objects.filter(id=loadout_id).first()
    if loadout is None:
        raise ThemeError("این لودآوت پیدا نشد.")
    base = f"{loadout.name} (کپی)"
    name, n = base, 2
    while ThemeLoadout.objects.filter(name=name).exists():
        name = f"{base} {n}"
        n += 1
    return ThemeLoadout.objects.create(name=name[:48], note=loadout.note, payload=loadout.payload)


def delete_loadout(loadout_id: int) -> bool:
    deleted, _ = ThemeLoadout.objects.filter(id=loadout_id).delete()
    return deleted > 0


def import_loadout(name: str, raw_json: str | bytes, note: str = "") -> ThemeLoadout:
    """Store an exported snapshot as a new loadout. Validated but *not* applied —
    importing shouldn't silently repaint a live bot; activating is a separate,
    deliberate click."""
    if isinstance(raw_json, bytes):
        raw_json = raw_json.decode("utf-8", errors="replace")
    try:
        snapshot = json.loads(raw_json)
    except ValueError as exc:
        raise ThemeError("فایل انتخاب‌شده JSON معتبر نیست.") from exc
    clean = validate_snapshot(snapshot)
    name = (name or "").strip()[:48] or "لودآوت واردشده"
    base, n = name, 2
    while ThemeLoadout.objects.filter(name=name).exists():
        name = f"{base} {n}"
        n += 1
    return ThemeLoadout.objects.create(
        name=name, note=note[:200], payload=json.dumps(clean, ensure_ascii=False)
    )

"""Whole-database backup and restore.

Deliberately different in kind from game/theme.py. A theme loadout is cosmetic
and safe to switch on a whim; a backup contains every player's gold, creatures
and buildings, so restoring one **destroys current game state** and needs an
explicit, typed confirmation from the operator.

Format: one gzipped JSON file holding a manifest plus Django's own serialised
objects::

    {"manifest": {...}, "objects": [ …django dumpdata output… ]}

Gzipped JSON rather than a raw ``pg_dump``/``.db`` copy on purpose — the point
is that a backup taken on the SQLite dev box restores onto the Postgres VPS.
That only works if the dump is engine-neutral, which Django's serialiser is and
a binary dump is not.

What's excluded and why:

* ``contenttypes`` and ``auth.Permission`` — recreated by ``migrate``. Including
  them makes restore fail on primary-key collisions the moment the target
  database has a different app/model ordering.
* ``sessions`` — restoring someone else's session cookies is pointless and
  slightly hostile. Everyone just logs in again.

``auth.User`` *is* included, so restoring onto a fresh VPS brings the panel
logins with it. That does mean a restore can change the password you're logged
in with, which is why the panel warns about it.
"""

from __future__ import annotations

import gzip
import io
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO

import django
from django.conf import settings
from django.core import serializers
from django.core.management import call_command
from django.db import transaction
from django.utils import timezone

BACKUP_FORMAT_VERSION = 1

# Everything the game owns, plus panel logins. Order matters for restore only in
# that Django resolves forward references itself, so this is just "what to dump".
BACKUP_APPS = ["bio_lab", "auth"]
BACKUP_EXCLUDE = ["contenttypes", "auth.permission", "sessions", "admin.logentry"]

_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")


class BackupError(Exception):
    """Anything that should be shown to the operator instead of a traceback."""


def backup_dir() -> Path:
    path = Path(getattr(settings, "BACKUP_DIR", Path(settings.BASE_DIR) / "backups"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_label(label: str) -> str:
    label = _SAFE_NAME.sub("-", (label or "").strip())[:40].strip("-")
    return label or "backup"


def _manifest(object_count: int, label: str) -> dict[str, Any]:
    engine = settings.DATABASES["default"]["ENGINE"].rsplit(".", 1)[-1]
    return {
        "format_version": BACKUP_FORMAT_VERSION,
        "created_at": timezone.now().isoformat(),
        "label": label,
        "django_version": django.get_version(),
        "db_engine": engine,
        "apps": BACKUP_APPS,
        "excluded": BACKUP_EXCLUDE,
        "object_count": object_count,
    }


def create_backup(label: str = "") -> dict[str, Any]:
    """Write a new backup file and return its metadata."""
    buffer = io.StringIO()
    call_command(
        "dumpdata",
        *BACKUP_APPS,
        *[f"--exclude={item}" for item in BACKUP_EXCLUDE],
        "--natural-foreign",
        "--natural-primary",
        stdout=buffer,
    )
    objects = json.loads(buffer.getvalue() or "[]")

    label = _safe_label(label)
    stamp = timezone.now().strftime("%Y%m%d-%H%M%S")
    path = backup_dir() / f"{stamp}_{label}.json.gz"
    payload = {"manifest": _manifest(len(objects), label), "objects": objects}
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)
    return describe_backup(path)


def describe_backup(path: Path) -> dict[str, Any]:
    """Metadata for one backup file, read without loading the objects.

    Files that aren't ours (or are truncated) are reported with
    ``manifest: None`` rather than raising — a broken file in the directory
    shouldn't take the whole listing page down.
    """
    stat = path.stat()
    manifest: dict[str, Any] | None = None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            # the manifest is first in the file, but json has no streaming reader
            # here; these archives are small enough that a full parse is fine
            data = json.load(fh)
        if isinstance(data, dict) and isinstance(data.get("manifest"), dict):
            manifest = data["manifest"]
    except (OSError, ValueError):
        manifest = None
    return {
        "name": path.name,
        "path": path,
        "size_bytes": stat.st_size,
        "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.get_current_timezone()),
        "manifest": manifest,
    }


def list_backups() -> list[dict[str, Any]]:
    return sorted(
        (describe_backup(p) for p in backup_dir().glob("*.json.gz")),
        key=lambda item: item["name"],
        reverse=True,
    )


def resolve_backup(name: str) -> Path:
    """Turn a user-supplied filename into a path inside the backup directory.

    The containment check is the security boundary: ``name`` arrives from a form
    field, and without it a crafted ``../../`` would let the panel read or delete
    arbitrary files.
    """
    candidate = (backup_dir() / Path(name).name).resolve()
    if candidate.parent != backup_dir().resolve() or not candidate.is_file():
        raise BackupError("این فایل پشتیبان پیدا نشد.")
    return candidate


def delete_backup(name: str) -> None:
    resolve_backup(name).unlink()


def read_payload(source: Path | BinaryIO) -> dict[str, Any]:
    """Parse and sanity-check an archive without applying it."""
    try:
        if isinstance(source, Path):
            with gzip.open(source, "rt", encoding="utf-8") as fh:
                data = json.load(fh)
        else:
            with gzip.open(source, "rt", encoding="utf-8") as fh:
                data = json.load(fh)
    except (OSError, ValueError, EOFError) as exc:
        raise BackupError("فایل پشتیبان خراب یا با فرمت اشتباهه (باید .json.gz باشه).") from exc

    if not isinstance(data, dict) or "objects" not in data:
        raise BackupError("ساختار فایل پشتیبان درست نیست.")
    manifest = data.get("manifest") or {}
    version = manifest.get("format_version")
    if version != BACKUP_FORMAT_VERSION:
        raise BackupError(
            f"نسخه‌ی فرمت این پشتیبان ({version}) با نسخه‌ی فعلی ({BACKUP_FORMAT_VERSION}) نمی‌خونه."
        )
    if not isinstance(data["objects"], list):
        raise BackupError("بخش objects باید یک لیست باشه.")
    return data


def restore_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Replace the entire database contents with `data`.

    Wrapped in one transaction so a mid-restore failure leaves the old data
    intact instead of a half-wiped database — the single most important property
    of this function. Everything is deserialised *before* the flush for the same
    reason: a malformed object should abort while the old rows are still there.
    """
    objects = list(
        serializers.deserialize("json", json.dumps(data["objects"]), ignorenonexistent=True)
    )

    with transaction.atomic():
        # flush truncates every table and re-emits post_migrate, which recreates
        # contenttypes and permissions — the two things the dump deliberately omits
        call_command("flush", "--noinput", verbosity=0)
        for obj in objects:
            obj.save()

    # the restored rows include the theme tables, so the in-memory caches the bot
    # reads from are now stale; refresh them here rather than at every call site
    from game.theme import refresh_theme_caches

    refresh_theme_caches()

    return {
        "object_count": len(objects),
        "manifest": data.get("manifest") or {},
    }


def restore_from_file(name: str) -> dict[str, Any]:
    return restore_payload(read_payload(resolve_backup(name)))


def restore_from_upload(fileobj: BinaryIO) -> dict[str, Any]:
    return restore_payload(read_payload(fileobj))


def store_upload(fileobj: BinaryIO, label: str = "uploaded") -> dict[str, Any]:
    """Validate an uploaded archive and keep it alongside the local ones, so an
    operator can move a backup between machines without restoring it right away."""
    data = read_payload(fileobj)
    stamp = timezone.now().strftime("%Y%m%d-%H%M%S")
    path = backup_dir() / f"{stamp}_{_safe_label(label)}.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    return describe_backup(path)

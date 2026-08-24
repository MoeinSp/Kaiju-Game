"""Extra admins — accounts the owner grants full admin-panel access, except the
ability to add or remove other admins (that stays owner-only).

Read through an in-memory set for the same reason game.botconfig does: the panel's
access check runs inside async handler code, where a lazy DB query would raise
SynchronousOnlyOperation. bot.main warms the cache at startup; every grant/revoke
refreshes it.
"""

from __future__ import annotations

from bio_lab.models import User

_admin_ids: set[int] = set()


def refresh_cache() -> None:
    """Reload admin ids from the DB. Sync context only (startup / after a write)."""
    global _admin_ids
    _admin_ids = set(User.objects.filter(is_admin=True).values_list("id", flat=True))


def is_admin(user_id: int) -> bool:
    """Pure in-memory check — safe from async handler code."""
    return user_id in _admin_ids


def list_admins() -> list[User]:
    return list(User.objects.filter(is_admin=True).order_by("id"))


def add_admin(identifier: str) -> User:
    from game.moderation import find_user_or_raise

    user = find_user_or_raise(identifier)
    if not user.is_admin:
        user.is_admin = True
        user.save(update_fields=["is_admin"])
        refresh_cache()
    return user


def remove_admin(user_id: int) -> None:
    User.objects.filter(id=user_id).update(is_admin=False)
    refresh_cache()

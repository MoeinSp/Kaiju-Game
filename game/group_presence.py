"""Who has LEFT a group, and since when — so a group «اتک» can't target someone long gone.

A player who left a group more than LEFT_GRACE_HOURS ago can no longer be reply-attacked
there. Within the grace window they still can (leaving must not be a way to dodge a fight).

Sources of «left» (GroupMembership.left_at):
* the «X left the group» service message (bots get it even with privacy mode on);
* chat_member updates (only when the bot is an admin);
* at attack time, a live getChatMember answer of left/kicked with no recorded time —
  then the clock starts NOW (first time we saw it), never in the past.
Any sign of being back (joined again, wrote in the group, getChatMember says member)
clears it.
"""

from __future__ import annotations

import datetime

from django.utils import timezone

from bio_lab.models import Group, GroupMembership, User

LEFT_GRACE_HOURS = 24


def mark_left(group_id: int, title: str | None, user_id: int) -> None:
    """Record that `user_id` left `group_id` (keeps the EARLIEST time if already set)."""
    if not User.objects.filter(id=user_id).exists():
        return  # never played — nothing to protect
    group, _ = Group.objects.get_or_create(id=group_id, defaults={"title": (title or "")[:128] or None})
    m, _ = GroupMembership.objects.get_or_create(group=group, user_id=user_id)
    if m.left_at is None:
        m.left_at = timezone.now()
        m.save(update_fields=["left_at"])


def mark_back(group_id: int, user_id: int) -> None:
    GroupMembership.objects.filter(group_id=group_id, user_id=user_id, left_at__isnull=False).update(left_at=None)


def observe(group_id: int, title: str | None, user_id: int, live_left: bool | None) -> datetime.datetime | None:
    """Fold in a live getChatMember answer (None = unknown) and return when they left, if they did."""
    if live_left is False:
        mark_back(group_id, user_id)
        return None
    if live_left is True:
        mark_left(group_id, title, user_id)
    return (GroupMembership.objects.filter(group_id=group_id, user_id=user_id)
            .values_list("left_at", flat=True).first())


def is_gone(left_at: datetime.datetime | None) -> bool:
    return left_at is not None and timezone.now() - left_at >= datetime.timedelta(hours=LEFT_GRACE_HOURS)


def ago_text(left_at: datetime.datetime) -> str:
    secs = max(0, int((timezone.now() - left_at).total_seconds()))
    days, hours = secs // 86400, (secs % 86400) // 3600
    if days:
        return f"{days} روز" + (f" و {hours} ساعت" if hours else "")
    return f"{hours} ساعت" if hours else f"{max(1, secs // 60)} دقیقه"


def grace_left_text(left_at: datetime.datetime) -> str:
    secs = max(0, int((left_at + datetime.timedelta(hours=LEFT_GRACE_HOURS) - timezone.now()).total_seconds()))
    hours, minutes = secs // 3600, (secs % 3600) // 60
    return f"{hours} ساعت و {minutes} دقیقه" if hours else f"{minutes} دقیقه"

"""Reward chests that wait for the player to open them («باز کردن جعبه»).

Used where a chest is a REWARD (the world boss today) rather than an arena slot: the chest is
stored unopened, the player gets a message with an «open» button, and opening it rolls and
grants the contents with game.arena_chests.grant_chest_contents — the same table the arena
chests use. A chest is opened at most once (row lock + opened_at).
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from bio_lab.models import RewardChest, User
from game.creature import GameError


def give(user: User, chest_type: str, source: str, ref: int | None = None) -> RewardChest:
    return RewardChest.objects.create(user=user, chest_type=chest_type, source=source, ref=ref)


def unopened(user: User, source: str | None = None, ref: int | None = None):
    qs = RewardChest.objects.filter(user=user, opened_at__isnull=True)
    if source:
        qs = qs.filter(source=source)
    if ref is not None:
        qs = qs.filter(ref=ref)
    return qs.order_by("id")


@transaction.atomic
def open_all(user: User, source: str | None = None, ref: int | None = None) -> list[dict]:
    """Open every unopened chest (of this source / boss). Returns the grant results, in order."""
    from game.arena_chests import grant_chest_contents

    chests = list(unopened(user, source, ref).select_for_update())
    if not chests:
        raise GameError("جعبه‌ی باز نشده‌ای نداری؛ شاید قبلاً بازشون کردی.")
    user = User.objects.select_for_update().get(pk=user.pk)
    out = []
    now = timezone.now()
    for chest in chests:
        res = grant_chest_contents(user, chest.chest_type, user.cup, source=chest.source)
        chest.opened_at = now
        chest.save(update_fields=["opened_at"])
        out.append(res)
    return out

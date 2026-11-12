"""«🏆 لیگ گروه‌ها» — a weekly table of the groups whose members win the most group PvP
fights (GroupWeekScore, PVP_WIN_POINTS per win). Just a leaderboard: there are no Monday
prizes and no group-vs-group war any more (owner, 2026-11-13) — the group war is the
alliance war, fought from the alliance's home group (game/group_growth.py).
"""

from __future__ import annotations

from django.db import transaction
from django.db.models import F

from bio_lab.models import GroupWeekScore

PVP_WIN_POINTS = 3


def _week(when=None) -> str:
    from game.season import week_key

    return week_key(when)


def add_points(group_id: int, points: int) -> None:
    if points <= 0 or not group_id:
        return
    row, _ = GroupWeekScore.objects.get_or_create(group_id=group_id, week=_week())
    GroupWeekScore.objects.filter(id=row.id).update(points=F("points") + points)


def on_pvp_win(group_id: int) -> None:
    try:
        with transaction.atomic():   # a savepoint: a failed write can't poison the fight's transaction
            add_points(group_id, PVP_WIN_POINTS)
    except Exception:  # noqa: BLE001 — league points must never break a fight
        pass


def league_top(limit: int = 10, week: str | None = None) -> list[dict]:
    rows = (GroupWeekScore.objects.filter(week=week or _week(), points__gt=0).select_related("group")
            .order_by("-points", "group_id")[:limit])
    return [{"group_id": r.group_id, "title": r.group.title or str(r.group_id), "points": r.points} for r in rows]


def group_rank(group_id: int) -> tuple[int | None, int]:
    week = _week()
    mine = GroupWeekScore.objects.filter(group_id=group_id, week=week).values_list("points", flat=True).first() or 0
    if not mine:
        return None, 0
    ahead = GroupWeekScore.objects.filter(week=week, points__gt=mine).count()
    return ahead + 1, mine

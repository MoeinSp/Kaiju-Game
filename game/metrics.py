"""Lightweight usage metrics for the admin panel — «who was active, hour by hour» and
«which buttons get pressed».

Two things the game couldn't answer before:

* **Active players at a given time of day.** DailyActionLog only has per-day counters, so
  «today at 15:00 vs yesterday at 15:00» was impossible. ActivityHour holds one row per
  (player, day, hour) in which the player did anything (pressed a button, sent the bot a
  private message, or completed a counted action in a group).
* **Which screens are used.** ButtonClick counts presses per day per button «key»
  (`menu:hunt`, `dsp:offer`, …) so rarely-opened sections can be found from data.

Nothing here touches the database on the hot path: handlers call mark()/click(), which
only update in-memory sets/counters; flush() (a JobQueue job, every minute) writes them.
At most a minute of marks is lost on a restart, which is fine for statistics.
"""

from __future__ import annotations

import datetime
import re
import threading
from collections import Counter

from django.db.models import Count, F, Sum
from django.utils import timezone

KEEP_DAYS = 120

_lock = threading.Lock()
_pending_marks: set[tuple[int, str, int, bool]] = set()
_written: set[tuple[int, str, int, bool]] = set()  # marks already in the DB (this process)
_pending_clicks: Counter = Counter()

_ID_PART = re.compile(r"^-?\d+$")


def _now() -> tuple[str, int]:
    local = timezone.localtime()
    return local.date().isoformat(), local.hour


def mark(user_id: int | None, private: bool = False) -> None:
    """Note that `user_id` was active this hour. In-memory; safe from any thread."""
    if not user_id:
        return
    day, hour = _now()
    uid, private = int(user_id), bool(private)
    # a «private» mark covers the group one for the same hour, never the other way round
    if (uid, day, hour, True) in _written or (uid, day, hour, private) in _written:
        return
    with _lock:
        _pending_marks.add((uid, day, hour, private))


def click_key(data: str | None) -> str | None:
    """Callback data → a stable, id-free button key: `menu:hunt`, `dsp:offer`, `wboss:hit`.
    Numeric parts (ids, pages) are dropped so one button is one key."""
    if not data:
        return None
    parts = [p for p in data.split(":") if p and not _ID_PART.match(p)]
    if not parts:
        return None
    return ":".join(parts[:2])[:48]


def click(data: str | None) -> None:
    key = click_key(data)
    if key is None:
        return
    day, _hour = _now()
    with _lock:
        _pending_clicks[(day, key)] += 1


def flush() -> None:
    """Write everything collected since the last flush (sync / ORM context)."""
    from bio_lab.models import ActivityHour, ButtonClick

    with _lock:
        marks = list(_pending_marks)
        _pending_marks.clear()
        clicks = dict(_pending_clicks)
        _pending_clicks.clear()
    if marks:
        ActivityHour.objects.bulk_create(
            [ActivityHour(user_id=u, day=d, hour=h, private=p) for u, d, h, p in marks],
            ignore_conflicts=True,
        )
        # a row first written from a group action can still become «used the private chat»
        private_marks = [(u, d, h) for u, d, h, p in marks if p]
        for u, d, h in private_marks:
            ActivityHour.objects.filter(user_id=u, day=d, hour=h, private=False).update(private=True)
        today = _now()[0]
        with _lock:
            _written.update(marks)
            for key in [k for k in _written if k[1] != today]:
                _written.discard(key)
    for (day, key), n in clicks.items():
        updated = ButtonClick.objects.filter(day=day, key=key).update(count=F("count") + n)
        if not updated:
            ButtonClick.objects.bulk_create([ButtonClick(day=day, key=key, count=n)], ignore_conflicts=True)


def prune() -> None:
    from bio_lab.models import ActivityHour, ButtonClick

    cutoff = (timezone.localdate() - datetime.timedelta(days=KEEP_DAYS)).isoformat()
    ActivityHour.objects.filter(day__lt=cutoff).delete()
    ButtonClick.objects.filter(day__lt=cutoff).delete()


# ── reports ───────────────────────────────────────────────────────────────────
def _active(day: str, upto_hour: int | None = None) -> dict | None:
    """Distinct active players of `day` (optionally only hours ≤ upto_hour) from the
    hourly table, or None when that day has no hourly data (before this was deployed)."""
    from bio_lab.models import ActivityHour

    qs = ActivityHour.objects.filter(day=day)
    if not qs.exists():
        return None
    if upto_hour is not None:
        qs = qs.filter(hour__lte=upto_hour)
    return {
        "total": qs.values("user_id").distinct().count(),
        "private": qs.filter(private=True).values("user_id").distinct().count(),
    }


def _active_by_actions(day: str) -> int:
    """Fallback for days without hourly data: players with any counted action that day."""
    from bio_lab.models import DailyActionLog

    return DailyActionLog.objects.filter(day=day, count__gt=0).values("user_id").distinct().count()


def _new_users(day: datetime.date, upto: datetime.datetime | None = None) -> int:
    from bio_lab.models import User

    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.datetime.combine(day, datetime.time.min), tz)
    end = upto or start + datetime.timedelta(days=1)
    return User.objects.filter(created_at__gte=start, created_at__lt=end).count()


def _actions(day: str) -> dict[str, tuple[int, int]]:
    from bio_lab.models import DailyActionLog

    rows = (
        DailyActionLog.objects.filter(day=day, count__gt=0)
        .values("action").annotate(users=Count("user_id", distinct=True), total=Sum("count"))
    )
    return {r["action"]: (r["users"], r["total"] or 0) for r in rows}


def change(now: int, before: int) -> dict:
    """{'diff', 'pct', 'trend'} — trend is 'up' / 'down' / 'flat' (within ±2%)."""
    diff = now - before
    pct = (100.0 * diff / before) if before else (100.0 if now else 0.0)
    trend = "flat" if abs(pct) < 2 else ("up" if diff > 0 else "down")
    return {"diff": diff, "pct": pct, "trend": trend}


def day_report(day: datetime.date) -> dict:
    """Everything the admin «آمار روزانه» screen shows for `day`, compared with the day
    before. For TODAY the comparison is like-for-like: yesterday up to the same hour."""
    from bio_lab.models import ActivityHour, ButtonClick

    flush()  # include the last minute
    today = timezone.localdate()
    is_today = day == today
    prev = day - datetime.timedelta(days=1)
    d, p = day.isoformat(), prev.isoformat()
    now_local = timezone.localtime()
    hour = now_local.hour if is_today else None

    active = _active(d, hour)
    prev_active = _active(p, hour)
    hourly_ok = active is not None and prev_active is not None
    if hourly_ok:
        active_now, active_before, basis = active["total"], prev_active["total"], "hourly"
    else:
        # one of the two days predates the hourly table → compare whole days by actions
        active_now, active_before, basis = _active_by_actions(d), _active_by_actions(p), "actions"

    new_now = _new_users(day, timezone.now() if is_today else None)
    new_before = _new_users(prev, timezone.now() - datetime.timedelta(days=1) if is_today else None)

    acts, prev_acts = _actions(d), _actions(p)
    top_actions = sorted(acts.items(), key=lambda kv: -kv[1][0])[:8]

    by_hour = dict(
        ActivityHour.objects.filter(day=d).values_list("hour").annotate(n=Count("user_id", distinct=True))
    )
    peak = max(by_hour.items(), key=lambda kv: kv[1]) if by_hour else None

    clicks = list(ButtonClick.objects.filter(day=d).order_by("-count").values_list("key", "count"))
    menu_clicks = [(k, n) for k, n in clicks if k.startswith("menu:")]

    return {
        "day": day, "is_today": is_today, "hour": hour, "basis": basis,
        "active": active_now, "active_before": active_before, "active_change": change(active_now, active_before),
        "active_private": active["private"] if active else None,
        "active_full_prev": (_active(p) or {}).get("total") if is_today else None,
        "new": new_now, "new_before": new_before, "new_change": change(new_now, new_before),
        "actions": [(a, u, t, prev_acts.get(a, (0, 0))[0]) for a, (u, t) in top_actions],
        "peak": peak,
        "clicks_total": sum(n for _k, n in clicks),
        "top_clicks": clicks[:8],
        "low_menu_clicks": sorted(menu_clicks, key=lambda kv: kv[1])[:6],
        "has_prev_day": True,
    }

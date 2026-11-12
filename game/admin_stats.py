"""Data for the owner's «📊 آمار کلی» dashboard (bot/handlers/owner.py) beyond the single
day report of game/metrics.day_report: how the game is MOVING.

* trend      — daily active / new players / day-1 return over N days, this week vs last
* retention  — new-player cohorts (D1/D3/D7), the level funnel, level spread, churn
* money      — approved sales by day, buyers, ARPPU, subscriptions, the economy totals and
               how they changed (StatSnapshot, written hourly by snapshot_today)
* growth     — where new players come from (invite vs organic), top inviters, groups,
               alliances and the growth features (newbie calendar, group boss, group war,
               friend challenge)

Everything is plain sync ORM returning plain dicts/lists: the handler formats them and
game/stats_chart.py draws them. Active players come from ActivityHour (game/metrics.py);
days from before that table existed fall back to DailyActionLog (players with any counted
action), and each series says which days used the fallback.
"""

from __future__ import annotations

import datetime

from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

LEVEL_FUNNEL = (2, 3, 5, 10, 15, 20, 30)
LEVEL_BUCKETS = ((1, 1), (2, 4), (5, 9), (10, 19), (20, 29), (30, 999))


def _today() -> datetime.date:
    return timezone.localdate()


def _days(n: int, end: datetime.date | None = None) -> list[datetime.date]:
    end = end or _today()
    return [end - datetime.timedelta(days=i) for i in range(n - 1, -1, -1)]


def _start_of(day: datetime.date) -> datetime.datetime:
    return timezone.make_aware(datetime.datetime.combine(day, datetime.time.min), timezone.get_current_timezone())


def _pct(part: float, whole: float) -> float | None:
    return (100.0 * part / whole) if whole else None


# ── active players ────────────────────────────────────────────────────────────
def _dau(days: list[datetime.date]) -> tuple[dict[str, int], set[str]]:
    """{iso day: distinct active players}, plus the days that used the per-action fallback."""
    from bio_lab.models import ActivityHour, DailyActionLog

    isos = [d.isoformat() for d in days]
    hourly = dict(ActivityHour.objects.filter(day__in=isos).values_list("day")
                  .annotate(n=Count("user_id", distinct=True)))
    missing = [d for d in isos if d not in hourly]
    fallback = {}
    if missing:
        fallback = dict(DailyActionLog.objects.filter(day__in=missing, count__gt=0).values_list("day")
                        .annotate(n=Count("user_id", distinct=True)))
    out = {d: hourly.get(d, fallback.get(d, 0)) for d in isos}
    return out, {d for d in missing if fallback.get(d)}


def _active_ids(first: datetime.date, last: datetime.date) -> set[int]:
    """Everyone active on any day in [first, last]."""
    from bio_lab.models import ActivityHour, DailyActionLog

    a, b = first.isoformat(), last.isoformat()
    ids = set(ActivityHour.objects.filter(day__gte=a, day__lte=b).values_list("user_id", flat=True).distinct())
    if not ActivityHour.objects.filter(day=a).exists():   # the window starts before the hourly table
        ids |= set(DailyActionLog.objects.filter(day__gte=a, day__lte=b, count__gt=0)
                   .values_list("user_id", flat=True).distinct())
    return ids


def _returned(ids: list[int], day: datetime.date) -> int:
    """How many of `ids` were active on `day`."""
    from bio_lab.models import ActivityHour, DailyActionLog

    if not ids:
        return 0
    d = day.isoformat()
    if ActivityHour.objects.filter(day=d).exists():
        return ActivityHour.objects.filter(day=d, user_id__in=ids).values("user_id").distinct().count()
    return DailyActionLog.objects.filter(day=d, user_id__in=ids, count__gt=0).values("user_id").distinct().count()


def _new_by_day(days: list[datetime.date], **filt) -> dict[str, int]:
    from bio_lab.models import User

    tz = timezone.get_current_timezone()
    rows = (User.objects.filter(created_at__gte=_start_of(days[0]), **filt)
            .annotate(d=TruncDate("created_at", tzinfo=tz)).values_list("d").annotate(n=Count("id")))
    got = {d.isoformat(): n for d, n in rows if d is not None}
    return {d.isoformat(): got.get(d.isoformat(), 0) for d in days}


def _cohort_ids(day: datetime.date) -> list[int]:
    from bio_lab.models import User

    return list(User.objects.filter(created_at__gte=_start_of(day), created_at__lt=_start_of(day + datetime.timedelta(days=1)))
                .values_list("id", flat=True))


# ── 1. trend ──────────────────────────────────────────────────────────────────
def trend(n: int = 14) -> dict:
    today = _today()
    days = _days(n)
    dau, approx = _dau(days)
    new = _new_by_day(days)
    rows = []
    for d in days:
        iso = d.isoformat()
        d1 = None
        if d + datetime.timedelta(days=1) < today:          # only days whose next day is complete
            ids = _cohort_ids(d)
            d1 = _pct(_returned(ids, d + datetime.timedelta(days=1)), len(ids))
        rows.append({"day": d, "dau": dau[iso], "new": new[iso], "d1": d1, "approx": iso in approx,
                     "partial": d == today})

    # this week vs the week before — whole days only (today is still running)
    week = _days(7, today - datetime.timedelta(days=1))
    prev = _days(7, today - datetime.timedelta(days=8))
    dw, _ = _dau(week)
    dp, _ = _dau(prev)
    nw, np_ = _new_by_day(week), _new_by_day(prev)
    avg_w, avg_p = sum(dw.values()) / 7, sum(dp.values()) / 7
    wau = len(_active_ids(today - datetime.timedelta(days=6), today))
    mau = len(_active_ids(today - datetime.timedelta(days=29), today))
    return {
        "rows": rows,
        "avg_week": avg_w, "avg_prev": avg_p,
        "new_week": sum(nw.values()), "new_prev": sum(np_.values()),
        "wau": wau, "mau": mau, "stickiness": _pct(avg_w, mau),   # average DAU / MAU
        "best": max(rows[:-1] or rows, key=lambda r: r["dau"]),
    }


# ── 2. retention ──────────────────────────────────────────────────────────────
def retention(cohorts: int = 10) -> dict:
    from bio_lab.models import User
    from game import lab

    today = _today()
    out = []
    for i in range(cohorts, 0, -1):
        d = today - datetime.timedelta(days=i)
        ids = _cohort_ids(d)
        row = {"day": d, "new": len(ids)}
        for k in (1, 3, 7):
            target = d + datetime.timedelta(days=k)
            row[f"d{k}"] = _pct(_returned(ids, target), len(ids)) if target < today else None
        out.append(row)

    def wavg(key):
        rows = [r for r in out if r[key] is not None and r["new"]]
        tot = sum(r["new"] for r in rows)
        return (sum(r[key] * r["new"] for r in rows) / tot) if tot else None

    xp = {lv: lab.xp_for_level(lv) for lv in LEVEL_FUNNEL}
    agg = User.objects.filter(is_banned=False).aggregate(
        total=Count("id"), tutorial=Count("id", filter=Q(onboarding_completed=True)),
        **{f"l{lv}": Count("id", filter=Q(lab_xp__gte=x)) for lv, x in xp.items()})
    funnel = [("ثبت‌نام", agg["total"]), ("آموزش", agg["tutorial"])] + [(f"سطح {lv}", agg[f"l{lv}"]) for lv in LEVEL_FUNNEL]

    # the same funnel only for the last 30 days' newcomers (at least 3 days old) — is it improving?
    recent = User.objects.filter(is_banned=False, created_at__gte=_start_of(today - datetime.timedelta(days=30)),
                                 created_at__lt=_start_of(today - datetime.timedelta(days=3)))
    ragg = recent.aggregate(total=Count("id"), **{f"l{lv}": Count("id", filter=Q(lab_xp__gte=xp[lv])) for lv in (3, 5, 10)})

    bucket_q = {}
    for lo, hi in LEVEL_BUCKETS:
        q = Q(lab_xp__gte=lab.xp_for_level(lo)) if lo > 1 else Q()
        if hi < 999:
            q &= Q(lab_xp__lt=lab.xp_for_level(hi + 1))
        bucket_q[f"b{lo}"] = Count("id", filter=q) if q else Count("id")
    b = User.objects.filter(is_banned=False).aggregate(**bucket_q)
    buckets = [(f"{lo}" if lo == hi else (f"{lo}+" if hi == 999 else f"{lo}–{hi}"), b[f"b{lo}"]) for lo, hi in LEVEL_BUCKETS]

    # churn: active in the week before last, not seen this week
    last7 = _active_ids(today - datetime.timedelta(days=6), today)
    prev7 = _active_ids(today - datetime.timedelta(days=13), today - datetime.timedelta(days=7))
    lost = prev7 - last7
    back = last7 - prev7
    return {
        "cohorts": out, "d1": wavg("d1"), "d3": wavg("d3"), "d7": wavg("d7"),
        "funnel": funnel, "recent": ragg, "buckets": buckets,
        "prev7": len(prev7), "kept": len(prev7 & last7), "lost": len(lost), "came": len(back),
    }


# ── 3. money + economy ────────────────────────────────────────────────────────
def money(n: int = 30) -> dict:
    from bio_lab.models import PurchaseRequest, StatSnapshot, User

    today = _today()
    days = _days(n)
    tz = timezone.get_current_timezone()
    rows = (PurchaseRequest.objects.filter(status="approved", reviewed_at__gte=_start_of(days[0]))
            .annotate(d=TruncDate("reviewed_at", tzinfo=tz)).values("d")
            .annotate(toman=Sum("price_toman"), n=Count("id"), buyers=Count("user_id", distinct=True)))
    by = {r["d"].isoformat(): r for r in rows if r["d"] is not None}
    series = [{"day": d, "toman": (by.get(d.isoformat()) or {}).get("toman") or 0,
               "n": (by.get(d.isoformat()) or {}).get("n") or 0} for d in days]

    def window(first, last):
        q = PurchaseRequest.objects.filter(status="approved", reviewed_at__gte=_start_of(first),
                                           reviewed_at__lt=_start_of(last + datetime.timedelta(days=1)))
        a = q.aggregate(toman=Sum("price_toman"), n=Count("id"), buyers=Count("user_id", distinct=True))
        return {"toman": a["toman"] or 0, "n": a["n"] or 0, "buyers": a["buyers"] or 0}

    w_today = window(today, today)
    w_yday = window(today - datetime.timedelta(days=1), today - datetime.timedelta(days=1))
    w7 = window(today - datetime.timedelta(days=6), today)
    wp7 = window(today - datetime.timedelta(days=13), today - datetime.timedelta(days=7))
    w30 = window(today - datetime.timedelta(days=29), today)
    ever_buyers = PurchaseRequest.objects.filter(status="approved").values("user_id").distinct().count()
    pending = PurchaseRequest.objects.filter(status="pending").count()
    subs = User.objects.filter(subscription_until__gt=timezone.now()).count()
    mau = len(_active_ids(today - datetime.timedelta(days=29), today))

    totals = User.objects.aggregate(coins=Sum("coins"), dna=Sum("dna_fragments"), diamonds=Sum("diamonds"))
    totals = {k: v or 0 for k, v in totals.items()}
    week_ago = StatSnapshot.objects.filter(day__lte=(today - datetime.timedelta(days=7)).isoformat()).order_by("-day").first()
    since = (today - datetime.timedelta(days=6)).isoformat()
    active = User.objects.filter(is_banned=False, last_login_day__gte=since)
    n_active = active.count()

    def median(field):
        if not n_active:
            return 0
        return active.order_by(field).values_list(field, flat=True)[n_active // 2]

    act = active.aggregate(coins=Sum("coins"), diamonds=Sum("diamonds"), dna=Sum("dna_fragments"))
    return {
        "series": series, "today": w_today, "yday": w_yday, "w7": w7, "wp7": wp7, "w30": w30,
        "arppu30": (w30["toman"] / w30["buyers"]) if w30["buyers"] else 0,
        "conv30": _pct(w30["buyers"], mau), "mau": mau,
        "ever_buyers": ever_buyers, "pending": pending, "subs": subs,
        "totals": totals,
        "week_ago": {"day": week_ago.day, "coins": week_ago.coins, "dna": week_ago.dna, "diamonds": week_ago.diamonds}
        if week_ago else None,
        "active7": n_active,
        "avg": {k: ((act[k] or 0) / n_active if n_active else 0) for k in ("coins", "diamonds", "dna")},
        "median": {"coins": median("coins"), "diamonds": median("diamonds"), "dna": median("dna_fragments")},
    }


# ── 4. growth ─────────────────────────────────────────────────────────────────
def growth(n: int = 14) -> dict:
    from bio_lab import models as m
    from bio_lab.repository import display_name

    today = _today()
    days = _days(n)
    invited = _new_by_day(days, referred_by__isnull=False)
    all_new = _new_by_day(days)
    rows = [{"day": d, "invited": invited[d.isoformat()], "organic": all_new[d.isoformat()] - invited[d.isoformat()]}
            for d in days]
    since7 = _start_of(today - datetime.timedelta(days=6))
    since30 = _start_of(today - datetime.timedelta(days=29))

    top = list(m.User.objects.filter(created_at__gte=since7, referred_by__isnull=False)
               .values("referred_by").annotate(n=Count("id")).order_by("-n")[:5])
    names = {u.id: display_name(u) for u in m.User.objects.filter(id__in=[t["referred_by"] for t in top])}
    referral = {
        "new7": sum(r["invited"] for r in rows[-7:]), "all7": sum(r["invited"] + r["organic"] for r in rows[-7:]),
        "grown30": m.User.objects.filter(referred_at__gte=since30, referral_step__gte=1).count(),
        "joined30": m.User.objects.filter(referred_at__gte=since30).count(),
        "top": [(names.get(t["referred_by"], str(t["referred_by"])), t["n"]) for t in top],
    }

    active7 = _active_ids(today - datetime.timedelta(days=6), today)
    active_groups = (m.GroupMembership.objects.filter(left_at__isnull=True, user_id__in=active7)
                     .values("group_id").annotate(n=Count("user_id")).filter(n__gte=3).count()) if active7 else 0
    groups = {
        "total": m.Group.objects.count(),
        "new7": m.Group.objects.filter(created_at__gte=since7).count(),
        "active7": active_groups,
        "added7": m.Group.objects.filter(added_at__gte=since7).count(),
        "adder_paid": m.Group.objects.filter(adder_step__gte=1).count(),
    }
    alliances = {
        "total": m.Alliance.objects.count(),
        "home": m.Alliance.objects.filter(home_group__isnull=False).count(),
        "members": m.User.objects.filter(alliance__isnull=False).count(),
    }
    nb = m.User.objects.filter(newbie_eligible=True).aggregate(
        eligible=Count("id"), d1=Count("id", filter=Q(newbie_day__gte=1)), d3=Count("id", filter=Q(newbie_day__gte=3)),
        d7=Count("id", filter=Q(newbie_day__gte=7)))
    wars = m.AllianceWar.objects.filter(started_at__gte=since7).aggregate(
        n=Count("id"), home=Count("id", filter=Q(alliance_a__home_group__isnull=False)))
    duels = {
        "links": m.FriendChallenge.objects.filter(created_at__gte=since7).count(),
        "runs": m.ChallengeRun.objects.filter(created_at__gte=since7).count(),
        "won": m.ChallengeRun.objects.filter(created_at__gte=since7, finished=True, won=True).count(),
        "newcomers": m.ChallengeRun.objects.filter(created_at__gte=since7, user__created_at__gte=since7).count(),
    }
    return {"rows": rows, "referral": referral, "groups": groups, "alliances": alliances,
            "newbie": nb, "wars": wars, "duels": duels}


# ── extras for the single-day page (owner._stats_day_text) ───────────────────
def day_extras(day: datetime.date) -> dict:
    """7-day average active players up to `day` vs the 7 days before (the real «progress»;
    a single day vs the day before is too noisy), the D1 return of the previous day's
    newcomers, and that day's sales."""
    from bio_lab.models import PurchaseRequest

    last = day if day < _today() else day - datetime.timedelta(days=1)   # whole days only
    a, _ = _dau(_days(7, last))
    b, _ = _dau(_days(7, last - datetime.timedelta(days=7)))
    prev = day - datetime.timedelta(days=1)
    ids = _cohort_ids(prev)
    back = _returned(ids, day) if ids else 0
    q = PurchaseRequest.objects.filter(status="approved", reviewed_at__gte=_start_of(day),
                                       reviewed_at__lt=_start_of(day + datetime.timedelta(days=1)))
    sales = q.aggregate(toman=Sum("price_toman"), n=Count("id"), buyers=Count("user_id", distinct=True))
    return {
        "avg7": sum(a.values()) / 7, "avg_prev7": sum(b.values()) / 7,
        "d1": _pct(back, len(ids)), "d1_back": back, "d1_new": len(ids),
        "sales": {"toman": sales["toman"] or 0, "n": sales["n"] or 0, "buyers": sales["buyers"] or 0},
        "pending": PurchaseRequest.objects.filter(status="pending").count() if day == _today() else 0,
    }


# ── the day chart: active players hour by hour, today vs the day before ───────
def hourly(day: datetime.date) -> dict:
    from bio_lab.models import ActivityHour

    def by_hour(d):
        got = dict(ActivityHour.objects.filter(day=d.isoformat()).values_list("hour")
                   .annotate(n=Count("user_id", distinct=True)))
        return [got.get(h, 0) for h in range(24)]

    is_today = day == _today()
    return {"day": day, "now": by_hour(day), "before": by_hour(day - datetime.timedelta(days=1)),
            "upto": timezone.localtime().hour if is_today else 23}


# ── hourly job: today's totals ────────────────────────────────────────────────
def snapshot_today() -> None:
    from bio_lab.models import Creature, StatSnapshot, User

    agg = User.objects.aggregate(coins=Sum("coins"), dna=Sum("dna_fragments"), diamonds=Sum("diamonds"))
    StatSnapshot.objects.update_or_create(day=_today().isoformat(), defaults={
        "users": User.objects.count(), "creatures": Creature.objects.count(),
        "coins": agg["coins"] or 0, "dna": agg["dna"] or 0, "diamonds": agg["diamonds"] or 0,
    })

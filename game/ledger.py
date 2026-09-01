"""Daily resource-gain ledger — records how much gold / DNA / diamonds each player
GAINED per day, per source, so the owner's player-search can surface a suspicious
jump (e.g. a diamond balloon) at a glance.

Deliberately best-effort and side-effect-free for the game itself: record_gain only
writes the ledger, it never touches balances (the caller already credited them). A
failure here must never break the action that earned the reward, so every write is
guarded. Positive amounts only — spending is not tracked."""

from __future__ import annotations

from django.db.models import F

from bio_lab.models import DailyResourceGain, User
from game.daily import today_str


def record_gain(user: User, source: str, *, coins: int = 0, dna: int = 0, diamonds: int = 0) -> None:
    coins, dna, diamonds = max(0, int(coins)), max(0, int(dna)), max(0, int(diamonds))
    if not (coins or dna or diamonds):
        return
    try:
        row, _ = DailyResourceGain.objects.get_or_create(user_id=user.id, day=today_str(), source=source)
        DailyResourceGain.objects.filter(pk=row.pk).update(
            coins=F("coins") + coins, dna=F("dna") + dna, diamonds=F("diamonds") + diamonds
        )
    except Exception:  # noqa: BLE001 — the ledger is diagnostic; never block a reward
        pass


# Persian labels for the per-source breakdown line in the owner's report.
SOURCE_LABELS = {
    "hunt": "شکار",
    "arena": "آرنا",
    "raid": "باس رید",
    "duel": "اتک گروهی",
    "mission": "ماموریت",
    "drop": "جایزه‌های گروه",
    "wheel": "گردونه",
    "casino": "کازینو",
    "login": "ورود روزانه",
    "salary": "حقوق محافظ",
    "collect": "برداشت ساختمون",
    "shop": "فروشگاه",
    "exchange": "مبادله",
    "referral": "دعوت دوستان",
    "battlepass": "پاس فصلی",
    "achievement": "دستاورد",
    "admin": "اعطای ادمین",
    "other": "سایر",
}


def recent_gains(user: User, days: int = 3) -> dict:
    """Gains for the last `days` game-days: a per-day breakdown (with per-source rows)
    plus the grand totals across the window. Newest day first."""
    import datetime

    from django.utils import timezone

    day_keys = [
        (timezone.localdate() - datetime.timedelta(days=i)).isoformat() for i in range(days)
    ]
    rows = DailyResourceGain.objects.filter(user_id=user.id, day__in=day_keys)
    per_day: dict[str, dict] = {d: {"coins": 0, "dna": 0, "diamonds": 0, "sources": []} for d in day_keys}
    tot = {"coins": 0, "dna": 0, "diamonds": 0}
    for r in rows:
        bucket = per_day[r.day]
        bucket["coins"] += r.coins
        bucket["dna"] += r.dna
        bucket["diamonds"] += r.diamonds
        bucket["sources"].append((r.source, r.coins, r.dna, r.diamonds))
        tot["coins"] += r.coins
        tot["dna"] += r.dna
        tot["diamonds"] += r.diamonds
    return {"days": day_keys, "per_day": per_day, "totals": tot}

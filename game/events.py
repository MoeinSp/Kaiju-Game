"""Limited-time events / رویدادهای زمان‌دار — the recurring-FOMO engine.

A themed event is always live and rotates every week, so there's constantly a
fresh reason to log in ("this week is Double-XP week!"). Two layers:

* a **passive weekly bonus** — the current event may double lab XP and/or Battle
  Pass points. Applied through the single central hooks (lab.add_lab_xp,
  battlepass.award), so no reward code is scattered with event checks.
* a **daily event reward calendar** — each day of the event you claim a reward
  that grows through the week, with a diamond jackpot on the last day. This is the
  "come back every day of the event" hook.

No cron, no event table: the active event is derived from the ISO week number, and
the reward is deduped by comparing the last-claim day (game timezone).
"""

from __future__ import annotations

import datetime

from django.utils import timezone

from bio_lab.models import User
from game.daily import today_str

# rotation — one is active per ISO week, chosen by week-number % len(EVENTS)
EVENTS = [
    {"key": "double_xp", "emoji": "⭐", "title": "هفته‌ی XP دوبل",
     "desc": "تا آخر هفته همه‌ی XP آزمایشگاه ۲ برابره!", "xp_mult": 2, "pass_mult": 1, "reward_mult": 1},
    {"key": "double_pass", "emoji": "🎟", "title": "هفته‌ی پاس دوبل",
     "desc": "تا آخر هفته امتیاز پاس فصلی ۲ برابره!", "xp_mult": 1, "pass_mult": 2, "reward_mult": 1},
    {"key": "bounty", "emoji": "🎁", "title": "هفته‌ی جایزه",
     "desc": "جایزه‌های روزانه‌ی رویداد این هفته دو برابرن!", "xp_mult": 1, "pass_mult": 1, "reward_mult": 2},
    {"key": "golden", "emoji": "🌟", "title": "هفته‌ی طلایی",
     "desc": "هم XP و هم امتیاز پاس ۲ برابر!", "xp_mult": 2, "pass_mult": 2, "reward_mult": 1},
]


def _now_local() -> datetime.datetime:
    return timezone.localtime(timezone.now())


def current_event() -> dict:
    week = _now_local().isocalendar().week
    return EVENTS[week % len(EVENTS)]


def ends_at() -> datetime.datetime:
    """End of the current ISO week (next Monday 00:00, local)."""
    now = _now_local()
    days_ahead = 7 - now.isoweekday()  # isoweekday: Mon=1..Sun=7
    end_day = (now + datetime.timedelta(days=days_ahead + 1)).date()
    return datetime.datetime.combine(end_day, datetime.time.min, tzinfo=now.tzinfo)


def seconds_left() -> int:
    return max(0, int((ends_at() - _now_local()).total_seconds()))


def xp_multiplier() -> int:
    return current_event().get("xp_mult", 1)


def pass_multiplier() -> int:
    return current_event().get("pass_mult", 1)


def _event_day() -> int:
    """Which day of the event week it is, 1 (Monday) .. 7 (Sunday)."""
    return _now_local().isoweekday()


def daily_reward(day: int | None = None) -> dict:
    """The event's daily reward for a given weekday (1..7). Grows through the week;
    the 7th day is a diamond jackpot. Scaled by the event's reward multiplier."""
    day = day or _event_day()
    mult = current_event().get("reward_mult", 1)
    if day >= 7:
        reward = {"diamonds": 25 * mult, "speedup": 60}
    elif day >= 4:
        reward = {"dna": 15 * mult, "coins": 200 * mult}
    else:
        reward = {"coins": 150 * mult, "dna": 5 * mult}
    return reward


def reward_text(reward: dict) -> str:
    parts = []
    if reward.get("coins"):
        parts.append(f"{reward['coins']} طلا")
    if reward.get("dna"):
        parts.append(f"{reward['dna']} DNA")
    if reward.get("diamonds"):
        parts.append(f"{reward['diamonds']} 💎")
    if reward.get("speedup"):
        parts.append(f"کارت {reward['speedup']}د")
    return " + ".join(parts) or "—"


def status(user: User) -> dict:
    ev = current_event()
    return {
        "event": ev,
        "seconds_left": seconds_left(),
        "day": _event_day(),
        "today_reward": daily_reward(),
        "can_claim": user.last_event_claim_day != today_str(),
    }


def claim_daily(user: User) -> dict | None:
    """Claim today's event reward once. Returns the reward, or None if already
    claimed today."""
    today = today_str()
    if user.last_event_claim_day == today:
        return None
    reward = daily_reward()
    from game.battlepass import _grant

    _grant(user, reward)
    user.last_event_claim_day = today
    user.save(update_fields=["last_event_claim_day"])
    return reward

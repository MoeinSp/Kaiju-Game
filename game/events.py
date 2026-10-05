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

from django.db import transaction

import datetime

from django.utils import timezone

from bio_lab.models import User
from bio_lab.repository import lock_row
from game.daily import today_str

# rotation — one is active per ISO week, chosen by week-number % len(EVENTS)
EVENTS = [
    {"key": "double_xp", "emoji": "⭐", "title": "هفته‌ی XP دوبل",
     "desc": "تا آخر هفته همه‌ی XP آزمایشگاه ۲ برابره!", "xp_mult": 2, "pass_mult": 1, "reward_mult": 1},
    {"key": "double_pass", "emoji": "🎟", "title": "هفته‌ی پاس دوبل",
     "desc": "تا آخر هفته امتیاز پاس ماهانه ۲ برابره!", "xp_mult": 1, "pass_mult": 2, "reward_mult": 1},
    {"key": "bounty", "emoji": "🎁", "title": "هفته‌ی جایزه",
     "desc": "جایزه‌های روزانه‌ی رویداد این هفته دو برابرن!", "xp_mult": 1, "pass_mult": 1, "reward_mult": 2},
    {"key": "golden", "emoji": "🌟", "title": "هفته‌ی طلایی",
     "desc": "هم XP و هم امتیاز پاس ۲ برابر!", "xp_mult": 2, "pass_mult": 2, "reward_mult": 1},
    # ── «قانون هفته»: weeks that change how a part of the GAME plays (read through the
    # accessors below — hunt_loot_mult, dispatch_time_mult, cave_top_bonus, …) ──
    {"key": "element_week", "emoji": "🔮", "title": "هفته‌ی عنصر",
     "desc": "هیولاهای عنصرِ این هفته توی شکار ۳۰٪ طلا و DNA بیشتر می‌آرن!",
     "xp_mult": 1, "pass_mult": 1, "reward_mult": 1, "rule": "hunt_element"},
    {"key": "fast_dispatch", "emoji": "🧭", "title": "هفته‌ی اعزام سریع",
     "desc": "مأموریت‌های اعزامی این هفته نصفِ زمان طول می‌کشن!",
     "xp_mult": 1, "pass_mult": 1, "reward_mult": 1, "rule": "dispatch_fast"},
    {"key": "cave_luck", "emoji": "🥚", "title": "هفته‌ی غار خوش‌شانس",
     "desc": "تخم‌هایی که این هفته باز می‌شن ۱۰٪ شانسِ بیشتر برای رده‌ی بالا دارن!",
     "xp_mult": 1, "pass_mult": 1, "reward_mult": 1, "rule": "cave_luck"},
    {"key": "surprise_week", "emoji": "🍀", "title": "هفته‌ی شگفتی",
     "desc": "شانس جایزه‌ی شگفتیِ مأموریت‌های اعزامی این هفته دو برابره!",
     "xp_mult": 1, "pass_mult": 1, "reward_mult": 1, "rule": "dispatch_surprise"},
]

# rule strengths
HUNT_ELEMENT_LOOT_BONUS = 0.30
DISPATCH_FAST_TIME_MULT = 0.5
CAVE_LUCK_TOP_BONUS = 0.10
DISPATCH_SURPRISE_MULT = 2.0


def _now_local() -> datetime.datetime:
    return timezone.localtime(timezone.now())


def current_event() -> dict:
    week = _now_local().isocalendar().week
    return EVENTS[week % len(EVENTS)]


def current_rule() -> str | None:
    return current_event().get("rule")


def week_element() -> str:
    """The element «هفته‌ی عنصر» favours — rotates through all six, week by week."""
    from game import constants

    week = _now_local().isocalendar().week
    return constants.ELEMENTS[(week // len(EVENTS)) % len(constants.ELEMENTS)]


def hunt_loot_mult(element: str | None) -> float:
    """Gold/DNA multiplier for a hunt fought by a creature of `element` this week."""
    if current_rule() == "hunt_element" and element == week_element():
        return 1 + HUNT_ELEMENT_LOOT_BONUS
    return 1.0


def dispatch_time_mult() -> float:
    return DISPATCH_FAST_TIME_MULT if current_rule() == "dispatch_fast" else 1.0


def dispatch_surprise_mult() -> float:
    return DISPATCH_SURPRISE_MULT if current_rule() == "dispatch_surprise" else 1.0


def cave_top_bonus() -> float:
    return CAVE_LUCK_TOP_BONUS if current_rule() == "cave_luck" else 0.0


def rule_line() -> str:
    """One line describing this week's rule for other screens (empty on bonus-only weeks)."""
    ev = current_event()
    if not ev.get("rule"):
        return ""
    extra = ""
    if ev["rule"] == "hunt_element":
        from game import constants

        extra = f" — عنصر این هفته: {constants.element_label(week_element())}"
    return f"{ev['emoji']} <b>{ev['title']}</b>{extra}"


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


@transaction.atomic
def claim_daily(user: User) -> dict | None:
    """Claim today's event reward once. Returns the reward, or None if already
    claimed today."""
    lock_row(user)  # two taps used to both pass the «already claimed» check
    today = today_str()
    if user.last_event_claim_day == today:
        return None
    reward = daily_reward()
    from game.battlepass import _grant

    _grant(user, reward)
    user.last_event_claim_day = today
    user.save(update_fields=["last_event_claim_day"])
    return reward

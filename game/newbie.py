"""«🎁 تقویم تازه‌وارد» — seven gifts on seven separate days for a new player.

On 2026-11-11, 88% of all players had never passed lab level 5: most stopped in the first
days. The calendar gives a reason to come back on day 2, 3 … 7, growing gifts and a
guaranteed epic kaiju on day 7. Days don't have to be consecutive (a missed day doesn't
reset anything) — only one gift per game day (game.daily.today_str).

Who gets it: every player created after this shipped (User.newbie_eligible is set at
creation, bio_lab.repository.get_or_create_user), plus — once, by the migration — the
players who were still under lab level 5, so a returning early quitter gets it too.

A daily reminder (collect_reminders, from the 5-minute notification job) tells an eligible
player who played in the last few days that today's gift is waiting.
"""

from __future__ import annotations

import datetime

from django.db import transaction

from bio_lab.models import User
from bio_lab.repository import lock_row
from game.creature import GameError

# day → reward. Same shape as game/referral.py rewards, plus "creature": rarity.
DAYS = [
    {"coins": 20_000, "dna": 1_000, "diamonds": 10, "cards": [(30, 2)]},
    {"coins": 30_000, "dna": 1_500, "diamonds": 15, "cards": [(30, 1)]},
    {"coins": 40_000, "dna": 2_000, "diamonds": 20, "cards": [(60, 1)]},
    {"coins": 50_000, "dna": 2_500, "diamonds": 25, "cards": [(30, 2)]},
    {"coins": 60_000, "dna": 3_000, "diamonds": 30, "cards": [(60, 1)]},
    {"coins": 80_000, "dna": 4_000, "diamonds": 40, "cards": [(60, 2)]},
    {"coins": 100_000, "dna": 5_000, "diamonds": 60, "creature": "epic"},
]
TOTAL_DAYS = len(DAYS)

REMIND_ACTIVE_DAYS = 3      # remind only players seen in the last few days
REMIND_HOURS = (10, 22)     # local hours a reminder may go out


def _today() -> str:
    from game.daily import today_str

    return today_str()


def reward_text(reward: dict) -> str:
    from game import constants

    parts = []
    if reward.get("diamonds"):
        parts.append(f"{reward['diamonds']:,} 💎")
    if reward.get("coins"):
        parts.append(f"{reward['coins']:,} 🪙")
    if reward.get("dna"):
        parts.append(f"{reward['dna']:,} 🧬")
    for minutes, count in reward.get("cards", []):
        parts.append(f"{count} کارت سرعت {minutes} دقیقه‌ای ⏩")
    if reward.get("creature"):
        parts.append(f"یه کایجوی {constants.RARITY_LABELS.get(reward['creature'], reward['creature'])} تضمینی 🦖")
    return " + ".join(parts)


def state(user: User) -> dict:
    """{eligible, done (gifts taken), can_claim, next_day (1-based) or None, days: [...]}."""
    done = user.newbie_day or 0
    eligible = bool(user.newbie_eligible) and done < TOTAL_DAYS
    claimed_today = (user.newbie_last or "") == _today()
    return {
        "eligible": eligible,
        "done": done,
        "can_claim": eligible and not claimed_today,
        "claimed_today": claimed_today,
        "next_day": done + 1 if done < TOTAL_DAYS else None,
        "days": [{"day": i + 1, "reward": reward_text(r), "taken": i < done, "big": "creature" in r}
                 for i, r in enumerate(DAYS)],
    }


@transaction.atomic
def claim(user: User) -> dict:
    """Take today's gift. Raises GameError when there's nothing to take today."""
    from game import lootbox
    from game.battlepass import _grant
    from game.buildings import grant_speedup_card
    from game.ledger import record_gain

    lock_row(user)
    user.refresh_from_db()
    st = state(user)
    if not st["eligible"]:
        raise GameError("تقویم تازه‌وارد برای تو تموم شده یا فعال نیست.")
    if not st["can_claim"]:
        raise GameError("هدیه‌ی امروز رو گرفتی! فردا برگرد تا هدیه‌ی روز بعد رو بگیری. 🎁")
    day = st["done"] + 1
    reward = DAYS[day - 1]
    claimed = User.objects.filter(id=user.id, newbie_day=st["done"]).exclude(newbie_last=_today()).update(
        newbie_day=day, newbie_last=_today())
    if not claimed:
        raise GameError("هدیه‌ی امروز رو گرفتی! فردا برگرد تا هدیه‌ی روز بعد رو بگیری. 🎁")
    user.refresh_from_db()
    _grant(user, {k: reward[k] for k in ("coins", "dna", "diamonds") if reward.get(k)})
    record_gain(user, "newbie", coins=reward.get("coins", 0), dna=reward.get("dna", 0),
                diamonds=reward.get("diamonds", 0))
    for minutes, count in reward.get("cards", []):
        grant_speedup_card(user, minutes, count=count)
    creature = None
    if reward.get("creature"):
        creature = lootbox._roll_creature(user, reward["creature"])
    return {"day": day, "reward": reward_text(reward), "creature": creature, "finished": day >= TOTAL_DAYS,
            "next": reward_text(DAYS[day]) if day < TOTAL_DAYS else None}


def collect_reminders() -> list[tuple]:
    """«today's gift is waiting» DMs — once a day per player, only in the day-time hours and
    only for players seen in the last REMIND_ACTIVE_DAYS days. (user_id, text, "newbie")."""
    from django.utils import timezone

    hour = timezone.localtime().hour
    if not (REMIND_HOURS[0] <= hour < REMIND_HOURS[1]):
        return []
    today = _today()
    since = (timezone.localdate() - datetime.timedelta(days=REMIND_ACTIVE_DAYS)).isoformat()
    rows = (User.objects.filter(newbie_eligible=True, newbie_day__lt=TOTAL_DAYS, notifications_on=True,
                                is_banned=False, last_active_day__gte=since)
            .exclude(newbie_last=today).exclude(newbie_remind_day=today)
            .values_list("id", "newbie_day"))
    out = []
    ids = []
    for uid, done in rows:
        day = done + 1
        big = "creature" in DAYS[done]
        head = "🦖 <b>امروز روز آخره — کایجوی حماسی‌ت منتظرته!</b>" if big else f"🎁 <b>هدیه‌ی روز {day} تقویم تازه‌وارد منتظرته!</b>"
        out.append((uid, f"{head}\n{reward_text(DAYS[done])}\n<i>یه سر بزن و بگیرش — هر روز یه هدیه‌ی بزرگ‌تر.</i>", "newbie"))
        ids.append(uid)
    if ids:
        User.objects.filter(id__in=ids).update(newbie_remind_day=today)
    return out

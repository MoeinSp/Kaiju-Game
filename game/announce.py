"""Broadcast DMs for the dated parts of the game — «the festival started», «tournament
registration is open», «this week's rule», «you still have unopened mission boxes».

The per-player timers (eggs, upgrades, dispatch, chests) and the live events (world boss
spawn, tournament rounds, festival prizes) already notify from their own modules; this
one covers the calendar moments nobody would otherwise hear about without opening the bot.

Rules that keep it from turning into spam:

* every announcement goes out ONCE (AnnouncementMark row per key), only inside
  QUIET-safe hours, and only to players with notifications on;
* broad ones go to recently-active players only; the reminders are TARGETED (only whoever
  actually has festival coins to spend / boxes to open / hasn't registered yet).

tick() is called from game.notifications.collect_due (sync, inside its transaction) and
returns (user_id, text, marker) tuples; the marker picks the button in bot/handlers/notify.py.
"""

from __future__ import annotations

import datetime

from django.utils import timezone

from bio_lab.models import AnnouncementMark, FestivalProgress, MissionClaim, TournamentEntry, User
from game import constants

SEND_HOURS = (10, 22)          # local (Asia/Tehran) hours announcements may go out in
ACTIVE_DAYS = 3                # «recently active» for the weekly announcements
FESTIVAL_ACTIVE_DAYS = 7       # the festival is monthly — reach a little further back
FESTIVAL_LAST_CALL_HOUR = 17   # last festival day: «spend your coins» from this hour
TOURNAMENT_LAST_CALL_HOUR = 16  # Friday: «registration closes at 19:30» from this hour
MISSION_BOX_HOUR = 18          # Sunday (last day of the mission week) from this hour
MARK_KEEP_DAYS = 120


def _once(key: str) -> bool:
    """True the first time `key` is seen (and records it)."""
    _mark, created = AnnouncementMark.objects.get_or_create(key=key)
    return created


def _active_ids(days: int) -> list[int]:
    since = timezone.now() - datetime.timedelta(days=days)
    return list(
        User.objects.filter(notifications_on=True, is_banned=False, energy_updated_at__gte=since)
        .values_list("id", flat=True)
    )


def _festival(now: datetime.datetime) -> list[tuple]:
    from game import festival

    key = festival.active_key()
    if key is None:
        return []
    out: list[tuple] = []
    th = festival.theme(key)
    _jy, _jm, jd = festival._jalali()
    if _once(f"fest_start:{key}"):
        grand = festival.SHOP["grand"]
        text = (
            f"{th['emoji']} <b>جشنواره‌ی «{th['title']}» شروع شد!</b>\n"
            f"تا <b>{festival.FESTIVAL_DAYS} روز</b> از شکار، آرنا، اعزام، غول سرگردان و مأموریت‌ها "
            "«سکه‌ی جشنواره» می‌گیری و از فروشگاه جشنواره خرج می‌کنی.\n"
            f"{grand[0]} جایزه‌ی بزرگ: <b>هیولای افسانه‌ای {constants.element_label(th['element'])}</b> "
            f"(<code>{grand[2]}</code> سکه)\n"
            "🏅 نفرات برتر جدول سکه، آخر جشنواره الماس می‌گیرن."
        )
        out.extend((uid, text, "festival") for uid in _active_ids(FESTIVAL_ACTIVE_DAYS))
    elif jd == festival.FESTIVAL_END_DAY and now.hour >= FESTIVAL_LAST_CALL_HOUR and _once(f"fest_last:{key}"):
        rows = FestivalProgress.objects.filter(
            festival_key=key, coins__gt=0, user__notifications_on=True, user__is_banned=False
        ).values_list("user_id", "coins")
        for uid, coins in rows:
            out.append((
                uid,
                f"{th['emoji']} <b>امشب آخرین شبِ جشنواره‌ست!</b>\n"
                f"هنوز <code>{coins:,}</code> سکه‌ی خرج‌نشده داری؛ سکه‌ها نیمه‌شب تموم می‌شن. "
                "یه سر به فروشگاه جشنواره بزن.",
                "festival",
            ))
    return out


def _tournament(now: datetime.datetime) -> list[tuple]:
    from game import tournament

    if not tournament.registration_open():
        return []
    wk = tournament._week_key(now)
    champion_diamonds = tournament.PRIZES[1][1]
    if _once(f"tour_reg:{wk}"):
        text = (
            "🏟 <b>ثبت‌نام جام آخر هفته باز شد!</b>\n"
            "تورنمنت حذفی در گروه‌های ۸ نفره‌ی هم‌سطح — ثبت‌نام رایگانه و همه جایزه می‌گیرن.\n"
            "🕢 مهلت ثبت‌نام: جمعه ۱۹:۳۰ · مسابقه: جمعه ۲۰، ۲۱ و ۲۲\n"
            f"🏆 قهرمان هر گروه: جعبه‌ی جادویی + <code>{champion_diamonds}</code> الماس"
        )
        return [(uid, text, "tournament") for uid in _active_ids(ACTIVE_DAYS)]
    is_last_day = now.date() == tournament.draw_at(now).date()
    if is_last_day and now.hour >= TOURNAMENT_LAST_CALL_HOUR and _once(f"tour_last:{wk}"):
        current = tournament.current()
        joined = set(
            TournamentEntry.objects.filter(tournament=current).values_list("user_id", flat=True)
        ) if current is not None else set()
        text = (
            "🏟 <b>ثبت‌نام جام آخر هفته تا ۱۹:۳۰ امشب بازه.</b>\n"
            "هنوز ثبت‌نام نکردی؛ رایگانه و حتی با یه باخت هم جعبه می‌گیری."
        )
        return [(uid, text, "tournament") for uid in _active_ids(ACTIVE_DAYS) if uid not in joined]
    return []


def _week_event(now: datetime.datetime) -> list[tuple]:
    """Monday: what this week's event/rule is."""
    from game import events
    from game.season import week_key

    if now.weekday() != 0 or not _once(f"event:{week_key(now)}"):
        return []
    ev = events.current_event()
    extra = ""
    if ev.get("rule") == "hunt_element":
        extra = f"\n🔮 عنصر این هفته: <b>{constants.element_label(events.week_element())}</b>"
    text = f"{ev['emoji']} <b>این هفته: {ev['title']}</b>\n{ev['desc']}{extra}"
    return [(uid, text, "events") for uid in _active_ids(ACTIVE_DAYS)]


def _mission_boxes(now: datetime.datetime) -> list[tuple]:
    """Last evening of the mission week: whoever reached a box and hasn't opened it."""
    from game import daily

    if now.weekday() != 6 or now.hour < MISSION_BOX_HOUR:
        return []
    wk, dates = daily.week_key(), daily.week_dates()
    if not _once(f"mission_box:{wk}"):
        return []
    points: dict[int, int] = {}
    opened: dict[int, set[str]] = {}
    for uid, key in MissionClaim.objects.filter(day__in=dates).values_list("user_id", "mission_key"):
        points[uid] = points.get(uid, 0) + constants.MISSION_DEFS.get(key, {}).get("points", 0)
    for uid, key in MissionClaim.objects.filter(day=wk).values_list("user_id", "mission_key"):
        points[uid] = points.get(uid, 0) + constants.WEEKLY_MISSION_DEFS.get(key, {}).get("points", 0)
        opened.setdefault(uid, set()).add(key)
    track = constants.mission_box_thresholds()
    waiting = {}
    for uid, pts in points.items():
        n = sum(1 for i, (need, _tier) in enumerate(track, start=1)
                if pts >= need and daily._box_key(i) not in opened.get(uid, ()))
        if n:
            waiting[uid] = n
    allowed = set(
        User.objects.filter(id__in=list(waiting), notifications_on=True, is_banned=False).values_list("id", flat=True)
    )
    return [
        (uid,
         f"🎁 <b>{n} باکسِ مأموریت بازنشده داری!</b>\n"
         "امتیازش رو این هفته گرفتی ولی بازش نکردی؛ نیمه‌شب هفته‌ی مأموریت‌ها عوض می‌شه.",
         "missions")
        for uid, n in waiting.items() if uid in allowed
    ]


def tick() -> list[tuple]:
    now = timezone.localtime()
    if not SEND_HOURS[0] <= now.hour < SEND_HOURS[1]:
        return []
    out: list[tuple] = []
    for part in (_festival, _tournament, _week_event, _mission_boxes):
        out.extend(part(now))
    if out:
        AnnouncementMark.objects.filter(
            created_at__lt=timezone.now() - datetime.timedelta(days=MARK_KEEP_DAYS)
        ).delete()
    return out

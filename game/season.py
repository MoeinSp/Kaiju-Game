"""Cup seasons — two weeks long (owner, 2026-11-11; they were weekly before).

The arena ladder resets every season so late joiners aren't permanently locked out
of the top of the table, but a reset that dumped everyone back to zero would
throw away a whole season of work. Instead each player is reset to a *floor*
derived from where they finished: the higher you placed, the higher you restart.

Settlement is lazy, like everything else here (see game/energy.py, game/buildings.py):
there's no cron. `close_due_season()` runs on any arena/leaderboard read, notices
that the stored "last closed season" is behind the current one, and settles once.
`SeasonState` makes that idempotent even if two requests race.

Two clocks live here. The cup season (season_key: 14 days from a Monday) resets cups and
pays the league division + rank rewards. The ISO week (week_key) is still the clock of
everything else weekly — the alliance league and raid ranking settled in the same close,
the dungeon element, group league, invite top list, weekend cup…
"""

import datetime

from django.db import transaction
from django.utils import timezone

from bio_lab.models import SeasonResult, SeasonState, User
from game import constants


def week_key(when=None) -> str:
    """ISO week label, e.g. "2026-W33". Weeks start Monday, and "Monday" is read
    in the game's timezone (Asia/Tehran) — same reason today_str() uses
    localdate(): a UTC boundary would end the season at 03:30 Tehran time."""
    when = timezone.localtime(when) if when is not None else timezone.localtime()
    iso = when.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


SEASON_DAYS = 14
SEASON_ANCHOR = datetime.date(2026, 11, 9)   # a Monday — seasons start on it ± k·14 days


def season_start(when=None) -> datetime.date:
    """First day (a Monday, game timezone) of the cup season `when` falls in."""
    day = (timezone.localtime(when) if when is not None else timezone.localtime()).date()
    return SEASON_ANCHOR + datetime.timedelta(days=((day - SEASON_ANCHOR).days // SEASON_DAYS) * SEASON_DAYS)


def season_key(when=None) -> str:
    """The cup season's label: its start date, e.g. "2026-11-09" (fits SeasonResult.week_key)."""
    return season_start(when).isoformat()


def _state() -> SeasonState:
    state, _ = SeasonState.objects.get_or_create(id=1)
    return state


def current_week() -> str:
    """The current CUP SEASON's key (the name is kept for the callers: arena, league, mini app)."""
    return season_key()


def reset_floor(rank: int, cup: int) -> int:
    """Where a player restarts next season. Top finishers keep more of what they
    earned; everyone else falls back toward the base floor. Never returns more
    than the cup they actually finished on — a reset must not be a promotion."""
    for max_rank, floor in constants.SEASON_RANK_FLOORS:
        if rank <= max_rank:
            base = floor
            break
    else:
        base = constants.SEASON_DEFAULT_FLOOR
    # keep a slice of the surplus above the floor, so a runaway leader still lands
    # meaningfully ahead of the pack without carrying the entire lead over
    surplus = max(0, cup - base)
    return min(cup, base + round(surplus * constants.SEASON_CARRYOVER_PCT))


def standings(limit: int = 10) -> list[dict]:
    """Current season's table, best first."""
    users = list(User.objects.filter(is_banned=False).order_by("-cup", "id")[:limit])
    return [{"rank": i, "user": u, "cup": u.cup} for i, u in enumerate(users, start=1)]


def close_due_season() -> str | None:
    """Settles the weekly part (alliance league, raid) when the ISO week turned over, and
    the cup season when the two-week season did. Returns the cup season key that was
    closed, or None."""
    st = _state()
    if st.last_closed_week == week_key() and st.last_closed_season == season_key():
        return None  # the common case: no lock needed
    _close_week()
    return _close_cups()


@transaction.atomic
def _close_cups() -> str | None:
    now_season = season_key()
    # lock the state row: only ONE request may settle the season (two at once used to
    # reset cups and pay the division rewards twice)
    state = SeasonState.objects.select_for_update().get(id=1)
    if state.last_closed_season == now_season:
        return None
    if state.last_closed_season is None:
        # first run of the two-week season — adopt the current one without wiping anyone's
        # cup (the last weekly reset already happened at the start of it)
        state.last_closed_season = now_season
        state.save(update_fields=["last_closed_season"])
        return None

    from game import league

    closing = state.last_closed_season
    ranked = list(User.objects.filter(is_banned=False, cup__gt=0).order_by("-cup", "id"))
    for rank, user in enumerate(ranked, start=1):
        # league division reward for how they FINISHED (their end-of-season cup),
        # granted before the cup is reset to next week's floor
        league.grant_season_reward(user, user.cup)
        league.grant_rank_reward(user, rank)  # top-of-the-table bonus
        new_cup = reset_floor(rank, user.cup)
        SeasonResult.objects.update_or_create(
            week_key=closing,
            user=user,
            defaults={"rank": rank, "cup_before": user.cup, "cup_after": new_cup},
        )
        user.cup = new_cup
        user.save(update_fields=["cup"])
    state.last_closed_season = now_season
    state.save(update_fields=["last_closed_season"])
    return closing


@transaction.atomic
def _close_week() -> None:
    """The weekly settlements that used to share the (then weekly) cup close."""
    now_week = week_key()
    state = SeasonState.objects.select_for_update().get(id=1)
    if state.last_closed_week == now_week:
        return
    if state.last_closed_week is None:
        state.last_closed_week = now_week
        state.save(update_fields=["last_closed_week"])
        return

    # weekly alliance-league reward: top-10 alliances by power pay their members.
    # Inside the same week-guarded block, so it settles exactly once per week.
    try:
        from game import alliance as alliance_mod

        with transaction.atomic():  # savepoint: a DB error here must not poison the close
            alliance_mod.award_alliance_league()
    except Exception:  # noqa: BLE001 — a league-reward hiccup must not block the cup reset
        pass

    # weekly raid ranking: pay the top raiders, then wipe raid progress for a fresh week
    try:
        from game.raid import settle_weekly_raid

        with transaction.atomic():
            settle_weekly_raid()
    except Exception:  # noqa: BLE001 — a raid-settle hiccup must not block the cup reset
        pass

    state.last_closed_week = now_week
    state.save(update_fields=["last_closed_week"])


def settle_daily_treasury() -> list[tuple[int, str]]:
    """Once per day, deposit the gold bonus into the treasury of the top-3 alliances
    (by treasury). Lazy + day-guarded like close_due_season, so it pays out exactly
    once per day no matter how often this is called. Returns DMs to send (user_id, text)."""
    from game.daily import today_str

    today = today_str()
    with transaction.atomic():
        state = SeasonState.objects.select_for_update().get_or_create(id=1)[0]
        if state.last_treasury_day == today:
            return []
        first_run = state.last_treasury_day is None
        state.last_treasury_day = today
        state.save(update_fields=["last_treasury_day"])
        if first_run:
            return []  # adopt today silently; first payout is tomorrow
        from game import alliance as alliance_mod

        return alliance_mod.award_daily_treasury_top3()


def last_season_results(limit: int = 10) -> tuple[str | None, list[SeasonResult]]:
    # newest by time, not by key: the old weekly keys ("2026-W45") sort above the
    # two-week season keys ("2026-11-09")
    latest = SeasonResult.objects.order_by("-created_at", "-id").first()
    if latest is None:
        return None, []
    # select_related is load-bearing, not an optimisation: the caller renders
    # `result.user.lab_name` from an async handler, and a lazy FK fetch there raises
    # Django's SynchronousOnlyOperation
    return latest.week_key, list(
        SeasonResult.objects.filter(week_key=latest.week_key)
        .select_related("user")
        .order_by("rank")[:limit]
    )


def seconds_until_season_end() -> int:
    """Time left in the current cup season, for the countdowns on the arena screens."""
    now = timezone.localtime()
    start = season_start(now)
    end = timezone.make_aware(datetime.datetime.combine(start + datetime.timedelta(days=SEASON_DAYS), datetime.time.min),
                              timezone.get_current_timezone())
    return max(0, int((end - now).total_seconds()))


def seconds_until_next_week() -> int:
    """Time left in the current ISO week (dungeon element etc. — NOT the cup season)."""
    now = timezone.localtime()
    days_ahead = 7 - now.isoweekday()  # isoweekday: Mon=1 .. Sun=7
    next_monday = (now + datetime.timedelta(days=days_ahead + 1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return max(0, int((next_monday - now).total_seconds()))

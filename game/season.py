"""Weekly cup seasons.

The arena ladder resets every week so late joiners aren't permanently locked out
of the top of the table, but a reset that dumped everyone back to zero would
throw away a whole week of work. Instead each player is reset to a *floor*
derived from where they finished: the higher you placed, the higher you restart.

Settlement is lazy, like everything else here (see game/energy.py, game/buildings.py):
there's no cron. `close_due_season()` runs on any arena/leaderboard read, notices
that the stored "last closed week" is behind the current one, and settles once.
`SeasonState` makes that idempotent even if two requests race.
"""

from django.db import transaction
from django.utils import timezone

from bio_lab.models import SeasonResult, SeasonState, User
from game import constants


def week_key(when=None) -> str:
    """ISO week label, e.g. "2026-W33". Weeks start Monday (ISO), so a season
    boundary is Monday 00:00 UTC."""
    when = when or timezone.now()
    iso = when.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _state() -> SeasonState:
    state, _ = SeasonState.objects.get_or_create(id=1)
    return state


def current_week() -> str:
    return week_key()


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
    """Current week's table, best first."""
    users = list(User.objects.filter(is_banned=False).order_by("-cup", "id")[:limit])
    return [{"rank": i, "user": u, "cup": u.cup} for i, u in enumerate(users, start=1)]


@transaction.atomic
def close_due_season() -> str | None:
    """Settles the previous week if it hasn't been settled yet. Returns the week
    key that was closed, or None when there was nothing to do."""
    state = _state()
    now_week = current_week()
    if state.last_closed_week == now_week:
        return None
    if state.last_closed_week is None:
        # first ever run — just adopt the current week without wiping anyone's cup
        state.last_closed_week = now_week
        state.save(update_fields=["last_closed_week"])
        return None

    closing = state.last_closed_week
    ranked = list(User.objects.filter(is_banned=False, cup__gt=0).order_by("-cup", "id"))
    for rank, user in enumerate(ranked, start=1):
        new_cup = reset_floor(rank, user.cup)
        SeasonResult.objects.update_or_create(
            week_key=closing,
            user=user,
            defaults={"rank": rank, "cup_before": user.cup, "cup_after": new_cup},
        )
        user.cup = new_cup
        user.save(update_fields=["cup"])

    state.last_closed_week = now_week
    state.save(update_fields=["last_closed_week"])
    return closing


def last_season_results(limit: int = 10) -> tuple[str | None, list[SeasonResult]]:
    latest = SeasonResult.objects.order_by("-week_key").first()
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


def seconds_until_next_week() -> int:
    """Time left in the current season, for the countdown on the arena screen."""
    import datetime

    now = timezone.now()
    days_ahead = 7 - now.isoweekday()  # isoweekday: Mon=1 .. Sun=7
    next_monday = (now + datetime.timedelta(days=days_ahead + 1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return max(0, int((next_monday - now).total_seconds()))

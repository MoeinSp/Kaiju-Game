import datetime

from django.db import transaction
from django.utils import timezone

from bio_lab.models import DailyActionLog, Group, GroupEventLog, MissionClaim, User
from game import constants
from game.creature import GameError


def today_str() -> str:
    """Today's date in the game's timezone (settings.TIME_ZONE = Asia/Tehran).

    localdate() rather than now().strftime(): the stored timestamps are UTC, so
    formatting them directly would roll the day over at 03:30 Tehran time and
    daily missions would reset in the middle of the evening."""
    return timezone.localdate().isoformat()


def _get_or_create_log(user: User, action: str) -> DailyActionLog:
    log, _ = DailyActionLog.objects.get_or_create(
        user=user, action=action, day=today_str(), defaults={"count": 0}
    )
    return log


def record_action(user: User, action: str) -> int:
    """Increments today's counter for `action` with no cap. Returns the new count."""
    log = _get_or_create_log(user, action)
    log.count += 1
    log.save(update_fields=["count"])
    return log.count


def assert_energy_available(user: User, action: str) -> None:
    """Raises GameError if `action`'s daily cap is already reached. Does not consume anything —
    call record_action() after the action actually succeeds."""
    cap = constants.ENERGY_CAPS.get(action)
    if cap is None:
        return
    if get_daily_count(user, action) >= cap:
        raise GameError(f"برای امروز انرژیت برای این کار تموم شده ({cap} بار در روز). فردا دوباره تلاش کن.")


def consume_daily(user: User, action: str) -> int:
    """ATOMIC check-and-increment of a daily cap — the anti-double-spam version of
    `assert_energy_available` + `record_action`. It locks today's counter row so two
    near-simultaneous taps can't both pass the cap and grant twice; the second sees
    the incremented count and is rejected. Call this INSIDE the same transaction that
    grants the reward, BEFORE granting. Returns the new count.

    Use this (not the check/record pair) for any daily-capped action that pays out."""
    cap = constants.ENERGY_CAPS.get(action)
    with transaction.atomic():
        _get_or_create_log(user, action)  # ensure the row exists before locking it
        log = DailyActionLog.objects.select_for_update().get(
            user=user, action=action, day=today_str()
        )
        if cap is not None and log.count >= cap:
            raise GameError(
                f"برای امروز انرژیت برای این کار تموم شده ({cap} بار در روز). فردا دوباره تلاش کن."
            )
        log.count += 1
        log.save(update_fields=["count"])
        return log.count


def get_daily_count(user: User, action: str) -> int:
    return _get_or_create_log(user, action).count


def check_missions(user: User, action: str) -> list[dict]:
    """Call right after record_action() for the same action. Grants rewards for any mission just completed."""
    day = today_str()
    completed = []
    for key, defn in constants.MISSION_DEFS.items():
        if defn["action"] != action:
            continue
        already = MissionClaim.objects.filter(user=user, mission_key=key, day=day).exists()
        if already:
            continue
        if get_daily_count(user, action) >= defn["target"]:
            MissionClaim.objects.create(user=user, mission_key=key, day=day)
            user.coins += defn["coins"]
            user.dna_fragments += defn["dna"]
            if defn.get("speedup"):
                # imported here rather than at module level: game.buildings imports
                # game.creature, which would make this a circular import at load time
                from game.buildings import grant_speedup_card

                grant_speedup_card(user, defn["speedup"], count=1)
            completed.append({**defn, "key": key})
    if completed:
        user.save(update_fields=["coins", "dna_fragments"])
        # imported lazily for the same circular-import reason as grant_speedup_card
        from game import lab

        for _ in completed:
            lab.award(user, "mission")
    return completed


def apply_daily_login(user: User) -> dict | None:
    """Call on /start. Grants a streak bonus once per UTC day; returns None if today's
    was already claimed. Streak resets to 1 if a day was missed."""
    today = today_str()
    if user.last_login_day == today:
        return None

    yesterday = (timezone.localdate() - datetime.timedelta(days=1)).isoformat()
    user.login_streak = user.login_streak + 1 if user.last_login_day == yesterday else 1
    user.last_login_day = today

    capped_streak = min(user.login_streak, constants.LOGIN_STREAK_CAP_DAYS)
    coins = constants.LOGIN_STREAK_BASE_COINS + capped_streak * constants.LOGIN_STREAK_COINS_PER_DAY
    dna = (
        constants.LOGIN_STREAK_DNA_BONUS
        if user.login_streak % constants.LOGIN_STREAK_DNA_EVERY == 0
        else 0
    )

    user.coins += coins
    user.dna_fragments += dna
    user.save(update_fields=["login_streak", "last_login_day", "coins", "dna_fragments"])

    # a healthy daily chunk of Battle Pass points, so even a login-only player
    # ticks the pass forward. Lazy + guarded so it can never break /start.
    try:
        from game import battlepass

        battlepass.award(user, 40)
    except Exception:  # pragma: no cover
        pass

    return {"streak": user.login_streak, "coins": coins, "dna": dna}


def group_event_available(group: Group, event_key: str) -> bool:
    return not GroupEventLog.objects.filter(group=group, event_key=event_key, day=today_str()).exists()


def mark_group_event(group: Group, event_key: str) -> None:
    GroupEventLog.objects.create(group=group, event_key=event_key, day=today_str())


def mission_status(user: User) -> list[dict]:
    day = today_str()
    claimed_keys = set(
        MissionClaim.objects.filter(user=user, day=day).values_list("mission_key", flat=True)
    )
    status = []
    for key, defn in constants.MISSION_DEFS.items():
        count = get_daily_count(user, defn["action"])
        status.append(
            {**defn, "key": key, "progress": min(count, defn["target"]), "done": key in claimed_keys}
        )
    return status

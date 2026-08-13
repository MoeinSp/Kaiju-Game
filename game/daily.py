import datetime

from django.utils import timezone

from bio_lab.models import DailyActionLog, Group, GroupEventLog, MissionClaim, User
from game import constants
from game.creature import GameError


def today_str() -> str:
    return timezone.now().strftime("%Y-%m-%d")


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
            completed.append({**defn, "key": key})
    if completed:
        user.save(update_fields=["coins", "dna_fragments"])
    return completed


def apply_daily_login(user: User) -> dict | None:
    """Call on /start. Grants a streak bonus once per UTC day; returns None if today's
    was already claimed. Streak resets to 1 if a day was missed."""
    today = today_str()
    if user.last_login_day == today:
        return None

    yesterday = (timezone.now() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
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

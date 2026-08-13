import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import DailyActionLog, MissionClaim, User
from game import constants
from game.creature import GameError


def today_str() -> str:
    return datetime.datetime.utcnow().strftime("%Y-%m-%d")


def _get_or_create_log(session: Session, user: User, action: str) -> DailyActionLog:
    day = today_str()
    stmt = select(DailyActionLog).where(
        DailyActionLog.user_id == user.id, DailyActionLog.action == action, DailyActionLog.day == day
    )
    log = session.execute(stmt).scalar_one_or_none()
    if log is None:
        log = DailyActionLog(user_id=user.id, action=action, day=day, count=0)
        session.add(log)
        session.flush()
    return log


def record_action(session: Session, user: User, action: str) -> int:
    """Increments today's counter for `action` with no cap. Returns the new count."""
    log = _get_or_create_log(session, user, action)
    log.count += 1
    session.commit()
    return log.count


def assert_energy_available(session: Session, user: User, action: str) -> None:
    """Raises GameError if `action`'s daily cap is already reached. Does not consume anything —
    call record_action() after the action actually succeeds."""
    cap = constants.ENERGY_CAPS.get(action)
    if cap is None:
        return
    if get_daily_count(session, user, action) >= cap:
        raise GameError(f"برای امروز انرژیت برای این کار تموم شده ({cap} بار در روز). فردا دوباره تلاش کن.")


def get_daily_count(session: Session, user: User, action: str) -> int:
    return _get_or_create_log(session, user, action).count


def check_missions(session: Session, user: User, action: str) -> list[dict]:
    """Call right after record_action() for the same action. Grants rewards for any mission just completed."""
    day = today_str()
    completed = []
    for key, defn in constants.MISSION_DEFS.items():
        if defn["action"] != action:
            continue
        already = session.execute(
            select(MissionClaim).where(
                MissionClaim.user_id == user.id, MissionClaim.mission_key == key, MissionClaim.day == day
            )
        ).scalar_one_or_none()
        if already is not None:
            continue
        if get_daily_count(session, user, action) >= defn["target"]:
            session.add(MissionClaim(user_id=user.id, mission_key=key, day=day))
            user.coins += defn["coins"]
            user.dna_fragments += defn["dna"]
            completed.append({**defn, "key": key})
    session.commit()
    return completed


def mission_status(session: Session, user: User) -> list[dict]:
    day = today_str()
    claimed_keys = {
        row.mission_key
        for row in session.execute(
            select(MissionClaim).where(MissionClaim.user_id == user.id, MissionClaim.day == day)
        ).scalars()
    }
    status = []
    for key, defn in constants.MISSION_DEFS.items():
        count = get_daily_count(session, user, defn["action"])
        status.append(
            {
                **defn,
                "key": key,
                "progress": min(count, defn["target"]),
                "done": key in claimed_keys,
            }
        )
    return status

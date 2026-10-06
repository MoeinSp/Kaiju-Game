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
    _festival_hook(user, action, log.count, 1)
    return log.count


def _festival_hook(user: User, action: str, new_count: int, n: int) -> None:
    """Every counted action also feeds the monthly festival (a no-op between festivals).
    Guarded: a festival hiccup must never break the action being recorded."""
    from game import metrics

    metrics.mark(user.id)  # «active this hour» for the admin stats (in-memory, no query)
    try:
        from game import festival

        festival.on_action(user, action, new_count, n)
    except Exception:  # noqa: BLE001
        pass


def record_action_bulk(user: User, action: str, n: int) -> int:
    """Add `n` to today's counter for `action` in a single write — so a batch action
    (e.g. auto-hunt running many hunts at once) counts each toward daily missions."""
    n = int(n)
    log = _get_or_create_log(user, action)
    if n > 0:
        log.count += n
        log.save(update_fields=["count"])
        _festival_hook(user, action, log.count, n)
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
        _festival_hook(user, action, log.count, 1)
        return log.count


def get_daily_count(user: User, action: str) -> int:
    return _get_or_create_log(user, action).count


# ── missions: daily + weekly, points, and the weekly box track ────────────────
# Rewards are expressed in «hunt units» so a mission is worth the same number of hunts
# at every stage of the game: one unit = what a won «هم‌سطح» hunt pays THIS player.
MISSION_UNIT_MIN_GOLD = 150  # a brand-new player's unit (their real hunt pays less)


def week_dates(when=None) -> list[str]:
    """The seven game days (ISO Monday..Sunday, Asia/Tehran) of the week containing
    `when` — the same week game.season uses for the arena."""
    today = timezone.localdate(when) if when is not None else timezone.localdate()
    monday = today - datetime.timedelta(days=today.weekday())
    return [(monday + datetime.timedelta(days=i)).isoformat() for i in range(7)]


def week_key() -> str:
    from game.season import week_key as _wk

    return _wk()


def seconds_until_week_reset() -> int:
    now = timezone.localtime()
    monday = (now - datetime.timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(0, int((monday + datetime.timedelta(days=7) - now).total_seconds()))


def mission_unit(user: User) -> tuple[int, int]:
    """(gold, dna) one hunt unit is worth for this player right now."""
    from game import hunt

    power = hunt.hunt_benchmark_power(user)
    lo, hi = hunt.hunt_coin_range(power, "normal")
    gold = max(MISSION_UNIT_MIN_GOLD, (lo + hi) // 2)
    dlo, dhi = hunt.hunt_dna_range(power, "normal")
    dna = max(5, (dlo + dhi) // 2)
    return gold, dna


def mission_reward(defn: dict, unit: tuple[int, int]) -> dict:
    """The concrete payout of one mission for a player whose hunt unit is `unit`."""
    gold_unit, dna_unit = unit
    reward = {
        "coins": round(defn.get("gold_u", 0) * gold_unit),
        "dna": round(defn.get("dna_u", 0) * dna_unit),
        "points": defn.get("points", 0),
    }
    for extra in ("speedup", "diamonds", "capsule"):
        if defn.get(extra):
            reward[extra] = defn[extra]
    return reward


def _weekly_count(user: User, action: str, dates: list[str]) -> int:
    from django.db.models import Sum

    return (
        DailyActionLog.objects.filter(user=user, action=action, day__in=dates).aggregate(s=Sum("count"))["s"] or 0
    )


def _pay_mission(user: User, reward: dict) -> None:
    """Apply one mission reward. F() updates: the caller's `user` may be stale (it's
    handed in after the action it just performed), so a balance is never overwritten."""
    from django.db.models import F

    updates = {}
    for key, field in (("coins", "coins"), ("dna", "dna_fragments"), ("diamonds", "diamonds")):
        amount = int(reward.get(key, 0) or 0)
        if amount:
            updates[field] = F(field) + amount
            setattr(user, field, getattr(user, field) + amount)
    if updates:
        User.objects.filter(pk=user.pk).update(**updates)
    if reward.get("capsule"):
        from game.creature import add_capsules

        with transaction.atomic():
            locked = User.objects.select_for_update().get(pk=user.pk)
            tier, count = reward["capsule"]
            add_capsules(locked, tier, count)
            locked.save(update_fields=["xp_capsules"])
            user.xp_capsules = locked.xp_capsules
    if reward.get("speedup"):
        # imported here rather than at module level: game.buildings imports
        # game.creature, which would make this a circular import at load time
        from game.buildings import grant_speedup_card

        grant_speedup_card(user, reward["speedup"], count=1)


def check_missions(user: User, action: str) -> list[dict]:
    """Call right after record_action() for the same action. Grants the reward of every
    DAILY and WEEKLY mission that action just completed, and returns them (each a dict
    with label / coins / dna / extras / points / weekly) for the caller's toast."""
    daily = [(k, d) for k, d in constants.MISSION_DEFS.items() if d["action"] == action]
    weekly = [(k, d) for k, d in constants.WEEKLY_MISSION_DEFS.items() if d["action"] == action]
    if not daily and not weekly:
        return []

    day = today_str()
    completed: list[dict] = []
    unit = None

    def _try(key: str, defn: dict, period: str, count: int, is_weekly: bool) -> None:
        nonlocal unit
        if count < defn["target"]:
            return
        if MissionClaim.objects.filter(user=user, mission_key=key, day=period).exists():
            return
        # the unique (user, mission_key, day) row IS the «paid once» guard
        _claim, created = MissionClaim.objects.get_or_create(user=user, mission_key=key, day=period)
        if not created:
            return
        if unit is None:
            unit = mission_unit(user)
        reward = mission_reward(defn, unit)
        _pay_mission(user, reward)
        completed.append({**defn, **reward, "key": key, "weekly": is_weekly})

    if daily:
        today_count = get_daily_count(user, action)
        for key, defn in daily:
            _try(key, defn, day, today_count, False)
    if weekly:
        wk = week_key()
        week_count = _weekly_count(user, action, week_dates())
        for key, defn in weekly:
            _try(key, defn, wk, week_count, True)

    if completed:
        from game.ledger import record_gain

        record_gain(
            user, "mission",
            coins=sum(m["coins"] for m in completed), dna=sum(m["dna"] for m in completed),
            diamonds=sum(m.get("diamonds", 0) for m in completed),
        )
        # imported lazily for the same circular-import reason as grant_speedup_card
        from game import lab

        for _ in completed:
            lab.award(user, "mission")
        try:
            from game import festival

            festival.on_missions(user, completed)
        except Exception:  # noqa: BLE001
            pass
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
    if coins or dna:
        from game.ledger import record_gain

        record_gain(user, "login", coins=coins, dna=dna)

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


def _week_points(user: User, dates: list[str], wk: str) -> int:
    """Points scored this week = every claimed daily mission of the week's days + every
    claimed weekly mission. Computed from the claim rows, so there's no counter to drift."""
    points = 0
    for key in MissionClaim.objects.filter(user=user, day__in=dates).values_list("mission_key", flat=True):
        points += constants.MISSION_DEFS.get(key, {}).get("points", 0)
    for key in MissionClaim.objects.filter(user=user, day=wk).values_list("mission_key", flat=True):
        points += constants.WEEKLY_MISSION_DEFS.get(key, {}).get("points", 0)
    return points


def _box_key(index: int) -> str:
    return f"box_{index}"


def mission_status(user: User) -> dict:
    """Everything the missions screen shows: today's missions, this week's missions, the
    week's points and the box track (which boxes are reached / opened)."""
    day, wk, dates = today_str(), week_key(), week_dates()
    unit = mission_unit(user)
    claimed_today = set(MissionClaim.objects.filter(user=user, day=day).values_list("mission_key", flat=True))
    claimed_week = set(MissionClaim.objects.filter(user=user, day=wk).values_list("mission_key", flat=True))

    daily = []
    for key, defn in constants.MISSION_DEFS.items():
        count = get_daily_count(user, defn["action"])
        daily.append({**defn, **mission_reward(defn, unit), "key": key,
                      "progress": min(count, defn["target"]), "done": key in claimed_today})
    weekly = []
    for key, defn in constants.WEEKLY_MISSION_DEFS.items():
        count = _weekly_count(user, defn["action"], dates)
        weekly.append({**defn, **mission_reward(defn, unit), "key": key,
                       "progress": min(count, defn["target"]), "done": key in claimed_week})

    points = _week_points(user, dates, wk)
    boxes = []
    for index, (need, tier) in enumerate(constants.mission_box_thresholds(), start=1):
        boxes.append({
            "index": index, "need": need, "tier": tier,
            "reached": points >= need, "opened": _box_key(index) in claimed_week,
        })
    return {
        "daily": daily, "weekly": weekly, "points": points,
        "max_points": constants.MISSION_WEEK_MAX_POINTS, "boxes": boxes,
        "reset_in": seconds_until_week_reset(),
        "today_points": sum(m["points"] for m in daily if m["done"]),
        "today_max": constants.MISSION_DAILY_POINTS,
    }


@transaction.atomic
def claim_box(user: User, index: int) -> dict:
    """Open box #`index` of this week's track (once): it's an arena-chest tier opened on
    the spot, sized to the player's cup like any chest. Returns the chest contents."""
    from game.arena_chests import grant_chest_contents

    track = constants.mission_box_thresholds()
    if not 1 <= index <= len(track):
        raise GameError("این باکس وجود نداره.")
    need, tier = track[index - 1]
    locked = User.objects.select_for_update().get(pk=user.pk)
    wk = week_key()
    points = _week_points(locked, week_dates(), wk)
    if points < need:
        raise GameError(f"برای این باکس {need} امتیاز لازمه (الان {points} داری).")
    _claim, created = MissionClaim.objects.get_or_create(user=locked, mission_key=_box_key(index), day=wk)
    if not created:
        raise GameError("این باکس رو این هفته قبلاً باز کردی.")
    contents = grant_chest_contents(locked, tier, locked.cup, source="mission")
    contents["index"] = index
    return contents

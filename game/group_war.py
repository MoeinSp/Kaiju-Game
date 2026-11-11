"""«🏆 لیگ گروه‌ها» and «⚔️ جنگ گروه‌ها».

League: every group collects points through the ISO week (GroupWeekScore): a group-boss kill
(+ the damage dealt, game/group_boss.py) and every PvP win inside the group. On Monday the top
three groups of last week are paid: every member who played that week gets the prize, and the
group is told.

War: an admin of group A types «چالش گروه» and gets a code; an admin of group B answers with
«قبول چالش <کد>». For 24 hours the points both groups earn also go to the war score; the higher
score wins. Results are posted to both groups and the members who played get rewards.
"""

from __future__ import annotations

import datetime
import random

from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.utils import timezone

from bio_lab.models import Group, GroupMembership, GroupWar, GroupWeekPrize, GroupWeekScore, User
from game.creature import GameError

PVP_WIN_POINTS = 3
WAR_HOURS = 24
CHALLENGE_TTL_MIN = 60

# week prize per member who played that week, for the top 3 groups
WEEK_PRIZES = [
    {"diamonds": 20, "coins": 60_000, "dna": 3_000},
    {"diamonds": 15, "coins": 40_000, "dna": 2_000},
    {"diamonds": 10, "coins": 25_000, "dna": 1_200},
]
WEEK_MIN_POINTS = 50
WEEK_PRIZE_MEMBER_CAP = 60

WAR_WIN = {"diamonds": 15, "coins": 50_000, "dna": 2_500}
WAR_LOSE = {"coins": 15_000, "dna": 800}
WAR_MEMBER_CAP = 60


def _week(when=None) -> str:
    from game.season import week_key

    return week_key(when)


# ── points ────────────────────────────────────────────────────────────────────
def add_points(group_id: int, points: int) -> None:
    if points <= 0 or not group_id:
        return
    week = _week()
    row, _ = GroupWeekScore.objects.get_or_create(group_id=group_id, week=week)
    GroupWeekScore.objects.filter(id=row.id).update(points=F("points") + points)
    now = timezone.now()
    GroupWar.objects.filter(status=GroupWar.ACTIVE, ends_at__gt=now, group_a_id=group_id).update(score_a=F("score_a") + points)
    GroupWar.objects.filter(status=GroupWar.ACTIVE, ends_at__gt=now, group_b_id=group_id).update(score_b=F("score_b") + points)


def on_pvp_win(group_id: int) -> None:
    try:
        with transaction.atomic():   # a savepoint: a failed write can't poison the fight's transaction
            add_points(group_id, PVP_WIN_POINTS)
    except Exception:  # noqa: BLE001 — league points must never break a fight
        pass


def league_top(limit: int = 10, week: str | None = None) -> list[dict]:
    rows = (GroupWeekScore.objects.filter(week=week or _week(), points__gt=0).select_related("group")
            .order_by("-points", "group_id")[:limit])
    return [{"group_id": r.group_id, "title": r.group.title or str(r.group_id), "points": r.points} for r in rows]


def group_rank(group_id: int) -> tuple[int | None, int]:
    week = _week()
    mine = GroupWeekScore.objects.filter(group_id=group_id, week=week).values_list("points", flat=True).first() or 0
    if not mine:
        return None, 0
    ahead = GroupWeekScore.objects.filter(week=week, points__gt=mine).count()
    return ahead + 1, mine


def _played_since(group_id: int, since_day: str, cap: int) -> list[int]:
    return list(GroupMembership.objects.filter(
        group_id=group_id, left_at__isnull=True, user__is_banned=False,
        user__last_active_day__gte=since_day).values_list("user_id", flat=True)[:cap])


def pay_week_prizes() -> list[tuple]:
    """Monday: last week's top 3 groups. Once per week (unique GroupWeekPrize row)."""
    from game import outbox
    from game.referral import _give, reward_text

    now = timezone.localtime()
    monday = (now - datetime.timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    last_start = monday - datetime.timedelta(days=7)
    week = _week(last_start)
    if GroupWeekPrize.objects.filter(week=week).exists():
        return []
    top = [r for r in league_top(3, week) if r["points"] >= WEEK_MIN_POINTS]
    try:
        with transaction.atomic():
            GroupWeekPrize.objects.create(week=week, winners=[[r["group_id"], r["points"]] for r in top])
    except IntegrityError:
        return []
    out = []
    for place, r in enumerate(top):
        prize = WEEK_PRIZES[place]
        ids = _played_since(r["group_id"], last_start.date().isoformat(), WEEK_PRIZE_MEMBER_CAP)
        for uid in ids:
            with transaction.atomic():
                u = User.objects.select_for_update().filter(id=uid).first()
                if u is not None:
                    _give(u, prize)
        medal = "🥇🥈🥉"[place]
        outbox.push("group_post", {"chat_id": r["group_id"], "text": (
            f"{medal} <b>این گروه نفر {place + 1} لیگ گروه‌های هفته شد!</b>\n"
            f"با <b>{r['points']:,}</b> امتیاز — <b>{len(ids)}</b> نفر از بازیکن‌های فعال این هفته هر کدوم "
            f"{reward_text(prize)} گرفتن. 🎉")})
    return out


# ── wars ──────────────────────────────────────────────────────────────────────
def active_war(group_id: int) -> GroupWar | None:
    return (GroupWar.objects.filter(Q(group_a_id=group_id) | Q(group_b_id=group_id), status=GroupWar.ACTIVE,
                                    ends_at__gt=timezone.now()).select_related("group_a", "group_b").first())


def _open_challenge(group_id: int) -> GroupWar | None:
    return GroupWar.objects.filter(group_a_id=group_id, status=GroupWar.PENDING,
                                   created_at__gt=timezone.now() - datetime.timedelta(minutes=CHALLENGE_TTL_MIN)).first()


def create_challenge(group: Group, user: User, is_admin: bool) -> GroupWar:
    if not is_admin:
        raise GameError("فقط ادمین‌های گروه می‌تونن به یه گروه دیگه چالش بدن.")
    if active_war(group.id):
        raise GameError("این گروه الان وسط یه جنگه.")
    existing = _open_challenge(group.id)
    if existing:
        return existing
    for _ in range(20):
        code = str(random.randint(1000, 9999))
        if not GroupWar.objects.filter(code=code).exists():
            break
    else:
        raise GameError("یه لحظه دیگه دوباره امتحان کن.")
    GroupWar.objects.filter(group_a=group, status=GroupWar.PENDING).update(status=GroupWar.EXPIRED)
    return GroupWar.objects.create(code=code, group_a=group, created_by=user.id)


@transaction.atomic
def accept(code: str, group: Group, user: User, is_admin: bool) -> GroupWar:
    if not is_admin:
        raise GameError("فقط ادمین‌های گروه می‌تونن چالش رو قبول کنن.")
    war = GroupWar.objects.select_for_update().select_related("group_a").filter(code=code).first()
    if war is None or war.status != GroupWar.PENDING or \
            war.created_at <= timezone.now() - datetime.timedelta(minutes=CHALLENGE_TTL_MIN):
        raise GameError("این کد چالش معتبر نیست یا منقضی شده.")
    if war.group_a_id == group.id:
        raise GameError("نمی‌شه با گروه خودت بجنگی! این کد رو بفرست برای ادمین یه گروه دیگه.")
    if active_war(group.id) or active_war(war.group_a_id):
        raise GameError("یکی از دو گروه الان وسط یه جنگه.")
    now = timezone.now()
    war.group_b = group
    war.status = GroupWar.ACTIVE
    war.starts_at = now
    war.ends_at = now + datetime.timedelta(hours=WAR_HOURS)
    war.save(update_fields=["group_b", "status", "starts_at", "ends_at"])
    return war


def settle_due() -> list[tuple]:
    """Close every war whose 24h is up; post the result to both groups and pay the members who
    played since it started. Returns nothing to DM (the groups are told)."""
    from game import outbox
    from game.referral import _give, reward_text

    for war in GroupWar.objects.filter(status=GroupWar.ACTIVE, ends_at__lte=timezone.now()).select_related("group_a", "group_b"):
        with transaction.atomic():
            w = GroupWar.objects.select_for_update().get(id=war.id)
            if w.status != GroupWar.ACTIVE:
                continue
            winner = None
            if w.score_a != w.score_b:
                winner = w.group_a if w.score_a > w.score_b else w.group_b
            w.status = GroupWar.SETTLED
            w.winner = winner
            w.save(update_fields=["status", "winner"])
            since = timezone.localtime(w.starts_at).date().isoformat()
            for g in (w.group_a, w.group_b):
                prize = WAR_WIN if (winner is not None and g.id == winner.id) else WAR_LOSE
                if winner is None:
                    prize = WAR_LOSE
                for uid in _played_since(g.id, since, WAR_MEMBER_CAP):
                    u = User.objects.select_for_update().filter(id=uid).first()
                    if u is not None:
                        _give(u, prize)
        a, b = war.group_a, war.group_b
        head = (f"⚔️ <b>جنگ گروه‌ها تموم شد!</b>\n«{a.title}» <b>{w.score_a:,}</b> — <b>{w.score_b:,}</b> «{b.title}»\n")
        for g in (a, b):
            if winner is None:
                line = f"🤝 مساوی شد! بازیکن‌های فعال هر کدوم {reward_text(WAR_LOSE)} گرفتن."
            elif g.id == winner.id:
                line = f"🏆 <b>این گروه برد!</b> بازیکن‌های فعال هر کدوم {reward_text(WAR_WIN)} گرفتن. 🎉"
            else:
                line = f"💀 این بار باختیم — بازیکن‌های فعال هر کدوم {reward_text(WAR_LOSE)} گرفتن. دفعه‌ی بعد!"
            outbox.push("group_post", {"chat_id": g.id, "text": head + line})
    GroupWar.objects.filter(status=GroupWar.PENDING,
                            created_at__lte=timezone.now() - datetime.timedelta(minutes=CHALLENGE_TTL_MIN)
                            ).update(status=GroupWar.EXPIRED)
    return []


def war_text(war: GroupWar, group_id: int) -> str:
    me_a = war.group_a_id == group_id
    mine, theirs = (war.score_a, war.score_b) if me_a else (war.score_b, war.score_a)
    other = war.group_b if me_a else war.group_a
    left = max(0, int((war.ends_at - timezone.now()).total_seconds() // 60))
    lead = "🔥 جلوییم!" if mine > theirs else ("😬 عقبیم — بجنبید!" if mine < theirs else "⚖️ مساوی")
    return (f"⚔️ <b>جنگ با «{other.title}»</b>\n"
            f"ما: <b>{mine:,}</b> — اونا: <b>{theirs:,}</b>  {lead}\n"
            f"⏳ {left // 60} ساعت و {left % 60} دقیقه مونده\n"
            f"<i>امتیاز: کشتن باس گروه (+{100}) و آسیب بهش، و هر برد اتک توی گروه (+{PVP_WIN_POINTS}).</i>")

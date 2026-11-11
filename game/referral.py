"""Referrals / دعوت دوستان — viral growth + social retention.

Each player has a deep link (``t.me/<bot>?start=ref_<id>``). When a brand-new
player opens the bot through one, we remember who invited them. When that new
player proves they're real by reaching a lab-level milestone, BOTH sides collect
a diamond reward — paying on a milestone (not on signup) is the anti-abuse gate,
so farming throwaway accounts earns nothing.

The reward payout is processed by the same periodic job as push notifications
(game/notifications.collect_due calls collect_rewards here), so it also gets to
DM both players "your friend joined — you both earned diamonds".

2026-11 upgrade: the friend's GROWTH pays — a reward at lab level 3, 5, 10, 15, 20, 25 and
30, each also needing a number of separate days played (a fake account would have to be
played for weeks to pay much; deleting it later gains nothing, the steps were earned by
real play). Level 5 + 3 days makes the invite «valid» (max 10 a day per referrer), which
counts toward the 1/3/5/10/25/50 tier rewards and the weekly «top inviters» prize. Plus a
10% purchase commission for 60 days.
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from bio_lab.models import User
from bio_lab.repository import lock_row
from game import lab

# Growth steps of an invited friend: (lab level, separate days played, referrer's reward,
# friend's reward, referrer also gets a relic, friend also gets a relic). Rewards grow with
# the step, so most of the value sits where only real players get (on 2026-11-11 only ~12%
# of all players ever passed level 5, and <1% reached 30).
STEPS = [
    (3, 2, {"diamonds": 15}, {"diamonds": 15}, False, False),
    (5, 3, {"diamonds": 50, "speedup": 30}, {"diamonds": 40, "speedup": 30}, False, False),
    (10, 4, {"diamonds": 80}, {"diamonds": 40}, False, False),
    (15, 5, {"diamonds": 120, "speedup": 60}, {"diamonds": 50}, False, False),
    (20, 7, {"diamonds": 180}, {"diamonds": 60}, True, False),
    (25, 9, {"diamonds": 250}, {"diamonds": 80}, False, False),
    (30, 12, {"diamonds": 400}, {"diamonds": 120}, True, True),
]
VALID_STEP = 1   # reaching STEPS[1] (level 5 + 3 days) makes the invite «valid»
MILESTONE_LEVEL = STEPS[VALID_STEP][0]
REFERRER_REWARD = STEPS[VALID_STEP][2]
FRIEND_REWARD = STEPS[VALID_STEP][3]
# invites bound before the steps existed keep their old one-time payout
LEGACY_REFERRER_REWARD = {"diamonds": 50}
LEGACY_FRIEND_REWARD = {"diamonds": 30}


def link_for(user_id: int) -> str:
    from config import BOT_USERNAME

    return f"https://t.me/{BOT_USERNAME}?start=ref_{user_id}"


def parse_payload(payload: str | None) -> int | None:
    """Extract a referrer id from a /start deep-link payload, or None."""
    if not payload or not payload.startswith("ref_"):
        return None
    rest = payload[4:]
    return int(rest) if rest.isdigit() else None


def register_referral(new_user: User, was_created: bool, referrer_id: int | None) -> bool:
    """Bind ``new_user`` to a referrer, if it's a legitimate brand-new invite.

    "Brand new" is judged by the player NOT having started playing yet — i.e. no
    creature. `was_created` alone is unreliable: the force-join middleware creates
    the User row before /start runs, so a genuinely new invitee arrives with
    was_created=False. Binding on "no creature yet" (called before the starter
    creature is made) fixes referrals for every bot that has required channels,
    while still refusing to re-refer an existing player."""
    if referrer_id is None:
        return False
    if referrer_id == new_user.id or new_user.referred_by is not None:
        return False
    from bio_lab.models import Creature

    already_playing = not was_created and Creature.objects.filter(owner=new_user).exists()
    if already_playing:
        return False
    if not User.objects.filter(id=referrer_id).exists():
        return False
    new_user.referred_by = referrer_id
    new_user.referred_at = timezone.now()
    new_user.save(update_fields=["referred_by", "referred_at"])
    try:
        notify_joined(new_user, referrer_id)
    except Exception:  # noqa: BLE001 — a notice must never break /start
        pass
    return True


def _grant(user: User, reward: dict) -> None:
    from game.battlepass import _grant as grant

    grant(user, reward)


@transaction.atomic
def _settle_one(friend: User) -> User | None:
    """Pay every growth step this friend has newly reached — race-safe: the guarded UPDATE on
    referral_step means the notification job and a manual claim can't pay a step twice.
    Returns the referrer (with ._paid_steps / ._tier_lines set) if THIS call paid something,
    else None. The «valid invite» step obeys the referrer's daily cap (it waits, not lost)."""
    from game.ledger import record_gain

    if friend.referred_at is None:
        return _settle_legacy(friend)
    friend = User.objects.filter(id=friend.id).first()
    if friend is None or friend.is_banned:
        return None
    due = due_steps(friend)
    if not due:
        return None
    if friend.referral_step <= VALID_STEP < due[-1] + 1 and friend.referred_by \
            and _valid_today(friend.referred_by) >= DAILY_VALID_CAP:
        due = [i for i in due if i < VALID_STEP]   # the valid step must wait for tomorrow
        if not due:
            return None
    new_step = due[-1] + 1
    fields = {"referral_step": new_step}
    validates = friend.referral_step <= VALID_STEP < new_step
    if validates:
        fields.update(referral_bonus_paid=True, referral_paid_at=timezone.now())
    claimed = User.objects.filter(id=friend.id, referral_step=friend.referral_step).update(**fields)
    if not claimed:
        return None
    referrer = User.objects.select_for_update().filter(id=friend.referred_by).first()
    lock_row(friend)
    from game import relics

    for i in due:
        level, _days, ref_r, fr_r, ref_relic, fr_relic = STEPS[i]
        _grant(friend, fr_r)
        record_gain(friend, "referral", diamonds=fr_r.get("diamonds", 0))
        if fr_relic:
            try:
                relics.grant(friend)
            except Exception:  # noqa: BLE001
                pass
        if referrer is not None and not referrer.is_banned:
            _grant(referrer, ref_r)
            record_gain(referrer, "referral", diamonds=ref_r.get("diamonds", 0))
            if ref_relic:
                try:
                    relics.grant(referrer)
                except Exception:  # noqa: BLE001
                    pass
    if referrer is None:
        return None
    referrer._paid_steps = due
    referrer._validated = validates
    referrer._tier_lines = _pay_tiers(referrer) if validates and not referrer.is_banned else []
    friend._paid_steps = due
    referrer._friend = friend
    return referrer


def _settle_legacy(friend: User) -> User | None:
    """An invite from before the growth steps: the old one-time payout at lab level 3."""
    from game.ledger import record_gain

    if friend.referral_bonus_paid or not is_valid(friend):
        return None
    claimed = User.objects.filter(id=friend.id, referral_bonus_paid=False).update(
        referral_bonus_paid=True, referral_paid_at=timezone.now())
    if not claimed:
        return None
    referrer = User.objects.select_for_update().filter(id=friend.referred_by).first()
    lock_row(friend)
    _grant(friend, LEGACY_FRIEND_REWARD)
    record_gain(friend, "referral", diamonds=LEGACY_FRIEND_REWARD["diamonds"])
    if referrer is None:
        return None
    if not referrer.is_banned:
        _grant(referrer, LEGACY_REFERRER_REWARD)
        record_gain(referrer, "referral", diamonds=LEGACY_REFERRER_REWARD["diamonds"])
        referrer._tier_lines = _pay_tiers(referrer)
    referrer._paid_steps = None
    referrer._validated = True
    referrer._friend = friend
    return referrer


def _pending_friends(**extra):
    from django.db.models import Q

    return User.objects.filter(
        Q(referral_bonus_paid=False) | Q(referred_at__isnull=False, referral_step__lt=len(STEPS)),
        referred_by__isnull=False, is_banned=False, **extra)


def collect_rewards() -> list[tuple[int, str]]:
    """Pay every growth step an invited friend just reached, and return (user_id, text) DMs
    for both sides. Called from the notification collector."""
    out: list[tuple[int, str]] = []
    for friend in _pending_friends():
        if friend.referred_at is None and not is_valid(friend):
            continue
        if friend.referred_at is not None and not due_steps(friend):
            continue
        referrer = _settle_one(friend)
        if referrer is None:
            continue  # already paid by a concurrent claim, or the referrer's daily cap is full
        f = referrer._friend
        if f.notifications_on:
            out.append((f.id, _friend_paid_text(f, referrer._paid_steps)))
        if referrer.notifications_on and not referrer.is_banned:
            out.append((referrer.id, _referrer_paid_text(referrer, f)))
    try:
        out.extend(pay_week_prizes())
    except Exception:  # noqa: BLE001 — a prize hiccup must not drop the invite payouts
        pass
    return out


def _sum_rewards(steps, side: int) -> tuple[dict, bool]:
    total: dict = {}
    relic = False
    for i in steps:
        r = STEPS[i][2 if side == 0 else 3]
        for k, v in r.items():
            total[k] = max(total.get(k, 0), v) if k == "speedup" else total.get(k, 0) + v
        relic = relic or STEPS[i][4 if side == 0 else 5]
    return total, relic


def _friend_paid_text(friend: User, steps) -> str:
    if steps is None:
        return f"🎁 <b>پاداش دعوت!</b> چون با لینک دعوت اومدی و پای بازی موندی، {reward_text(LEGACY_FRIEND_REWARD)} گرفتی."
    reward, relic = _sum_rewards(steps, 1)
    level = STEPS[steps[-1]][0]
    lines = [f"🎁 <b>جایزه‌ی رشد!</b> به <b>سطح {level}</b> رسیدی و چون با لینک دعوت اومدی، "
             f"{reward_text(reward, relic)} گرفتی."]
    nxt = next_step(steps[-1] + 1)
    if nxt:
        lines.append(f"<i>🎯 جایزه‌ی بعدی توی سطح {nxt[0]}: {reward_text(nxt[3], nxt[5])}</i>")
    return "\n".join(lines)


def _referrer_paid_text(referrer: User, friend: User) -> str:
    from bio_lab.repository import lab_display

    steps = referrer._paid_steps
    if steps is None:
        lines = [f"🎉 <b>دعوتت معتبر شد!</b> {lab_display(friend)} به بازی چسبید.",
                 f"🎁 جایزه‌ات: {reward_text(LEGACY_REFERRER_REWARD)}"]
    else:
        reward, relic = _sum_rewards(steps, 0)
        level = STEPS[steps[-1]][0]
        head = (f"🎉 <b>دعوتت معتبر شد!</b> {lab_display(friend)} به سطح {level} رسید."
                if referrer._validated else
                f"📈 <b>دوستت داره رشد می‌کنه!</b> {lab_display(friend)} به <b>سطح {level}</b> رسید.")
        lines = [head, f"🎁 جایزه‌ات: {reward_text(reward, relic)}"]
        nxt = next_step(steps[-1] + 1)
        if nxt:
            lines.append(f"<i>🎯 وقتی به سطح {nxt[0]} برسه: {reward_text(nxt[2], nxt[4])}</i>")
    if referrer._validated:
        lines.append(f"👥 دعوت‌های معتبرت: <b>{valid_count(referrer.id)}</b>")
        lines += getattr(referrer, "_tier_lines", [])
        nt = next_tier(valid_count(referrer.id))
        if nt:
            lines.append(f"<i>🏅 پله‌ی بعدی دعوت: {nt[0]} دعوت معتبر ← {reward_text(nt[1], nt[2])}</i>")
    return "\n".join(lines)


def claim_ready(user: User) -> dict:
    """Let a referrer collect, on demand, every growth step their friends have reached but
    that isn't paid yet. Returns how many friends paid out, the diamonds and tier lines."""
    paid = diamonds = 0
    tier_lines: list[str] = []
    for friend in list(_pending_friends(referred_by=user.id)):
        before = User.objects.filter(id=user.id).values_list("diamonds", flat=True).first() or 0
        ref = _settle_one(friend)
        if ref is not None:
            paid += 1
            diamonds += (User.objects.filter(id=user.id).values_list("diamonds", flat=True).first() or 0) - before
            tier_lines += getattr(ref, "_tier_lines", [])
    return {"claimed": paid, "diamonds": diamonds, "tier_lines": tier_lines}


def stats(user: User) -> dict:
    """Panel data: the player's link and how their invites are doing, incl. a
    per-friend breakdown and how many rewards are ready to claim right now."""
    from bio_lab.repository import lab_display

    referred = list(User.objects.filter(referred_by=user.id).order_by("referral_bonus_paid", "-lab_xp", "id"))
    friends = []
    claimable = 0
    for f in referred:
        lvl = lab.lab_level(f)
        if f.referred_at is None:
            ready = is_valid(f) and not f.referral_bonus_paid
            nxt = None if f.referral_bonus_paid else (LEGACY_MILESTONE_LEVEL, 0)
            step, total = (1 if f.referral_bonus_paid else 0), 1
        else:
            ready = bool(due_steps(f))
            ns = next_step(f.referral_step)
            nxt = (ns[0], ns[1]) if ns else None
            step, total = f.referral_step, len(STEPS)
        if ready and not f.is_banned:
            claimable += 1
        friends.append({
            "name": lab_display(f),
            "level": lvl,
            "days": f.active_days or 0,
            "next_level": nxt[0] if nxt else None,
            "next_days": nxt[1] if nxt else None,
            "step": step, "steps": total,
            "banned": bool(f.is_banned),
            "paid": f.referral_bonus_paid,
            "done": nxt is None,
            "reached": ready,
        })
    successful = sum(1 for x in friends if x["paid"])
    top = []
    for uid, n in week_top(0, 5):
        u = User.objects.filter(id=uid).first()
        top.append({"name": lab_display(u) if u else str(uid), "n": n, "me": uid == user.id})
    return {
        "link": link_for(user.id),
        "total": len(referred),
        "successful": successful,
        "pending": sum(1 for x in friends if not x["paid"]),
        "claimable": claimable,
        "milestone_level": MILESTONE_LEVEL,
        "min_days": MIN_ACTIVE_DAYS,
        "steps": [(lvl, d, reward_text(r, rr), reward_text(f, fr)) for lvl, d, r, f, rr, fr in STEPS],
        "steps_total": reward_text(*_sum_rewards(range(len(STEPS)), 0)),
        "referrer_reward_text": reward_text(STEPS[VALID_STEP][2]),
        "friend_reward_text": reward_text(STEPS[VALID_STEP][3]),
        "next_tier": next_tier(successful),
        "tiers": [(need, reward_text(r, rel), successful >= need) for need, r, rel in TIERS],
        "week_top": top,
        "week_prizes": WEEK_PRIZES,
        "week_min": WEEK_MIN_VALID,
        "commission": COMMISSION_PERCENT,
        "commission_days": COMMISSION_DAYS,
        "daily_cap": DAILY_VALID_CAP,
        "share_url": share_url(user.id),
        "friends": friends,
    }


# ── 2026-11 invite system ─────────────────────────────────────────────────────
# Anti-fake: an invite only counts once the friend has really played — lab level
# MILESTONE_LEVEL *and* MIN_ACTIVE_DAYS separate days (User.active_days, counted by the
# bot middleware). A throwaway account would have to be played for days to pay anything.
# A referrer can validate at most DAILY_VALID_CAP invites a day (the rest wait, not lost).
# Banned players never count. The purchase commission only comes from receipts the
# owner approved by hand, so a fake account can't produce it at all.
# Invites bound before this system (referred_at is None) keep their old rule (level 3).
LEGACY_MILESTONE_LEVEL = 3
MIN_ACTIVE_DAYS = STEPS[VALID_STEP][1]
DAILY_VALID_CAP = 10

# (valid invites needed, reward, also a random relic?)
TIERS = [
    (1, {"diamonds": 50}, False),
    (3, {"diamonds": 150}, True),
    (5, {"diamonds": 300, "speedup": 60}, False),
    (10, {"diamonds": 600}, True),
    (25, {"diamonds": 1500}, True),
    (50, {"diamonds": 3000}, True),
]

COMMISSION_PERCENT = 10      # of every purchase the friend makes, as diamonds, to the referrer
COMMISSION_DAYS = 60         # … during their first 60 days

WEEK_PRIZES = [500, 300, 150]   # top 3 inviters of the week (valid invites)
WEEK_MIN_VALID = 3              # need at least this many valid invites that week to win


def _milestone_for(friend: User) -> int:
    return LEGACY_MILESTONE_LEVEL if friend.referred_at is None else MILESTONE_LEVEL


def _days_needed(friend: User) -> int:
    return 0 if friend.referred_at is None else MIN_ACTIVE_DAYS


def is_valid(friend: User) -> bool:
    """Has this invited friend proved to be a real player (the «valid invite» bar)?"""
    if getattr(friend, "is_banned", False):
        return False
    return lab.lab_level(friend) >= _milestone_for(friend) and (friend.active_days or 0) >= _days_needed(friend)


def due_steps(friend: User) -> list[int]:
    """Indexes of the growth steps this (non-legacy) friend has reached but isn't paid for."""
    if friend.referred_at is None or getattr(friend, "is_banned", False):
        return []
    level, days = lab.lab_level(friend), friend.active_days or 0
    due = []
    for i in range(friend.referral_step or 0, len(STEPS)):
        if level >= STEPS[i][0] and days >= STEPS[i][1]:
            due.append(i)
        else:
            break
    return due


def next_step(index: int):
    return STEPS[index] if index < len(STEPS) else None


def _valid_today(referrer_id: int) -> int:
    from game.daily import today_str  # noqa: F401 — same day boundary as the game

    start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    return User.objects.filter(referred_by=referrer_id, referral_bonus_paid=True, referral_paid_at__gte=start).count()


def valid_count(referrer_id: int) -> int:
    return User.objects.filter(referred_by=referrer_id, referral_bonus_paid=True).count()


def tier_index_for(count: int) -> int:
    """How many tiers a referrer with `count` valid invites has reached."""
    return sum(1 for need, _r, _rel in TIERS if count >= need)


def next_tier(count: int):
    for need, reward, relic in TIERS:
        if count < need:
            return need, reward, relic
    return None


def reward_text(reward: dict, relic: bool = False) -> str:
    parts = []
    if reward.get("diamonds"):
        parts.append(f"{reward['diamonds']:,} 💎")
    if reward.get("coins"):
        parts.append(f"{reward['coins']:,} 🪙")
    if reward.get("speedup"):
        parts.append(f"کارت سرعت {reward['speedup']} دقیقه‌ای ⏩")
    if relic:
        parts.append("یه اثر باستانی 🏺")
    return " + ".join(parts)


def _pay_tiers(referrer: User) -> list[str]:
    """Pay every tier the referrer has newly reached; returns the reward lines."""
    from game import relics
    from game.ledger import record_gain

    count = valid_count(referrer.id)
    reached = tier_index_for(count)
    lines = []
    while referrer.referral_tier < reached:
        need, reward, relic = TIERS[referrer.referral_tier]
        _grant(referrer, reward)
        record_gain(referrer, "referral", diamonds=reward.get("diamonds", 0), coins=reward.get("coins", 0))
        extra = ""
        if relic:
            try:
                got = relics.grant(referrer)
                extra = f" (🏺 {got['label']})"
            except Exception:  # noqa: BLE001
                extra = ""
        referrer.referral_tier += 1
        User.objects.filter(id=referrer.id).update(referral_tier=referrer.referral_tier)
        lines.append(f"🏅 <b>پله‌ی {need} دعوت:</b> {reward_text(reward, relic)}{extra}")
    return lines


def share_url(user_id: int) -> str:
    from urllib.parse import quote

    text = ("🦖 بیا Kaiju Legends بازی کنیم! هیولای خودتو بساز، تو گروه‌ها نبرد کن و جایزه بگیر.\n"
            "با این لینک بیای هر دومون الماس می‌گیریم 💎")
    return f"https://t.me/share/url?url={quote(link_for(user_id))}&text={quote(text)}"


def notify_joined(new_user: User, referrer_id: int) -> None:
    """Tell the referrer right away that someone came in through their link."""
    from bio_lab.repository import lab_display
    from game import outbox

    ref = User.objects.filter(id=referrer_id).only("id", "notifications_on").first()
    if ref is None or not ref.notifications_on:
        return
    outbox.push("group_post", {"chat_id": referrer_id, "text": "\n".join([
        "📨 <b>یه نفر با لینک دعوتت وارد بازی شد!</b>",
        f"👤 {lab_display(new_user)}",
        "──────────────",
        f"هر بار که رشد کنه جایزه می‌گیری — از <b>سطح {STEPS[0][0]}</b> تا <b>سطح {STEPS[-1][0]}</b>، "
        f"روی هم تا <b>{reward_text(*_sum_rewards(range(len(STEPS)), 0))}</b>.",
        f"اولین جایزه وقتی به <b>سطح {STEPS[0][0]}</b> برسه و <b>{STEPS[0][1]} روز جدا</b> بازی کنه.",
        "<i>💡 کمکش کن زودتر قوی شه — هر خریدی هم تا ۶۰ روز بکنه، ۱۰٪ش الماس به تو می‌رسه.</i>",
    ])})


def on_purchase_approved(buyer: User, price_toman: int) -> None:
    """The commission: COMMISSION_PERCENT of an owner-approved purchase, as diamonds, to whoever
    invited the buyer — during the buyer's first COMMISSION_DAYS. Called inside approve()."""
    import datetime

    from game import botconfig, outbox
    from game.ledger import record_gain

    if not buyer.referred_by or buyer.referred_at is None or price_toman <= 0:
        return
    if timezone.now() - buyer.referred_at > datetime.timedelta(days=COMMISSION_DAYS):
        return
    per_diamond = botconfig.get_buy_prices()["diamonds"]
    if per_diamond <= 0:
        return
    diamonds = int(price_toman * COMMISSION_PERCENT / 100 / per_diamond)
    if diamonds <= 0:
        return
    ref = User.objects.select_for_update().filter(id=buyer.referred_by).first()
    if ref is None or ref.is_banned:
        return
    ref.diamonds += diamonds
    ref.save(update_fields=["diamonds"])
    record_gain(ref, "referral", diamonds=diamonds)
    if ref.notifications_on:
        from bio_lab.repository import lab_display

        outbox.push("group_post", {"chat_id": ref.id, "text": (
            f"💰 <b>پورسانت دعوت!</b>\n{lab_display(buyer)} که دعوتش کرده بودی خرید کرد — "
            f"<b>{diamonds:,} 💎</b> ({COMMISSION_PERCENT}٪) به حسابت اومد.")})


def _week_bounds(weeks_ago: int):
    import datetime

    now = timezone.localtime()
    monday = (now - datetime.timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    start = monday - datetime.timedelta(days=7 * weeks_ago)
    return start, start + datetime.timedelta(days=7)


def week_top(weeks_ago: int = 0, limit: int = 5) -> list[tuple[int, int]]:
    """[(referrer_id, valid invites)] of a week, best first."""
    from django.db.models import Count

    start, end = _week_bounds(weeks_ago)
    rows = (User.objects.filter(referral_bonus_paid=True, referral_paid_at__gte=start, referral_paid_at__lt=end)
            .values("referred_by").annotate(n=Count("id")).order_by("-n", "referred_by")[:limit])
    return [(r["referred_by"], r["n"]) for r in rows if r["referred_by"]]


def pay_week_prizes() -> list[tuple[int, str]]:
    """Once per week: the top inviters of LAST week get WEEK_PRIZES. The unique week row makes it
    happen once (two runs at the same time → one wins the insert)."""
    from django.db import IntegrityError

    from bio_lab.models import ReferralWeekPrize
    from game.ledger import record_gain
    from game.season import week_key

    start, _end = _week_bounds(1)
    week = week_key(start)
    if ReferralWeekPrize.objects.filter(week=week).exists():
        return []
    top = [(uid, n) for uid, n in week_top(1, len(WEEK_PRIZES)) if n >= WEEK_MIN_VALID]
    try:
        with transaction.atomic():
            ReferralWeekPrize.objects.create(week=week, winners=[[u, n] for u, n in top])
    except IntegrityError:
        return []
    out = []
    for place, (uid, n) in enumerate(top):
        prize = WEEK_PRIZES[place]
        with transaction.atomic():
            u = User.objects.select_for_update().filter(id=uid).first()
            if u is None or u.is_banned:
                continue
            u.diamonds += prize
            u.save(update_fields=["diamonds"])
            record_gain(u, "referral", diamonds=prize)
        if u.notifications_on:
            medal = "🥇🥈🥉"[place]
            out.append((uid, f"{medal} <b>نفر {place + 1} دعوت‌کننده‌های هفته شدی!</b>\n"
                             f"با <b>{n}</b> دعوت معتبر — <b>{prize:,} 💎</b> جایزه گرفتی. 🎉"))
    return out

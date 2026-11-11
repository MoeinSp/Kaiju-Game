"""Referrals / دعوت دوستان — viral growth + social retention.

Each player has a deep link (``t.me/<bot>?start=ref_<id>``). When a brand-new player
opens the bot through one, we remember who invited them. From then on the friend's
GROWTH pays both sides: a reward at lab level 3, 5, 10, 15, 20, 25 and 30, each also
needing a number of separate days played (User.active_days, counted once a day by the
bot middleware). Most of the value sits at the high levels, which only real players
reach, so a throwaway account would have to be played for weeks to pay much — and
deleting it later gains nothing, every step was earned by real play.

Also: a 10% purchase commission (owner-approved receipts only) for the friend's first
60 days, and a weekly «top inviters» prize. Payouts run in the periodic notification job
(game/notifications.collect_due → collect_rewards), which also DMs both players.
"""

from __future__ import annotations

import datetime

from django.db import transaction
from django.utils import timezone

from bio_lab.models import User
from bio_lab.repository import lock_row
from game import lab

# A reward: {"diamonds", "coins", "dna": int, "cards": [(minutes, count)], "relics": int}.
# Each step: (lab level, separate days played, referrer's reward, friend's reward).
# Owner's numbers (2026-11-11): the referrer gets 350 💎 / 2,000,000 gold / 100,000 DNA in
# all; the friend gets 3× their first draft in diamonds, cards and relics.
STEPS = [
    (3, 2,
     {"diamonds": 15, "coins": 20_000, "dna": 1_000},
     {"diamonds": 45, "coins": 2_000}),
    (5, 3,
     {"diamonds": 50, "coins": 50_000, "dna": 3_000, "cards": [(30, 1)]},
     {"diamonds": 120, "coins": 5_000, "dna": 500, "cards": [(30, 3)]}),
    (10, 4,
     {"diamonds": 80, "coins": 100_000, "dna": 5_000, "cards": [(30, 1)]},
     {"diamonds": 120, "coins": 8_000, "dna": 800}),
    (15, 5,
     {"diamonds": 30, "coins": 200_000, "dna": 10_000, "cards": [(60, 1)]},
     {"diamonds": 60, "coins": 10_000, "dna": 1_000, "cards": [(30, 3)]}),
    (20, 7,
     {"diamonds": 45, "coins": 330_000, "dna": 16_000, "cards": [(60, 1)], "relics": 1},
     {"diamonds": 75, "coins": 12_000, "dna": 1_200}),
    (25, 9,
     {"diamonds": 55, "coins": 500_000, "dna": 25_000, "cards": [(60, 2)]},
     {"diamonds": 90, "coins": 15_000, "dna": 1_500, "cards": [(60, 3)]}),
    (30, 12,
     {"diamonds": 75, "coins": 800_000, "dna": 40_000, "cards": [(60, 2)], "relics": 1},
     {"diamonds": 120, "coins": 20_000, "dna": 2_000, "relics": 3}),
]
# Reaching STEPS[ACTIVE_STEP] (level 5 + 3 days) is what the daily cap and the weekly
# top list count — the point where an invite has clearly become a real player.
ACTIVE_STEP = 1
DAILY_ACTIVE_CAP = 10        # per referrer per day; the rest wait for tomorrow (not lost)

COMMISSION_PERCENT = 10      # of every purchase the friend makes, as diamonds, to the referrer
COMMISSION_DAYS = 60         # … during their first 60 days

WEEK_PRIZES = [300, 200, 100]   # top 3 inviters of the week
WEEK_MIN = 3                    # friends who reached STEPS[ACTIVE_STEP] that week, to win

# invites bound before the growth steps existed (referred_at is None) keep their old
# one-time payout at lab level 3
LEGACY_LEVEL = 3
LEGACY_REFERRER_REWARD = {"diamonds": 50}
LEGACY_FRIEND_REWARD = {"diamonds": 30}


# ── links ─────────────────────────────────────────────────────────────────────
def link_for(user_id: int) -> str:
    from config import BOT_USERNAME

    return f"https://t.me/{BOT_USERNAME}?start=ref_{user_id}"


def share_url(user_id: int) -> str:
    from urllib.parse import quote

    text = ("🦖 بیا Kaiju Legends بازی کنیم! هیولای خودتو بساز، تو گروه‌ها نبرد کن و جایزه بگیر.\n"
            "با این لینک بیای کلی الماس و طلا و کارت سرعت جایزه می‌گیری 💎")
    return f"https://t.me/share/url?url={quote(link_for(user_id))}&text={quote(text)}"


def parse_payload(payload: str | None) -> int | None:
    """Extract a referrer id from a /start deep-link payload, or None."""
    if not payload or not payload.startswith("ref_"):
        return None
    rest = payload[4:]
    return int(rest) if rest.isdigit() else None


# ── rewards ───────────────────────────────────────────────────────────────────
def reward_text(reward: dict) -> str:
    parts = []
    if reward.get("diamonds"):
        parts.append(f"{reward['diamonds']:,} 💎")
    if reward.get("coins"):
        parts.append(f"{reward['coins']:,} 🪙")
    if reward.get("dna"):
        parts.append(f"{reward['dna']:,} 🧬")
    for minutes, count in reward.get("cards", []):
        parts.append(f"{count} کارت سرعت {minutes} دقیقه‌ای ⏩")
    if reward.get("relics"):
        parts.append(f"{reward['relics']} اثر باستانی 🏺")
    return " + ".join(parts)


def sum_rewards(steps, side: int) -> dict:
    """Add up the rewards of several steps; side 0 = referrer, 1 = friend."""
    total: dict = {}
    cards: dict[int, int] = {}
    for i in steps:
        r = STEPS[i][2 + side]
        for k in ("diamonds", "coins", "dna", "relics"):
            if r.get(k):
                total[k] = total.get(k, 0) + r[k]
        for minutes, count in r.get("cards", []):
            cards[minutes] = cards.get(minutes, 0) + count
    if cards:
        total["cards"] = sorted(cards.items())
    return total


def _give(user: User, reward: dict) -> None:
    from game import relics
    from game.battlepass import _grant
    from game.buildings import grant_speedup_card
    from game.ledger import record_gain

    _grant(user, {k: reward[k] for k in ("coins", "dna", "diamonds") if reward.get(k)})
    record_gain(user, "referral", coins=reward.get("coins", 0), dna=reward.get("dna", 0),
                diamonds=reward.get("diamonds", 0))
    for minutes, count in reward.get("cards", []):
        grant_speedup_card(user, minutes, count=count)
    for _ in range(reward.get("relics", 0)):
        try:
            relics.grant(user)
        except Exception:  # noqa: BLE001 — a relic hiccup must not undo the rest
            pass


# ── binding ───────────────────────────────────────────────────────────────────
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


def notify_joined(new_user: User, referrer_id: int) -> None:
    """Tell the referrer right away that someone came in through their link."""
    from bio_lab.repository import lab_display
    from game import outbox

    ref = User.objects.filter(id=referrer_id).only("id", "notifications_on").first()
    if ref is None or not ref.notifications_on:
        return
    first = STEPS[0]
    outbox.push("group_post", {"chat_id": referrer_id, "text": "\n".join([
        "📨 <b>یه نفر با لینک دعوتت وارد بازی شد!</b>",
        f"👤 {lab_display(new_user)}",
        "──────────────",
        f"هر بار که رشد کنه جایزه می‌گیری — از <b>سطح {first[0]}</b> تا <b>سطح {STEPS[-1][0]}</b>، روی هم:",
        f"<b>{reward_text(sum_rewards(range(len(STEPS)), 0))}</b>",
        f"اولین جایزه وقتی به <b>سطح {first[0]}</b> برسه و <b>{first[1]} روز جدا</b> بازی کنه: {reward_text(first[2])}",
        f"<i>💡 کمکش کن زودتر قوی شه — هر خریدی هم تا {COMMISSION_DAYS} روز بکنه، {COMMISSION_PERCENT}٪ش الماس به تو می‌رسه.</i>",
    ])})


# ── progress ──────────────────────────────────────────────────────────────────
def is_legacy(friend: User) -> bool:
    return friend.referred_at is None


def due_steps(friend: User) -> list[int]:
    """Indexes of the growth steps this (non-legacy) friend has reached but isn't paid for."""
    if is_legacy(friend) or getattr(friend, "is_banned", False):
        return []
    level, days = lab.lab_level(friend), friend.active_days or 0
    due = []
    for i in range(friend.referral_step or 0, len(STEPS)):
        if level >= STEPS[i][0] and days >= STEPS[i][1]:
            due.append(i)
        else:
            break
    return due


def legacy_due(friend: User) -> bool:
    return (is_legacy(friend) and not friend.referral_bonus_paid and not friend.is_banned
            and lab.lab_level(friend) >= LEGACY_LEVEL)


def next_step(index: int):
    return STEPS[index] if index < len(STEPS) else None


def _active_today(referrer_id: int) -> int:
    start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    return User.objects.filter(referred_by=referrer_id, referral_bonus_paid=True, referral_paid_at__gte=start).count()


def active_count(referrer_id: int) -> int:
    """Friends who reached STEPS[ACTIVE_STEP] (or, for old invites, got the old payout)."""
    return User.objects.filter(referred_by=referrer_id, referral_bonus_paid=True).count()


# ── payout ────────────────────────────────────────────────────────────────────
@transaction.atomic
def _settle_one(friend: User) -> User | None:
    """Pay every growth step this friend has newly reached — race-safe: the guarded UPDATE on
    referral_step means the notification job and a manual claim can't pay a step twice.
    Returns the referrer (with ._paid_steps / ._friend set) if THIS call paid something,
    else None. STEPS[ACTIVE_STEP] obeys the referrer's daily cap (it waits, not lost)."""
    if is_legacy(friend):
        return _settle_legacy(friend)
    friend = User.objects.filter(id=friend.id).first()
    if friend is None or friend.is_banned:
        return None
    due = due_steps(friend)
    if not due:
        return None
    if due[0] <= ACTIVE_STEP <= due[-1] and friend.referred_by \
            and _active_today(friend.referred_by) >= DAILY_ACTIVE_CAP:
        due = [i for i in due if i < ACTIVE_STEP]   # the level-5 step waits for tomorrow
        if not due:
            return None
    new_step = due[-1] + 1
    fields = {"referral_step": new_step}
    if due[0] <= ACTIVE_STEP <= due[-1]:
        fields.update(referral_bonus_paid=True, referral_paid_at=timezone.now())
    claimed = User.objects.filter(id=friend.id, referral_step=friend.referral_step).update(**fields)
    if not claimed:
        return None
    referrer = User.objects.select_for_update().filter(id=friend.referred_by).first()
    lock_row(friend)
    _give(friend, sum_rewards(due, 1))
    if referrer is not None and not referrer.is_banned:
        _give(referrer, sum_rewards(due, 0))
    if referrer is None:
        return None
    referrer._paid_steps = due
    referrer._friend = friend
    return referrer


def _settle_legacy(friend: User) -> User | None:
    """An invite from before the growth steps: the old one-time payout at lab level 3."""
    if not legacy_due(friend):
        return None
    claimed = User.objects.filter(id=friend.id, referral_bonus_paid=False).update(
        referral_bonus_paid=True, referral_paid_at=timezone.now())
    if not claimed:
        return None
    referrer = User.objects.select_for_update().filter(id=friend.referred_by).first()
    lock_row(friend)
    _give(friend, LEGACY_FRIEND_REWARD)
    if referrer is None:
        return None
    if not referrer.is_banned:
        _give(referrer, LEGACY_REFERRER_REWARD)
    referrer._paid_steps = None
    referrer._friend = friend
    return referrer


def _pending_friends(**extra):
    from django.db.models import Q

    return User.objects.filter(
        Q(referred_at__isnull=True, referral_bonus_paid=False) | Q(referred_at__isnull=False, referral_step__lt=len(STEPS)),
        referred_by__isnull=False, is_banned=False, **extra)


def collect_rewards() -> list[tuple[int, str]]:
    """Pay every growth step an invited friend just reached, and return (user_id, text) DMs
    for both sides. Called from the notification collector."""
    out: list[tuple[int, str]] = []
    for friend in _pending_friends():
        if not (legacy_due(friend) or due_steps(friend)):
            continue
        referrer = _settle_one(friend)
        if referrer is None:
            continue  # already paid by a concurrent claim, or the referrer's daily cap is full
        f = referrer._friend
        if f.notifications_on:
            out.append((f.id, friend_paid_text(f, referrer._paid_steps)))
        if referrer.notifications_on and not referrer.is_banned:
            out.append((referrer.id, referrer_paid_text(f, referrer._paid_steps)))
    try:
        out.extend(pay_week_prizes())
    except Exception:  # noqa: BLE001 — a prize hiccup must not drop the invite payouts
        pass
    return out


def friend_paid_text(friend: User, steps) -> str:
    if steps is None:
        return f"🎁 <b>پاداش دعوت!</b> چون با لینک دعوت اومدی و پای بازی موندی، {reward_text(LEGACY_FRIEND_REWARD)} گرفتی."
    level = STEPS[steps[-1]][0]
    lines = [f"🎁 <b>جایزه‌ی رشد!</b> به <b>سطح {level}</b> رسیدی و چون با لینک دعوت اومدی، اینا مال توئه:",
             f"<b>{reward_text(sum_rewards(steps, 1))}</b>"]
    nxt = next_step(steps[-1] + 1)
    if nxt:
        lines.append(f"<i>🎯 جایزه‌ی بعدی توی سطح {nxt[0]}: {reward_text(nxt[3])}</i>")
    return "\n".join(lines)


def referrer_paid_text(friend: User, steps) -> str:
    from bio_lab.repository import lab_display

    if steps is None:
        return (f"🎉 <b>دعوتت جواب داد!</b> {lab_display(friend)} به بازی چسبید.\n"
                f"🎁 جایزه‌ات: {reward_text(LEGACY_REFERRER_REWARD)}")
    level = STEPS[steps[-1]][0]
    lines = [f"📈 <b>دوستت داره رشد می‌کنه!</b> {lab_display(friend)} به <b>سطح {level}</b> رسید.",
             f"🎁 جایزه‌ات: <b>{reward_text(sum_rewards(steps, 0))}</b>"]
    nxt = next_step(steps[-1] + 1)
    if nxt:
        lines.append(f"<i>🎯 وقتی به سطح {nxt[0]} برسه: {reward_text(nxt[2])}</i>")
    else:
        lines.append("🏁 <i>همه‌ی جایزه‌های این دوستت رو گرفتی!</i>")
    return "\n".join(lines)


def claim_ready(user: User) -> dict:
    """Let a referrer collect, on demand, every growth step their friends have reached but
    that isn't paid yet. Returns how many friends paid out and what the referrer got."""
    paid = 0
    got: dict = {}
    for friend in list(_pending_friends(referred_by=user.id)):
        ref = _settle_one(friend)
        if ref is None:
            continue
        paid += 1
        r = LEGACY_REFERRER_REWARD if ref._paid_steps is None else sum_rewards(ref._paid_steps, 0)
        for k in ("diamonds", "coins", "dna", "relics"):
            if r.get(k):
                got[k] = got.get(k, 0) + r[k]
        for minutes, count in r.get("cards", []):
            cards = dict(got.get("cards", []))
            cards[minutes] = cards.get(minutes, 0) + count
            got["cards"] = sorted(cards.items())
    return {"claimed": paid, "reward": got, "diamonds": got.get("diamonds", 0)}


# ── commission ────────────────────────────────────────────────────────────────
def on_purchase_approved(buyer: User, price_toman: int) -> None:
    """The commission: COMMISSION_PERCENT of an owner-approved purchase, as diamonds, to whoever
    invited the buyer — during the buyer's first COMMISSION_DAYS. Called inside approve()."""
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


# ── weekly top inviters ───────────────────────────────────────────────────────
def _week_bounds(weeks_ago: int):
    now = timezone.localtime()
    monday = (now - datetime.timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    start = monday - datetime.timedelta(days=7 * weeks_ago)
    return start, start + datetime.timedelta(days=7)


def week_top(weeks_ago: int = 0, limit: int = 5) -> list[tuple[int, int]]:
    """[(referrer_id, friends who reached STEPS[ACTIVE_STEP] that week)], best first."""
    from django.db.models import Count

    start, end = _week_bounds(weeks_ago)
    rows = (User.objects.filter(referral_bonus_paid=True, referred_at__isnull=False,   # old invites: not «reached level 5»
                                referral_paid_at__gte=start, referral_paid_at__lt=end)
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
    top = [(uid, n) for uid, n in week_top(1, len(WEEK_PRIZES)) if n >= WEEK_MIN]
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
                             f"<b>{n}</b> تا از دوست‌هات این هفته به سطح {STEPS[ACTIVE_STEP][0]} رسیدن — "
                             f"<b>{prize:,} 💎</b> جایزه گرفتی. 🎉"))
    return out


# ── panel data ────────────────────────────────────────────────────────────────
def stats(user: User) -> dict:
    """Panel data: the player's link and how each invited friend is doing."""
    from bio_lab.repository import lab_display

    referred = list(User.objects.filter(referred_by=user.id).order_by("-lab_xp", "id"))
    friends = []
    claimable = 0
    for f in referred:
        lvl = lab.lab_level(f)
        if is_legacy(f):
            ready = legacy_due(f)
            nxt = None if f.referral_bonus_paid else (LEGACY_LEVEL, 0)
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
            "started": step > 0,
            "done": nxt is None,
            "reached": ready and not f.is_banned,
        })
    top = []
    for uid, n in week_top(0, 5):
        u = User.objects.filter(id=uid).first()
        top.append({"name": lab_display(u) if u else str(uid), "n": n, "me": uid == user.id})
    return {
        "link": link_for(user.id),
        "share_url": share_url(user.id),
        "total": len(referred),
        "claimable": claimable,
        "steps": [(lvl, d, reward_text(r), reward_text(f)) for lvl, d, r, f in STEPS],
        "steps_total": reward_text(sum_rewards(range(len(STEPS)), 0)),
        "friend_total": reward_text(sum_rewards(range(len(STEPS)), 1)),
        "active_level": STEPS[ACTIVE_STEP][0],
        "week_top": top,
        "week_prizes": WEEK_PRIZES,
        "week_min": WEEK_MIN,
        "commission": COMMISSION_PERCENT,
        "commission_days": COMMISSION_DAYS,
        "daily_cap": DAILY_ACTIVE_CAP,
        "friends": friends,
    }

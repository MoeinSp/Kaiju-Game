"""Referrals / دعوت دوستان — viral growth + social retention.

Each player has a deep link (``t.me/<bot>?start=ref_<id>``). When a brand-new
player opens the bot through one, we remember who invited them. When that new
player proves they're real by reaching a lab-level milestone, BOTH sides collect
a diamond reward — paying on a milestone (not on signup) is the anti-abuse gate,
so farming throwaway accounts earns nothing.

The reward payout is processed by the same periodic job as push notifications
(game/notifications.collect_due calls collect_rewards here), so it also gets to
DM both players "your friend joined — you both earned diamonds".

2026-11 upgrade: a stronger gate (lab level 5 + 3 separate days played, max 10 valid
invites a day), bigger per-invite rewards, tier rewards for 1/3/5/10/25/50 valid invites,
a 10% purchase commission for 60 days, and a weekly «top inviters» prize.
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from bio_lab.models import User
from bio_lab.repository import lock_row
from game import lab

MILESTONE_LEVEL = 5  # the friend must reach this lab level for the reward to unlock
REFERRER_REWARD = {"diamonds": 60, "speedup": 30}
FRIEND_REWARD = {"diamonds": 40, "speedup": 30}


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
    """Atomically claim and pay one milestone-reached referral. Returns the referrer
    if THIS call did the payout, or None if it was already paid (race-safe: the
    guarded UPDATE ensures the notification job and a manual claim can't double-pay)."""
    from game.ledger import record_gain

    if friend.referred_by and _valid_today(friend.referred_by) >= DAILY_VALID_CAP:
        return None   # the referrer's daily cap is full — this one waits for tomorrow
    claimed = User.objects.filter(id=friend.id, referral_bonus_paid=False).update(
        referral_bonus_paid=True, referral_paid_at=timezone.now()
    )
    if not claimed:
        return None
    referrer = User.objects.select_for_update().filter(id=friend.referred_by).first()
    lock_row(friend)
    _grant(friend, FRIEND_REWARD)
    record_gain(friend, "referral", diamonds=FRIEND_REWARD.get("diamonds", 0))
    if referrer is not None and not referrer.is_banned:
        _grant(referrer, REFERRER_REWARD)
        record_gain(referrer, "referral", diamonds=REFERRER_REWARD.get("diamonds", 0))
        referrer._tier_lines = _pay_tiers(referrer)
    return referrer


def collect_rewards() -> list[tuple[int, str]]:
    """Pay out every referral whose friend just crossed the milestone, and return
    (user_id, text) DMs for both sides. Called from the notification collector."""
    out: list[tuple[int, str]] = []
    friends = User.objects.filter(referred_by__isnull=False, referral_bonus_paid=False, is_banned=False)
    for friend in friends:
        if not is_valid(friend):
            continue
        referrer = _settle_one(friend)
        if referrer is None:
            continue  # already paid by a concurrent claim, or the referrer's daily cap is full
        if friend.notifications_on:
            out.append((friend.id, f"🎁 <b>پاداش دعوت!</b> چون با لینک دعوت اومدی و پای بازی موندی، "
                                   f"{reward_text(FRIEND_REWARD)} گرفتی."))
        if referrer.notifications_on and not referrer.is_banned:
            out.append((referrer.id, _referrer_paid_text(referrer, friend)))
    try:
        out.extend(pay_week_prizes())
    except Exception:  # noqa: BLE001 — a prize hiccup must not drop the invite payouts
        pass
    return out


def _referrer_paid_text(referrer: User, friend: User) -> str:
    from bio_lab.repository import lab_display

    count = valid_count(referrer.id)
    lines = [
        f"🎉 <b>دعوتت معتبر شد!</b> {lab_display(friend)} به بازی چسبید.",
        f"🎁 جایزه‌ات: {reward_text(REFERRER_REWARD)}",
        f"👥 دعوت‌های معتبرت: <b>{count}</b>",
    ]
    lines += getattr(referrer, "_tier_lines", [])
    nxt = next_tier(count)
    if nxt:
        lines.append(f"<i>🎯 پله‌ی بعدی: {nxt[0]} دعوت ← {reward_text(nxt[1], nxt[2])}</i>")
    return "\n".join(lines)


def claim_ready(user: User) -> dict:
    """Let a referrer collect, on demand, every one of THEIR invitees who has already
    reached the milestone but isn't paid yet. Returns how many and how much."""
    paid = 0
    friends = list(User.objects.filter(referred_by=user.id, referral_bonus_paid=False, is_banned=False))
    tier_lines: list[str] = []
    for friend in friends:
        if not is_valid(friend):
            continue
        ref = _settle_one(friend)
        if ref is not None:
            paid += 1
            tier_lines += getattr(ref, "_tier_lines", [])
    return {"claimed": paid, "diamonds": paid * REFERRER_REWARD["diamonds"], "tier_lines": tier_lines}


def stats(user: User) -> dict:
    """Panel data: the player's link and how their invites are doing, incl. a
    per-friend breakdown and how many rewards are ready to claim right now."""
    from bio_lab.repository import lab_display

    referred = list(User.objects.filter(referred_by=user.id).order_by("referral_bonus_paid", "id"))
    friends = []
    claimable = 0
    for f in referred:
        lvl = lab.lab_level(f)
        reached = is_valid(f)
        if reached and not f.referral_bonus_paid:
            claimable += 1
        friends.append({
            "name": lab_display(f),
            "level": lvl,
            "need_level": _milestone_for(f),
            "days": f.active_days or 0,
            "need_days": _days_needed(f),
            "banned": bool(f.is_banned),
            "paid": f.referral_bonus_paid,
            "reached": reached,
        })
    successful = sum(1 for x in friends if x["paid"])
    top = []
    for uid, n in week_top(0, 5):
        u = User.objects.filter(id=uid).first()
        top.append({"name": lab_display(u) if u else str(uid), "n": n, "me": uid == user.id})
    return {
        "link": link_for(user.id),
        "total": len(referred),
        "successful": sum(1 for x in friends if x["paid"]),
        "pending": sum(1 for x in friends if not x["paid"]),
        "claimable": claimable,
        "milestone_level": MILESTONE_LEVEL,
        "min_days": MIN_ACTIVE_DAYS,
        "referrer_reward": REFERRER_REWARD["diamonds"],
        "friend_reward": FRIEND_REWARD["diamonds"],
        "referrer_reward_text": reward_text(REFERRER_REWARD),
        "friend_reward_text": reward_text(FRIEND_REWARD),
        "next_tier": next_tier(successful),
        "tiers": [(need, reward_text(r, rel), successful >= need) for need, r, rel in TIERS],
        "week_top": top,
        "week_prizes": WEEK_PRIZES,
        "week_min": WEEK_MIN_VALID,
        "commission": COMMISSION_PERCENT,
        "commission_days": COMMISSION_DAYS,
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
MIN_ACTIVE_DAYS = 3
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
    """Has this invited friend proved to be a real player?"""
    if getattr(friend, "is_banned", False):
        return False
    return lab.lab_level(friend) >= _milestone_for(friend) and (friend.active_days or 0) >= _days_needed(friend)


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
        f"وقتی به <b>سطح آزمایشگاه {MILESTONE_LEVEL}</b> برسه و <b>{MIN_ACTIVE_DAYS} روز جدا</b> بازی کنه، "
        f"دعوتش معتبر می‌شه و هردوتون جایزه می‌گیرین: تو {reward_text(REFERRER_REWARD)}، اون {reward_text(FRIEND_REWARD)}.",
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

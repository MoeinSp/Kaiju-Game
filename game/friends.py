"""Friends, plus «تف» to ask and «لیس» between friends.

«تف» never becomes a friendship until the other person taps «دوست شویم».
A lick pays only the person who was licked.
"""

from __future__ import annotations

import datetime
import random
import time

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from bio_lab.models import FriendLink, FriendMark, User
from bio_lab.repository import creature_name, get_active_creature, lab_mention
from game.buildings import main_hall_level
from game.creature import GameError
from game.daily import today_str

MAX_FRIENDS = 50
DECLINE_HOURS = 24
SELF_GAP = 50
PAIR_GAP = 180
PING_DAYS = 7
LICK_CAP = 15
LICK_KIND = "lick"

WORDS = {
    "تف": "تف",
    "تف کردن": "تف",
    "لیس": "لیس",
    "لیسیدن": "لیس",
}

_actor_at: dict[int, float] = {}
_pair_at: dict[tuple[int, int], float] = {}
_warned_at: dict[int, float] = {}


def reset_cooldowns() -> None:
    _actor_at.clear()
    _pair_at.clear()
    _warned_at.clear()


def pair_ids(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a < b else (b, a)


def seen_label(day: str) -> str:
    """Coarse last-online. Friends see this; the group and strangers do not."""
    if not day:
        return "هنوز معلوم نیست"
    today = datetime.date.fromisoformat(today_str())
    try:
        then = datetime.date.fromisoformat(day)
    except ValueError:
        return "هنوز معلوم نیست"
    gap = (today - then).days
    if gap <= 0:
        return "امروز"
    if gap == 1:
        return "دیروز"
    return f"{gap} روز پیش"


def friend_count(user_id: int) -> int:
    return FriendLink.objects.filter(status=FriendLink.FRIENDS).filter(
        Q(user_lo_id=user_id) | Q(user_hi_id=user_id)
    ).count()


def link_of(a: int, b: int) -> FriendLink | None:
    lo, hi = pair_ids(a, b)
    return FriendLink.objects.filter(user_lo_id=lo, user_hi_id=hi).first()


def are_friends(a: int, b: int) -> bool:
    row = link_of(a, b)
    return row is not None and row.status == FriendLink.FRIENDS


def friends_of(user_id: int) -> list[User]:
    rows = (
        FriendLink.objects.filter(status=FriendLink.FRIENDS)
        .filter(Q(user_lo_id=user_id) | Q(user_hi_id=user_id))
        .select_related("user_lo", "user_hi")
    )
    out = []
    for row in rows:
        out.append(row.user_hi if row.user_lo_id == user_id else row.user_lo)
    out.sort(key=lambda u: (u.lab_name or "", u.id))
    return out


def hint_gate(actor_id: int) -> str:
    """Cooldown for a poke that has no target yet (no reply, or a reply to yourself)."""
    return _cooldown(actor_id, 0)


def _cooldown(actor_id: int, other_id: int) -> str:
    now = time.monotonic()
    pair = pair_ids(actor_id, other_id)
    if now - _actor_at.get(actor_id, 0) < SELF_GAP or now - _pair_at.get(pair, 0) < PAIR_GAP:
        if now - _warned_at.get(actor_id, 0) > SELF_GAP:
            _warned_at[actor_id] = now
            return "wait"
        return "quiet"
    _actor_at[actor_id] = now
    _pair_at[pair] = now
    return "ok"


def _decline_blocks(row: FriendLink | None) -> bool:
    if row is None or row.status != FriendLink.DECLINED:
        return False
    return row.updated_at > timezone.now() - datetime.timedelta(hours=DECLINE_HOURS)


@transaction.atomic
def ask(sender_id: int, receiver_id: int) -> str:
    """'asked', 'already', 'friends', 'full', 'blocked', or 'closed'. A pending row is not a friend."""
    if friend_count(sender_id) >= MAX_FRIENDS:
        return "full"
    invites = User.objects.filter(id=receiver_id).values_list("friend_invites", flat=True).first()
    lo, hi = pair_ids(sender_id, receiver_id)
    row = FriendLink.objects.select_for_update().filter(user_lo_id=lo, user_hi_id=hi).first()
    if row is not None and row.status == FriendLink.FRIENDS:
        return "friends"
    if invites is False:
        return "closed"
    if row is not None and row.status == FriendLink.PENDING:
        return "already"
    if _decline_blocks(row):
        return "blocked"
    if row is None:
        FriendLink.objects.create(
            user_lo_id=lo, user_hi_id=hi, status=FriendLink.PENDING, asked_by_id=sender_id,
        )
    else:
        row.status = FriendLink.PENDING
        row.asked_by_id = sender_id
        row.save(update_fields=["status", "asked_by_id", "updated_at"])
    return "asked"


@transaction.atomic
def accept(receiver_id: int, other_id: int) -> None:
    lo, hi = pair_ids(receiver_id, other_id)
    row = FriendLink.objects.select_for_update().filter(user_lo_id=lo, user_hi_id=hi).first()
    if row is None or row.status != FriendLink.PENDING or row.asked_by_id == receiver_id:
        raise GameError("درخواست دوستی‌ای باز نیست.")
    if not User.objects.filter(id=receiver_id).values_list("friend_invites", flat=True).first():
        raise GameError("دریافت تف را بسته‌ای.")
    if friend_count(receiver_id) >= MAX_FRIENDS:
        raise GameError(f"لیست دوستات پر است. سقف {MAX_FRIENDS} نفر است.")
    if friend_count(other_id) >= MAX_FRIENDS:
        raise GameError("لیست طرف پر شده.")
    row.status = FriendLink.FRIENDS
    row.save(update_fields=["status", "updated_at"])


@transaction.atomic
def decline(receiver_id: int, other_id: int) -> None:
    lo, hi = pair_ids(receiver_id, other_id)
    row = FriendLink.objects.select_for_update().filter(user_lo_id=lo, user_hi_id=hi).first()
    if row is None or row.status != FriendLink.PENDING or row.asked_by_id == receiver_id:
        raise GameError("درخواست دوستی‌ای باز نیست.")
    row.status = FriendLink.DECLINED
    row.save(update_fields=["status", "updated_at"])


@transaction.atomic
def remove(user_id: int, other_id: int) -> None:
    lo, hi = pair_ids(user_id, other_id)
    deleted, _ = FriendLink.objects.filter(
        user_lo_id=lo, user_hi_id=hi, status=FriendLink.FRIENDS,
    ).delete()
    if not deleted:
        raise GameError("این نفر توی لیست دوستات نیست.")


def _mark(kind: str, actor_id: int, target_id: int, *, week: bool = False) -> bool:
    """True the first time today (or this week, for a ping)."""
    today = today_str()
    if week:
        since = datetime.date.fromisoformat(today) - datetime.timedelta(days=PING_DAYS - 1)
        if FriendMark.objects.filter(
            kind=kind, actor_id=actor_id, target_id=target_id, day__gte=since.isoformat(),
        ).exists():
            return False
    _, created = FriendMark.objects.get_or_create(
        kind=kind, actor_id=actor_id, target_id=target_id, day=today,
    )
    return created


def cheer(actor_id: int, target_id: int) -> bool:
    return _mark(FriendMark.CHEER, actor_id, target_id)


def visit(actor_id: int, target_id: int) -> bool:
    if not are_friends(actor_id, target_id):
        raise GameError("فقط می‌تونی به آزمایشگاه دوستت سر بزنی.")
    return _mark(FriendMark.VISIT, actor_id, target_id)


def ping(actor_id: int, target_id: int) -> bool:
    if not are_friends(actor_id, target_id):
        raise GameError("فقط به یه دوست می‌تونی بگی بیا بازی.")
    return _mark(FriendMark.PING, actor_id, target_id, week=True)


def take_spar(a: int, b: int) -> bool:
    if not are_friends(a, b):
        raise GameError("تمرین دوستانه فقط با دوست‌هاست.")
    lo, hi = pair_ids(a, b)
    return _mark(FriendMark.SPAR, lo, hi)


def cheers_today(user_id: int) -> int:
    return FriendMark.objects.filter(kind=FriendMark.CHEER, target_id=user_id, day=today_str()).count()


def visits_today(user_id: int) -> list[User]:
    ids = FriendMark.objects.filter(
        kind=FriendMark.VISIT, target_id=user_id, day=today_str(),
    ).values_list("actor_id", flat=True)
    return list(User.objects.filter(id__in=list(ids)))


def unseen_pings(user_id: int) -> list[FriendMark]:
    return list(
        FriendMark.objects.filter(kind=FriendMark.PING, target_id=user_id, seen=False)
        .select_related("actor")
        .order_by("id")
    )


def see_pings(user_id: int) -> int:
    return FriendMark.objects.filter(kind=FriendMark.PING, target_id=user_id, seen=False).update(seen=True)


def card(viewer_id: int, friend: User) -> str:
    if not are_friends(viewer_id, friend.id):
        raise GameError("این نفر دیگه دوستت نیست.")
    creature = get_active_creature(friend)
    if creature is None:
        who = "هیولای فعالی ندارد"
    else:
        stars = "⭐" * creature.star_level
        who = f"{creature_name(creature)} {stars}"
    lines = [
        f"🤝 <b>{lab_mention(friend)}</b>",
        f"تالار مِهر: سطح <code>{main_hall_level(friend)}</code>",
        f"هیولای فعال: {who}",
        f"آخرین بازی: {seen_label(friend.last_active_day)}",
    ]
    guests = visits_today(friend.id)
    if guests:
        names = "، ".join(lab_mention(g) for g in guests[:5])
        lines.append(f"امروز سر زدن: {names}")
    return "\n".join(lines)


def spar(a: User, b: User) -> str:
    """A friendly fight. No cup, no gold, no energy — the caller must not pay any."""
    if not are_friends(a.id, b.id):
        raise GameError("تمرین دوستانه فقط با دوست‌هاست.")
    mine = get_active_creature(a)
    theirs = get_active_creature(b)
    if mine is None or theirs is None:
        raise GameError("هر دو باید هیولای فعال داشته باشن.")
    if not take_spar(a.id, b.id):
        raise GameError("امروز با این دوست تمرین کردی.")
    from game.combat import resolve_duel

    seed = int(today_str().replace("-", ""))
    winner, log = resolve_duel(mine, theirs, seed=seed, deterministic=True)
    return (
        f"🤝 <b>تمرین دوستانه</b>\nبرنده: {creature_name(winner)}\n{log}\n"
        "<i>کاپ، طلا و انرژی جابه‌جا نشد.</i>"
    )


def _fa(n: int) -> str:
    return str(n).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))


def _jackpot_kind() -> str:
    return random.choice(("full", "gems", "key"))


def _pay_lick(receiver: User, step: int) -> str:
    """The prize for the Nth lick this person received today. 5×2 gems, 5×2 energy, 4×two 30-minute cards, then one jackpot."""
    receiver = User.objects.select_for_update().get(id=receiver.id)
    if step <= 5:
        receiver.diamonds += 2
        receiver.save(update_fields=["diamonds"])
        return "۲ الماس"
    if step <= 10:
        from game.energy import get_max_energy, sync_energy

        sync_energy(receiver)
        gain = min(2, max(0, get_max_energy(receiver) - receiver.energy))
        if gain:
            receiver.energy += gain
            if receiver.energy >= get_max_energy(receiver):
                receiver.energy_updated_at = timezone.now()
            receiver.save(update_fields=["energy", "energy_updated_at"])
        return f"{gain} انرژی" if gain else "انرژی، ولی نوار انرژی پر بود"
    if step <= 14:
        from game.buildings import grant_speedup_card

        grant_speedup_card(receiver, 30, count=2)
        return "۲ کارت سرعت ۳۰ دقیقه‌ای"
    kind = _jackpot_kind()
    if kind == "full":
        from game.energy import get_max_energy, sync_energy

        sync_energy(receiver)
        receiver.energy = get_max_energy(receiver)
        receiver.energy_updated_at = timezone.now()
        receiver.save(update_fields=["energy", "energy_updated_at"])
        return "انرژی پر شد"
    if kind == "gems":
        receiver.diamonds += 20
        receiver.save(update_fields=["diamonds"])
        return "۲۰ الماس"
    from bio_lab.models import DungeonProfile
    from game.dungeon import KEY_HARD_CAP

    prof = DungeonProfile.objects.select_for_update().filter(user=receiver).first()
    if prof is None:
        DungeonProfile.objects.create(user=receiver)
        prof = DungeonProfile.objects.select_for_update().get(user=receiver)
    key_line = "۱ کلید سیاه‌چال"
    if prof.keys >= KEY_HARD_CAP:
        key_line = "کلید سیاه‌چال به سقف رسیده بود"
    else:
        prof.keys += 1
        prof.save(update_fields=["keys"])
    receiver.coins += 100_000
    receiver.save(update_fields=["coins"])
    return f"{key_line} و ۱۰۰٬۰۰۰ طلا"


@transaction.atomic
def give_lick(giver: User, receiver: User) -> tuple[str, int]:
    """One lick per pair per day. Fifteen different people fill one player's prizes."""
    if not are_friends(giver.id, receiver.id):
        raise GameError("لیس فقط بین دوست‌هاست.")
    for uid in sorted((giver.id, receiver.id)):
        User.objects.select_for_update().get(id=uid)
    day = today_str()
    if FriendMark.objects.filter(
        kind=LICK_KIND, actor_id=giver.id, target_id=receiver.id, day=day,
    ).exists():
        raise GameError(
            "👅💧 یبار لیسش زدی. چند بار دیگه می‌خوای بزنی؟\n"
            "همین الانم خیسش کردی."
        )
    if FriendMark.objects.filter(kind=LICK_KIND, actor_id=giver.id, day=day).count() >= LICK_CAP:
        raise GameError(
            "👅💧 برای ۱۵ نفر لیس زدی. زبونت خشک شد!\n"
            "برو فردا بیا تا دوباره لیسش بزنی."
        )
    if FriendMark.objects.filter(kind=LICK_KIND, target_id=receiver.id, day=day).count() >= LICK_CAP:
        raise GameError(
            "👅💧 امروز این یکی حسابی لیس خورده. حسابی خیس شده و کثیفه. فردا دوباره بیا تا خشک شه."
        )
    FriendMark.objects.create(kind=LICK_KIND, actor_id=giver.id, target_id=receiver.id, day=day)
    step = FriendMark.objects.filter(kind=LICK_KIND, target_id=receiver.id, day=day).count()
    return _pay_lick(receiver, step), step


def licks_received_text(user_id: int) -> str:
    """Who licked this player today. Names stay on their own lines."""
    rows = (
        FriendMark.objects.filter(kind=LICK_KIND, target_id=user_id, day=today_str())
        .select_related("actor")
        .order_by("created_at", "id")
    )
    names = [lab_mention(row.actor) for row in rows]
    head = f"👅 <b>لیس‌های امروز</b>\n{_fa(len(names))} از {_fa(LICK_CAP)}"
    if not names:
        return head + "\n\nهنوز امروز کسی لیس نزده."
    return head + "\n\n" + "\n".join(names)


def set_invites(user: User, enabled: bool) -> bool:
    """Close or open incoming تف. Closing also drops requests that are still waiting."""
    user.friend_invites = enabled
    user.save(update_fields=["friend_invites"])
    if not enabled:
        # Delete, don't decline: a decline would block the same person for a day after reopening.
        FriendLink.objects.filter(status=FriendLink.PENDING).filter(
            Q(user_lo=user) | Q(user_hi=user),
        ).exclude(asked_by_id=user.id).delete()
    return enabled


def _bounced(sender: User, target: User) -> dict:
    """Spit came back: this player closed incoming requests. The lock glyph is the premium one."""
    return {
        "text": "🔒 <b>تف</b>\n\n" + _names(
            lab_mention(sender), lab_mention(target),
            "تفت برگشت. این بازیکن دریافت تف را بسته است.",
        ),
        "ask": False, "wait": False, "quiet": False,
    }


def _rejected_wait(sender_id: int, receiver_id: int) -> str:
    """Why a new spit did not open a request after «بی‌خیال», and roughly how long is left."""
    lo, hi = pair_ids(sender_id, receiver_id)
    row = FriendLink.objects.filter(user_lo_id=lo, user_hi_id=hi).first()
    hours = DECLINE_HOURS
    if row is not None and row.status == FriendLink.DECLINED:
        left = row.updated_at + datetime.timedelta(hours=DECLINE_HOURS) - timezone.now()
        hours = max(1, -(-int(left.total_seconds()) // 3600))
    return (
        "تفت نرفت.\n"
        f"این بازیکن درخواست قبلی را رد کرده. حدود {_fa(hours)} ساعت دیگر می‌توانی دوباره تف کنی."
    )


def spit_request(sender: User, target: User) -> dict:
    """The تف card. Creates the request; the other person still has to accept."""
    if not getattr(target, "friend_invites", True):
        return _bounced(sender, target)
    result = ask(sender.id, target.id)
    if result == "closed":
        return _bounced(sender, target)
    if result == "blocked":
        return {
            "text": "🤝 <b>تف</b>\n\n" + _names(
                lab_mention(sender), lab_mention(target),
                _rejected_wait(sender.id, target.id),
            ),
            "ask": False, "wait": False, "quiet": False,
        }
    extra = {
        "asked": "درخواست دوستی فرستاده شد. فقط خودِ طرف می‌تواند قبول کند.",
        "already": "درخواست قبلی هنوز باز است.",
        "friends": "از قبل دوست هستید. درخواست تازه‌ای نرفت.",
        "full": "لیست دوست‌ها پر است و درخواست ثبت نشد.",
        "closed": "این بازیکن دریافت تف را بسته است.",
    }.get(result, "")
    text = "🤝 <b>تف</b>\n\n" + _names(
        lab_mention(sender), lab_mention(target),
        "به نشانه‌ی علاقه‌ی هیولاها تف کرد." + (f"\n<i>{extra}</i>" if extra else ""),
    )
    return {
        "text": text,
        "ask": result in ("asked", "already"),
        "wait": False,
        "quiet": False,
    }


def _names(who: str, them: str, body: str) -> str:
    """Names sit on their own lines, so a Latin lab name does not break the Persian sentence."""
    return f"از: {who}\nبه: {them}\n\n{body}"


def play(sender: User, target: User | None, word: str, other_id: int) -> dict:
    """The group reply. `quiet` means say nothing. `ask` means offer the two buttons."""
    if word != "لیس":
        gate = _cooldown(sender.id, other_id)
        if gate == "wait":
            return {"text": "یه کم صبر کن و دوباره بزن.", "ask": False, "wait": True, "quiet": False}
        if gate == "quiet":
            return {"text": "", "ask": False, "wait": False, "quiet": True}
    if target is None:
        return {"text": "این بازیکن هنوز بازی را شروع نکرده.", "ask": False, "wait": False, "quiet": False}
    who = lab_mention(sender)
    them = lab_mention(target)
    if word == "تف":
        return spit_request(sender, target)
    if word == "لیس":
        if not are_friends(sender.id, target.id):
            if not getattr(target, "friend_invites", True):
                text = (
                    "👅 <b>لیس</b>\n\n"
                    "شما دوست نیستید و نمی‌توانید به هم لیس بزنید.\n\n"
                    f"{them} دریافت تف را بسته است."
                )
                return {"text": text, "ask": False, "wait": False, "quiet": False}
            text = (
                "👅 <b>لیس</b>\n\n"
                "شما دوست نیستید و نمی‌توانید به هم لیس بزنید.\n\n"
                f"برای دوست شدن، با دکمه‌ی زیر به {them} تف کن."
            )
            return {"text": text, "ask": False, "spit": True, "wait": False, "quiet": False}
        try:
            prize, step = give_lick(sender, target)
        except GameError as exc:
            return {"text": str(exc), "ask": False, "wait": False, "quiet": False}
        text = "👅 <b>لیس</b>\n\n" + _names(
            who, them,
            "به نشانه‌ی دوستی لیسید.\n"
            f"{them} جایزه گرفت: {prize}\n"
            f"<i>امروز {_fa(step)} از {_fa(LICK_CAP)} لیسِ دریافتی.</i>",
        )
        return {"text": text, "ask": False, "wait": False, "quiet": False}
    return {"text": "", "ask": False, "wait": False, "quiet": True}

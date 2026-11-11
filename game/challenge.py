"""«🥊 چالش رفیق» — share a link, and whoever opens it fights your kaiju with a guest kaiju.

The link (t.me/<bot>?start=duel_<code>) is also an invite link: a brand-new player who opens
it is bound to the sharer as their referrer (game/referral.py), so a challenge is the most
fun way in. The newcomer doesn't need a kaiju of their own yet — they get a «کایجوی مهمان»
a bit stronger than the sharer's and fight right away.

The fight is three interactive rounds. Each round the player picks a move: heavy attack
beats an elemental strike, an elemental strike beats a guard, a guard beats a heavy
attack. The opponent picks at random but «telegraphs» its move (HINT_ACCURACY right), so a
player who reads the hint wins more: simulated, a random player wins ~63%, one who follows
the hint half the time ~77%, always ~87%. Most newcomers win — that's the point — and the
sharer is told «X beat you!» with a button for a new challenge.

Rewards: the friend's win pays (more for a new player); only the first DAILY_REWARDED
challenge results a day pay. The sharer gets gold + DNA (no diamonds — fake accounts could
farm those) when a new player finishes their challenge, at most SHARER_DAILY_CAP a day.
"""

from __future__ import annotations

import datetime
import random
import secrets

from django.db import IntegrityError, transaction
from django.utils import timezone

from bio_lab.models import ChallengeRun, FriendChallenge, User
from bio_lab.repository import get_active_creature, lock_row
from game.creature import GameError, creature_power

MOVES = {
    "heavy": ("⚔️", "حمله‌ی سنگین"),
    "guard": ("🛡", "دفاع و ضدحمله"),
    "element": ("⚡", "ضربه‌ی عنصری"),
}
BEATS = {"heavy": "element", "element": "guard", "guard": "heavy"}
# (damage to me, damage to the foe) by round result
DAMAGE = {"W": (5, 45), "L": (35, 5), "T": (15, 20)}
ROUNDS = 3
HINT_ACCURACY = 0.6
GUEST_POWER = 1.10
LINK_HOURS = 48

NEW_PLAYER_DAYS = 2
WIN_NEW = {"diamonds": 30, "coins": 20_000, "dna": 1_000, "cards": [(30, 1)]}
WIN_OLD = {"coins": 8_000, "dna": 400}
LOSE_NEW = {"coins": 8_000, "dna": 400}
DAILY_REWARDED = 3
SHARER_REWARD = {"coins": 10_000, "dna": 500}
SHARER_DAILY_CAP = 10

HINTS = {
    "heavy": "💢 کایجوی حریف داره نیرو جمع می‌کنه…",
    "guard": "🧱 کایجوی حریف پاهاشو محکم کرده و گارد گرفته…",
    "element": "✨ دور کایجوی حریف انرژی عنصری می‌چرخه…",
}


def link_for(code: str) -> str:
    from config import BOT_USERNAME

    return f"https://t.me/{BOT_USERNAME}?start=duel_{code}"


def share_url(code: str, creature_name: str) -> str:
    from urllib.parse import quote

    text = f"🥊 جرئت داری با «{creature_name}» من بجنگی؟ یه کایجوی مهمان بهت می‌دن — ببینم می‌بری! 😏"
    return f"https://t.me/share/url?url={quote(link_for(code))}&text={quote(text)}"


def parse_payload(payload: str | None) -> str | None:
    if payload and payload.startswith("duel_") and 4 <= len(payload) - 5 <= 12 and payload[5:].isalnum():
        return payload[5:]
    return None


# ── the link ──────────────────────────────────────────────────────────────────
def create(user: User) -> FriendChallenge:
    """The sharer's challenge link (reused for 30 minutes so a double tap doesn't make two)."""
    from bio_lab.repository import creature_name
    from game.equipment import get_equipped_items

    creature = get_active_creature(user)
    if creature is None:
        raise GameError("اول یه هیولای فعال انتخاب کن.")
    recent = FriendChallenge.objects.filter(challenger=user, created_at__gt=timezone.now() - datetime.timedelta(minutes=30),
                                            creature_name=creature_name(creature, mark=False)).order_by("-id").first()
    if recent:
        return recent
    power = creature_power(creature, get_equipped_items(creature))
    for _ in range(10):
        code = secrets.token_hex(4)
        try:
            return FriendChallenge.objects.create(
                code=code, challenger=user, creature_name=creature_name(creature, mark=False),
                element=creature.element, power=power,
                expires_at=timezone.now() + datetime.timedelta(hours=LINK_HOURS))
        except IntegrityError:
            continue
    raise GameError("یه لحظه دیگه دوباره امتحان کن.")


def get(code: str) -> FriendChallenge | None:
    return FriendChallenge.objects.select_related("challenger").filter(code=code).first()


def is_new_player(user: User) -> bool:
    created = getattr(user, "created_at", None)
    return created is not None and timezone.now() - created < datetime.timedelta(days=NEW_PLAYER_DAYS)


# ── the fight ─────────────────────────────────────────────────────────────────
def _hint_for(foe_move: str) -> str:
    shown = foe_move if random.random() < HINT_ACCURACY else random.choice([m for m in MOVES if m != foe_move])
    return shown


@transaction.atomic
def start(user: User, code: str) -> ChallengeRun:
    ch = FriendChallenge.objects.select_for_update().filter(code=code).first()
    if ch is None or ch.expires_at <= timezone.now():
        raise GameError("این لینک چالش منقضی شده. از رفیقت بخواه یه لینک تازه بفرسته!")
    if ch.challenger_id == user.id:
        raise GameError("این چالش خودته! لینکش رو برای رفیقات بفرست. 😄")
    run, created = ChallengeRun.objects.get_or_create(challenge=ch, user=user)
    if created:
        foe = random.choice(list(MOVES))
        run.log = [{"foe": foe, "hint": _hint_for(foe)}]
        run.save(update_fields=["log"])
    return run


def view(run: ChallengeRun) -> dict:
    ch = run.challenge
    pending = run.log[-1] if run.log and "me" not in run.log[-1] else None
    return {
        "challenger_id": ch.challenger_id, "foe_name": ch.creature_name, "element": ch.element,
        "guest_power": int(ch.power * GUEST_POWER), "foe_power": ch.power,
        "round": run.round, "rounds": ROUNDS, "my_hp": run.my_hp, "foe_hp": run.foe_hp,
        "hint": HINTS[pending["hint"]] if pending else None,
        "history": [r for r in run.log if "me" in r], "finished": run.finished, "won": run.won,
    }


def _result(me: str, foe: str) -> str:
    if me == foe:
        return "T"
    return "W" if BEATS[me] == foe else "L"


@transaction.atomic
def play(user: User, run_id: int, move: str) -> dict:
    """One round. Returns {run, round, result, me, foe, finished, won, rewards…}."""
    if move not in MOVES:
        raise GameError("حرکت نامعتبر.")
    run = ChallengeRun.objects.select_for_update().select_related("challenge", "challenge__challenger").filter(
        id=run_id, user=user).first()
    if run is None:
        raise GameError("این نبرد پیدا نشد.")
    if run.finished:
        raise GameError("این نبرد تموم شده.")
    cur = run.log[-1]
    foe = cur["foe"]
    res = _result(move, foe)
    d_me, d_foe = DAMAGE[res]
    run.my_hp = max(0, run.my_hp - d_me)
    run.foe_hp = max(0, run.foe_hp - d_foe)
    run.round += 1
    cur.update(me=move, result=res)
    done = run.round >= ROUNDS or run.my_hp <= 0 or run.foe_hp <= 0
    out = {"round": run.round, "result": res, "me": move, "foe": foe, "finished": done}
    if done:
        run.finished = True
        run.won = run.foe_hp <= 0 or (run.my_hp > 0 and run.my_hp >= run.foe_hp)
        out.update(_finish(user, run))
    else:
        nxt = random.choice(list(MOVES))
        run.log.append({"foe": nxt, "hint": _hint_for(nxt)})
    run.save(update_fields=["my_hp", "foe_hp", "round", "log", "finished", "won"])
    out["run"] = run
    out["won"] = run.won
    return out


def _rewarded_today(user_id: int) -> int:
    start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    return ChallengeRun.objects.filter(user_id=user_id, finished=True, created_at__gte=start).count()


def _sharer_paid_today(challenger_id: int) -> int:
    start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    return ChallengeRun.objects.filter(challenge__challenger_id=challenger_id, finished=True,
                                       created_at__gte=start, user__created_at__gte=timezone.now()
                                       - datetime.timedelta(days=NEW_PLAYER_DAYS)).count()


def _finish(user: User, run: ChallengeRun) -> dict:
    from game import outbox
    from game.referral import _give, reward_text

    lock_row(user)
    new = is_new_player(user)
    reward = (WIN_NEW if new else WIN_OLD) if run.won else (LOSE_NEW if new else {})
    # this run is already counted as finished below by the caller's save; count the earlier ones
    if reward and _rewarded_today(user.id) >= DAILY_REWARDED:
        reward = {}
    if reward:
        _give(user, reward)
    ch = run.challenge
    sharer_reward = {}
    if new and _sharer_paid_today(ch.challenger_id) < SHARER_DAILY_CAP:
        sharer = User.objects.select_for_update().filter(id=ch.challenger_id).first()
        if sharer is not None and not sharer.is_banned:
            _give(sharer, SHARER_REWARD)
            sharer_reward = SHARER_REWARD
    try:
        from bio_lab.repository import lab_display

        who = lab_display(user) if user.lab_name else (user.first_name or "یه بازیکن تازه")
        if run.won:
            text = (f"😱 <b>{who} با کایجوی مهمان «{ch.creature_name}»ت رو شکست داد!</b>\n"
                    f"❤️ {run.my_hp} — {run.foe_hp}\n<i>انتقام؟ یه چالش تازه بساز و دوباره بفرست!</i>")
        else:
            text = (f"💪 <b>«{ch.creature_name}»ت چالش {who} رو برد!</b>\n"
                    f"❤️ {run.foe_hp} — {run.my_hp}\n<i>یه چالش دیگه بفرست برای رفیقای دیگه!</i>")
        if sharer_reward:
            text += f"\n🎁 چون یه بازیکن تازه چالشت رو انجام داد: {reward_text(sharer_reward)}"
        if User.objects.filter(id=ch.challenger_id, notifications_on=True).exists():
            outbox.push("group_post", {"chat_id": ch.challenger_id, "text": text})
    except Exception:  # noqa: BLE001
        pass
    return {"reward": reward, "reward_text": reward_text(reward) if reward else "", "new_player": new}

"""Referrals / دعوت دوستان — viral growth + social retention.

Each player has a deep link (``t.me/<bot>?start=ref_<id>``). When a brand-new
player opens the bot through one, we remember who invited them. When that new
player proves they're real by reaching a lab-level milestone, BOTH sides collect
a diamond reward — paying on a milestone (not on signup) is the anti-abuse gate,
so farming throwaway accounts earns nothing.

The reward payout is processed by the same periodic job as push notifications
(game/notifications.collect_due calls collect_rewards here), so it also gets to
DM both players "your friend joined — you both earned diamonds".
"""

from __future__ import annotations

from django.db import transaction

from bio_lab.models import User
from game import lab

MILESTONE_LEVEL = 3  # the friend must reach this lab level for the reward to unlock
REFERRER_REWARD = {"diamonds": 50}
FRIEND_REWARD = {"diamonds": 30}


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
    new_user.save(update_fields=["referred_by"])
    return True


def _grant(user: User, reward: dict) -> None:
    from game.battlepass import _grant as grant

    grant(user, reward)


@transaction.atomic
def _settle_one(friend: User) -> User | None:
    """Atomically claim and pay one milestone-reached referral. Returns the referrer
    if THIS call did the payout, or None if it was already paid (race-safe: the
    guarded UPDATE ensures the notification job and a manual claim can't double-pay)."""
    claimed = User.objects.filter(id=friend.id, referral_bonus_paid=False).update(
        referral_bonus_paid=True
    )
    if not claimed:
        return None
    referrer = User.objects.filter(id=friend.referred_by).first()
    _grant(friend, FRIEND_REWARD)
    if referrer is not None:
        _grant(referrer, REFERRER_REWARD)
    return referrer


def collect_rewards() -> list[tuple[int, str]]:
    """Pay out every referral whose friend just crossed the milestone, and return
    (user_id, text) DMs for both sides. Called from the notification collector."""
    out: list[tuple[int, str]] = []
    friends = User.objects.filter(referred_by__isnull=False, referral_bonus_paid=False)
    for friend in friends:
        if lab.lab_level(friend) < MILESTONE_LEVEL:
            continue
        referrer = _settle_one(friend)
        if referrer is None:
            continue  # already paid by a concurrent claim
        if friend.notifications_on:
            out.append(
                (friend.id, f"🎁 <b>پاداش دعوت!</b> {FRIEND_REWARD['diamonds']} 💎 گرفتی چون با لینک دعوت اومدی.")
            )
        if referrer.notifications_on:
            from bio_lab.repository import lab_display

            out.append(
                (
                    referrer.id,
                    f"🎉 <b>دوستی که دعوت کردی ({lab_display(friend)}) به بازی چسبید!</b> "
                    f"{REFERRER_REWARD['diamonds']} 💎 پاداش گرفتی.",
                )
            )
    return out


def claim_ready(user: User) -> dict:
    """Let a referrer collect, on demand, every one of THEIR invitees who has already
    reached the milestone but isn't paid yet. Returns how many and how much."""
    paid = 0
    friends = list(User.objects.filter(referred_by=user.id, referral_bonus_paid=False))
    for friend in friends:
        if lab.lab_level(friend) < MILESTONE_LEVEL:
            continue
        if _settle_one(friend) is not None:
            paid += 1
    return {"claimed": paid, "diamonds": paid * REFERRER_REWARD["diamonds"]}


def stats(user: User) -> dict:
    """Panel data: the player's link and how their invites are doing, incl. a
    per-friend breakdown and how many rewards are ready to claim right now."""
    from bio_lab.repository import lab_display

    referred = list(User.objects.filter(referred_by=user.id).order_by("referral_bonus_paid", "id"))
    friends = []
    claimable = 0
    for f in referred:
        lvl = lab.lab_level(f)
        reached = lvl >= MILESTONE_LEVEL
        if reached and not f.referral_bonus_paid:
            claimable += 1
        friends.append({
            "name": lab_display(f),
            "level": lvl,
            "paid": f.referral_bonus_paid,
            "reached": reached,
        })
    return {
        "link": link_for(user.id),
        "total": len(referred),
        "successful": sum(1 for x in friends if x["paid"]),
        "pending": sum(1 for x in friends if not x["paid"]),
        "claimable": claimable,
        "milestone_level": MILESTONE_LEVEL,
        "referrer_reward": REFERRER_REWARD["diamonds"],
        "friend_reward": FRIEND_REWARD["diamonds"],
        "friends": friends,
    }

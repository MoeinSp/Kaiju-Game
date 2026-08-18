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

    Only binds when the invited player is genuinely new, isn't referring
    themselves, has no referrer yet, and the referrer actually exists. Returns
    True if a binding was made."""
    if referrer_id is None or not was_created:
        return False
    if referrer_id == new_user.id or new_user.referred_by is not None:
        return False
    if not User.objects.filter(id=referrer_id).exists():
        return False
    new_user.referred_by = referrer_id
    new_user.save(update_fields=["referred_by"])
    return True


def _grant(user: User, reward: dict) -> None:
    from game.battlepass import _grant as grant

    grant(user, reward)


def collect_rewards() -> list[tuple[int, str]]:
    """Pay out every referral whose friend just crossed the milestone, and return
    (user_id, text) DMs for both sides. Called from the notification collector."""
    out: list[tuple[int, str]] = []
    friends = User.objects.filter(referred_by__isnull=False, referral_bonus_paid=False)
    for friend in friends:
        if lab.lab_level(friend) < MILESTONE_LEVEL:
            continue
        referrer = User.objects.filter(id=friend.referred_by).first()
        with transaction.atomic():
            friend.referral_bonus_paid = True
            friend.save(update_fields=["referral_bonus_paid"])
            _grant(friend, FRIEND_REWARD)
            if referrer is not None:
                _grant(referrer, REFERRER_REWARD)
        if friend.notifications_on:
            out.append(
                (friend.id, f"🎁 <b>پاداش دعوت!</b> {FRIEND_REWARD['diamonds']} 💎 گرفتی چون با لینک دعوت اومدی.")
            )
        if referrer is not None and referrer.notifications_on:
            from bio_lab.repository import lab_display

            out.append(
                (
                    referrer.id,
                    f"🎉 <b>دوستی که دعوت کردی ({lab_display(friend)}) به بازی چسبید!</b> "
                    f"{REFERRER_REWARD['diamonds']} 💎 پاداش گرفتی.",
                )
            )
    return out


def stats(user: User) -> dict:
    """Panel data: the player's link and how their invites are doing."""
    referred = User.objects.filter(referred_by=user.id)
    return {
        "link": link_for(user.id),
        "successful": referred.filter(referral_bonus_paid=True).count(),
        "pending": referred.filter(referral_bonus_paid=False).count(),
        "milestone_level": MILESTONE_LEVEL,
        "referrer_reward": REFERRER_REWARD["diamonds"],
        "friend_reward": FRIEND_REWARD["diamonds"],
    }

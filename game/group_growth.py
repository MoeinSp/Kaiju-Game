"""Growth through groups.

1. «افزودن ربات به گروه» — whoever adds the bot to a group (the FIRST adder only, recorded
   by bot/handlers/welcome.py) is paid as the group fills with REAL players: 10, 30 and 100.
   A real player has played in that group (a GroupMembership row, still in it), is at lab
   level 5+, has played on 3+ separate days and isn't banned — the same anti-fake bar as
   the invite system. Checked by the 5-minute notification job (collect_adder_rewards).

2. «پایگاه اتحاد» — an alliance and its Telegram group are one (Alliance.home_group). Anyone
   in an alliance registers the group with «ثبت پایگاه» (or removes it with «حذف پایگاه»);
   a group admin's «قفل پایگاه» limits both to the admins. The alliance's raid boss is only
   fought there, «اتحاد گروه» shows the alliance with a join button, and alliance-war results
   are posted there too.
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from bio_lab.models import Alliance, Group, GroupMembership, User
from game.creature import GameError

REAL_MIN_LEVEL = 5
REAL_MIN_DAYS = 3

ADD_STEPS = [
    (10, {"diamonds": 50, "coins": 200_000, "dna": 10_000, "cards": [(60, 1)]}),
    (30, {"diamonds": 120, "coins": 600_000, "dna": 30_000, "cards": [(60, 2)], "relics": 1}),
    (100, {"diamonds": 300, "coins": 2_000_000, "dna": 100_000, "cards": [(60, 3)], "relics": 2}),
]



def _real_xp() -> int:
    from game.lab import xp_for_level

    return xp_for_level(REAL_MIN_LEVEL)


def _today() -> str:
    from game.daily import today_str

    return today_str()


# ── who added the bot ─────────────────────────────────────────────────────────
def record_adder(chat_id: int, title: str | None, adder_id: int | None) -> bool:
    """Remember the first person who added the bot to this group. Never overwritten — removing
    and re-adding the bot can't move the reward to someone else."""
    group, _ = Group.objects.get_or_create(id=chat_id, defaults={"title": title})
    if group.added_by is not None or not adder_id:
        return False
    return bool(Group.objects.filter(id=chat_id, added_by__isnull=True).update(
        added_by=adder_id, added_at=timezone.now()))


def real_players(group_id: int, exclude: int | None = None) -> int:
    qs = GroupMembership.objects.filter(
        group_id=group_id, left_at__isnull=True, user__lab_xp__gte=_real_xp(),
        user__active_days__gte=REAL_MIN_DAYS, user__is_banned=False)
    if exclude:
        qs = qs.exclude(user_id=exclude)
    return qs.count()


def adder_progress(user_id: int) -> list[dict]:
    """The groups this player added the bot to, with their real-player count — for the panel."""
    out = []
    for g in Group.objects.filter(added_by=user_id).order_by("-added_at")[:10]:
        n = real_players(g.id, exclude=user_id)
        nxt = ADD_STEPS[g.adder_step][0] if g.adder_step < len(ADD_STEPS) else None
        out.append({"title": g.title or str(g.id), "real": n, "step": g.adder_step, "next": nxt})
    return out


ADDER_SCAN_EVERY = 600   # seconds
_ADDER_LAST_RUN = -1e9


def collect_adder_rewards() -> list[tuple]:
    """Pay the adder of every group that just reached a real-player step. Returns DMs; the
    group itself is told through the outbox."""
    from bio_lab.repository import lab_display
    from game import outbox
    from game.referral import _give, reward_text

    import time

    global _ADDER_LAST_RUN
    if time.monotonic() - _ADDER_LAST_RUN < ADDER_SCAN_EVERY:
        return []   # one count query per group — no need to run it every notify tick
    _ADDER_LAST_RUN = time.monotonic()
    out = []
    for g in Group.objects.filter(added_by__isnull=False, adder_step__lt=len(ADD_STEPS)):
        n = real_players(g.id, exclude=g.added_by)
        reached = sum(1 for need, _r in ADD_STEPS if n >= need)
        if reached <= g.adder_step:
            continue
        with transaction.atomic():
            claimed = Group.objects.filter(id=g.id, adder_step=g.adder_step).update(adder_step=reached)
            if not claimed:
                continue
            adder = User.objects.select_for_update().filter(id=g.added_by).first()
            if adder is None or adder.is_banned:
                continue
            paid = ADD_STEPS[g.adder_step:reached]
            for _need, reward in paid:
                _give(adder, reward)
        need = paid[-1][0]
        text = "\n".join(
            [f"🏟 <b>گروهی که ربات رو بهش اضافه کردی به {need} بازیکن واقعی رسید!</b>",
             f"👥 {g.title or g.id} — الان <b>{n}</b> بازیکن واقعی داره."]
            + [f"🎁 پله‌ی {nd} بازیکن: {reward_text(r)}" for nd, r in paid]
            + ([f"<i>🎯 پله‌ی بعدی: {ADD_STEPS[reached][0]} بازیکن ← {reward_text(ADD_STEPS[reached][1])}</i>"]
               if reached < len(ADD_STEPS) else ["🏁 <i>همه‌ی جایزه‌های این گروه رو گرفتی!</i>"]))
        if adder.notifications_on:
            out.append((adder.id, text))
        try:
            outbox.push("group_post", {"chat_id": g.id, "text": (
                f"🎉 <b>این گروه به {need} بازیکن واقعی رسید!</b>\n"
                f"{lab_display(adder)} که ربات رو اینجا آورد جایزه گرفت. 🎁\n"
                "<i>هر کی ربات رو به یه گروه شلوغ اضافه کنه، با رشد گروه جایزه می‌گیره — «دعوت» توی پیوی.</i>")})
        except Exception:  # noqa: BLE001
            pass
    return out


# ── alliance home group ───────────────────────────────────────────────────────
# The alliance and its group are one (owner, 2026-11-13): the alliance's raid boss is only
# fought in its home group and war results are posted there. Anyone in an alliance may
# register or remove the base; once a group admin types «قفل پایگاه», only the admins may.
_LOCKED_MSG = ("🔒 پایگاه این گروه قفله: فقط ادمین‌های گروه می‌تونن ثبت یا حذفش کنن.\n"
               "<i>ادمین‌ها با «باز کردن پایگاه» قفل رو برمی‌دارن.</i>")


def link_home(user: User, group: Group, sender_is_group_admin: bool) -> Alliance:
    if user.alliance_id is None:
        raise GameError("اول باید عضو یه اتحاد باشی.")
    if group.base_locked and not sender_is_group_admin:
        raise GameError(_LOCKED_MSG)
    alliance = Alliance.objects.select_related("home_group").get(id=user.alliance_id)
    other = Alliance.objects.filter(home_group=group).exclude(id=alliance.id).first()
    if other is not None:
        raise GameError(f"این گروه پایگاه اتحاد «{other.name}»ـه. اول باید «حذف پایگاه» بزنید.")
    alliance.home_group = group
    alliance.save(update_fields=["home_group"])
    return alliance


def unlink_home(user: User, group: Group, sender_is_group_admin: bool = False) -> Alliance:
    alliance = Alliance.objects.filter(home_group=group).first()
    if alliance is None:
        raise GameError("این گروه پایگاه هیچ اتحادی نیست.")
    if group.base_locked and not sender_is_group_admin:
        raise GameError(_LOCKED_MSG)
    alliance.home_group = None
    alliance.save(update_fields=["home_group"])
    return alliance


def set_base_lock(group: Group, sender_is_group_admin: bool, locked: bool) -> None:
    if not sender_is_group_admin:
        raise GameError("فقط ادمین‌های گروه می‌تونن پایگاه رو قفل یا باز کنن.")
    Group.objects.filter(id=group.id).update(base_locked=locked)


def home_alliance(group_id: int) -> Alliance | None:
    return Alliance.objects.filter(home_group_id=group_id).first()


def present_members(alliance: Alliance, group_id: int) -> int:
    return GroupMembership.objects.filter(group_id=group_id, left_at__isnull=True,
                                          user__alliance_id=alliance.id).count()


def home_card(group: Group) -> dict | None:
    from config import BOT_USERNAME
    from game import alliance_recruit

    alliance = Alliance.objects.filter(home_group=group).select_related("leader").first()
    if alliance is None:
        return None
    return {
        "name": alliance.name,
        "members": alliance.members.count(),
        "present": present_members(alliance, group.id),
        "leader": alliance.leader,
        "locked": group.base_locked,
        "join_url": f"https://t.me/{BOT_USERNAME}?start=ally_{alliance_recruit.invite_code(alliance)}",
    }


def post_war_result(alliance_id: int, text: str) -> None:
    """Alliance-war result → the alliance's home group, if it has one."""
    from game import outbox

    home = Alliance.objects.filter(id=alliance_id).values_list("home_group_id", flat=True).first()
    if home:
        outbox.push("group_post", {"chat_id": home, "text": text})

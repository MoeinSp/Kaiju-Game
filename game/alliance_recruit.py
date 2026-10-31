"""Finding an alliance, and alliances finding players (2026-10-31).

Joining used to be one list sorted by treasury: the same few rich, nearly full alliances on
top, dead one-member alliances in between, and 54% of the active players in no alliance at
all. This module is the rest of the system around game/alliance.py:

- **browse** — «پیشنهادی» (room, activity and a power match, with a daily per-player shuffle so
  every live alliance gets seen), «تازه و در حال رشد», «برترین‌ها»; filters for join mode, size,
  «فقط اونایی که بهشون می‌رسم» and «دنبال عضو»; and search by name.
- **recruiting** — a leader's one-line ad and «دنبال عضو» flag, both shown on the card.
- **invites** — a leader / deputy invites a player (by @username, lab name or id) or shares
  the alliance's invite link; accepting joins at once, without the approval queue or the
  min-power gate (the leadership asked for this player).
- **«پاداش رشد»** — a one-time reward for joining a small, live alliance, scaled by lab level,
  with conditions that keep throw-away accounts out.
- **«صندوق نامه»** (Mail) — invites, request answers, kicks and the bonus land here, in the
  bot and the Mini App; the web process DMs through game/outbox.py.
"""

from __future__ import annotations

import datetime
import random
import secrets
import time

from django.db import transaction
from django.db.models import Count, F, Q, Sum
from django.utils import timezone

from bio_lab.models import (Alliance, AllianceBonusLog, AllianceInvite, AllianceInviteBlock, AllianceJoinRequest, Creature,
                            DailyActionLog, Mail, User)
from game.creature import GameError

ACTIVE_DAYS = 3                 # «active» = played on one of the last 3 game days
SMALL_ALLIANCE = 10             # fewer members than this = small (bonus, «در حال رشد»)
NEW_ALLIANCE_DAYS = 14
SUGGEST_MIN_ACTIVE = 2          # the suggestions only show alliances with 2+ active members
PAGE_SIZE = 6
NOTE_MAX = 120

INVITE_TTL = datetime.timedelta(hours=72)
INVITE_MAX_PENDING = 10         # open invites an alliance may have at once (the link doesn't count)
INVITE_DAILY = 20
GROUP_INVITE_GAP = datetime.timedelta(minutes=5)   # one «دعوت» from a group per alliance per 5 min

# «پاداش رشد» — owner's call: generous, by lab level, and not for fake accounts
BONUS_BASE, BONUS_PER_LEVEL = 10_000, 1_500     # lab 10 → 25k, lab 30 → 55k, lab 50 → 85k
BONUS_TREASURY_SHARE = 0.5
BONUS_MIN_LAB = 10
BONUS_MIN_ACCOUNT_DAYS = 3
BONUS_MIN_ACTIVE_DAYS = 2       # distinct days played in the last 7
BONUS_MIN_ALLIANCE_DAYS = 2
BONUS_MIN_ACTIVE_MEMBERS = 2    # besides the newcomer
BONUS_ALLIANCE_DAILY = 3        # bonuses one alliance can bring in a day

SIZES = {"small": (0, 9), "medium": (10, 19), "large": (20, 10 ** 6)}

_ACTIVE: tuple[float, set[int]] = (0.0, set())


# ══════════════════════════ shared numbers ══════════════════════════
def active_ids() -> set[int]:
    """Players who played on one of the last ACTIVE_DAYS game days (cached 2 minutes)."""
    global _ACTIVE
    if time.monotonic() - _ACTIVE[0] < 120:
        return _ACTIVE[1]
    today = timezone.localdate()
    days = [(today - datetime.timedelta(days=i)).isoformat() for i in range(ACTIVE_DAYS)]
    ids = set(DailyActionLog.objects.filter(day__in=days).values_list("user_id", flat=True))
    _ACTIVE = (time.monotonic(), ids)
    return ids


def player_power(user: User) -> int:
    """The same yardstick as game.alliance._alliance_power: the active kaiju's base stats."""
    c = Creature.objects.filter(owner=user, is_active=True).only("base_hp", "base_atk", "base_def", "base_spd").first()
    return (c.base_hp + c.base_atk + c.base_def + c.base_spd) if c else 0


def _power_map() -> dict[int, int]:
    rows = (Creature.objects.filter(is_active=True, owner__alliance__isnull=False)
            .values("owner__alliance_id")
            .annotate(p=Sum(F("base_hp") + F("base_atk") + F("base_def") + F("base_spd"))))
    return {r["owner__alliance_id"]: int(r["p"] or 0) for r in rows}


def _active_map(ids: set[int]) -> dict[int, int]:
    rows = (User.objects.filter(id__in=ids, alliance__isnull=False)
            .values("alliance_id").annotate(n=Count("id")))
    return {r["alliance_id"]: r["n"] for r in rows}


def _card(a: Alliance, n: int, power: int, active: int, viewer_power: int) -> dict:
    from game.alliance import max_members

    cap = max_members(a)
    age = (timezone.now() - a.created_at).days if a.created_at else 999
    return {
        "id": a.id, "name": a.name, "members": n, "capacity": cap, "active": active,
        "power": power, "avg_power": round(power / n) if n else 0,
        "auto_accept": a.auto_accept, "min_power": a.min_join_power,
        "note": a.recruit_note, "recruiting": a.recruiting,
        "is_new": age <= NEW_ALLIANCE_DAYS, "small": n < SMALL_ALLIANCE,
        "full": n >= cap, "reachable": viewer_power >= a.min_join_power,
    }


# ══════════════════════════ browse & search ══════════════════════════
def browse(user: User, tab: str = "suggested", *, join: str = "", size: str = "", reach: bool = True,
           recruiting: bool = False, page: int = 0) -> dict:
    """One page of alliance cards for a player looking for one."""
    act = active_ids()
    powers, actives = _power_map(), _active_map(act)
    mine = player_power(user)
    cards = []
    for a in Alliance.objects.annotate(n=Count("members")).exclude(id=user.alliance_id or 0):
        c = _card(a, a.n, powers.get(a.id, 0), actives.get(a.id, 0), mine)
        if c["full"] or (reach and not c["reachable"]):
            continue
        if join == "auto" and not c["auto_accept"] or join == "approval" and c["auto_accept"]:
            continue
        if size in SIZES and not SIZES[size][0] <= c["members"] <= SIZES[size][1]:
            continue
        if recruiting and not c["recruiting"]:
            continue
        cards.append(c)

    if tab == "top":
        cards = [c for c in cards if c["active"] >= 1]
        cards.sort(key=lambda c: c["power"], reverse=True)
    elif tab == "new":
        cards = [c for c in cards if c["active"] >= 1 and (c["is_new"] or c["small"])]
        cards.sort(key=lambda c: (c["active"], c["recruiting"], c["id"]), reverse=True)
    else:
        cards = [c for c in cards if c["active"] >= SUGGEST_MIN_ACTIVE]
        day = timezone.localdate().isoformat()

        def score(c: dict) -> float:
            room = (c["capacity"] - c["members"]) / c["capacity"]
            activity = c["active"] / max(1, c["members"])
            fit = 1 - min(1.0, abs(c["avg_power"] - mine) / max(mine, c["avg_power"], 1)) if mine else 0.5
            s = 0.35 * room + 0.35 * activity + 0.2 * fit
            s += 0.25 * c["recruiting"] + 0.15 * (c["small"] and c["active"] >= SUGGEST_MIN_ACTIVE)
            s -= 0.3 * (c["members"] / c["capacity"] > 0.8)
            # the same player sees a steady order through the day, another player another order
            return s + random.Random(f"{user.id}:{day}:{c['id']}").uniform(0, 0.25)

        cards.sort(key=score, reverse=True)
    total = len(cards)
    page = max(0, int(page))
    chunk = cards[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
    return {"cards": chunk, "total": total, "page": page, "page_size": PAGE_SIZE,
            "has_next": (page + 1) * PAGE_SIZE < total, "has_prev": page > 0, "my_power": mine}


def search(user: User, query: str, limit: int = 8) -> list[dict]:
    q = (query or "").strip()
    if not q:
        return []
    act = active_ids()
    powers, actives = _power_map(), _active_map(act)
    mine = player_power(user)
    return [_card(a, a.n, powers.get(a.id, 0), actives.get(a.id, 0), mine)
            for a in Alliance.objects.filter(name__icontains=q).annotate(n=Count("members"))[:limit]]


# ══════════════════════════ recruiting (leaders) ══════════════════════════
def _managed(actor: User) -> Alliance:
    from game.alliance import _is_manager

    if actor.alliance_id is None:
        raise GameError("عضو هیچ اتحادی نیستی.")
    alliance = Alliance.objects.get(id=actor.alliance_id)
    if not _is_manager(actor, alliance):
        raise GameError("فقط رهبر یا قائم‌مقام می‌تونه این کار رو بکنه.")
    return alliance


def set_recruit(actor: User, *, note: str | None = None, recruiting: bool | None = None) -> Alliance:
    alliance = _managed(actor)
    fields = []
    if note is not None:
        note = " ".join(note.split())
        if len(note) > NOTE_MAX:
            raise GameError(f"آگهی حداکثر {NOTE_MAX} حرف می‌تونه باشه.")
        if any(ch in note for ch in "<>&"):
            raise GameError("آگهی نمی‌تونه علامت‌های < > & داشته باشه.")
        alliance.recruit_note = note
        fields.append("recruit_note")
    if recruiting is not None:
        alliance.recruiting = bool(recruiting)
        fields.append("recruiting")
    if fields:
        alliance.save(update_fields=fields)
    return alliance


def invite_code(alliance: Alliance, renew: bool = False) -> str:
    if renew or not alliance.invite_code:
        alliance.invite_code = secrets.token_hex(5)
        alliance.save(update_fields=["invite_code"])
    return alliance.invite_code


def manage_view(actor: User) -> dict:
    """The leader's «جذب و دعوت» page."""
    alliance = _managed(actor)
    from game.alliance import max_members, pending_requests

    now = timezone.now()
    invites = list(AllianceInvite.objects.filter(alliance=alliance, status=AllianceInvite.PENDING, via_link=False,
                                                 expires_at__gt=now).select_related("user").order_by("-created_at"))
    return {
        "alliance": alliance, "note": alliance.recruit_note, "recruiting": alliance.recruiting,
        "code": invite_code(alliance), "members": alliance.members.count(), "capacity": max_members(alliance),
        "invites": [{"id": i.id, "user": i.user, "expires_at": i.expires_at} for i in invites],
        "requests": pending_requests(alliance),
        "sent_today": _sent_today(alliance),
    }


def _sent_today(alliance: Alliance) -> int:
    since = timezone.now() - datetime.timedelta(days=1)
    return AllianceInvite.objects.filter(alliance=alliance, via_link=False, created_at__gte=since).count()


# ══════════════════════════ invites ══════════════════════════
def find_player(query: str) -> User | None:
    q = (query or "").strip()
    if not q:
        return None
    if q.lstrip("-").isdigit():
        return User.objects.filter(id=int(q)).first()
    if q.startswith("@"):
        return User.objects.filter(username__iexact=q[1:]).first()
    return (User.objects.filter(lab_name__iexact=q).first()
            or User.objects.filter(username__iexact=q).first())


def invite(actor: User, query: str) -> AllianceInvite:
    target = find_player(query)
    if target is None or not target.lab_name:
        raise GameError("همچین بازیکنی پیدا نشد. یوزرنیم (با @)، اسم آزمایشگاه یا آیدی عددیش رو بفرست.")
    return invite_user(actor, target)


@transaction.atomic
def invite_user(actor: User, target: User, via_group: bool = False) -> AllianceInvite:
    from game.alliance import _assert_has_room

    alliance = _managed(actor)
    alliance = Alliance.objects.select_for_update().get(id=alliance.id)
    if target.id == actor.id:
        raise GameError("خودت رو نمی‌تونی دعوت کنی!")
    if target.alliance_id == alliance.id:
        raise GameError("این بازیکن همین الان عضو اتحاد شماست.")
    if target.alliance_id is not None:
        raise GameError("این بازیکن الان عضو یه اتحاد دیگه‌ست.")
    if target.is_banned:
        raise GameError("این بازیکن مسدوده.")
    if target.block_alliance_invites:
        raise GameError("این بازیکن دعوت اتحاد رو بسته.")
    if AllianceInviteBlock.objects.filter(user=target, blocked=actor).exists():
        raise GameError("این بازیکن دعوت‌های تو رو بلاک کرده.")
    _assert_has_room(alliance)
    now = timezone.now()
    open_q = AllianceInvite.objects.filter(alliance=alliance, status=AllianceInvite.PENDING, expires_at__gt=now)
    if open_q.filter(user=target).exists():
        raise GameError("این بازیکن رو قبلاً دعوت کردی؛ منتظر جوابشه.")
    day0 = timezone.localtime(now).replace(hour=0, minute=0, second=0, microsecond=0)
    if AllianceInvite.objects.filter(alliance=alliance, user=target, via_link=False, created_at__gte=day0).exists():
        raise GameError("این بازیکن رو امروز یه بار دعوت کردی؛ فردا دوباره.")
    if via_group:
        last = (AllianceInvite.objects.filter(alliance=alliance, via_group=True, created_at__gt=now - GROUP_INVITE_GAP)
                .order_by("-created_at").values_list("created_at", flat=True).first())
        if last is not None:
            mins = max(1, -(-int((last + GROUP_INVITE_GAP - now).total_seconds()) // 60))
            raise GameError(f"یکم صبر کن؛ {mins} دقیقه‌ی دیگه می‌تونی دوباره از گروه دعوت کنی.")
    if open_q.filter(via_link=False).count() >= INVITE_MAX_PENDING:
        raise GameError(f"اتحادت {INVITE_MAX_PENDING} دعوت بی‌جواب داره؛ صبر کن یا چندتاش رو لغو کن.")
    if _sent_today(alliance) >= INVITE_DAILY:
        raise GameError(f"امروز {INVITE_DAILY} دعوت فرستادی؛ فردا دوباره.")
    inv = AllianceInvite.objects.create(alliance=alliance, user=target, inviter=actor, via_group=via_group,
                                        expires_at=now + INVITE_TTL)
    _invite_mail(inv)
    return inv


def use_link(user: User, code: str) -> AllianceInvite | None:
    """The player opened an alliance's invite link: an invite for them (from the link)."""
    alliance = Alliance.objects.filter(invite_code=code).first() if code else None
    if alliance is None:
        raise GameError("این لینک دعوت دیگه معتبر نیست.")
    if user.alliance_id == alliance.id:
        raise GameError(f"تو همین الان عضو «{alliance.name}» هستی.")
    now = timezone.now()
    inv = AllianceInvite.objects.filter(alliance=alliance, user=user, status=AllianceInvite.PENDING, expires_at__gt=now).first()
    if inv is None:
        inv = AllianceInvite.objects.create(alliance=alliance, user=user, inviter=alliance.leader, via_link=True,
                                            expires_at=now + INVITE_TTL)
        _invite_mail(inv, dm=False)   # the player is looking at it right now
    return inv


def _invite_mail(inv: AllianceInvite, dm: bool = True) -> None:
    a = inv.alliance
    who = f" از طرف {inv.inviter.lab_name or inv.inviter.first_name}" if inv.inviter and not inv.via_link else ""
    body = f"اتحاد «{a.name}»{who} دعوتت کرده. با قبول کردن، همین الان عضو می‌شی."
    u = inv.user
    if dm and (not u.notifications_on or "invites" in (u.notify_off or "").split(",")):
        dm = False   # «دعوت اتحاد» notifications are off: the letter still lands in the inbox
    send_mail(inv.user_id, "ally_invite", f"دعوت به اتحاد «{a.name}»", body,
              {"invite": inv.id, "alliance": a.id}, dm=dm, buttons=invite_buttons(inv))


def invite_buttons(inv: AllianceInvite) -> list:
    """[[(label, callback_data)…]…] under an invite: answer it, block the inviter, mute invite DMs."""
    rows = [[("قبول دعوت", f"ainv_ok:{inv.id}"), ("رد", f"ainv_no:{inv.id}")]]
    if inv.inviter_id and not inv.via_link:
        who = (inv.inviter.lab_name or inv.inviter.first_name or str(inv.inviter_id))[:16]
        rows.append([(f"بلاک {who}", f"ainv_blk:{inv.id}")])
    rows.append([("خاموش کردن اعلان دعوت‌ها", f"ainv_mute:{inv.id}")])
    return rows


@transaction.atomic
def block_inviter(user: User, invite_id: int) -> str:
    """«بلاک»: no more invites from this invite's sender; the invite itself is declined."""
    inv = AllianceInvite.objects.select_related("inviter").filter(id=invite_id, user=user).first()
    if inv is None or inv.inviter_id is None or inv.via_link:
        raise GameError("این دعوت فرستنده‌ای نداره که بلاکش کنی.")
    AllianceInviteBlock.objects.get_or_create(user=user, blocked_id=inv.inviter_id)
    if inv.status == AllianceInvite.PENDING:
        inv.status = AllianceInvite.DECLINED
        inv.save(update_fields=["status"])
        _close_invite_mails([inv.id])
    return inv.inviter.lab_name or inv.inviter.first_name or str(inv.inviter_id)


def mute_invites(user: User) -> None:
    off = {c for c in (user.notify_off or "").split(",") if c}
    off.add("invites")
    User.objects.filter(id=user.id).update(notify_off=",".join(sorted(off)))


def blocked_count(user: User) -> int:
    return AllianceInviteBlock.objects.filter(user=user).count()


def clear_blocks(user: User) -> int:
    return AllianceInviteBlock.objects.filter(user=user).delete()[0]


def mail_body(m: Mail) -> str:
    """What a letter shows: invites sent before 2026-10-31 also carried the alliance's ad (one read
    «۲۴ ساعت حمله …» and looked like a deadline), which is dropped."""
    return m.body.split("\nآگهی اتحاد:")[0] if m.kind == "ally_invite" else m.body


def pending_invites(user: User) -> list[AllianceInvite]:
    return list(AllianceInvite.objects.filter(user=user, status=AllianceInvite.PENDING, expires_at__gt=timezone.now())
                .select_related("alliance", "inviter").order_by("-created_at"))


def _close_invite_mails(invite_ids: list[int]) -> None:
    for m in Mail.objects.filter(kind="ally_invite", done=False, data__invite__in=invite_ids):
        m.done = True
        m.save(update_fields=["done"])


@transaction.atomic
def accept_invite(user: User, invite_id: int, switch: bool = False) -> Alliance:
    """switch=True: a member of another alliance leaves it first (same transaction), so
    «خروج از اتحاد قبلی و پیوستن» is one tap and never leaves them alliance-less on an error."""
    from game.alliance import _assert_has_room, _join_stamp, leave_alliance

    user = User.objects.select_for_update().get(id=user.id)
    inv = AllianceInvite.objects.select_for_update().select_related("alliance").filter(id=invite_id, user=user).first()
    if inv is None or inv.status != AllianceInvite.PENDING:
        raise GameError("این دعوت دیگه معتبر نیست.")
    if inv.expires_at <= timezone.now():
        inv.status = AllianceInvite.EXPIRED
        inv.save(update_fields=["status"])
        _close_invite_mails([inv.id])
        raise GameError("مهلت این دعوت تموم شده.")
    if user.alliance_id == inv.alliance_id:
        raise GameError("تو همین الان عضو این اتحادی.")
    if user.alliance_id is not None and not switch:
        raise GameError("تو الان توی یه اتحادی — اول ازش خارج شو، بعد دعوت رو قبول کن.")
    alliance = Alliance.objects.select_for_update().get(id=inv.alliance_id)
    _assert_has_room(alliance)   # before leaving: a full alliance must not cost them the old one
    if user.alliance_id is not None:
        leave_alliance(user)
    inv.status = AllianceInvite.ACCEPTED
    inv.save(update_fields=["status"])
    _close_invite_mails([inv.id])
    _join_stamp(user, alliance)   # → on_joined: the bonus, the other invites and requests
    who = user.lab_name or user.first_name or str(user.id)
    for mid in {alliance.leader_id, alliance.deputy_id, inv.inviter_id} - {None}:
        send_mail(mid, "ally_note", f"{who} به اتحاد پیوست", f"«{who}» دعوت اتحاد «{alliance.name}» رو قبول کرد.", dm=False)
    return alliance


def switch_view(user: User, invite_id: int) -> dict | None:
    """The «خروج و پیوستن» screen of a pending invite; None when the player has no alliance."""
    from game.alliance import leave_note

    inv = (AllianceInvite.objects.select_related("alliance")
           .filter(id=invite_id, user=user, status=AllianceInvite.PENDING, expires_at__gt=timezone.now()).first())
    if inv is None:
        raise GameError("این دعوت دیگه معتبر نیست.")
    current = Alliance.objects.filter(id=user.alliance_id).first() if user.alliance_id else None
    if current is None:
        return None
    return {"invite": inv.id, "target": inv.alliance.name, "current": current.name, "note": leave_note(user)}


@transaction.atomic
def decline_invite(user: User, invite_id: int) -> None:
    inv = AllianceInvite.objects.select_for_update().filter(id=invite_id, user=user, status=AllianceInvite.PENDING).first()
    if inv is None:
        raise GameError("این دعوت دیگه معتبر نیست.")
    inv.status = AllianceInvite.DECLINED
    inv.save(update_fields=["status"])
    _close_invite_mails([inv.id])


@transaction.atomic
def cancel_invite(actor: User, invite_id: int) -> None:
    alliance = _managed(actor)
    inv = AllianceInvite.objects.select_for_update().filter(id=invite_id, alliance=alliance, status=AllianceInvite.PENDING).first()
    if inv is None:
        raise GameError("این دعوت دیگه باز نیست.")
    inv.status = AllianceInvite.CANCELLED
    inv.save(update_fields=["status"])
    _close_invite_mails([inv.id])


def set_invite_block(user: User, on: bool) -> bool:
    User.objects.filter(id=user.id).update(block_alliance_invites=bool(on))
    return bool(on)


# ══════════════════════════ joined (any path) ══════════════════════════
def on_joined(user: User, alliance: Alliance) -> dict | None:
    """Called by game.alliance._join_stamp for every join: closes the player's other invites
    and requests, and pays «پاداش رشد» when it applies. Returns the bonus or None."""
    open_inv = list(AllianceInvite.objects.filter(user=user, status=AllianceInvite.PENDING).exclude(alliance=alliance)
                    .values_list("id", flat=True))
    if open_inv:
        AllianceInvite.objects.filter(id__in=open_inv).update(status=AllianceInvite.EXPIRED)
        _close_invite_mails(open_inv)
    same = list(AllianceInvite.objects.filter(user=user, alliance=alliance, status=AllianceInvite.PENDING)
                .values_list("id", flat=True))
    if same:
        AllianceInvite.objects.filter(id__in=same).update(status=AllianceInvite.ACCEPTED)
        _close_invite_mails(same)
    try:
        return _growth_bonus(user, alliance)
    except Exception:  # noqa: BLE001 — a bonus problem must never undo a join
        import logging

        logging.getLogger(__name__).exception("alliance growth bonus failed")
        return None


def bonus_amount(user: User) -> int:
    from game import lab

    return BONUS_BASE + BONUS_PER_LEVEL * lab.reward_level(user)


def bonus_blockers(user: User, alliance: Alliance) -> list[str]:
    """Why joining this alliance would NOT pay «پاداش رشد» (empty = it would). The joiner is
    counted as not yet a member."""
    from game import lab

    out = []
    now = timezone.now()
    if user.ally_bonus_claimed:
        out.append("claimed")
    if lab.lab_level(user) < BONUS_MIN_LAB:
        out.append("lab")
    if user.created_at and user.created_at > now - datetime.timedelta(days=BONUS_MIN_ACCOUNT_DAYS):
        out.append("account_age")
    week = [(timezone.localdate() - datetime.timedelta(days=i)).isoformat() for i in range(7)]
    if DailyActionLog.objects.filter(user=user, day__in=week).values("day").distinct().count() < BONUS_MIN_ACTIVE_DAYS:
        out.append("activity")
    others = alliance.members.exclude(id=user.id)
    if others.count() >= SMALL_ALLIANCE:
        out.append("not_small")
    if alliance.created_at and alliance.created_at > now - datetime.timedelta(days=BONUS_MIN_ALLIANCE_DAYS):
        out.append("alliance_age")
    if others.filter(id__in=active_ids()).count() < BONUS_MIN_ACTIVE_MEMBERS:
        out.append("alliance_inactive")
    if AllianceBonusLog.objects.filter(alliance=alliance, created_at__gte=now - datetime.timedelta(days=1)).count() >= BONUS_ALLIANCE_DAILY:
        out.append("alliance_daily")
    return out


def _growth_bonus(user: User, alliance: Alliance) -> dict | None:
    from game.ledger import record_gain

    if bonus_blockers(user, alliance):
        return None
    if not User.objects.filter(id=user.id, ally_bonus_claimed=False).update(ally_bonus_claimed=True):
        return None   # a second join racing this one already took it
    coins = bonus_amount(user)
    treasury = round(coins * BONUS_TREASURY_SHARE)
    User.objects.filter(id=user.id).update(coins=F("coins") + coins)
    Alliance.objects.filter(id=alliance.id).update(treasury_gold=F("treasury_gold") + treasury)
    record_gain(user, "alliance_bonus", coins=coins)
    AllianceBonusLog.objects.create(user=user, alliance=alliance, coins=coins, treasury=treasury)
    send_mail(user.id, "ally_bonus", "🎁 پاداش رشد اتحاد",
              f"به خاطر پیوستن به اتحاد در حال رشد «{alliance.name}»، {coins:,} طلا گرفتی و "
              f"{treasury:,} طلا هم به خزانه‌ی اتحاد رفت.")
    return {"coins": coins, "treasury": treasury}


# ══════════════════════════ «صندوق نامه» ══════════════════════════
def send_mail(user_id: int, kind: str, title: str, body: str = "", data: dict | None = None, *,
              dm: bool = True, buttons: list | None = None) -> Mail:
    """A letter in the player's inbox; `dm` also sends it to their private chat (through the bot's
    outbox, so it works from the web process too), with `buttons` = [[(label, callback_data)…]…]."""
    m = Mail.objects.create(user_id=user_id, kind=kind, title=title[:120], body=body, data=data or {})
    if dm:
        from game import outbox

        try:
            outbox.push("dm", {"user_id": user_id, "title": title, "body": body,
                               "buttons": [[[lbl, cb] for lbl, cb in row] for row in (buttons or [])]})
        except Exception:  # noqa: BLE001 — the letter is in the inbox anyway
            pass
    return m


def inbox(user: User, limit: int = 30) -> list[Mail]:
    mails = list(Mail.objects.filter(user=user).order_by("-created_at")[:limit])
    # an invite letter is live only while its invite is
    inv_ids = [m.data.get("invite") for m in mails if m.kind == "ally_invite" and not m.done]
    if inv_ids:
        live = set(AllianceInvite.objects.filter(id__in=inv_ids, status=AllianceInvite.PENDING, expires_at__gt=timezone.now())
                   .values_list("id", flat=True))
        for m in mails:
            if m.kind == "ally_invite" and not m.done and m.data.get("invite") not in live:
                m.done = True
                Mail.objects.filter(id=m.id).update(done=True)
    return mails


def unread_count(user_or_id) -> int:
    uid = getattr(user_or_id, "id", user_or_id)
    return Mail.objects.filter(user_id=uid, read_at__isnull=True).count()


def mark_read(user: User, mail_id: int | None = None) -> None:
    q = Mail.objects.filter(user=user, read_at__isnull=True)
    if mail_id is not None:
        q = q.filter(id=mail_id)
    q.update(read_at=timezone.now())


def close_request_mails(request_id: int) -> None:
    Mail.objects.filter(kind="ally_request", done=False, data__request=request_id).update(done=True)

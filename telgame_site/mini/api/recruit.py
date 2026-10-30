"""Finding an alliance, invites and the «صندوق نامه» in the Mini App — game/alliance_recruit.py.

GET  recruit/browse/?tab=suggested|new|top&join=|auto|approval&size=|small|medium|large&reach=1&rec=0&page=0
GET  recruit/search/?q=
POST recruit/join/              {id}         request or join (the alliance's own rule)
GET  recruit/inbox/                          letters + «دعوت نگیرم»
POST recruit/inbox/read/        {id?}        one letter, or all
POST recruit/inbox/block/       {on}
POST recruit/invite/accept/     {id}         join now (no approval, no min power)
POST recruit/invite/decline/    {id}
GET  recruit/manage/                         leaders: ad, «دنبال عضو», link, invites, requests
POST recruit/manage/note/ {note} · recruiting/ {on} · link/ · invite/ {query} · cancel/ {id}
     approve/ {id} · reject/ {id}
"""

from __future__ import annotations

import html

from django.utils import timezone

from bio_lab.models import AllianceBonusLog, Mail, User
from game import alliance as A, alliance_recruit as R, outbox
from game.creature import GameError
from telgame_site.mini.core import clean, endpoint, need_int, need_str

ACTIONS = {"ally_invite": "invite", "ally_request": "request"}


def _txt(s) -> str:
    return html.unescape(clean(s or "")).strip()


def _q(request, key: str, choices: tuple, default: str = "") -> str:
    v = request.GET.get(key, default)
    return v if v in choices else default


def _cards(cards: list[dict]) -> list[dict]:
    return [dict(c, name=_txt(c["name"]), note=_txt(c["note"])) for c in cards]


def hub_badges(user) -> dict:
    """The «نامه‌ها» tile badge (merged into /app/api/profile/me/ by the core)."""
    return {"inbox": R.unread_count(user)}


@endpoint()
def browse(request, user):
    page = request.GET.get("page", "0")
    d = R.browse(user, _q(request, "tab", ("suggested", "new", "top"), "suggested"),
                 join=_q(request, "join", ("auto", "approval")), size=_q(request, "size", ("small", "medium", "large")),
                 reach=request.GET.get("reach", "1") != "0", recruiting=request.GET.get("rec") == "1",
                 page=int(page) if page.isdigit() else 0)
    d["cards"] = _cards(d["cards"])
    d["in_alliance"] = user.alliance_id is not None
    d["unread"] = R.unread_count(user)
    return d


@endpoint()
def search(request, user):
    return {"cards": _cards(R.search(user, request.GET.get("q", "")[:40]))}


def _bonus_since(user, alliance_id: int, since) -> int:
    b = AllianceBonusLog.objects.filter(user=user, alliance_id=alliance_id, created_at__gte=since).first()
    return b.coins if b else 0


@endpoint("POST")
def join(request, user, data):
    since = timezone.now()
    res = A.request_or_join_by_id(user, need_int(data, "id", 1))
    al = res["alliance"]
    if not res["joined"] and not res.get("already"):
        who = user.lab_name or user.first_name or str(user.id)
        for mid in res.get("notify_ids") or []:   # the bot path DMs the leaders itself; this one doesn't
            outbox.push("dm", {"user_id": mid, "title": f"درخواست عضویت از {who}",
                               "body": f"«{who}» (قدرت {res.get('requester_power', 0):,}) می‌خواد به «{al.name}» بپیونده.",
                               "buttons": [[["تأیید", f"ally_approve:{_req_id(user, al)}"], ["رد", f"ally_reject:{_req_id(user, al)}"]]]})
    return {"joined": res["joined"], "already": bool(res.get("already")), "name": _txt(al.name),
            "bonus": _bonus_since(user, al.id, since) if res["joined"] else 0}


def _req_id(user, alliance) -> int:
    from bio_lab.models import AllianceJoinRequest

    r = AllianceJoinRequest.objects.filter(user=user, alliance=alliance).first()
    return r.id if r else 0


# ── «صندوق نامه» ──────────────────────────────────────────────────────────────
def _mail(m: Mail) -> dict:
    return {"id": m.id, "kind": m.kind, "title": _txt(m.title), "body": _txt(m.body),
            "at": int(m.created_at.timestamp()), "read": m.read_at is not None, "done": m.done,
            "action": ACTIONS.get(m.kind) if not m.done else None,
            "ref": m.data.get("invite") or m.data.get("request")}


@endpoint()
def inbox(request, user):
    mails = R.inbox(user, 40)
    return {"mails": [_mail(m) for m in mails], "blocked": user.block_alliance_invites,
            "unread": sum(1 for m in mails if m.read_at is None)}


@endpoint("POST")
def inbox_read(request, user, data):
    R.mark_read(user, int(data["id"]) if isinstance(data.get("id"), int) else None)
    return {"unread": R.unread_count(user)}


@endpoint("POST")
def inbox_block(request, user, data):
    return {"blocked": R.set_invite_block(user, bool(data.get("on")))}


@endpoint("POST")
def invite_accept(request, user, data):
    since = timezone.now()
    al = R.accept_invite(user, need_int(data, "id", 1))
    return {"name": _txt(al.name), "bonus": _bonus_since(user, al.id, since)}


@endpoint("POST")
def invite_decline(request, user, data):
    R.decline_invite(user, need_int(data, "id", 1))
    return {"ok": True}


# ── leaders: «جذب و دعوت» ────────────────────────────────────────────────────
def _manage(user) -> dict:
    from config import BOT_USERNAME

    v = R.manage_view(user)
    return {
        "name": _txt(v["alliance"].name), "note": _txt(v["note"]), "recruiting": v["recruiting"],
        "link": f"https://t.me/{BOT_USERNAME}?start=ally_{v['code']}" if BOT_USERNAME else "",
        "members": v["members"], "capacity": v["capacity"], "sent_today": v["sent_today"], "daily": R.INVITE_DAILY,
        "note_max": R.NOTE_MAX,
        "invites": [{"id": i["id"], "name": _txt(i["user"].lab_name or i["user"].first_name or i["user"].id),
                     "until": int(i["expires_at"].timestamp())} for i in v["invites"]],
        "requests": [{"id": r["id"], "name": _txt(r["user"].lab_name or r["user"].first_name or r["user"].id),
                      "power": r["power"]} for r in v["requests"]],
    }


@endpoint()
def manage(request, user):
    return _manage(user)


@endpoint("POST")
def manage_note(request, user, data):
    note = data.get("note")
    if not isinstance(note, str):
        raise GameError("درخواست ناقصه.")
    R.set_recruit(user, note=note)
    return _manage(user)


@endpoint("POST")
def manage_recruiting(request, user, data):
    R.set_recruit(user, recruiting=bool(data.get("on")))
    return _manage(user)


@endpoint("POST")
def manage_link(request, user, data):
    R.invite_code(R._managed(user), renew=True)
    return _manage(user)


@endpoint("POST")
def manage_invite(request, user, data):
    inv = R.invite(user, need_str(data, "query", max_len=64))
    out = _manage(user)
    out["invited"] = _txt(inv.user.lab_name or inv.user.first_name or inv.user.id)
    return out


@endpoint("POST")
def manage_cancel(request, user, data):
    R.cancel_invite(user, need_int(data, "id", 1))
    return _manage(user)


@endpoint("POST")
def manage_approve(request, user, data):
    res = A.approve_request(user, need_int(data, "id", 1))
    outbox.push("dm", {"user_id": res["applicant"].id, "title": f"درخواستت به «{res['alliance'].name}» قبول شد",
                       "body": f"حالا عضو اتحاد «{res['alliance'].name}» هستی.", "buttons": []})
    return _manage(user)


@endpoint("POST")
def manage_reject(request, user, data):
    res = A.reject_request(user, need_int(data, "id", 1))
    outbox.push("dm", {"user_id": res["applicant"].id, "title": f"درخواستت به «{res['alliance_name']}» رد شد",
                       "body": "می‌تونی به یه اتحاد دیگه درخواست بدی.", "buttons": []})
    return _manage(user)


routes = [
    ("browse/", browse),
    ("search/", search),
    ("join/", join),
    ("inbox/", inbox),
    ("inbox/read/", inbox_read),
    ("inbox/block/", inbox_block),
    ("invite/accept/", invite_accept),
    ("invite/decline/", invite_decline),
    ("manage/", manage),
    ("manage/note/", manage_note),
    ("manage/recruiting/", manage_recruiting),
    ("manage/link/", manage_link),
    ("manage/invite/", manage_invite),
    ("manage/cancel/", manage_cancel),
    ("manage/approve/", manage_approve),
    ("manage/reject/", manage_reject),
]

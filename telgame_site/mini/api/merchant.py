"""Mini App: «تابلوی سفارش تاجر» (game/merchant.py) — the bot's «mo:» screen, same game functions."""

from __future__ import annotations

from pathlib import Path

from game import merchant as M
from telgame_site.mini.core import clean, endpoint, need_int, need_str

_IMG = Path(__file__).resolve().parent.parent / "static" / "img"


def _img(name: str) -> str | None:
    """A portrait the app has, or None (the card then shows an icon)."""
    return f"img/{name}.jpg" if (_IMG / f"{name}.jpg").exists() else None


def _order(o: dict | None) -> dict | None:
    if not o:
        return o
    return {**o, "img": _img(f"merchant_{o['who']}"), "extra_label": clean(o["extra_label"]),
            "reqs": [{**r, "label": clean(r["label"])} for r in o["reqs"]]}


def _state(user) -> dict:
    st = M.state(user)
    st["banner"] = _img("hub_merchant")
    if st.get("unlocked"):
        st["slots"] = [_order(s) if "reqs" in s else s for s in st["slots"]]
        st["special"] = _order(st["special"])
    return st


def _slot(data):
    s = need_str(data, "slot", choices=["0", "1", "2", "s"])
    return s if s == "s" else int(s)


@endpoint()
def board(request, user):
    return _state(user)


@endpoint()
def preview(request, user):
    q = request.GET
    return {"creatures": [{"id": cid, "name": clean(n)} for cid, n in M.preview_handover(user, _slot(q), need_int(q, "id", 1))]}


@endpoint("POST")
def deliver(request, user, data):
    cid = data.get("creature")
    got = M.deliver(user, _slot(data), need_int(data, "id", 1), [int(cid)] if str(cid or "").isdigit() else None)
    return {"name": got["name"], "coins": got["coins"], "xp": got["xp"], "diamonds": got["diamonds"],
            "extra": clean(got["extra"] or ""), "levelup": got.get("levelup"),
            "handed": [clean(n) for n in got["handed"]], "state": _state(user)}


@endpoint("POST")
def reject(request, user, data):
    M.reject(user, _slot(data), need_int(data, "id", 1))
    return {"state": _state(user)}


routes = [
    ("", board),
    ("preview/", preview),
    ("deliver/", deliver),
    ("reject/", reject),
]

"""Mini App: «آزمایشگاه سرم» and «انبار» (game/serums.py, game/arena_chests.py) — the bot's
menu:serums / menu:backpack screens, same game functions."""

from __future__ import annotations

from game import serums as SR
from telgame_site.mini.core import clean, endpoint, need_int, need_str


def _state(user) -> dict:
    st = SR.state(user)
    for k in st["kinds"]:
        k["img"] = f"img/serum_{k['key']}.jpg"
        k["effect"], k["next_effect"], k["where"] = clean(k["effect"]), clean(k["next_effect"]), clean(k["where"])
        k["all"] = [clean(x) for x in k["all"]]
    return st


@endpoint()
def home(request, user):
    return _state(user)


@endpoint("POST")
def brew(request, user, data):
    count = need_int(data, "count", 1, 8) if data.get("count") not in (None, "") else 1
    r = SR.brew(user, need_str(data, "kind", choices=SR.ORDER), count)
    return {"queued": r["queued"], "cost": r["cost"], "state": _state(user)}


@endpoint("POST")
def cancel(request, user, data):
    r = SR.cancel_last(user)
    return {"refund": r["refund"], "state": _state(user)}


@endpoint("POST")
def use(request, user, data):
    member = data.get("member")
    r = SR.use(user, need_str(data, "kind", choices=SR.ORDER), int(member) if str(member or "").isdigit() else None)
    return {"label": clean(r["label"]), "note": clean(r["note"]), "state": _state(user)}


@endpoint("POST")
def notify(request, user, data):
    SR.set_notify(user, bool(data.get("on")))
    return {"state": _state(user)}


@endpoint("POST")
def upgrade(request, user, data):
    r = SR.upgrade(user, need_str(data, "kind", choices=SR.ORDER))
    return {"to": r["to"], "hours": r["hours"], "state": _state(user)}


# ── «انبار» ───────────────────────────────────────────────────────────────────
def _backpack(user) -> dict:
    from bio_lab.models import SerumLab
    from game import reward_chests
    from game.buildings import list_speedup_cards

    boss = reward_chests.unopened(user, "worldboss").count()
    cup = reward_chests.unopened(user, "tournament").count()
    war = reward_chests.unopened(user, "alliance_war").count()
    offer = reward_chests.unopened(user, "offer").count()
    row = SerumLab.objects.filter(user_id=user.id).first()
    stock = {k: int(v) for k, v in ((row.stock if row else {}) or {}).items() if int(v) > 0}
    return {
        "boss_chests": boss, "cup_chests": cup, "war_chests": war, "offer_chests": offer,
        "speedups": [{"minutes": c.minutes, "count": c.count} for c in list_speedup_cards(user)],
        "serums": [{"key": k, "label": SR.KINDS[k]["label"], "count": stock[k], "img": f"img/serum_{k}.jpg"} for k in SR.ORDER if k in stock],
    }


@endpoint()
def backpack(request, user):
    return _backpack(user)


@endpoint("POST")
def backpack_open(request, user, data):
    """«باز کردن جعبه‌ها» for boxes with no screen of their own — the boxes in a bought pack
    (bot: serums.open_chests, «rc:open:offer»)."""
    import html

    from game import reward_chests, worldboss
    from telgame_site.mini.core import creature_dict

    source = need_str(data, "source", ("offer",))
    results = reward_chests.open_all(user, source)
    return {"count": len(results), "unopened": reward_chests.unopened(user, source).count(),
            "coins": sum(r["coins"] for r in results), "dna": sum(r["dna"] for r in results),
            "diamonds": sum(r["diamonds"] for r in results),
            "lines": [html.unescape(clean(worldboss._chest_line(r))) for r in results],
            "creatures": [creature_dict(r["creature"]) for r in results if r.get("creature") is not None]}


routes = [
    ("", home),
    ("brew/", brew),
    ("cancel/", cancel),
    ("use/", use),
    ("upgrade/", upgrade),
    ("notify/", notify),
    ("backpack/", backpack),
    ("backpack/open/", backpack_open),
]

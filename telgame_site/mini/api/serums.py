"""Mini App: «آزمایشگاه سرم» and «انبار» (game/serums.py, game/arena_chests.py) — the bot's
menu:serums / menu:backpack screens, same game functions."""

from __future__ import annotations

from game import arena_chests as AC, serums as SR
from telgame_site.mini.core import asset_img, clean, endpoint, need_int, need_str


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
    from game.media import get_arena_chest_image_path

    chests = [{"id": c.id, "tier": c.chest_type, "name": AC.ARENA_CHEST_TIERS.get(c.chest_type, {}).get("name", "جعبه آرنا"),
               "img": asset_img(get_arena_chest_image_path(c.chest_type, "locked"))} for c in AC.stored_chests(user)]
    boss = reward_chests.unopened(user, "worldboss").count()
    cup = reward_chests.unopened(user, "tournament").count()
    row = SerumLab.objects.filter(user_id=user.id).first()
    stock = {k: int(v) for k, v in ((row.stock if row else {}) or {}).items() if int(v) > 0}
    return {
        "chests": chests, "free_slot": AC.get_free_slot(user) is not None,
        "boss_chests": boss, "cup_chests": cup,
        "speedups": [{"minutes": c.minutes, "count": c.count} for c in list_speedup_cards(user)],
        "serums": [{"key": k, "label": SR.KINDS[k]["label"], "count": stock[k], "img": f"img/serum_{k}.jpg"} for k in SR.ORDER if k in stock],
    }


@endpoint()
def backpack(request, user):
    return _backpack(user)


@endpoint("POST")
def backpack_move(request, user, data):
    c = AC.move_to_slot(user, need_int(data, "id", 1))
    return {"slot": c.slot, "backpack": _backpack(user)}


routes = [
    ("", home),
    ("brew/", brew),
    ("cancel/", cancel),
    ("use/", use),
    ("upgrade/", upgrade),
    ("notify/", notify),
    ("backpack/", backpack),
    ("backpack/move/", backpack_move),
]

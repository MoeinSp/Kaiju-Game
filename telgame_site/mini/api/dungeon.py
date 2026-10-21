"""«سیاه‌چال» (game/dungeon.py) for the Mini App: home, the run, the black-rune shop and the
relic album — the same calls, in the same order, as bot/handlers/dungeon.py.

The run lives in the database (DungeonRun.state), so nothing is carried between requests: every
answer is the full current state. Each action locks the player and the run (game.dungeon), so a
double tap can't act twice; an action that doesn't fit the run's phase any more is a GameError.
Labels are plain text — no emoji in the app.
"""

from __future__ import annotations

import html
import re

from bio_lab.models import Creature, User
from game import constants, dungeon as D, relics
from telgame_site.mini.core import clean, creature_img, endpoint, need_int, need_str, species_img


_EMOJI = re.compile("[\U0001F000-\U0001FFFF☀-➿⬀-⯿️‍⃣]+")


def _txt(value) -> str:
    """Plain text for the app: unescaped, and without the emoji the bot's lines carry."""
    return _EMOJI.sub("", html.unescape(clean(value))).strip()


def _rune(key: str) -> dict:
    label, desc = D.rune_label(key, plain=True)
    return {"key": key, "label": label, "desc": desc}


def _enemy_img(enemy: dict) -> str | None:
    species = next((n for n, el in constants.SPECIES.items() if el == enemy["element"]), None)
    rarity = "mythic" if enemy["kind"] == "boss" else "legendary" if enemy["kind"] == "elite" else "epic"
    return species_img(species, enemy["element"], rarity, 5 if enemy["kind"] == "boss" else 3) if species else None


def _relic(r: dict) -> dict:
    return {"key": r["key"], "label": r["label"], "desc": _txt(r["desc"]),
            "extended": r.get("extended", False)}


def _state(user: User) -> dict:
    v = D.view(user)
    out = {
        "unlocked": v["unlocked"], "stage": v["stage"], "unlock_stage": v["unlock_stage"],
        "keys": v["keys"], "black_runes": v["black_runes"], "runs": v["runs"], "clears": v["clears"],
        "relics": len(relics.active(user)) if v["unlocked"] else 0,
        "daily_keys": D.DAILY_KEYS, "rooms": D.ROOMS, "run": None,
    }
    run = v["run"]
    if run is None:
        return out
    st = run["state"]
    imgs = {c.id: creature_img(c) for c in Creature.objects.filter(id__in=[m["id"] for m in run["members"]])}
    enemy = st.get("enemy")
    out["run"] = {
        "phase": st["phase"], "room": st["room"], "boss": st["room"] > D.ROOMS,
        "members": [{
            "id": m["id"], "name": _txt(m["name"]), "element": m["element"], "rarity": m["rarity"], "star": m["star"],
            "hp": m["hp"], "power": m["now_power"], "img": imgs.get(m["id"]),
            "vs": m.get("vs"), "enemy_vs": m.get("enemy_vs"), "wins": m.get("wins"),
        } for m in run["members"]],
        "doors": [{"type": d} for d in st["doors"]] if st["phase"] == "doors" else [],
        "enemy": dict(enemy, name=_txt(enemy["name"]), img=_enemy_img(enemy)) if enemy else None,
        "runes": [_rune(r) for r in st["runes"]],
        "rune_offer": [_rune(r) for r in st["rune_offer"]] if st["phase"] == "rune" else [],
        "merchant": [dict(_rune(o["rune"]), price=int(o["price"])) for o in st["merchant"]] if st["phase"] == "merchant" else [],
        "first_strike": bool(st.get("first_strike")),
        "loot": st["loot"],
        "log": [_txt(x) for x in (st.get("log") or [])[-3:]],
    }
    return out


def _finish(fin: dict | None) -> dict | None:
    if not fin:
        return None
    return {"status": fin["status"], "loot": fin["loot"], "room": fin["room"], "share": fin["share"],
            "relic": _relic(fin["relic"]) if fin.get("relic") else None}


@endpoint()
def home(request, user):
    return _state(user)


@endpoint("POST")
def start(request, user, data):
    D.start(user)
    return _state(User.objects.get(id=user.id))


@endpoint("POST")
def door(request, user, data):
    ev = D.choose_door(user, need_int(data, "i"))
    out = _state(User.objects.get(id=user.id))
    out["event"] = {"door": ev["door"], "got": ev.get("got")}
    return out


@endpoint("POST")
def fight(request, user, data):
    res = D.fight(user, need_int(data, "creature_id", 1))
    out = _state(User.objects.get(id=user.id))
    out["fight"] = {"won": res["won"], "mine": res["mine"], "theirs": res["theirs"], "member": _txt(res["member"]),
                    "hp_lost": res.get("hp_lost", 0), "got": res.get("got")}
    out["finish"] = _finish(res.get("finish"))
    return out


@endpoint("POST")
def rune(request, user, data):
    res = D.pick_rune(user, need_int(data, "i"))
    out = _state(User.objects.get(id=user.id))
    out["picked"] = _rune(res["rune"])
    return out


@endpoint("POST")
def merchant(request, user, data):
    i = data.get("i")
    D.merchant_buy(user, None if i is None else need_int(data, "i"))
    return _state(User.objects.get(id=user.id))


@endpoint("POST")
def quit_run(request, user, data):
    res = D.quit_run(user)
    out = _state(User.objects.get(id=user.id))
    out["finish"] = _finish(res["finish"])
    return out


def _shop_state(user: User) -> dict:
    prof = D.profile(user)
    return {"black_runes": prof.black_runes, "keys": prof.keys,
            "items": [{"key": k, "label": v["label"], "price": v["price"]} for k, v in D.SHOP.items()]}


@endpoint()
def shop(request, user):
    return _shop_state(user)


@endpoint("POST")
def shop_buy(request, user, data):
    got = D.shop_buy(user, need_str(data, "key", max_len=24))
    res = {"label": got["label"]}
    if got.get("relic"):
        res["relic"] = _relic(got["relic"])
    if got.get("item") is not None:
        res["item"] = {"name": _txt(got["item"].name), "rarity": got["item"].rarity}
    if got.get("chest"):
        res["chest"] = {"coins": got["chest"].get("coins", 0), "dna": got["chest"].get("dna", 0),
                        "diamonds": got["chest"].get("diamonds", 0)}
    out = _shop_state(User.objects.get(id=user.id))
    out["bought"] = res
    return out


@endpoint()
def relic_album(request, user):
    return {"days": relics.RELIC_DAYS, "max_days": relics.RELIC_MAX_DAYS,
            "relics": [dict(r, desc=_txt(r["desc"])) for r in relics.album(user)]}


routes = [
    ("", home),
    ("start/", start),
    ("door/", door),
    ("fight/", fight),
    ("rune/", rune),
    ("merchant/", merchant),
    ("quit/", quit_run),
    ("shop/", shop),
    ("shop/buy/", shop_buy),
    ("relics/", relic_album),
]

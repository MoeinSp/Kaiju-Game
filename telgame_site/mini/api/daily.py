"""The daily loop: «پاداش امروز» (game/today.py), the missions and their weekly box track
(game/daily.py), the story quest «قدم بعدی» (game/story.py) and the daily wheel
(game/wheel.py). Thin adapters over the functions the bot's handlers call
(bot/handlers/today.py, wheel.py and the missions/story parts of private.py)."""

import datetime

from django.utils import timezone

from bio_lab.models import Creature, User
from game import constants
from telgame_site.mini.core import GameError, clean, creature_list, endpoint, equipment_img, need_int

_RESOURCE_KEY = {"coins": "coins", "dna_fragments": "dna", "diamonds": "diamonds"}
# game.today.collect_all names its result blocks; the app draws an icon per block
_SECTION_KIND = {
    "ساختمان‌ها": "building", "مأموریت‌های اعزامی": "dispatch", "جایزه‌ها": "gift",
    "گردونه": "wheel", "باکس‌ها و جعبه‌ها": "box",
}


# ── serialisers (also used by api/dispatch.py) ────────────────────────────────
def capsule_dict(tier: str, count) -> dict:
    return {"tier": tier, "count": int(count or 1), "label": constants.XP_CAPSULES[tier]["label"]}


def speedup_dict(minutes) -> dict:
    return {"minutes": int(minutes), "label": constants.speedup_plain_label(int(minutes))}


def mission_dict(m: dict) -> dict:
    """A mission row of game.daily.mission_status / a mission game.daily.check_missions
    just completed (same keys; the second has no progress)."""
    out = {
        "key": m["key"], "label": clean(m["label"]), "target": m["target"],
        "progress": m.get("progress"), "done": bool(m.get("done", True)), "weekly": bool(m.get("weekly")),
        "points": m.get("points", 0), "coins": m.get("coins", 0), "dna": m.get("dna", 0),
        "diamonds": m.get("diamonds", 0),
    }
    if m.get("capsule"):
        out["capsule"] = capsule_dict(*m["capsule"])
    if m.get("speedup"):
        out["speedup"] = speedup_dict(m["speedup"])
    return out


def missions_list(done) -> list[dict]:
    return [mission_dict(m) for m in (done or [])]


def item_dict(item) -> dict:
    from game.equipment import equipment_power

    return {"id": item.id, "name": item.name, "slot": item.slot, "rarity": item.rarity, "level": item.level,
            "power": equipment_power(item), "img": equipment_img(item)}


def loot_dict(user, contents: dict) -> dict:
    """A chest / box result (game.arena_chests.grant_chest_contents and friends)."""
    creature, item = contents.get("creature"), contents.get("item")
    return {
        "name": clean(contents.get("name", "")), "tier": contents.get("tier"),
        "coins": int(contents.get("coins", 0) or 0), "dna": int(contents.get("dna", 0) or 0),
        "diamonds": int(contents.get("diamonds", 0) or 0), "rarity": contents.get("rarity"),
        "creatures": creature_list(user, [creature]) if creature is not None else [],
        "items": [item_dict(item)] if item is not None else [],
    }


def quest_dict(quest: dict | None) -> dict | None:
    if not quest:
        return None
    reward = quest["reward"]
    cb = quest["cta_callback"]
    return {
        "id": quest["id"], "chapter": quest["chapter"], "chapter_name": clean(quest["chapter_name"]),
        "title": clean(quest["title"]), "desc": clean(quest["desc"]),
        "cur": min(quest["cur"], quest["target"]), "target": quest["target"], "done": bool(quest["is_done"]),
        "step": quest["step_num"], "total": quest["total_steps"],
        "reward": _story_reward(reward),
        "cta": cb[5:] if cb.startswith("menu:") else None, "cta_label": clean(quest["cta_label"]),
    }


def _story_reward(reward: dict) -> dict:
    out = {"coins": reward.get("coins", 0), "dna": reward.get("dna", 0), "diamonds": reward.get("diamonds", 0),
           "tickets": reward.get("biocrate_tickets", 0)}
    if reward.get("speedup"):
        out["speedup"] = speedup_dict(reward["speedup"])
    return out


def _seconds_until_day_reset() -> int:
    """Daily counters reset at midnight of the game's timezone (game.daily.today_str)."""
    now = timezone.localtime()
    nxt = (now + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(0, int((nxt - now).total_seconds()))


def _live(st: dict) -> dict:
    return {
        "boss": {"name": clean(st["boss"]["name"]), "hits_left": st["boss"]["hits_left"]} if st["boss"] else None,
        "tournament_open": bool(st["tournament_open"]),
        "festival": {"title": clean(st["festival"]["title"]), "element": st["festival"].get("element")}
        if st["festival"] else None,
        "rule": clean(st["rule"]),
    }


# ── home summary (cheap: opened on every visit of the home screen) ────────────
@endpoint()
def home(request, user):
    from game import story, today

    st = today.state(user, full=False)
    out = _live(st)
    out.update({
        "hall": st["hall"], "waiting": today.waiting_count(st), "collectable": today.collectable_count(st),
        "wheel": bool(st["wheel"]), "dispatch_ready": st["dispatch_ready"], "boxes_ready": st["boxes_ready"],
        "free_boxes": len(st["free_boxes"]),   # the badge of the «باکس‌ها» tile (no request of its own)
        "quest": quest_dict(story.get_active_quest(user)),
    })
    return out


# ── «پاداش امروز» ─────────────────────────────────────────────────────────────
def _today_payload(user) -> dict:
    """The «پاداش امروز» screen: game.today.state, row by row."""
    from game import events, story, today

    st = today.state(user)
    ready = [{"kind": "building", "label": clean(label), "amount": amount, "resource": _RESOURCE_KEY.get(resource, resource)}
             for label, amount, resource in st["pending"]]
    if st["dispatch_ready"]:
        ready.append({"kind": "dispatch", "count": st["dispatch_ready"]})
    if st["event_daily"]:
        reward = events.status(user)["today_reward"] or {}
        ready.append({"kind": "event", "reward": {
            "coins": reward.get("coins", 0), "dna": reward.get("dna", 0), "diamonds": reward.get("diamonds", 0),
            **({"speedup": speedup_dict(reward["speedup"])} if reward.get("speedup") else {}),
        }})
    if st["story_ready"]:
        quest = story.get_active_quest(user)
        ready.append({"kind": "story", "label": clean(quest["title"]) if quest else "",
                      "reward": _story_reward(quest["reward"]) if quest else {}})
    if st["wheel"]:
        ready.append({"kind": "wheel"})
    if st["free_boxes"]:
        ready.append({"kind": "free_box", "count": len(st["free_boxes"]), "tiers": st["free_boxes"]})
    if st["chests_ready"]:
        ready.append({"kind": "chest", "count": st["chests_ready"]})
    if st["boxes_ready"]:
        ready.append({"kind": "mission_box", "count": st["boxes_ready"]})

    next_box = st["next_box"]
    out = _live(st)
    out.update({
        "hall": st["hall"], "energy": st["energy"], "max_energy": st["max_energy"],
        "ready": ready, "collectable": today.collectable_count(st), "waiting": today.waiting_count(st),
        "missions_done": st["missions_done"], "missions_total": st["missions_total"], "points": st["points"],
        "next_box": {"index": next_box["index"], "need": next_box["need"], "tier": next_box["tier"],
                     "left": max(0, next_box["need"] - st["points"])} if next_box else None,
        "can_dispatch": bool(st["can_dispatch"]),
    })
    return out


@endpoint()
def today_state(request, user):
    return _today_payload(user)


def _collect_line(line: str) -> dict:
    """One line of a game.today.collect_all section. Indented lines belong to the line
    above them (what a dispatch mission paid, its «شگفتی», a level-up)."""
    sub = line.startswith(" ")
    text = clean(line.replace("┃", "·"))
    return {"text": text, "sub": sub,
            "tone": ("bonus" if text.startswith("شگفتی") else "level" if "به سطح" in text else "") if sub else ""}


@endpoint("POST")
def today_collect(request, user, data):
    """«دریافت همه»: game.today.collect_all — buildings, returned dispatch missions, the
    event's daily reward, a finished story quest, the wheel, free boxes, ready arena chests
    and reached mission boxes. Everything it returns is sent back."""
    from game import today

    res = today.collect_all(user)
    totals = res["totals"]
    creatures = [r["creature"] for r in res["rolls"] if r.get("kind") == "creature" and r.get("creature") is not None]
    items = [r["item"] for r in res["rolls"] if r.get("kind") == "equipment" and r.get("item") is not None]
    sections = []
    for title, lines in res["sections"]:
        rows = [_collect_line(line) for line in lines]
        sections.append({"title": clean(title), "kind": _SECTION_KIND.get(title, "gift"),
                         "lines": [r for r in rows if r["text"]]})
    return {
        "empty": not res["sections"],
        "totals": {"coins": int(totals.get("coins", 0)), "dna": int(totals.get("dna_fragments", 0)),
                   "diamonds": int(totals.get("diamonds", 0))},
        "sections": sections,
        "creatures": creature_list(user, creatures) if creatures else [],
        "items": [item_dict(i) for i in items],
        "missions": missions_list(res["missions"]),
        # the screen as it is NOW (balances, energy and the story step changed under `user`)
        "today": _today_payload(User.objects.get(pk=user.pk)),
    }


# ── missions ──────────────────────────────────────────────────────────────────
@endpoint()
def missions(request, user):
    from game.arena_chests import ARENA_CHEST_TIERS
    from game.daily import mission_status

    st = mission_status(user)
    boxes = [{
        "index": b["index"], "need": b["need"], "tier": b["tier"],
        "name": clean(ARENA_CHEST_TIERS.get(b["tier"], {}).get("name", "")),
        "reached": bool(b["reached"]), "opened": bool(b["opened"]),
    } for b in st["boxes"]]
    return {
        "daily": [mission_dict(m) for m in st["daily"]],
        "weekly": [{**mission_dict(m), "weekly": True} for m in st["weekly"]],
        "points": st["points"], "max_points": st["max_points"], "boxes": boxes,
        "reset_in": st["reset_in"], "day_reset_in": _seconds_until_day_reset(),
        "today_points": st["today_points"], "today_max": st["today_max"],
    }


@endpoint("POST")
def mission_box(request, user, data):
    from game.daily import claim_box

    index = need_int(data, "index", minimum=1)
    contents = claim_box(user, index)
    out = loot_dict(user, contents)
    out["index"] = index
    return out


# ── story quest ───────────────────────────────────────────────────────────────
@endpoint()
def story_state(request, user):
    from game import story

    return {"quest": quest_dict(story.get_active_quest(user))}


@endpoint("POST")
def story_claim(request, user, data):
    from game import story

    res = story.claim_active_quest(user)
    if not res.get("success"):
        raise GameError(res.get("msg") or "هنوز شرایط این مأموریت کامل نشده.")
    claimed = res["claimed_quest"]
    return {"title": clean(claimed["title"]), "reward": _story_reward(claimed["reward"]),
            "next": quest_dict(res.get("next_quest"))}


# ── wheel ─────────────────────────────────────────────────────────────────────
def _wheel_available(user) -> bool:
    from bio_lab.models import DailyActionLog
    from game.daily import today_str

    return not DailyActionLog.objects.filter(
        user=user, action="wheel_spin", day=today_str(), count__gte=constants.WHEEL_DAILY_LIMIT
    ).exists()


@endpoint()
def wheel_state(request, user):
    return {"available": _wheel_available(user), "reset_in": _seconds_until_day_reset(),
            "limit": constants.WHEEL_DAILY_LIMIT}


@endpoint("POST")
def wheel_spin(request, user, data):
    from game import wheel

    prize = wheel.spin(user)
    kind = prize.get("kind")
    out = {"kind": kind, "label": clean(prize.get("label", "")), "coins": 0, "dna": 0, "diamonds": 0,
           "creatures": [], "missions": missions_list(prize.get("missions"))}
    if kind in ("coins", "dna", "diamonds"):
        out[kind] = int(prize.get("amount", 0))
    elif kind == "xp_capsule":
        out["capsule"] = capsule_dict(prize["tier"], prize.get("count", 1))
    elif kind == "speedup":
        out["speedup"] = speedup_dict(prize["amount"])
    elif kind == "creature":
        creature = Creature.objects.filter(id=prize.get("creature_id"), owner=user).first()
        out["rarity"] = prize.get("rarity")
        if creature is not None:
            out["creatures"] = creature_list(user, [creature])
    elif kind == "jackpot":
        out.update({"coins": int(prize["coins"]), "dna": int(prize["dna"]), "diamonds": int(prize["diamonds"]),
                    "speedup": speedup_dict(prize["speedup"]),
                    "capsule": capsule_dict(prize["capsule_tier"], prize["capsule_count"])})
    out["reset_in"] = _seconds_until_day_reset()
    return out


routes = [
    ("home/", home),
    ("today/", today_state),
    ("today/collect/", today_collect),
    ("missions/", missions),
    ("missions/box/", mission_box),
    ("story/", story_state),
    ("story/claim/", story_claim),
    ("wheel/", wheel_state),
    ("wheel/spin/", wheel_spin),
]

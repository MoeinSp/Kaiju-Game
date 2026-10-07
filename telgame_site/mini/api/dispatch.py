"""«مأموریت اعزامی» — game/dispatch.py behind the same steps as bot/handlers/dispatch.py:
board → offer (candidates with their exact previewed reward) → send → collect / cancel."""

from game import dispatch
from telgame_site.mini.api.daily import capsule_dict, missions_list, speedup_dict
from telgame_site.mini.core import GameError, clean, creature_img, creature_list, endpoint, need_int


def _hall_req() -> int:
    """SECTION_HALL_REQ["dispatch"] — the same main-hall gate as the bot's menu button
    (bot.gates.hall_gated). game.today hard-codes the same level for its dispatch parts."""
    try:
        from bot.handlers.private import SECTION_HALL_REQ
    except Exception:  # noqa: BLE001 — the bot package may be unavailable in a bare web process
        return 2
    return SECTION_HALL_REQ.get("dispatch", 2)


def _gate(user) -> None:
    from game.buildings import main_hall_level

    req, level = _hall_req(), main_hall_level(user)
    if level < req:
        raise GameError(f"این بخش از سطح {req} «تالار مِهر» باز می‌شه (الان سطح {level}). اول تالار مِهرت رو ارتقا بده.")


def _guaranteed(reward: dict) -> dict:
    """Only the part of a reward the player may see before collecting — the «شگفتی» stays
    hidden until then (dispatch.reward_text(..., with_bonus=False) in the bot)."""
    return {"coins": int(reward.get("coins", 0)), "dna": int(reward.get("dna", 0)), "xp": int(reward.get("xp", 0))}


def _bonus(reward: dict) -> dict | None:
    if not dispatch.has_bonus(reward):
        return None
    out = {}
    if reward.get("diamonds"):
        out["diamonds"] = int(reward["diamonds"])
    if reward.get("capsule"):
        out["capsule"] = capsule_dict(reward["capsule"], reward.get("capsule_count", 1))
    if reward.get("speedup"):
        out["speedup"] = speedup_dict(reward["speedup"])
    return out


def _offer_dict(o: dict) -> dict:
    return {
        "idx": o["idx"], "key": o["key"], "title": clean(o["title"]), "flavor": clean(o["flavor"]),
        "focus": o["focus"], "hours": o["hours"], "seconds": dispatch.real_seconds(o["hours"]),
        "element": o["element"], "min_rarity": o["min_rarity"], "taken": bool(o["taken"]),
        "special": bool(o["special"]),
        "chance": int(dispatch.bonus_chance(o["hours"], o["hq_level"]) * 100),
    }


def _board_meta(level: int) -> dict:
    return {
        "element_bonus": int(dispatch.ELEMENT_MATCH_BONUS * 100),
        "special_mult": dispatch.SPECIAL_REWARD_MULT,
        "hq_bonus": int(dispatch.HQ_REWARD_BONUS[level] * 100),
    }


CANDIDATES_MAX = 60  # per ordering (see `offer`) — the bot pages its picker for the same reason


def _candidate(creature, preview: dict) -> dict:
    """One row of the picker: only what the row draws (the full creature card — stats, gear —
    was 350 bytes a row that nothing read) and the exact reward this creature would bring.
    preview["power"] is creature_power() with research and gear, the number every card shows."""
    from bio_lab.repository import creature_name

    return {
        "creature": {"id": creature.id, "name": creature_name(creature), "element": creature.element,
                     "rarity": creature.rarity, "star": creature.star_level, "level": creature.level,
                     "power": preview["power"], "img": creature_img(creature)},
        "coins": preview["coins"], "dna": preview["dna"], "xp": preview["xp"],
        "match": bool(preview["element_match"]),
    }


@endpoint()
def panel(request, user):
    from game import events

    _gate(user)
    level = dispatch.hq_level(user)
    running = dispatch.active_missions(user)
    by_id = {c["id"]: c for c in creature_list(user, [m.creature for m in running])}
    missions = []
    for m in running:
        _key, _emoji, title, flavor, focus = dispatch.template(m)
        missions.append({
            "id": m.id, "key": m.template_key, "title": clean(title), "flavor": clean(flavor), "focus": focus,
            "hours": m.hours, "creature": by_id.get(m.creature_id),
            "ready": dispatch.is_ready(m), "left": dispatch.seconds_left(m),
            "total": max(1, int((m.finishes_at - m.started_at).total_seconds())),
            "reward": _guaranteed(m.reward or {}),
        })
    rule = None
    if events.current_rule() in ("dispatch_fast", "dispatch_surprise"):
        rule = {"title": clean(events.rule_line()), "desc": clean(events.current_event().get("desc", ""))}
    out = _board_meta(level)
    out.update({
        "hq": level, "hq_max": len(dispatch.HQ_SLOTS) - 1, "slots": dispatch.slots(user, level),
        "slots_base": dispatch.HQ_SLOTS[level],  # the rest is the subscriber's extra slot
        "perks": [clean(line) for line in dispatch.hq_perks_text(level).split("\n")],
        "missions": missions,
        "offers": [_offer_dict(o) for o in dispatch.offers_for(user, level=level)],
        "rule": rule,
    })
    return out


@endpoint()
def offer(request, user):
    """One offer and every idle creature that may take it, each with the exact reward it
    would bring (dispatch.preview_rewards — batched)."""
    _gate(user)
    idx = need_int(request.GET, "idx", minimum=0)
    o = dispatch.get_offer(user, idx)
    if o["taken"]:
        raise GameError("این مأموریت رو امروز قبلاً فرستادی.")
    creatures = dispatch.eligible_creatures(user, o)  # idle + rare enough; rarest, then strongest
    if request.GET.get("creature_id"):
        # ONE creature picked from the full collection grid (it may be outside the capped list)
        creature_id = need_int(request.GET, "creature_id", minimum=1)
        creatures = [c for c in creatures if c.id == creature_id]
        if not creatures:
            raise GameError("این هیولا الان نمی‌تونه این مأموریت رو بره.")
    total = len(creatures)
    previews = dispatch.preview_rewards(user, o, creatures)  # research + all the gear: two queries
    if total > 2 * CANDIDATES_MAX:
        # a big collection: the best payers and the head of the bot's own order; every other
        # idle creature is still reachable through the grid (priced one at a time, see above)
        best = sorted(creatures, key=lambda c: (-previews[c.id]["coins"], -previews[c.id]["dna"], c.id))[:CANDIDATES_MAX]
        keep = {c.id for c in best} | {c.id for c in creatures[:CANDIDATES_MAX]}
        creatures = [c for c in creatures if c.id in keep]
    out = _board_meta(o["hq_level"])
    out.update({"offer": _offer_dict(o), "hq": o["hq_level"], "total": total,
                "candidates": [_candidate(c, previews[c.id]) for c in creatures]})
    return out


@endpoint("POST")
def send(request, user, data):
    from bio_lab.repository import creature_name
    from game.daily import check_missions, record_action

    _gate(user)
    idx = need_int(data, "idx", minimum=0)
    creature_id = need_int(data, "creature_id", minimum=1)
    dispatch.prune_old(user)
    mission = dispatch.start(user, idx, creature_id)
    record_action(user, "dispatch")
    done = check_missions(user, "dispatch")
    return {"id": mission.id, "name": creature_name(mission.creature), "left": dispatch.seconds_left(mission),
            "reward": _guaranteed(mission.reward or {}), "missions": missions_list(done)}


def _result(user, res: dict) -> dict:
    from bio_lab.repository import creature_name

    mission, reward, creature = res["mission"], res["reward"], res["creature"]
    _key, _emoji, title, _flavor, _focus = dispatch.template(mission)
    out = _guaranteed(reward)
    out.update({
        "id": mission.id, "key": mission.template_key, "title": clean(title),
        "name": creature_name(creature) if creature is not None else "هیولات",
        "bonus": _bonus(reward), "levels": int(res["levels"] or 0),
        "level": creature.level if creature is not None else None,
    })
    return out


@endpoint("POST")
def collect(request, user, data):
    return {"results": [_result(user, dispatch.collect(user, need_int(data, "id", minimum=1)))]}


@endpoint("POST")
def collect_all(request, user, data):
    results = dispatch.collect_all(user)
    if not results:
        raise GameError("هنوز هیچ مأموریتی تموم نشده.")
    return {"results": [_result(user, r) for r in results]}


@endpoint("POST")
def cancel(request, user, data):
    mission_id = need_int(data, "id", minimum=1)
    dispatch.cancel(user, mission_id)
    return {"ok": True}


routes = [
    ("", panel),
    ("offer/", offer),
    ("send/", send),
    ("collect/", collect),
    ("collect_all/", collect_all),
    ("cancel/", cancel),
]

"""Fusion («تالار ادغام») and the Monster Cave («غار هیولا») — thin adapters over
game.fusion / game.breeding, in the same order the bot's `*_sync` helpers call them
(bot/handlers/private.py `_fusion_*`, bot/handlers/breeding.py)."""

from bio_lab.models import BreedingJob, Creature, Egg
from game import breeding, constants
from telgame_site.mini.core import (GameError, asset_img, clean, creature_img, creature_list, endpoint,
                                    equipment_img, need_int, own_creature)


def _gate(user, action: str) -> None:
    """Main-hall unlock, exactly like bot.gates.hall_gated / menu_callback."""
    from game import story
    from game.buildings import main_hall_level

    try:
        from bot.handlers.private import SECTION_HALL_REQ
    except Exception:  # noqa: BLE001 — the bot package isn't importable here: mirror its table
        SECTION_HALL_REQ = {"fusion": 2, "breeding": 2}
    req = SECTION_HALL_REQ.get(action)
    if req is None:
        return
    level = main_hall_level(user)
    if level < req and story.active_cta_action(user) != action:
        raise GameError(f"این بخش از سطح {req} «تالار مِهر» باز می‌شه (الان سطح {level}). اول تالار مِهرت رو ارتقا بده.")


def _dicts(user, creatures) -> dict:
    """{creature id: creatureDict} for a handful of rows (one gear query)."""
    rows = list(creatures)
    return {d["id"]: d for d in creature_list(user, rows)} if rows else {}


def _mini(c: Creature) -> dict:
    """A creature as a THUMBNAIL (list rows, the pair in the cave): the creatureDict keys a
    picture needs and nothing that costs a query (no power, no gear)."""
    from bio_lab.repository import creature_name

    return {"id": c.id, "name": creature_name(c), "species": c.name, "element": c.element, "rarity": c.rarity,
            "star": c.star_level, "level": c.level, "img": creature_img(c)}


def _pct(p: float) -> float:
    return round(p * 100, 1)


def _missions(completed) -> list[dict]:
    """game.daily.check_missions rows as plain data (the parts bot.utils.mission_reward_text
    prints) — the same shape the hunt module sends."""
    out = []
    for m in completed or []:
        extras = []
        if m.get("capsule"):
            tier, count = m["capsule"]
            extras.append(f"{count} کپسول {clean(constants.XP_CAPSULES[tier]['label'])}")
        if m.get("speedup"):
            extras.append(f"کارت سرعت {clean(constants.speedup_plain_label(m['speedup']))}")
        out.append({
            "label": clean(m.get("label", "")), "weekly": bool(m.get("weekly")),
            "coins": m.get("coins", 0), "dna": m.get("dna", 0), "diamonds": m.get("diamonds", 0),
            "extras": extras,
        })
    return out


# ══════════════════════════════ fusion ══════════════════════════════
def _fusion_state(user) -> dict:
    from game.buildings import building_level, main_hall_level, star_cap
    from game.fusion import FUSION_BUILDING

    level = building_level(user, FUSION_BUILDING)
    return {
        "built": level > 0, "lab_level": level, "hall_level": main_hall_level(user),
        "cap": star_cap(user), "star_max": constants.STAR_MAX, "coins": user.coins,
        "lab_label": clean(constants.BUILDING_LABELS[FUSION_BUILDING]),
    }


@endpoint()
def fusion_panel(request, user):
    """The pairs that can be fused right now (game.fusion.ready_pairs — the bot's «تالار
    ادغام» list) plus the duplicate groups that are blocked, each with the reason."""
    from game.fusion import ready_pairs
    from game.workers import busy_creature_ids

    _gate(user, "fusion")
    state = _fusion_state(user)
    pairs = ready_pairs(user)
    ready_keys = {(p["name"], p["rarity"], p["star"]) for p in pairs}

    # duplicates the player owns that are NOT offered above, and why
    busy = busy_creature_ids(user)
    groups: dict = {}
    for c in Creature.objects.filter(owner=user).order_by("-level"):
        groups.setdefault((c.name, c.rarity, c.star_level), []).append(c)
    blocked_rows = []
    for key, members in groups.items():
        if len(members) < 2 or key in ready_keys:
            continue
        star = key[2]
        if star >= constants.STAR_MAX:
            reason = "max"
        elif not state["built"]:
            reason = "lab"
        elif star >= state["cap"]:
            reason = "cap"
        else:
            reason = "busy"
        blocked_rows.append((key, members, reason))

    out = []
    for p in pairs:
        cost = constants.fusion_cost(p["star"], p["rarity"])
        out.append({
            "name": p["name"], "rarity": p["rarity"], "star": p["star"], "count": p["count"],
            "cost": cost, "enough": user.coins >= cost,
            "a": _mini(p["parent_a"]), "b": _mini(p["parent_b"]),
        })
    blocked = [{
        "name": key[0], "rarity": key[1], "star": key[2], "count": len(members),
        "free": sum(1 for m in members if m.id not in busy),
        "reason": reason, "need_level": key[2] + 1, "sample": _mini(members[0]),
    } for key, members, reason in blocked_rows]
    blocked.sort(key=lambda b: (-b["star"], b["name"]))
    return {**state, "pairs": out, "blocked": blocked}


@endpoint()
def fusion_pair(request, user):
    """One creature's fusion card: every requirement for raising its star (the bot's «ورود
    به فیوژن» checklist, `_fusion_gate_sync`) and its same-species partners with the busy
    ones marked (`fusion_partners_annotated`)."""
    from game.fusion import fusion_partners_annotated
    from game.workers import busy_creature_ids, creature_status

    _gate(user, "fusion")
    creature = own_creature(user, need_int(request.GET, "id"))
    state = _fusion_state(user)
    at_cap = creature.star_level >= state["cap"]
    at_max = creature.star_level >= constants.STAR_MAX
    cost = constants.fusion_cost(creature.star_level, creature.rarity)
    annotated = fusion_partners_annotated(user, creature)
    # `fusion_partners` (the bot's «ورود به فیوژن» count) is this same list without the busy ones
    free_partners = [c for c, is_busy in annotated if not is_busy]
    dicts = _dicts(user, [creature] + [c for c, _busy in annotated])
    partners = []
    for c, is_busy in annotated:
        d = dict(dicts[c.id])
        d["busy"] = is_busy
        d["why"] = clean(creature_status(user, c) or "مشغول") if is_busy else ""
        partners.append(d)
    self_busy = creature.id in busy_creature_ids(user)
    gold_ok = user.coins >= cost
    return {
        **state,
        "creature": dicts[creature.id], "species": creature.name,
        "star": creature.star_level, "next_star": creature.star_level + 1,
        "at_cap": at_cap, "at_max": at_max,
        "partner_count": len(free_partners), "partners": partners,
        "self_busy": self_busy, "self_why": clean(creature_status(user, creature) or "مشغول") if self_busy else "",
        "cost": cost, "gold_ok": gold_ok, "missing_gold": max(0, cost - user.coins),
        "ready": state["built"] and not at_cap and bool(free_partners) and gold_ok and not self_busy,
        "inherit_pct": _pct(constants.FUSION_INHERIT_CHANCE),
    }


@endpoint("POST")
def fusion_do(request, user, data):
    """bot `_fusion_sync`: fuse → record_action("fusion") → check_missions("fusion")."""
    from game.daily import check_missions, record_action
    from game.fusion import fuse

    _gate(user, "fusion")
    parent_a = own_creature(user, need_int(data, "a"))
    parent_b = own_creature(user, need_int(data, "b"))
    child, inherited = fuse(user, parent_a, parent_b)
    record_action(user, "fusion")
    completed = check_missions(user, "fusion")
    return {
        "child": creature_list(user, [Creature.objects.get(id=child.id)])[0],
        "inherited": None if inherited is None else {
            "id": inherited.id, "name": inherited.name, "slot": inherited.slot, "rarity": inherited.rarity,
            "level": inherited.level, "img": equipment_img(inherited),
        },
        "missions": _missions(completed),
    }


# ══════════════════════════════ cave ══════════════════════════════
def _cave_rules() -> dict:
    """The numbers of the bot's «راهنمای کامل غار», read from the same constants."""
    from game import events

    ro = constants.RARITY_ORDER
    hours = constants.CAVE_MATING_HOURS_BY_RARITY_SUM
    hatch = [constants.egg_hatch_minutes(r, r) for r in ro]
    combos = []
    for i in range(len(ro) - 1, -1, -1):
        combos.append((ro[i], ro[i]))
        if i > 0:
            combos.append((ro[i], ro[i - 1]))
    return {
        "same_top_pct": _pct(constants.cave_offspring_rarities(ro[-1], ro[-1])["p_top"]),
        "gap_pct": [{"gap": gap, "pct": _pct(constants.cave_mixed_top_chance(ro[0], ro[gap]))} for gap in range(1, len(ro))],
        "half_elements": list(constants.CAVE_HALF_CHANCE_ELEMENTS),
        "half_factor": constants.CAVE_HALF_CHANCE_FACTOR,
        "mating_hours": [hours[min(hours)], hours[max(hours)]],
        "hatch_minutes": [min(hatch), max(hatch)],
        "dna": [{"a": a, "b": b, "dna": constants.cave_dna_cost(a, b)} for a, b in combos],
        "gems_per_hour": constants.DIAMOND_FINISH_PER_HOUR * constants.CAVE_FINISH_MULTIPLIER,
        "luck_pct": _pct(events.cave_top_bonus()),
    }


def _cave_state(user) -> dict:
    """bot `_panel_sync`, as JSON."""
    from game.buildings import is_built

    jobs = breeding.active_jobs(user)
    eggs = breeding.active_eggs(user)
    free = breeding.parent_candidates(user)
    user.refresh_from_db(fields=["diamonds", "dna_fragments"])
    return {
        "built": is_built(user, breeding.BREEDING_BUILDING),
        "lab_label": clean(constants.BUILDING_LABELS[breeding.BREEDING_BUILDING]),
        "max_jobs": breeding.max_cave_jobs(user),
        "jobs": [{
            "id": j.id, "a": _mini(j.parent_a), "b": _mini(j.parent_b),
            "ready": breeding.ready(j), "left": breeding.seconds_left(j),
            "total": max(1, int((j.finishes_at - j.started_at).total_seconds())),
            "finish_price": breeding.cave_finish_price(j),
        } for j in jobs],
        "eggs": [{
            "id": e.id, "ready": breeding.egg_ready(e), "left": breeding.egg_seconds_left(e),
            "total": max(1, int((e.finishes_at - e.started_at).total_seconds())),
            "finish_price": breeding.egg_finish_price(e),
        } for e in eggs],
        "free_ids": [c.id for c in free], "free_count": len(free),
        "diamonds": user.diamonds, "dna": user.dna_fragments,
        "img": asset_img(_cave_image()),
        "rules": _cave_rules(),
    }


def _cave_image():
    from game.media import get_cave_image_path

    return get_cave_image_path()


@endpoint()
def cave_panel(request, user):
    _gate(user, "breeding")
    return _cave_state(user)


def _cave_waiting(user) -> int:
    """How many things in the cave wait for a tap (a finished mating, a ready egg)."""
    return (sum(1 for j in BreedingJob.objects.filter(owner=user) if breeding.ready(j))
            + sum(1 for e in Egg.objects.filter(owner=user) if breeding.egg_ready(e)))


def hub_badges(user) -> dict:
    """Hub-tile badge, merged into /app/api/profile/me/ by the core (two tiny queries)."""
    return {"cave": _cave_waiting(user)}


@endpoint()
def badge(request, user):
    """Kept for copies of the app opened before the badge moved into the profile."""
    try:
        _gate(user, "breeding")
    except GameError:
        return {"cave": 0}
    return {"cave": _cave_waiting(user)}


@endpoint()
def cave_preview(request, user):
    """bot `_preview_sync` + the pairing screen: breeding.preview for this exact pair."""
    from game import events
    from game.workers import assert_free

    _gate(user, "breeding")
    parent_a = own_creature(user, need_int(request.GET, "a"))
    parent_b = own_creature(user, need_int(request.GET, "b"))
    if parent_a.id == parent_b.id:
        raise GameError("دو هیولای متفاوت انتخاب کن.")
    info = breeding.preview(user, parent_a, parent_b)
    dicts = _dicts(user, [parent_a, parent_b])
    top_pct = round(info["top_chance"] * 100)  # the bot's rounding
    # what breeding.start would refuse right now, in the game's own words (checked in its order)
    problem = ""
    try:
        breeding.assert_available(user)
        max_jobs = breeding.max_cave_jobs(user)
        if BreedingJob.objects.filter(owner=user).count() >= max_jobs:
            raise GameError(f"ظرفیت غار هیولا پره ({max_jobs} جفت همزمان). صبر کن تخم بذارن، بعد جفت بعدی رو بفرست.")
        assert_free(user, parent_a, for_action="بفرستی توی غار هیولا")
        assert_free(user, parent_b, for_action="بفرستی توی غار هیولا")
    except GameError as exc:
        problem = clean(exc)
    certain = info["top_rarity"] == info["fallback_rarity"]
    # the newborn is one of the two parent species; a crystal/plasma newborn rolls the top
    # rarity at a reduced chance (constants.cave_top_chance_for_element, applied at hatch)
    children, seen = [], set()
    for p in (parent_a, parent_b):
        if (p.name, p.element) in seen:
            continue
        seen.add((p.name, p.element))
        chance = constants.cave_top_chance_for_element(info["top_chance"], p.element)
        children.append({"name": p.name, "element": p.element, "top_pct": _pct(chance),
                         "halved": chance != info["top_chance"]})
    return {
        "a": dicts[parent_a.id], "b": dicts[parent_b.id],
        "top": info["top_rarity"], "fallback": info["fallback_rarity"], "certain": certain,
        "top_pct": 100 if certain else top_pct, "fallback_pct": 0 if certain else 100 - top_pct,
        "same_rarity": parent_a.rarity == parent_b.rarity,
        "children": children,
        "luck_pct": _pct(events.cave_top_bonus()),
        "mating_seconds": info["mating_minutes"] * 60, "hatch_seconds": info["hatch_minutes"] * 60,
        "dna": info["dna"], "have_dna": user.dna_fragments, "enough": user.dna_fragments >= info["dna"],
        "problem": problem,
    }


@endpoint("POST")
def cave_start(request, user, data):
    _gate(user, "breeding")
    parent_a = own_creature(user, need_int(data, "a"))
    parent_b = own_creature(user, need_int(data, "b"))
    breeding.start(user, parent_a, parent_b)
    return _cave_state(user)


@endpoint("POST")
def cave_lay(request, user, data):
    _gate(user, "breeding")
    breeding.lay_egg(user, job_id=need_int(data, "job"))
    return _cave_state(user)


@endpoint("POST")
def cave_hatch(request, user, data):
    _gate(user, "breeding")
    child, info = breeding.hatch(user, need_int(data, "egg"))
    return {
        "cave": _cave_state(user),
        "child": creature_list(user, [child])[0],
        "hit_top": bool(info["hit_top"]), "top": info["base_rarity"], "top_reached": info["rarity"] == info["base_rarity"],
        "parents": list(info["parents"]),
    }


@endpoint("POST")
def cave_finish(request, user, data):
    _gate(user, "breeding")
    breeding.finish_cave_with_diamonds(user, job_id=need_int(data, "job"))
    return _cave_state(user)


@endpoint("POST")
def egg_finish(request, user, data):
    _gate(user, "breeding")
    breeding.finish_egg_with_diamonds(user, need_int(data, "egg"))
    return _cave_state(user)


@endpoint("POST")
def cave_cancel(request, user, data):
    _gate(user, "breeding")
    breeding.cancel(user, job_id=need_int(data, "job"))
    return _cave_state(user)


routes = [
    ("fusion/", fusion_panel),
    ("fusion/pair/", fusion_pair),
    ("fusion/do/", fusion_do),
    ("cave/", cave_panel),
    ("cave/preview/", cave_preview),
    ("cave/start/", cave_start),
    ("cave/lay/", cave_lay),
    ("cave/hatch/", cave_hatch),
    ("cave/finish/", cave_finish),
    ("cave/egg_finish/", egg_finish),
    ("cave/cancel/", cave_cancel),
    ("badge/", badge),
]

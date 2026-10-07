"""Looking after ONE creature and its gear: make it active, feed it (XP capsules), upgrade
its body parts, rename it, dress it (per-slot equipment) and the blacksmith (forge with gold,
merge an identical piece, same-slot fusion).

Every view mirrors a `*_sync` helper of bot/handlers/private.py or bot/handlers/inventory.py
and calls the same game functions in the same order. Numbers shown before an action
(costs, gains, chances) come from those functions too; where the game has no "what would
happen" function, the real action is run inside a transaction that is rolled back
(`_dry_feed`, `_dry_merge`) instead of re-deriving the rule here.

Not here on purpose: feeding with GOLD. The bot's «تغذیه» button only opens the capsule
panel now (constants: capsules are "the ONLY way to «تغذیه»"); the old `lab:feed` branch
of `_lab_action_sync` is unreachable from any bot screen.
"""

from __future__ import annotations

import copy

from django.db import transaction

from bio_lab.models import Equipment, User
from game import constants
from telgame_site.mini.core import (
    GameError,
    InsufficientGoldError,
    clean,
    creature_dict,
    endpoint,
    equipment_img,
    need_int,
    need_str,
    own_creature,
    own_equipment,
)

MAX_FUSE_BATCH = 50  # sacrifices accepted in one fusion request


# ── small helpers ─────────────────────────────────────────────────────────────
def _bot():
    """The bot's private-chat module: the spec for the step sizes, the capsule plan and the
    section gate. Imported lazily (it pulls in python-telegram-bot)."""
    from bot.handlers import private

    return private


def _qint(request, key: str) -> int:
    try:
        return int(request.GET.get(key, ""))
    except (TypeError, ValueError):
        raise GameError("درخواست ناقصه.")


def _item_dict(it: Equipment) -> dict:
    """Same shape as a row of /app/api/profile/equipment/."""
    from bio_lab.repository import creature_name
    from game.equipment import bonus_text, equipment_power

    return {
        "id": it.id, "name": it.name, "slot": it.slot, "rarity": it.rarity, "level": it.level,
        "power": equipment_power(it), "bonus": clean(bonus_text(it)),
        "on": creature_name(it.equipped_on) if it.equipped_on_id else None,
        "on_id": it.equipped_on_id,
        "img": equipment_img(it),
    }


def _load(user: User, creature_id: int):
    """creature (research attached, like the bot's `_upgrade_pick_sync`) + its worn gear."""
    from game import research
    from game.equipment import get_equipped_items

    creature = own_creature(user, creature_id)
    research.attach_research(user, creature)
    return creature, get_equipped_items(creature)


def _creature_block(user: User, creature, gear: list) -> dict:
    from bio_lab.repository import creature_has_nickname
    from game.creature import creature_base_power, creature_is_maxed, effective_stats, xp_to_max_level
    from game.workers import busy_creature_ids

    out = creature_dict(creature, gear, busy_creature_ids(user))
    out.update({
        "max_level": constants.creature_max_level(creature.rarity, creature.star_level),
        "xp": creature.xp,
        "xp_need": constants.xp_for_creature_level(creature.level),
        "xp_to_max": xp_to_max_level(creature),
        "maxed": creature_is_maxed(creature),
        "poison": round(effective_stats(creature, gear)["poison"]),
        "base_power": creature_base_power(creature, gear),  # without the research lab
        "nickname": creature_has_nickname(creature),
        "star_max": constants.STAR_MAX,
    })
    return out


def _capsules(user: User) -> list[dict]:
    from game.creature import capsule_counts

    counts = capsule_counts(user)
    return [
        {"tier": tier, "label": clean(constants.XP_CAPSULES[tier]["label"]),
         "xp": constants.XP_CAPSULES[tier]["xp"], "count": counts.get(tier, 0)}
        for tier in constants.XP_CAPSULE_ORDER
    ]


# ── the upgrade panel (bot: upgrade_panel_text / upgrade_panel_keyboard) ───────
def _stats_with_part(creature, gear, part: str, plus: int) -> dict:
    """effective_stats with one body part bumped `plus` levels (then restored) — the same
    trick as the bot's `_part_power_gain`, so the preview is the real result."""
    from game.creature import effective_stats

    attr = f"{part}_lvl"
    current = getattr(creature, attr)
    setattr(creature, attr, current + max(1, plus))
    try:
        return effective_stats(creature, gear)
    finally:
        setattr(creature, attr, current)


def _parts(user: User, creature, gear: list) -> list[dict]:
    from game.creature import combat_rating, effective_stats, part_bulk_cost

    steps = _bot()._UPG_STEPS
    cap = constants.part_upgrade_cap(creature.rarity, creature.star_level)
    before = effective_stats(creature, gear)
    rating = combat_rating(before)
    rows = []
    for part, cfg in constants.BODY_PARTS.items():
        level = getattr(creature, f"{part}_lvl")
        name, _, hint = clean(cfg["label"]).partition("(")
        stat = cfg["stat"]
        options = []
        if level < cap:
            for step in steps:
                buy = min(step, cap - level)
                cost = part_bulk_cost(level, buy, creature.rarity)
                after = _stats_with_part(creature, gear, part, buy)
                options.append({
                    "step": step, "buy": buy, "cost": cost,
                    "stat_after": round(after[stat]),
                    "power_gain": combat_rating(after) - rating,
                    "afford": user.coins >= cost,
                })
        rows.append({
            "part": part, "label": name.strip(), "hint": hint.strip(" )"),
            "stat": stat, "stat_label": constants.EQUIPMENT_BONUS_LABELS.get(stat, (stat, False))[0],
            "stat_now": round(before[stat]),
            "level": level, "cap": cap, "maxed": level >= cap, "options": options,
        })
    return rows


def _panel(user: User, creature, gear: list) -> dict:
    from game.naming import NAME_MAX_LEN, RENAME_STEP, rename_cost

    star = creature.star_level
    can_star = star < constants.STAR_MAX
    return {
        "creature": _creature_block(user, creature, gear),
        "steps": list(_bot()._UPG_STEPS),
        "parts": _parts(user, creature, gear),
        "part_cap": constants.part_upgrade_cap(creature.rarity, star),
        # what one more star (fusion) would raise the ceilings to — None at the last star
        "next_star": {
            "star": star + 1,
            "part_cap": constants.part_upgrade_cap(creature.rarity, star + 1),
            "max_level": constants.creature_max_level(creature.rarity, star + 1),
        } if can_star else None,
        "capsules": _capsules(user),
        "rename": {"cost": rename_cost(creature), "step": RENAME_STEP, "max_len": NAME_MAX_LEN, "species": creature.name},
        "coins": user.coins,
    }


@endpoint()
def panel(request, user):
    creature, gear = _load(user, _qint(request, "id"))
    return _panel(user, creature, gear)


@endpoint("POST")
def activate(request, user, data):
    """bot: `_select_sync` (collection «انتخاب» / upgrade panel «انتخاب به عنوان فعال»)."""
    from game.creature import set_active_creature

    target = set_active_creature(user, need_int(data, "id", 1))
    creature, gear = _load(user, target.id)
    return {"creature": _creature_block(user, creature, gear)}


@endpoint("POST")
def part(request, user, data):
    """bot: `_lab_action_sync(action="up_<part>", count=step)` — gold only, no energy."""
    from game.creature import creature_power, upgrade_part

    creature, gear = _load(user, need_int(data, "id", 1))
    name = need_str(data, "part", choices=tuple(constants.BODY_PARTS))
    count = need_int(data, "count", 1)
    if count not in _bot()._UPG_STEPS:
        raise GameError("مقدار نامعتبره.")
    old_level = getattr(creature, f"{name}_lvl")
    power_before = creature_power(creature, gear)
    new_level, spent = upgrade_part(user, creature, name, count)
    out = _panel(user, creature, gear)
    out["done"] = {
        "part": name, "old_level": old_level, "new_level": new_level, "bought": new_level - old_level,
        "spent": spent, "power_before": power_before, "power_after": creature_power(creature, gear),
    }
    return out


# ── feeding with XP capsules (bot: feedcap_*) ─────────────────────────────────
def _dry_feed(user: User, creature, gear: list, plan: list) -> dict | None:
    """What `feed_capsules` WOULD do for this plan: the real function runs on copies inside
    a transaction that is always rolled back. None when it would refuse."""
    from game.creature import creature_power, feed_capsules

    u, c = copy.copy(user), copy.copy(creature)
    try:
        with transaction.atomic():
            result = feed_capsules(u, c, plan)
            transaction.set_rollback(True)
    except GameError:
        return None
    return {
        "eaten": sum(result["consumed"].values()), "xp": result["xp"], "levels": result["levels"],
        "level_after": result["new_level"], "maxed": result["maxed"],
        "xp_after": c.xp, "xp_need_after": constants.xp_for_creature_level(c.level),
        "power_after": creature_power(c, gear),
    }


def _feed_view(user: User, creature, gear: list, preview: bool = True) -> dict:
    from game.creature import capsule_counts

    block = _creature_block(user, creature, gear)
    caps = _capsules(user)
    counts = capsule_counts(user)
    total = sum(counts.values())
    plan_of = _bot()._feedcap_plan
    everything = None
    if preview and not block["maxed"]:
        for row in caps:
            if row["count"] <= 0:
                continue
            row["one"] = _dry_feed(user, creature, gear, plan_of("one", row["tier"], counts))
            # with a single capsule «همه» is the same plan as «+۱»
            row["all"] = row["one"] if row["count"] == 1 else _dry_feed(user, creature, gear, plan_of("allt", row["tier"], counts))
        if total > 0:
            everything = _dry_feed(user, creature, gear, plan_of("all", "", counts))
    return {"creature": block, "capsules": caps, "total": total, "all": everything}


@endpoint()
def feed_panel(request, user):
    creature, gear = _load(user, _qint(request, "id"))
    return _feed_view(user, creature, gear)


@endpoint("POST")
def feed(request, user, data):
    """bot: `_feedcap_do_sync` — the plan comes from the bot's own `_feedcap_plan`."""
    from game.creature import capsule_counts, creature_power, feed_capsules
    from game.daily import check_missions, record_action

    creature, gear = _load(user, need_int(data, "id", 1))
    kind = need_str(data, "kind", choices=("one", "allt", "all"))
    tier = need_str(data, "tier", choices=constants.XP_CAPSULE_ORDER) if kind != "all" else ""
    level_before, power_before = creature.level, creature_power(creature, gear)
    result = feed_capsules(user, creature, _bot()._feedcap_plan(kind, tier, capsule_counts(user)))
    record_action(user, "feed")
    completed = check_missions(user, "feed")
    creature, gear = _load(user, creature.id)
    out = _feed_view(User.objects.get(id=user.id), creature, gear)
    out["done"] = {
        "eaten": sum(result["consumed"].values()), "consumed": result["consumed"], "xp": result["xp"],
        "levels": result["levels"], "level_before": level_before, "level_after": result["new_level"],
        "maxed": result["maxed"], "power_before": power_before, "power_after": creature_power(creature, gear),
        "missions": [clean(m.get("label", "")) for m in (completed or []) if isinstance(m, dict) and m.get("label")],
    }
    return out


# ── renaming (bot: kaiju_rename_* → game.naming) ──────────────────────────────
@endpoint("POST")
def rename(request, user, data):
    from game.naming import rename_creature

    creature_id = need_int(data, "id", 1)
    name = data.get("name")
    if not isinstance(name, str) or len(name) > 200:
        raise GameError("یه اسم بفرست (خالی نباشه).")
    own_creature(user, creature_id)
    res = rename_creature(user, creature_id, name)  # validates, charges the rising diamond price
    return {"name": res["name"], "breed": res["breed"], "cost": res["cost"], "next_cost": res["next_cost"]}


# ── equipment on a creature (bot: equip_panel_* / equip_slot_callback) ────────
def _gear_view(user: User, creature) -> dict:
    from game import research
    from game.creature import creature_power
    from game.equipment import get_equipped_items, slot_loadout

    research.attach_research(user, creature)
    worn = get_equipped_items(creature)
    power = creature_power(creature, worn)
    slots = []
    for row in slot_loadout(user, creature):
        others = [g for g in worn if g.slot != row["slot"]]
        item = None
        if row["item"] is not None:
            item = _item_dict(row["item"])
            item["power_without"] = creature_power(creature, others)  # the creature's power if it's taken off
        candidates = []
        for cand in row["candidates"]:
            d = _item_dict(cand)
            d["power_after"] = creature_power(creature, others + [cand])  # the creature's power wearing it
            candidates.append(d)
        slots.append({"slot": row["slot"], "item": item, "candidates": candidates})
    return {"creature": _creature_block(user, creature, worn), "power": power, "slots": slots}


@endpoint()
def gear(request, user):
    return _gear_view(user, own_creature(user, _qint(request, "id")))


@endpoint("POST")
def equip(request, user, data):
    """bot: `_equip_do_sync` (any creature of the player; the piece moves if it was worn elsewhere)."""
    from game.equipment import equip_item

    creature = own_creature(user, need_int(data, "id", 1))
    item = equip_item(user, creature, need_int(data, "item", 1))
    out = _gear_view(user, creature)
    out["item"] = _item_dict(item)
    return out


@endpoint("POST")
def unequip(request, user, data):
    """bot: `_unequip_do_sync` / `_unequip_sync`."""
    from game.equipment import unequip_item

    item = own_equipment(user, need_int(data, "item", 1))
    creature = item.equipped_on if item.equipped_on_id else None
    item = unequip_item(user, item.id)
    out = _gear_view(user, creature) if creature is not None and creature.owner_id == user.id else {}
    out["item"] = _item_dict(item)
    return out


# ── blacksmith (bot: inventory.py forge_* / efuse_* / inv_upgrade) ─────────────
def _forge_lock(user: User) -> str | None:
    """The main-hall gate of the «آهنگری» section, exactly like bot.gates.hall_gated: the
    reason it is closed, or None when it's open (the active story quest's section is let
    through)."""
    from game import story
    from game.buildings import main_hall_level

    req = _bot().SECTION_HALL_REQ.get("blacksmith")
    if req is None:
        return None
    level = main_hall_level(user)
    if level < req and story.active_cta_action(user) != "blacksmith":
        return f"این بخش از سطح {req} «تالار مِهر» باز می‌شه (الان سطح {level}). اول تالار مِهرت رو ارتقا بده."
    return None


def _need_forge_section(user: User) -> None:
    reason = _forge_lock(user)
    if reason:
        raise GameError(reason)


def _forge_state(user: User) -> dict:
    from game.blacksmith import equipment_cap
    from game.buildings import building_level

    return {
        "locked": _forge_lock(user),
        "level": building_level(user, "blacksmith"),
        "built": building_level(user, "blacksmith") > 0,
        "cap": equipment_cap(user),
        "max": constants.EQUIPMENT_MAX_LEVEL,
        "coins": user.coins,
    }


def _forge_preview(user: User, item: Equipment) -> dict:
    """`forge_preview` plus what the piece becomes one level up (the game's own functions on
    an unsaved copy)."""
    from game.blacksmith import forge_preview
    from game.equipment import bonus_text, equipment_power

    p = forge_preview(item, user)
    nxt = copy.copy(item)
    nxt.level = p["target_level"]
    return {
        "target_level": p["target_level"], "cost": p["cost"], "fail_chance": p["fail_chance"],
        "cap": p["cap"], "at_max": p["at_max"], "afford": user.coins >= p["cost"],
        "power_after": equipment_power(nxt), "bonus_after": clean(bonus_text(nxt)),
    }


@endpoint()
def forge_list(request, user):
    """bot: `blacksmith_panel` + `forge_cat` — everything still below the forge's ceiling."""
    from game.blacksmith import forgeable_items

    _need_forge_section(user)
    order = {r: i for i, r in enumerate(constants.RARITY_ORDER)}
    items = []
    for it in forgeable_items(user):
        d = _item_dict(it)
        d["forge"] = _forge_preview(user, it)
        items.append(d)
    items.sort(key=lambda d: (-order.get(d["rarity"], 0), -d["level"], d["id"]))
    out = _forge_state(user)
    out.update({"items": items, "owned": Equipment.objects.filter(owner=user).count()})
    return out


def _dry_merge(user: User, item: Equipment, dupe: Equipment) -> dict:
    """Price / refusal of «ارتقا با نمونه مشابه»: `upgrade_item` itself, rolled back."""
    from game.equipment import upgrade_item

    u = copy.copy(user)
    try:
        with transaction.atomic():
            upgrade_item(u, item.id, dupe.id)
            transaction.set_rollback(True)
    except InsufficientGoldError as exc:
        return {"ok": False, "cost": exc.need, "why": clean(exc), "gold": True}
    except GameError as exc:
        return {"ok": False, "cost": None, "why": clean(exc), "gold": False}
    return {"ok": True, "cost": user.coins - u.coins, "why": "", "gold": False}


def _item_view(user: User, item: Equipment) -> dict:
    from game.equipment import _fuse_fail_chance, same_slot_candidates

    state = _forge_state(user)
    out = {"item": _item_dict(item), "smith": state, "forge": _forge_preview(user, item)}

    # exact duplicates (same slot + model + rarity) — bot: `_item_detail_sync`
    dupes = list(
        Equipment.objects.filter(owner=user, slot=item.slot, template_key=item.template_key, rarity=item.rarity)
        .exclude(id=item.id).select_related("equipped_on").order_by("equipped_on_id", "level", "id")
    )
    out["dupes"] = [_item_dict(d) for d in dupes]
    out["merge"] = _dry_merge(user, item, dupes[0]) if dupes and item.level < constants.EQUIPMENT_MAX_LEVEL else None

    # same-slot fusion sacrifices with their real odds — bot: `_efuse_scored_sync`
    fuse = []
    if not state["locked"]:
        for cand in same_slot_candidates(user, item.id):
            d = _item_dict(cand)
            d["fail_chance"] = _fuse_fail_chance(item, cand)
            fuse.append(d)
    out["fuse"] = fuse
    return out


@endpoint()
def item(request, user):
    it = Equipment.objects.filter(id=_qint(request, "id"), owner=user).select_related("equipped_on").first()
    if it is None:
        raise GameError("این تجهیزات دیگه توی کوله‌پشتیت نیست.")
    return _item_view(user, it)


def _fresh_item_view(user: User, item_id: int) -> dict:
    it = Equipment.objects.filter(id=item_id, owner=user).select_related("equipped_on").first()
    if it is None:
        raise GameError("این تجهیزات پیدا نشد.")
    return _item_view(User.objects.get(id=user.id), it)


@endpoint("POST")
def forge_do(request, user, data):
    """bot: `_forge_do_sync` — gold for one level; past the safe level it can fail."""
    from game.blacksmith import forge
    from game.equipment import equipment_power

    _need_forge_section(user)
    it = own_equipment(user, need_int(data, "item", 1))
    level_before, power_before = it.level, equipment_power(it)
    result = forge(user, it.id)
    out = _fresh_item_view(user, it.id)
    out["done"] = {
        "kind": "forge", "success": result["success"], "cost": result["cost"], "fail_chance": result["fail_chance"],
        "level_before": level_before, "level_after": result["item"].level,
        "power_before": power_before, "power_after": equipment_power(result["item"]),
    }
    return out


@endpoint("POST")
def merge(request, user, data):
    """bot: `_upgrade_item_sync` — consume an identical piece (+ gold) for a sure +1."""
    from game.equipment import equipment_power, upgrade_item

    it = own_equipment(user, need_int(data, "item", 1))
    dupe = own_equipment(user, need_int(data, "dupe", 1))
    level_before, power_before, coins_before = it.level, equipment_power(it), User.objects.get(id=user.id).coins
    upgraded = upgrade_item(user, it.id, dupe.id)
    out = _fresh_item_view(user, it.id)
    out["done"] = {
        "kind": "merge", "success": True, "cost": coins_before - out["smith"]["coins"],
        "level_before": level_before, "level_after": upgraded.level,
        "power_before": power_before, "power_after": equipment_power(upgraded),
    }
    return out


@endpoint("POST")
def fuse(request, user, data):
    """bot: `_efuse_multi_sync` — each ticked same-slot piece is one roll for +1; every
    sacrifice is consumed either way."""
    from game.equipment import equipment_power, fuse_equipment_many

    _need_forge_section(user)
    it = own_equipment(user, need_int(data, "item", 1))
    raw = data.get("sacrifices")
    if not isinstance(raw, list) or not raw or len(raw) > MAX_FUSE_BATCH:
        raise GameError("اول حداقل یه تجهیزات رو تیک بزن.")
    try:
        ids = [int(x) for x in raw]
    except (TypeError, ValueError):
        raise GameError("درخواست ناقصه.")
    level_before, power_before = it.level, equipment_power(it)
    result = fuse_equipment_many(user, it.id, ids)
    out = _fresh_item_view(user, it.id)
    out["done"] = {
        "kind": "fuse", "successes": result["successes"], "fails": result["fails"], "consumed": result["consumed"],
        "capped": result["capped"], "level_before": level_before, "level_after": result["new_level"],
        "power_before": power_before, "power_after": equipment_power(result["target"]),
    }
    return out


routes = [
    ("panel/", panel),            # GET  ?id=    upgrade panel of one creature
    ("activate/", activate),      # POST {id}
    ("part/", part),              # POST {id, part, count}
    ("feed/", feed_panel),        # GET  ?id=    capsules + exact previews
    ("feed/do/", feed),           # POST {id, kind: one|allt|all, tier}
    ("rename/", rename),          # POST {id, name}
    ("gear/", gear),              # GET  ?id=    slots, worn pieces, candidates
    ("equip/", equip),            # POST {id, item}
    ("unequip/", unequip),        # POST {item}
    ("forge/", forge_list),       # GET          upgradable gear (hall gate)
    ("item/", item),              # GET  ?id=    forge / merge / fusion options of one piece
    ("forge/do/", forge_do),      # POST {item}
    ("merge/", merge),            # POST {item, dupe}
    ("fuse/", fuse),              # POST {item, sacrifices: [ids]}
]

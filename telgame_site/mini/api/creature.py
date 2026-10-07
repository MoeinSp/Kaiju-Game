"""Looking after ONE creature and its gear: make it active, feed it (XP capsules), upgrade
its body parts, rename it, dress it (per-slot equipment) and the blacksmith (forge with gold,
merge an identical piece, same-slot fusion).

Every view mirrors a `*_sync` helper of bot/handlers/private.py or bot/handlers/inventory.py
and calls the same game functions in the same order. Numbers shown before an action
(costs, gains, chances) come from those functions too; where the game has no "what would
happen" function, the real action is run inside a transaction that is rolled back
(`_dry_feed`, `_dry_merge`) instead of re-deriving the rule here.

«بلعیدن هیولا» (devour) is here too: the bot's upgrade panel offers it next to feeding.

Big accounts own thousands of pieces, so every list is capped on the server (the best
candidates of a slot, the cheapest fusion sacrifices of each rarity, …) and says how many
there are in total; nothing is fetched per row (`_attach_wearers`).

Not here on purpose: feeding with GOLD. The bot's «تغذیه» button only opens the capsule
panel now (constants: capsules are "the ONLY way to «تغذیه»"); the old `lab:feed` branch
of `_lab_action_sync` is unreachable from any bot screen.
"""

from __future__ import annotations

import copy

from django.db import transaction

from bio_lab.models import Creature, Equipment, User
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

MAX_FUSE_BATCH = 50      # sacrifices accepted in one fusion request
MAX_DEVOUR_BATCH = 40    # creatures accepted in one devour request (the game checks each one's status)
GEAR_PAGE = 24           # candidates of one slot sent at a time (best for THIS creature first)
FUSE_PER_RARITY = 40     # fusion sacrifices shown per rarity (cheapest first)
DUPES_SHOWN = 12         # identical pieces offered for «نمونه‌ی مشابه»


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


def _attach_wearers(items) -> None:
    """Load the creatures that wear these pieces in ONE query (for rows that did not come
    from a select_related queryset) — `_item_dict` then reads the name without a query."""
    field = Equipment._meta.get_field("equipped_on")
    need = [it for it in items if it.equipped_on_id and not field.is_cached(it)]
    if not need:
        return
    rows = Creature.objects.in_bulk({it.equipped_on_id for it in need})
    for it in need:
        field.set_cached_value(it, rows.get(it.equipped_on_id))


def _wearer(it: Equipment) -> str | None:
    from bio_lab.repository import creature_name

    if not it.equipped_on_id:
        return None
    owner = it.equipped_on
    return creature_name(owner) if owner is not None else None


def _item_row(it: Equipment) -> dict:
    """The short form used in long lists (no bonus text / power)."""
    return {
        "id": it.id, "name": it.name, "slot": it.slot, "rarity": it.rarity, "level": it.level,
        "on": _wearer(it), "on_id": it.equipped_on_id, "img": equipment_img(it),
    }


def _item_dict(it: Equipment) -> dict:
    """Same shape as a row of /app/api/profile/equipment/."""
    from game.equipment import bonus_text, equipment_power

    out = _item_row(it)
    out.update({"power": equipment_power(it), "bonus": clean(bonus_text(it))})
    return out


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
        stocked = [row for row in caps if row["count"] > 0]
        if len(stocked) == 1:
            everything = stocked[0]["all"]  # one kind of food only: «همه» of that kind IS «تغذیه با همه»
        elif stocked:
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
def _rank_slot(creature, others: list, candidates: list) -> list[tuple[int, Equipment]]:
    """[(the creature's power wearing it, piece)] — best for THIS creature first, spare
    pieces before ones another creature wears. `creature_power` only reads a piece through
    `equipment_bonus`, so pieces with an identical bonus share one calculation (a big bag
    has ~2,000 pieces but only a few hundred distinct bonuses)."""
    from game.creature import creature_power
    from game.equipment import equipment_bonus

    memo: dict = {}
    ranked = []
    for cand in candidates:
        key = tuple(sorted(equipment_bonus(cand).items()))
        if key not in memo:
            memo[key] = creature_power(creature, others + [cand])
        ranked.append((memo[key], cand))
    ranked.sort(key=lambda t: (-t[0], t[1].equipped_on_id is not None, t[1].id))
    return ranked


def _slot_block(creature, worn: list, row: dict, skip: int = 0, rarity: str = "") -> dict:
    """One slot of the loadout: what is in it and ONE page of what could be."""
    from game.creature import creature_power

    others = [g for g in worn if g.slot != row["slot"]]
    item = None
    if row["item"] is not None:
        item = _item_dict(row["item"])
        item["power_without"] = creature_power(creature, others)  # the creature's power if it's taken off
    counts: dict = {}
    for cand in row["candidates"]:
        counts[cand.rarity] = counts.get(cand.rarity, 0) + 1
    pool = [c for c in row["candidates"] if not rarity or c.rarity == rarity]
    page = _rank_slot(creature, others, pool)[skip:skip + GEAR_PAGE]
    candidates = []
    for power_after, cand in page:
        d = _item_dict(cand)
        d["power_after"] = power_after  # the creature's power wearing it
        candidates.append(d)
    return {"slot": row["slot"], "item": item, "candidates": candidates, "skip": skip, "rarity": rarity,
            "total": len(pool), "all": len(row["candidates"]), "counts": counts}


def _gear_view(user: User, creature) -> dict:
    from game import research
    from game.creature import creature_power
    from game.equipment import get_equipped_items, slot_loadout

    research.attach_research(user, creature)
    worn = get_equipped_items(creature)
    slots = [_slot_block(creature, worn, row) for row in slot_loadout(user, creature)]
    return {"creature": _creature_block(user, creature, worn), "power": creature_power(creature, worn), "slots": slots}


@endpoint()
def gear(request, user):
    return _gear_view(user, own_creature(user, _qint(request, "id")))


@endpoint()
def gear_slot(request, user):
    """More candidates of one slot (bot: `equip_slot_callback` — rarity tabs + pages)."""
    from game import research
    from game.equipment import get_equipped_items, slot_loadout

    creature = own_creature(user, _qint(request, "id"))
    slot = request.GET.get("slot", "")
    rarity = request.GET.get("rarity", "")
    if slot not in constants.EQUIPMENT_SLOTS or (rarity and rarity not in constants.RARITY_ORDER):
        raise GameError("این جایگاه وجود نداره.")
    try:
        skip = max(0, int(request.GET.get("skip", "0")))
    except ValueError:
        skip = 0
    research.attach_research(user, creature)
    worn = get_equipped_items(creature)
    row = next(r for r in slot_loadout(user, creature) if r["slot"] == slot)
    return _slot_block(creature, worn, row, skip, rarity)


@endpoint("POST")
def equip(request, user, data):
    """bot: `_equip_do_sync` (any creature of the player; the piece moves if it was worn elsewhere).
    `brief` (sent by the item screen, which shows no loadout) skips the candidate lists."""
    from game import research
    from game.creature import creature_power
    from game.equipment import equip_item, get_equipped_items

    creature = own_creature(user, need_int(data, "id", 1))
    item = equip_item(user, creature, need_int(data, "item", 1))
    if data.get("brief"):
        research.attach_research(user, creature)
        out = {"power": creature_power(creature, get_equipped_items(creature))}
    else:
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
    brief = data.get("brief") or creature is None or creature.owner_id != user.id
    out = {} if brief else _gear_view(user, creature)
    out["item"] = _item_dict(item)
    return out


# ── devouring (bot: devour_* → game.creature.devour_creatures) ────────────────
def _devour_view(user: User, creature, gear: list) -> dict:
    """The sacrifices the bot's «بلعیدن هیولا» list offers, with the XP each is worth.
    The idle creatures come from `workers.free_creatures` — the same rule and the same
    order as `game.creature.devour_candidates` (not active, not working / breeding / on a
    mission; rarest first) without its status queries per creature."""
    from bio_lab.repository import creature_name
    from game.creature import _devour_xp, xp_to_max_level
    from game.workers import free_creatures
    from telgame_site.mini.core import creature_img

    rows = [{
        "id": c.id, "name": creature_name(c), "species": c.name, "element": c.element, "rarity": c.rarity,
        "star": c.star_level, "level": c.level, "img": creature_img(c), "xp": _devour_xp(c),
    } for c in free_creatures(user) if c.id != creature.id]
    return {"creature": _creature_block(user, creature, gear), "need": xp_to_max_level(creature),
            "candidates": rows, "max_batch": MAX_DEVOUR_BATCH}


@endpoint()
def devour_panel(request, user):
    creature, gear = _load(user, _qint(request, "id"))
    return _devour_view(user, creature, gear)


@endpoint("POST")
def devour(request, user, data):
    """bot: `_devour_multi_sync` after the tick rules of `_devour_can_add_sync` (a maxed
    target eats nothing; no extra sacrifice once the ticked ones already reach the cap)."""
    from game.creature import _devour_xp, creature_power, devour_creatures, xp_to_max_level

    creature, gear = _load(user, need_int(data, "id", 1))
    raw = data.get("sacrifices")
    if not isinstance(raw, list) or not raw:
        raise GameError("اول حداقل یه موجود رو تیک بزن.")
    if len(raw) > MAX_DEVOUR_BATCH:
        raise GameError(f"هر بار حداکثر {MAX_DEVOUR_BATCH} هیولا رو می‌شه بلعید.")
    try:
        ids = list(dict.fromkeys(int(x) for x in raw))
    except (TypeError, ValueError):
        raise GameError("درخواست ناقصه.")
    need = xp_to_max_level(creature)
    if need <= 0:
        raise GameError("این موجود به سقف سطحش رسیده و دیگه نمی‌تونه هیولا بخوره.")
    worth = {c.id: _devour_xp(c) for c in Creature.objects.filter(id__in=ids, owner=user)}
    running = 0
    for sac_id in ids:
        if running >= need:
            raise GameError("همین‌ها برای رسیدن به سقف سطح کافیه — بیشتر از این، قربانی هدر می‌ره.")
        running += worth.get(sac_id, 0)
    level_before, power_before = creature.level, creature_power(creature, gear)
    result = devour_creatures(user, creature.id, ids)
    creature, gear = _load(user, creature.id)
    out = _devour_view(user, creature, gear)
    out["done"] = {
        "count": result["count"], "eaten": result["eaten"][:6], "xp": result["xp"], "levels": result["levels"],
        "level_before": level_before, "level_after": result["new_level"],
        "power_before": power_before, "power_after": creature_power(creature, gear),
        "maxed": out["creature"]["maxed"],
    }
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

    level = building_level(user, "blacksmith")
    return {
        "locked": _forge_lock(user),
        "level": level,
        "built": level > 0,
        "cap": equipment_cap(user),
        "max": constants.EQUIPMENT_MAX_LEVEL,
        "coins": user.coins,
    }


FORGE_LIST_PER_SLOT = 60


def _forge_preview(user: User, item: Equipment, cap: int | None = None) -> dict:
    """`forge_preview` plus what the piece becomes one level up (the game's own functions on
    an unsaved copy)."""
    from game.blacksmith import forge_preview
    from game.equipment import bonus_text, equipment_power

    p = forge_preview(item, user if cap is None else None)
    if cap is not None:      # the caller already knows the forge's ceiling (saves a query per piece)
        p["cap"], p["at_max"] = cap, item.level >= cap
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
    _need_forge_section(user)
    from game.blacksmith import forge_preview

    out = _forge_state(user)
    cap = out["cap"]
    order = {r: i for i, r in enumerate(constants.RARITY_ORDER)}
    # big accounts own thousands of pieces: rank on the raw rows, then build only the best of each slot
    rows = list(Equipment.objects.filter(owner=user, level__lt=cap).select_related("equipped_on"))
    rows.sort(key=lambda it: (-order.get(it.rarity, 0), -it.level, it.id))
    shown, per_slot = [], {}
    for it in rows:
        per_slot[it.slot] = per_slot.get(it.slot, 0) + 1
        if per_slot[it.slot] <= FORGE_LIST_PER_SLOT:
            shown.append(it)
    items = []
    for it in shown:
        p = forge_preview(it)  # only cost + risk are used here; the ceiling was applied by the query
        d = _item_row(it)
        d["forge"] = {"cost": p["cost"], "fail_chance": p["fail_chance"], "afford": user.coins >= p["cost"]}
        items.append(d)
    out.update({"items": items, "total": len(rows), "counts": per_slot,
                "owned": len(rows) or Equipment.objects.filter(owner=user).count()})
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
    out = {"item": _item_dict(item), "smith": state, "forge": _forge_preview(user, item, cap=state["cap"])}

    # exact duplicates (same slot + model + rarity) — bot: `_item_detail_sync`; spare and
    # low-level ones first, because the one that is picked gets consumed
    dupes = list(
        Equipment.objects.filter(owner=user, slot=item.slot, template_key=item.template_key, rarity=item.rarity)
        .exclude(id=item.id).select_related("equipped_on")
    )
    dupes.sort(key=lambda d: (d.equipped_on_id is not None, d.level, d.id))
    out["dupes"] = [_item_row(d) for d in dupes[:DUPES_SHOWN]]
    out["dupes_total"] = len(dupes)
    out["merge"] = _dry_merge(user, item, dupes[0]) if dupes and item.level < constants.EQUIPMENT_MAX_LEVEL else None

    # same-slot fusion sacrifices with their real odds — bot: `_efuse_scored_sync`. The
    # cheapest of every rarity (spare before worn, low level first) and the totals.
    fuse, counts = [], {}
    if not state["locked"]:
        cands = same_slot_candidates(user, item.id)
        cands.sort(key=lambda c: (c.equipped_on_id is not None, c.level, c.id))
        shown = []
        for cand in cands:
            counts[cand.rarity] = counts.get(cand.rarity, 0) + 1
            if counts[cand.rarity] <= FUSE_PER_RARITY:
                shown.append(cand)
        _attach_wearers(shown)
        order = {r: i for i, r in enumerate(constants.RARITY_ORDER)}
        shown.sort(key=lambda c: order.get(c.rarity, 0))  # stable: keeps spare/low-level first inside a rarity
        for cand in shown:
            d = _item_row(cand)
            d["fail_chance"] = _fuse_fail_chance(item, cand)
            fuse.append(d)
    out["fuse"] = fuse
    out["fuse_counts"] = counts
    out["fuse_total"] = sum(counts.values())
    out["fuse_batch"] = MAX_FUSE_BATCH
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
    ("gear/", gear),              # GET  ?id=    slots, worn pieces, the best candidates of each
    ("gear/slot/", gear_slot),    # GET  ?id=&slot=&skip=&rarity=   one more page of a slot
    ("equip/", equip),            # POST {id, item, brief?}
    ("unequip/", unequip),        # POST {item, brief?}
    ("devour/", devour_panel),    # GET  ?id=    idle creatures that can be eaten + the XP each gives
    ("devour/do/", devour),       # POST {id, sacrifices: [ids]}
    ("forge/", forge_list),       # GET          upgradable gear (hall gate)
    ("item/", item),              # GET  ?id=    forge / merge / fusion options of one piece
    ("forge/do/", forge_do),      # POST {item}
    ("merge/", merge),            # POST {item, dupe}
    ("fuse/", fuse),              # POST {item, sacrifices: [ids]}
]

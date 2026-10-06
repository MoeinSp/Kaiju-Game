"""«📋 امروز» — one screen that answers «what is worth doing right now?».

The daily loop was scattered over five hubs (free boxes in the shop, the wheel and the
missions in the city, collecting in the base, chests in the arena, …) and fewer than half
of the active players collected even their free rewards. This module gathers the state
of all of it (state) and takes whatever is a plain «collect» in one go (collect_all).

Nothing here adds a reward — it only points at, or collects, what already exists.
"""

from __future__ import annotations

from django.db import transaction

from bio_lab.models import ArenaChest, Building, DispatchMission, User
from game import constants
from game.creature import GameError

EVENTS_HALL_REQ = 3  # the «رویدادهای ویژه» section (and its daily reward) opens here


def _pending_buildings(user: User) -> list[tuple[Building, int, str]]:
    from game.buildings import pending_amount, produces

    out = []
    for b in Building.objects.filter(owner=user, level__gt=0):
        if not produces(b.building_type):
            continue
        amount = pending_amount(b)
        if amount > 0:
            out.append((b, amount, constants.BUILDING_PRODUCTION[b.building_type]["resource"]))
    return out


def state(user: User) -> dict:
    """Everything the «امروز» screen shows. Read-only."""
    from django.utils import timezone

    from game import daily, events, festival, story, tournament, worldboss
    from game.buildings import main_hall_level
    from game.daily import get_daily_count
    from game.energy import get_max_energy, sync_energy
    from game.lootbox import can_claim_free_diamond_box

    hall = main_hall_level(user)
    pending: dict[str, int] = {}
    for _b, amount, resource in _pending_buildings(user):
        pending[resource] = pending.get(resource, 0) + amount

    missions = daily.mission_status(user)
    quest = story.get_active_quest(user)
    boss = worldboss.current_boss()
    boss_hits_left = 0
    if boss is not None:
        entry = worldboss.my_entry(boss, user)
        boss_hits_left = max(0, worldboss.HITS_PER_PLAYER - (entry.hits if entry else 0))

    return {
        "hall": hall,
        "energy": sync_energy(user),
        "max_energy": get_max_energy(user),
        "pending": pending,
        "dispatch_ready": DispatchMission.objects.filter(
            owner=user, status=DispatchMission.ACTIVE, finishes_at__lte=timezone.now()
        ).count() if hall >= 2 else 0,
        "dispatch_open": hall >= 2,
        "event_daily": hall >= EVENTS_HALL_REQ and events.status(user)["can_claim"],
        "story_ready": bool(quest and quest["is_done"]),
        "free_boxes": [t for t in ("bronze", "silver") if can_claim_free_diamond_box(user, t)],
        "wheel": get_daily_count(user, "wheel_spin") < constants.WHEEL_DAILY_LIMIT,
        "chests_ready": ArenaChest.objects.filter(user=user, status="ready").count(),
        "boxes_ready": sum(1 for b in missions["boxes"] if b["reached"] and not b["opened"]),
        "missions_done": sum(1 for m in missions["daily"] if m["done"]),
        "missions_total": len(missions["daily"]),
        "points": missions["points"],
        "next_box": next((b for b in missions["boxes"] if not b["reached"]), None),
        "boss": {"name": boss.name, "hits_left": boss_hits_left} if boss is not None else None,
        "tournament_open": tournament.registration_open() and tournament.my_entry(user) is None,
        "festival": festival.theme() if festival.active_key() else None,
        "rule": events.rule_line(),
    }


def collectable_count(st: dict) -> int:
    """How many things «دریافت همه» would take right now."""
    return (len(st["pending"]) + st["dispatch_ready"] + int(st["event_daily"]) + int(st["story_ready"]))


def waiting_count(st: dict) -> int:
    """The number on the menu button: everything that's ready, collectable or not."""
    return (collectable_count(st) + len(st["free_boxes"]) + int(st["wheel"]) + st["chests_ready"]
            + st["boxes_ready"] + int(bool(st["boss"] and st["boss"]["hits_left"])) + int(st["tournament_open"]))


def collect_all(user: User) -> dict:
    """Take every plain «collect»: building output, finished dispatch missions, the event's
    daily reward and a finished story quest. Each part runs in its own transaction, so one
    failing never undoes the others. Returns totals + the lines to show."""
    from game import daily, dispatch, events, story
    from game.buildings import collect, main_hall_level

    totals = {"coins": 0, "dna_fragments": 0, "diamonds": 0}
    lines: list[str] = []
    missions_done: list[dict] = []

    collected = 0
    for building, _amount, _resource in _pending_buildings(user):
        try:
            amount, resource = collect(user, building)
        except GameError:
            continue
        totals[resource] = totals.get(resource, 0) + amount
        collected += 1
        daily.record_action(user, "collect")
        missions_done += daily.check_missions(user, "collect")
    if collected:
        lines.append(f"🏗 جمع‌آوری از <code>{collected}</code> ساختمان")

    hall = main_hall_level(user)
    if hall >= 2:
        for res in dispatch.collect_all(user):
            reward = res["reward"]
            totals["coins"] += int(reward.get("coins", 0))
            totals["dna_fragments"] += int(reward.get("dna", 0))
            totals["diamonds"] += int(reward.get("diamonds", 0))
            _key, emoji, title, _flavor, _focus = dispatch.template(res["mission"])
            extra = " · 🍀 جایزه‌ی شگفتی" if dispatch.has_bonus(reward) else ""
            lines.append(f"{emoji} {title}{extra}")

    if hall >= EVENTS_HALL_REQ:
        reward = events.claim_daily(user)
        if reward:
            lines.append(f"🎁 جایزه‌ی روزانه‌ی رویداد: {events.reward_text(reward)}")

    with transaction.atomic():
        res = story.claim_active_quest(user)
    if res.get("success"):
        lines.append(f"🎯 مأموریت «{res['claimed_quest']['title']}»: {res['reward_text']}")

    return {"totals": totals, "lines": lines, "missions": missions_done}

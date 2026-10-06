"""«📋 امروز» — one screen that answers «what is worth doing right now?».

The daily loop was scattered over five hubs (free boxes in the shop, the wheel and the
missions in the city, collecting in the base, chests in the arena, …) and fewer than half
of the active players collected even their free rewards. This module gathers the state
of all of it (state) and takes ALL of it in one go (collect_all): building output,
finished dispatch missions, the event's daily reward, a finished story quest, the daily
wheel spin, the free monster boxes, ready arena chests and reached mission boxes.

Nothing here adds a reward — it only points at, or collects, what already exists.
"""

from __future__ import annotations

from django.db import transaction

from bio_lab.models import ArenaChest, Building, DispatchMission, User
from game import constants
from game.creature import GameError

EVENTS_HALL_REQ = 3  # the «رویدادهای ویژه» section (and its daily reward) opens here
# a building's output is only offered once this long has passed since it was last
# collected — otherwise «دریافت همه» lit up again a minute later for a handful of coins
COLLECT_MIN_MINUTES = 10
_BOX_NAMES = {"bronze": ("🥉", "باکس برنزی"), "silver": ("🥈", "باکس نقره‌ای")}


def _pending_buildings(user: User) -> list[tuple[Building, int, str]]:
    """(building, amount, resource) for every producer worth collecting now: it has
    something AND was last collected at least COLLECT_MIN_MINUTES ago."""
    import datetime

    from django.utils import timezone

    from game.buildings import pending_amount, produces

    cutoff = timezone.now() - datetime.timedelta(minutes=COLLECT_MIN_MINUTES)
    out = []
    for b in Building.objects.filter(owner=user, level__gt=0, last_collected_at__lte=cutoff).order_by("id"):
        if not produces(b.building_type):
            continue
        amount = pending_amount(b)
        if amount > 0:
            out.append((b, amount, constants.BUILDING_PRODUCTION[b.building_type]["resource"]))
    return out


def _reached_boxes(user: User) -> tuple[list[int], int]:
    """(indexes of mission boxes reached but not opened, this week's points) straight from
    the claim rows — three small queries instead of the ~40 the full missions screen needs."""
    from bio_lab.models import MissionClaim
    from game import daily

    wk, dates = daily.week_key(), daily.week_dates()
    points = daily._week_points(user, dates, wk)
    opened = set(MissionClaim.objects.filter(user=user, day=wk, mission_key__startswith="box_")
                 .values_list("mission_key", flat=True))
    ready = [i for i, (need, _tier) in enumerate(constants.mission_box_thresholds(), start=1)
             if points >= need and daily._box_key(i) not in opened]
    return ready, points


def state(user: User, full: bool = True) -> dict:
    """Everything the «امروز» screen shows. Read-only. `full=False` is the cheap version
    for the badge on the main menu (opened on every visit): it skips the per-mission
    progress, which is by far the most expensive part."""
    from django.utils import timezone

    from bio_lab.models import DailyActionLog
    from game import daily, events, festival, story, tournament, worldboss
    from game.buildings import main_hall_level
    from game.daily import today_str
    from game.energy import get_max_energy, sync_energy
    from game.lootbox import can_claim_free_diamond_box

    hall = main_hall_level(user)
    # one entry per building, so the screen can name each: (label, amount, resource)
    pending = [(constants.BUILDING_LABELS.get(b.building_type, b.building_type), amount, resource)
               for b, amount, resource in _pending_buildings(user)]

    # «اعزام» is only worth a button while a mission can actually be SENT: a free slot and
    # an offer of today's board that hasn't been taken (checked on the full screen only)
    can_dispatch = False
    if full and hall >= 2:
        from game import dispatch

        level = dispatch.hq_level(user)
        running = DispatchMission.objects.filter(owner=user, status=DispatchMission.ACTIVE).count()
        can_dispatch = running < dispatch.slots(user, level) and any(
            not o["taken"] for o in dispatch.offers_for(user, level=level)
        )

    boxes, points = _reached_boxes(user)
    missions = daily.mission_status(user) if full else None
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
        "can_dispatch": can_dispatch,
        "event_daily": hall >= EVENTS_HALL_REQ and events.status(user)["can_claim"],
        "story_ready": bool(quest and quest["is_done"]),
        "free_boxes": [t for t in ("bronze", "silver") if can_claim_free_diamond_box(user, t)],
        "wheel": not DailyActionLog.objects.filter(
            user=user, action="wheel_spin", day=today_str(), count__gte=constants.WHEEL_DAILY_LIMIT
        ).exists(),
        "chests_ready": ArenaChest.objects.filter(user=user, status="ready").count(),
        "boxes_ready": len(boxes),
        "missions_done": sum(1 for m in missions["daily"] if m["done"]) if missions else 0,
        "missions_total": len(missions["daily"]) if missions else 0,
        "points": points,
        "next_box": next((b for b in missions["boxes"] if not b["reached"]), None) if missions else None,
        # the boss only counts while it is here AND the player still has a hit left
        "boss": {"name": boss.name, "hits_left": boss_hits_left} if boss is not None and boss_hits_left else None,
        "tournament_open": tournament.registration_open() and tournament.my_entry(user) is None,
        "festival": festival.theme() if festival.active_key() else None,
        "rule": events.rule_line(),
    }


def collectable_count(st: dict) -> int:
    """How many things «دریافت همه» would take right now."""
    return (len(st["pending"]) + st["dispatch_ready"] + int(st["event_daily"]) + int(st["story_ready"])
            + len(st["free_boxes"]) + int(st["wheel"]) + st["chests_ready"] + st["boxes_ready"])


def waiting_count(st: dict) -> int:
    """The number on the menu button: everything collectable plus what is live right now."""
    return collectable_count(st) + int(bool(st["boss"])) + int(st["tournament_open"])


def _loot_roll(contents: dict) -> dict | None:
    """A chest/box result → the entry game.media.composite_lootbox_batch_image draws."""
    if contents.get("creature") is not None:
        return {"kind": "creature", "creature": contents["creature"], "rarity": contents.get("rarity", "")}
    if contents.get("item") is not None:
        return {"kind": "equipment", "item": contents["item"], "rarity": contents.get("rarity", "")}
    return None


def _prize_name(contents: dict) -> str:
    rarity = constants.RARITY_LABELS.get(contents.get("rarity"), "")
    if contents.get("creature") is not None:
        return f"{rarity} {contents['creature'].name}".strip()
    if contents.get("item") is not None:
        return f"{rarity} «{contents['item'].name}»".strip()
    return ""


def collect_all(user: User) -> dict:
    """Take everything that is ready. Each part runs in its own transaction, so one failing
    never undoes the others. Returns:
      totals   – gold / DNA / diamonds gained in all of it
      sections – [(title, [lines])] for the result text, in a fixed order
      rolls    – creatures/items won, for the collage image
      missions – missions completed along the way
    """
    from game import daily, dispatch, events, story, wheel
    from game.arena_chests import advance_user_chests, open_chest
    from game.buildings import collect, main_hall_level
    from game.lootbox import can_claim_free_diamond_box, open_diamond_box

    totals = {"coins": 0, "dna_fragments": 0, "diamonds": 0}
    sections: list[tuple[str, list[str]]] = []
    rolls: list[dict] = []
    missions_done: list[dict] = []

    def add(contents: dict) -> None:
        totals["coins"] += int(contents.get("coins", 0) or 0)
        totals["dna_fragments"] += int(contents.get("dna", 0) or 0)
        totals["diamonds"] += int(contents.get("diamonds", 0) or 0)
        roll = _loot_roll(contents)
        if roll:
            rolls.append(roll)

    # ── buildings ──
    built: list[str] = []
    for building, _amount, _resource in _pending_buildings(user):
        try:
            amount, resource = collect(user, building)
        except GameError:
            continue
        totals[resource] = totals.get(resource, 0) + amount
        label = constants.BUILDING_LABELS.get(building.building_type, building.building_type)
        built.append(f"{label}: <code>+{amount:,}</code>")
        daily.record_action(user, "collect")
        missions_done += daily.check_missions(user, "collect")
    if built:
        sections.append(("ساختمان‌ها", built))
    base: list[str] = []

    # ── dispatch ──
    hall = main_hall_level(user)
    if hall >= 2:
        from bio_lab.repository import creature_name

        # one block per mission: who went, what it paid, and — spelled out — what the
        # «شگفتی» actually was (a bare «🍀 1 شگفتی» told the player nothing)
        sent: list[str] = []
        for res in dispatch.collect_all(user):
            reward = res["reward"]
            add(reward)
            _key, emoji, title, _flavor, _focus = dispatch.template(res["mission"])
            who = creature_name(res["creature"]) if res["creature"] is not None else "هیولا"
            sent.append(f"{emoji} <b>{title}</b> — {who}")
            sent.append("   " + dispatch.reward_text(reward, with_bonus=False))
            if dispatch.has_bonus(reward):
                sent.append("   🍀 شگفتی: " + dispatch.bonus_text(reward))
            if res["levels"]:
                sent.append(f"   ⬆️ {who} به سطح <code>{res['creature'].level}</code> رسید")
        if sent:
            sections.append(("مأموریت‌های اعزامی", sent))

    # ── event daily + story ──
    if hall >= EVENTS_HALL_REQ:
        reward = events.claim_daily(user)
        if reward:
            add(reward)
            base.append(f"🎁 جایزه‌ی روزانه‌ی رویداد: {events.reward_text(reward)}")
    with transaction.atomic():
        res = story.claim_active_quest(user)
    if res.get("success"):
        add(res["claimed_quest"]["reward"])
        base.append(f"🎯 «{res['claimed_quest']['title']}»: {res['reward_text']}")
    if base:
        sections.append(("جایزه‌ها", base))

    # ── wheel ──
    try:
        prize = wheel.spin(user)
    except GameError:
        prize = None
    if prize is not None:
        if prize.get("kind") in ("coins", "dna", "diamonds"):
            key = {"coins": "coins", "dna": "dna_fragments", "diamonds": "diamonds"}[prize["kind"]]
            totals[key] += int(prize.get("amount", 0))
        elif prize.get("kind") == "jackpot":
            add(prize)
        elif prize.get("kind") == "creature" and prize.get("creature_id"):
            from bio_lab.models import Creature

            creature = Creature.objects.filter(id=prize["creature_id"]).first()
            if creature is not None:
                rolls.append({"kind": "creature", "creature": creature, "rarity": prize.get("rarity", "")})
        missions_done += prize.get("missions", [])
        sections.append(("گردونه", [f"🎡 {prize['label']}"]))
    # what each chest/box held besides its creature or item
    def _extras(contents: dict) -> str:
        parts = []
        if contents.get("coins"):
            parts.append(f"<code>+{int(contents['coins']):,}</code> طلا")
        if contents.get("dna"):
            parts.append(f"<code>+{int(contents['dna']):,}</code> DNA")
        if contents.get("diamonds"):
            parts.append(f"<code>+{int(contents['diamonds'])}</code> الماس")
        return (" · " + " · ".join(parts)) if parts else ""


    # ── boxes and chests ──
    opened: list[str] = []
    for tier in ("bronze", "silver"):
        if not can_claim_free_diamond_box(user, tier):
            continue
        try:
            box = open_diamond_box(user, tier, require_free=True)
        except GameError:
            continue
        add(box)
        emoji, name = _BOX_NAMES[tier]
        opened.append(f"{emoji} {name}: {_prize_name(box)}")

    with transaction.atomic():
        advance_user_chests(user)
    for chest_id in list(ArenaChest.objects.filter(user=user, status="ready").values_list("id", flat=True)):
        try:
            chest = open_chest(user, chest_id)
        except GameError:
            continue
        add(chest)
        opened.append(f"{chest['emoji']} {chest['name']}: {_prize_name(chest)}{_extras(chest)}")

    for index in _reached_boxes(user)[0]:
        try:
            box = daily.claim_box(user, index)
        except GameError:
            continue
        add(box)
        opened.append(f"{box['emoji']} باکس مأموریت {index}: {_prize_name(box)}{_extras(box)}")
    if opened:
        sections.append(("باکس‌ها و جعبه‌ها", opened))

    return {"totals": totals, "sections": sections, "rolls": rolls, "missions": missions_done}

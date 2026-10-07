"""The base: buildings (collect / build / upgrade / speed-up / workers) and the research lab.

A thin adapter over game.buildings, game.workers and game.research — the same calls, in
the same order, as bot/handlers/buildings.py and bot/handlers/research.py. Finished
upgrades are applied lazily at read time exactly like the bot does (active_upgrades →
check_and_apply_upgrade, research.check_and_apply), so opening a screen after a timer
ran out is what completes the job.

Every action answers with the screen it was sent from (`view`: "list" | "detail" in the
body → the same payload as the GET), so the app redraws from the POST and never needs a
second request. Numbers that are the same for every row (the main-hall cap, builder slots,
lab level, worker counts) are worked out once per request, not once per building.
"""

from django.db import transaction
from django.utils import timezone

from django.db.models import Count

from bio_lab.models import Building, CreatureAssignment, User
from bio_lab.repository import lock_row
from game import buildings as B
from game import constants, research, workers
from game.daily import check_missions, record_action
from telgame_site.mini.core import (GameError, asset_img, clean, creature_list, endpoint, need_int,
                                    need_str, own_creature)

_RES = {"coins": "coins", "dna_fragments": "dna", "diamonds": "diamonds"}

# What the next main-hall level opens — the bullet list of the bot's main-hall card
# (bot/handlers/buildings.py keeps it inside a function, so it can't be imported).
_HALL_UNLOCKS = {
    2: ["تالار ادغام و غار هیولا", "ساخت آزمایشگاه DNA و تالار تجارت", "دسترسی به پاس ماهانه و صرافی طلا/DNA",
        "افزایش سقف ترکیب به ۲ ستاره"],
    3: ["ساخت و بازگشایی آهنگری تجهیزات", "چیدمان تیم ۳ نفره مبارزات", "ساخت معدن جمع‌کننده الماس",
        "ورود به لیگ رتبه‌بندی و رویدادها", "فروشگاه سپر و مبادله تجهیزات", "افزایش سقف ترکیب به ۳ ستاره"],
    4: ["نبردهای کمپین و دانجن داستانی", "دسترسی به حراجی بازار سیاه", "گردونه و بازی‌های کازینو",
        "کسب عناوین افتخاری و رتبه‌بندی خزانه", "افزایش سقف ترکیب به ۴ ستاره"],
    5: ["صعود به ۱۰۰ طبقه برج موگن", "ساخت آزمایشگاه تحقیقات ژنتیک", "شرکت در لیگ اتحادها و رتبه‌بندی رید",
        "گردونه بنر ویژه و شاپ آیتم‌های خاص", "افزایش سقف ترکیب به ۵ ستاره (نهایی)"],
}


# ── helpers ───────────────────────────────────────────────────────────────────
def _own_building(user: User, building_id: int) -> Building:
    building = Building.objects.filter(id=building_id, owner=user).first()
    if building is None:
        raise GameError("این ساختمون پیدا نشد.")
    return building


def _left(when) -> int:
    return max(0, int((when - timezone.now()).total_seconds() + 0.999))


def _building_img(building: Building) -> str | None:
    from game.media import get_building_image_path

    return asset_img(get_building_image_path(building.building_type, building.level))


def _shared(user: User, rows, busy: int) -> dict:
    """What every building row needs and is the same for all of them — asked ONCE."""
    other = next((b.building_type for b in rows if b.building_type != constants.MAIN_BUILDING), None)
    return {
        "busy": busy, "slots": B.builder_slots(user), "lab_level": _lab_level(user),
        # `max_level_for` gives every non-main building the same ceiling (the main hall's level)
        "cap_main": B.max_level_for(user, constants.MAIN_BUILDING),
        "cap_other": B.max_level_for(user, other) if other else constants.BUILDING_MAX_LEVEL,
        "staff": dict(CreatureAssignment.objects.filter(building__owner=user)
                      .values_list("building_id").annotate(n=Count("id"))),
    }


def _building_dict(user: User, b: Building, upgrade, sh: dict) -> dict:
    """One building as the list card AND the detail header need it. Every number comes
    from the function the bot's screen uses for it."""
    btype = b.building_type
    main = btype == constants.MAIN_BUILDING
    cap = sh["cap_main"] if main else sh["cap_other"]
    # a building that stands has passed its gate; only an unbuilt one can still be locked
    unlocked = True if b.level > 0 else B.is_unlocked(user, btype)
    if upgrade is not None:
        state = "upgrading"
    elif b.level == 0 and not unlocked:
        state = "locked"
    elif b.level >= constants.BUILDING_MAX_LEVEL:
        state = "max"
    elif b.level >= cap:
        state = "capped"
    elif sh["busy"] >= sh["slots"]:
        state = "busy"  # every builder is working on another building
    else:
        state = "ready"
    out = {
        "id": b.id, "type": btype, "label": clean(constants.BUILDING_LABELS[btype]),
        "level": b.level, "max_level": constants.BUILDING_MAX_LEVEL, "cap": cap,
        "main": main,
        "unlocked": unlocked, "unlock_hall": B.unlock_level_for(btype),
        "state": state, "img": _building_img(b),
        "desc": clean(constants.BUILDING_DESCRIPTIONS[btype]),
        "produces": False, "upgrade": None, "next": None,
    }
    if upgrade is not None:
        out["upgrade"] = {
            "target": upgrade.target_level, "left": _left(upgrade.finishes_at),
            "total": B.upgrade_seconds(b),
            "finish_price": B.diamond_finish_price(upgrade), "refund": B.cancel_refund(upgrade),
        }
    elif b.level < constants.BUILDING_MAX_LEVEL:
        cost, _minutes = B.upgrade_cost_and_minutes(b)
        target = b.level + 1
        lab_req = constants.BUILDING_LEVEL_LAB_REQ.get(target, 0)
        out["next"] = {"target": target, "cost": cost, "seconds": B.upgrade_seconds(b),
                       "lab_req": lab_req, "lab_ok": sh["lab_level"] >= lab_req, "afford": user.coins >= cost}
    if B.produces(btype):
        out["produces"] = True
        out["resource"] = _RES[constants.BUILDING_PRODUCTION[btype]["resource"]]
        if b.level > 0:
            staff = sh["staff"].get(b.id, 0)
            # nobody stationed → the bonus is 0 by definition (worker_bonus sums over the staff)
            bonus = workers.worker_bonus(b) if staff else 0.0
            out.update({
                "rate": round(B.production_rate(b, bonus), 1),
                "base_rate": round(B.production_rate(b, 0.0), 2),
                "pending": B.pending_amount(b), "store_cap": B.storage_cap(b, bonus),
                "bonus": round(bonus, 4),
                "workers": staff, "slots": workers.worker_slots(b),
            })
    return out


def _cards(user: User) -> list[dict]:
    return [{"minutes": c.minutes, "count": c.count, "label": clean(constants.speedup_label(c.minutes))}
            for c in B.list_speedup_cards(user)]


def _builders(user: User, busy: int) -> dict:
    slots = B.builder_slots(user)
    return {"busy": busy, "slots": slots, "max": constants.MAX_BUILDER_SLOTS,
            "second_cost": constants.SECOND_BUILDER_DIAMONDS if slots < constants.MAX_BUILDER_SLOTS else None}


def _mission_dicts(completed: list[dict]) -> list[dict]:
    out = []
    for m in completed or []:
        extra = []
        if m.get("capsule"):
            tier, count = m["capsule"]
            extra.append(f"{count} {clean(constants.XP_CAPSULES[tier]['label'])}")
        if m.get("speedup"):
            extra.append(f"کارت سرعت {constants.speedup_plain_label(m['speedup'])}")
        if m.get("points"):
            extra.append(f"{m['points']} امتیاز هفتگی")
        out.append({"label": clean(m.get("label", "")), "coins": m.get("coins", 0), "dna": m.get("dna", 0),
                    "diamonds": m.get("diamonds", 0), "extra": extra})
    return out


def _lab_level(user: User) -> int:
    from game import lab

    return lab.lab_level(user)


# ── buildings: list ───────────────────────────────────────────────────────────
def _list(user: User) -> dict:
    research.warm(user)  # worker bonuses read creature power → research buffs must be fresh
    upgrades = {u.building_id: u for u in B.active_upgrades(user)}  # lazily finishes due upgrades first
    rows = B.get_or_create_buildings(user)
    sh = _shared(user, rows, len(upgrades))
    # main hall first, then the rest — it's the gate everything else waits on
    rows.sort(key=lambda b: (b.building_type != constants.MAIN_BUILDING, b.building_type))
    out = [_building_dict(user, b, upgrades.get(b.id), sh) for b in rows]
    waiting = {"coins": 0, "dna": 0, "diamonds": 0}
    for d in out:
        if d.get("pending"):
            waiting[d["resource"]] += d["pending"]
    plunder = None
    if (user.plundered_alert_gold or 0) > 0 or (user.plundered_alert_dna or 0) > 0:
        plunder = {"coins": user.plundered_alert_gold or 0, "dna": user.plundered_alert_dna or 0}
    hall = next((d["level"] for d in out if d["main"]), 1)
    return {
        "hall_level": max(1, hall), "max_level": constants.BUILDING_MAX_LEVEL,
        "lab_level": sh["lab_level"], "builders": _builders(user, len(upgrades)),
        "buildings": out, "waiting": waiting, "cards": _cards(user), "plunder": plunder,
    }


@endpoint()
def buildings(request, user):
    return _list(user)


def _view(user: User, data: dict, b: Building | None = None) -> dict:
    """The screen an action was sent from, as fresh as a GET (the action already committed)."""
    user = User.objects.get(id=user.id)  # the action changed coins / diamonds / builder slots
    if data.get("view") == "list":
        return {"list": _list(user)}
    if data.get("view") == "detail" and b is not None:
        return {"detail": _detail(user, Building.objects.get(id=b.id))}
    return {}


@endpoint("POST")
def plunder_seen(request, user, data):
    """The bot shows the «معدنت غارت شد» notice once and clears it; the app clears it when
    the notice has been displayed."""
    with transaction.atomic():
        lock_row(user)
        user.plundered_alert_gold = 0
        user.plundered_alert_dna = 0
        user.save(update_fields=["plundered_alert_gold", "plundered_alert_dna"])
    return {"ok": True}


# ── buildings: detail ─────────────────────────────────────────────────────────
def _detail(user: User, building: Building) -> dict:
    """Mirror of the bot's _detail_view."""
    research.warm(user)
    B.active_upgrade(user)  # finish any due upgrades before we count/inspect
    building.refresh_from_db()
    busy = B.active_upgrade_count(user)
    d = _building_dict(user, building, B.upgrade_for_building(user, building), _shared(user, [building], busy))
    btype = building.building_type
    d["benefit"] = clean(constants.BUILDING_UPGRADE_BENEFIT.get(btype, ""))
    d["note"] = clean(constants.BUILDING_RULE_NOTE.get(btype, ""))
    extras = []
    if btype == "dispatch_hq":
        from game import dispatch

        extras += [clean(line) for line in dispatch.hq_perks_text(building.level).split("\n")]
    if btype == "blacksmith" and building.level > 0:
        from game import blacksmith

        d["equip_cap"] = blacksmith.equipment_cap(user)
    if btype == constants.MAIN_BUILDING and building.level > 0:
        extras.append("سقف ستاره‌ی هیولاها = سطح «تالار ادغام» (که خودش از سطح این تالار بالاتر نمی‌ره).")
        d["unlocks"] = {"level": building.level + 1, "items": _HALL_UNLOCKS.get(building.level + 1, [])}
    d["extras"] = [e for e in extras if e]

    out = {"building": d, "builders": _builders(user, busy), "cards": _cards(user),
           "workers": [], "influence": {}}
    if d["produces"] and building.level > 0:
        from game.equipment import equipped_items_map

        staff = workers.assigned_creatures(building)
        free = workers.free_creatures(user) if len(staff) < d["slots"] else []
        gear = equipped_items_map([*staff, *free])
        inf = {c.id: round(workers.creature_mine_influence(c, building, gear[c.pk]), 4) for c in [*staff, *free]}
        rows = creature_list(user, staff)
        for row in rows:
            row["influence"] = inf.get(row["id"], 0)
        out["workers"] = rows
        # the bonus each idle creature WOULD give here (shown in the picker, like the bot's list)
        out["influence"] = {str(c.id): inf[c.id] for c in free}
        out["bonus_cap"] = constants.BUILDING_PRODUCTION[btype].get("worker_bonus_cap")
    return out


@endpoint()
def building(request, user):
    try:
        building_id = int(request.GET.get("id", ""))
    except ValueError:
        raise GameError("این ساختمون پیدا نشد.")
    return _detail(user, _own_building(user, building_id))


# ── buildings: actions ────────────────────────────────────────────────────────
def _collect_one(user: User, b: Building) -> tuple[int, str, list[dict]]:
    amount, resource = B.collect(user, b)
    record_action(user, "collect")
    return amount, _RES[resource], check_missions(user, "collect")


@endpoint("POST")
def collect(request, user, data):
    b = _own_building(user, need_int(data, "id"))
    amount, resource, missions = _collect_one(user, b)
    return {"amount": amount, "resource": resource, "missions": _mission_dicts(missions), **_view(user, data, b)}


@endpoint("POST")
def collect_all(request, user, data):
    """«جمع‌آوری همه»: the same collect, once per producer that has something waiting."""
    got = {"coins": 0, "dna": 0, "diamonds": 0}
    missions: list[dict] = []
    for b in B.get_or_create_buildings(user):
        if b.level <= 0 or not B.produces(b.building_type) or B.pending_amount(b) <= 0:
            continue
        try:
            amount, resource, done = _collect_one(user, b)
        except GameError:
            continue  # emptied by a parallel tap
        got[resource] += amount
        missions += done
    if not any(got.values()):
        raise GameError("چیزی برای جمع‌آوری نیست، بعداً دوباره سر بزن.")
    return {"got": got, "missions": _mission_dicts(missions), **_view(user, data)}


@endpoint("POST")
def upgrade(request, user, data):
    b = _own_building(user, need_int(data, "id"))
    built = b.level > 0
    job = B.start_upgrade(user, b)
    return {"target": job.target_level, "left": _left(job.finishes_at), "construct": not built, **_view(user, data, b)}


@endpoint("POST")
def speedup(request, user, data):
    b = _own_building(user, need_int(data, "id"))
    minutes = need_int(data, "minutes")
    # «همه» in the bot = a big count; the game clamps it to what is owned and what is needed
    count = 9999 if data.get("all") else 1
    with transaction.atomic():
        lock_row(user)  # two parallel taps must not both spend the same last card
        job = B.upgrade_for_building(user, b)
        due = job is not None and job.finishes_at <= timezone.now()
        B.check_and_apply_upgrade(user)
        if due:
            # the timer ran out while the sheet was open: the upgrade is simply applied and
            # no card is spent (the game would otherwise burn one on a finished job)
            left, completed, used = None, True, 0
        else:
            left, completed, used = B.apply_speedup_bulk(user, minutes, count, b.id)
    return {"completed": completed, "used": used, "cards": _cards(user),
            "left": _left(left.finishes_at) if left is not None else 0,
            "finish_price": B.diamond_finish_price(left) if left is not None else 0,
            **(_view(user, data, b) if completed else {})}


@endpoint("POST")
def finish(request, user, data):
    """`price` = the diamonds the player just confirmed. The price only ever falls while the
    timer runs, so a different (lower) one is fine; a HIGHER one (the job changed under the
    sheet) is refused instead of charged."""
    b = _own_building(user, need_int(data, "id"))
    job = B.upgrade_for_building(user, b)
    seen = data.get("price")
    if job is not None and isinstance(seen, int) and job.finishes_at > timezone.now() and B.diamond_finish_price(job) > seen:
        raise GameError("قیمت اتمام فوری عوض شده؛ دوباره نگاه کن و تأیید کن.")
    done, cost = B.finish_with_diamonds(user, b.id)
    return {"cost": cost, "level": done.level, **_view(user, data, b)}


@endpoint("POST")
def cancel(request, user, data):
    b = _own_building(user, need_int(data, "id"))
    _building, refund = B.cancel_upgrade(user, b.id)
    return {"refund": refund, **_view(user, data, b)}


@endpoint("POST")
def buy_builder(request, user, data):
    B.buy_second_builder(user)
    b = Building.objects.filter(id=data.get("id"), owner=user).first() if isinstance(data.get("id"), int) else None
    return {"slots": constants.MAX_BUILDER_SLOTS, **_view(user, data, b)}


@endpoint("POST")
def assign(request, user, data):
    b = _own_building(user, need_int(data, "id"))
    creature = own_creature(user, need_int(data, "creature"))
    workers.assign(user, b, creature)
    return {"ok": True, **_view(user, data, b)}


@endpoint("POST")
def unassign(request, user, data):
    creature = own_creature(user, need_int(data, "creature"))
    b = workers.unassign(user, creature)
    return {"ok": True, **_view(user, data, b)}


# ── research lab ──────────────────────────────────────────────────────────────
def _research_hall_req() -> int:
    return B.unlock_level_for("research_lab")


def _assert_research_open(user: User) -> None:
    need = _research_hall_req()
    if B.main_hall_level(user) < need:
        raise GameError(f"پژوهش از سطح {need} تالار مِهر باز می‌شه.")


def _research(user: User) -> dict:
    research.check_and_apply(user)
    lab_level = research.research_cap(user)
    levels = research.research_levels(user)
    running = {u.key: u for u in research.active_upgrades(user)}
    lab = Building.objects.filter(owner=user, building_type="research_lab").first()
    tracks = []
    for key, d in research.RESEARCH_DEFS.items():
        level, up, per = levels.get(key, 0), running.get(key), d["per_level"]
        if up is not None:
            state = "running"
        elif level >= constants.RESEARCH_MAX_LEVEL:
            state = "max"
        elif level >= lab_level:
            state = "capped"  # the lab building must be upgraded first
        elif running:
            state = "busy"  # only one research at a time
        else:
            state = "ready"
        row = {
            "key": key, "label": clean(d["label"]), "desc": clean(research.RESEARCH_DESC[key]),
            "kind": d["kind"], "element": d.get("element"),
            "level": level, "max_level": constants.RESEARCH_MAX_LEVEL,
            "effect": round(level * per * 100), "per_level": round(per * 100),
            "state": state, "upgrade": None, "next": None,
        }
        if up is not None:
            row["upgrade"] = {"target": up.target_level, "left": _left(up.finishes_at),
                              "total": constants.research_seconds(up.target_level, key),
                              "finish_price": research.diamond_finish_price(up)}
        elif level < constants.RESEARCH_MAX_LEVEL:
            target = level + 1
            gold, dna = research.next_cost(target, key)
            row["next"] = {"target": target, "coins": gold, "dna": dna,
                           "seconds": constants.research_seconds(target, key),
                           "effect": round(target * per * 100),
                           "afford": user.coins >= gold and user.dna_fragments >= dna}
        tracks.append(row)
    return {
        "lab_level": lab_level, "max_level": constants.BUILDING_MAX_LEVEL, "hall_req": _research_hall_req(),
        "lab_id": lab.id if lab else None, "img": _building_img(lab) if lab else None,
        "running": next(iter(running), None), "tracks": tracks,
        "coins": user.coins, "dna": user.dna_fragments,
    }


@endpoint()
def research_panel(request, user):
    _assert_research_open(user)
    return _research(user)


@endpoint("POST")
def research_start(request, user, data):
    _assert_research_open(user)
    key = need_str(data, "key", choices=research.RESEARCH_KEYS)
    job = research.start_research(user, key)
    return {"target": job.target_level, "left": _left(job.finishes_at), "panel": _research(User.objects.get(id=user.id))}


@endpoint("POST")
def research_finish(request, user, data):
    """`game.research.finish_with_diamonds` charges even when the timer has already run out,
    so a due job is applied here for free instead (like the building version does itself).
    `price` = what the player confirmed; a higher price than that is refused, not charged."""
    _assert_research_open(user)
    key = need_str(data, "key", choices=research.RESEARCH_KEYS)
    job = research.upgrade_for(user, key)
    if job is not None and job.finishes_at <= timezone.now():
        research.check_and_apply(user)  # the timer ran out while the confirm was open — free
        cost = 0
    else:
        seen = data.get("price")
        if job is not None and isinstance(seen, int) and research.diamond_finish_price(job) > seen:
            raise GameError("قیمت اتمام فوری عوض شده؛ دوباره نگاه کن و تأیید کن.")
        cost = research.finish_with_diamonds(user, key)
    return {"cost": cost, "panel": _research(User.objects.get(id=user.id))}


routes = [
    ("", buildings),
    ("building/", building),
    ("plunder_seen/", plunder_seen),
    ("collect/", collect),
    ("collect_all/", collect_all),
    ("upgrade/", upgrade),
    ("speedup/", speedup),
    ("finish/", finish),
    ("cancel/", cancel),
    ("builder/", buy_builder),
    ("assign/", assign),
    ("unassign/", unassign),
    ("research/", research_panel),
    ("research/start/", research_start),
    ("research/finish/", research_finish),
]

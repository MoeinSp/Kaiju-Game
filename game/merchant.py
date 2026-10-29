"""«تابلوی سفارش تاجر» — a Hay Day–style order board.

Three merchants wait on the board at once, each with an order of up to two things the player
can make or spare (serums, a spare creature of some rarity, unworn gear — never raw DNA or gold,
the exchange already trades those; with nothing to sell, no merchant comes). Delivering
pays gold and lab XP, sometimes an extra (a speed card, a dungeon key, a chest, legendary
gear) and rarely a few diamonds. Delivering or turning an order down empties its slot for
an hour; then a new merchant arrives. One harder special order a day pays more.

Fairness rules:
  - a creature is only asked for when the player has a spare one of that rarity, and the
    one handed over is always the weakest spare: never the active creature, a team or
    defence-team member, a dungeon/tournament fighter, a busy one, a 2★+ or a shadow form;
  - only unworn gear is ever handed over;
  - at most DAILY_ORDERS normal deliveries and DIAMOND_CAP diamonds a day.
"""

from __future__ import annotations

import datetime
import random

from django.db import transaction
from django.utils import timezone

from bio_lab.models import Creature, Equipment, MerchantBoard, User
from game import constants
from game.creature import GameError

SLOTS = 3
REFILL = datetime.timedelta(hours=1)
IDLE_RETRY = 10 * 60   # seconds: with nothing to sell, an empty slot looks again this often
DAILY_ORDERS = 12
DIAMOND_CAP = 10
UNLOCK_HALL = 2

MERCHANTS = {
    "alchemist": "کیمیاگر پیر",
    "captain": "ناخدای ابرکشتی",
    "cyborg": "مهندس سایبورگ",
    "huntress": "شکارچی جنگل",
    "witch": "ساحره‌ی بازار شب",
    "monk": "راهب کوهستان",
}

# value of one unit, in «hunts' worth» of loot
CREATURE_VALUE = {"common": 3, "rare": 6, "epic": 12, "legendary": 25, "mythic": 50}
GEAR_VALUE = {"common": 0.5, "rare": 1, "epic": 2, "legendary": 4, "mythic": 8}
SERUM_VALUE = 2.5
# The GOLD an order pays is priced per item, between what the game already pays and charges for
# that thing, so an order beats selling but never beats buying (no buy-here-sell-there loop).
GOLD_PER_DNA = 15                         # a serum's worth = the DNA it took to brew × this
CREATURE_GOLD = {"common": 2000, "rare": 6000, "epic": 15000}    # a crate costs far more per creature
GEAR_GOLD = {r: round(g * 2.5) for r, g in constants.EQUIP_RECYCLE_GOLD.items()}   # recycle pays ×1
ASKED_RARITIES = ("common", "rare", "epic")   # nobody is asked to give up a legendary or mythic
RARITY_FA = {"common": "معمولی", "rare": "نایاب", "epic": "حماسی", "legendary": "افسانه‌ای", "mythic": "اساطیری"}
CHEST_FA = {"golden": "جعبه‌ی طلایی", "magical": "جعبه‌ی جادویی"}


# ── helpers ───────────────────────────────────────────────────────────────────
def _today() -> str:
    from game.daily import today_str

    return today_str()


def _nice(n: float) -> int:
    """Round to a number that reads well on a card: 1,234 → 1,250 · 48,310 → 48,500."""
    n = max(1, int(round(n)))
    step = 10 if n < 1000 else 50 if n < 10000 else 500 if n < 100000 else 5000
    return max(step, int(round(n / step)) * step)


def unlocked(user: User) -> bool:
    from game.buildings import building_level

    return building_level(user, constants.MAIN_BUILDING) >= UNLOCK_HALL


def spare_creatures(user: User, rarity: str | None = None) -> list[Creature]:
    """Creatures the player could hand over without losing anything they use, weakest first."""
    from bio_lab.models import DefenseTeam, DungeonRun, InteractiveBattle, Team, TournamentEntry
    from game.workers import busy_creature_ids

    keep = set(busy_creature_ids(user))
    for model in (Team, DefenseTeam):
        for row in model.objects.filter(owner=user).values("slot1_id", "slot2_id", "slot3_id"):
            keep.update(v for v in row.values() if v)
    for run in DungeonRun.objects.filter(user=user, status=DungeonRun.ACTIVE).only("state"):
        keep.update(m["id"] for m in (run.state or {}).get("team", []))
    keep.update(TournamentEntry.objects.filter(user=user, place=0).exclude(creature=None).values_list("creature_id", flat=True))
    keep.update(InteractiveBattle.objects.filter(player_a=user).values_list("creature_a_id", flat=True))
    keep.update(InteractiveBattle.objects.filter(player_b=user).values_list("creature_b_id", flat=True))
    qs = Creature.objects.filter(owner=user, is_active=False, star_level=1, shadow_level=0).exclude(id__in=keep)
    if rarity:
        qs = qs.filter(rarity=rarity)
    return list(qs.order_by("level", "xp", "id"))


def spare_gear(user: User, rarity: str) -> list[Equipment]:
    return list(Equipment.objects.filter(owner=user, equipped_on__isnull=True, rarity=rarity).order_by("level", "id"))


def _serum_kinds(user: User) -> list[str]:
    from game import serums

    level = serums.lab_level(user)
    return [k for k in serums.ORDER if level >= serums.KINDS[k]["unlock"]] if level else []


# ── making orders ─────────────────────────────────────────────────────────────
def _context(user: User) -> dict:
    from collections import Counter

    spare = Counter(c.rarity for c in spare_creatures(user) if c.rarity in ASKED_RARITIES)
    gear = Counter(Equipment.objects.filter(owner=user, equipped_on__isnull=True).values_list("rarity", flat=True))
    from game.serums import brew_cost

    return {"serums": _serum_kinds(user), "spare": spare, "gear": gear, "brew": brew_cost(user)}


def _pool(ctx: dict) -> dict[str, float]:
    """What merchants may ask this player for (weights); empty = nothing to sell yet."""
    pool = {}
    if ctx["serums"]:
        pool["serum"] = 2
    if ctx["spare"]:
        pool["creature"] = 1.5
    if ctx["gear"]:
        pool["gear"] = 2
    return pool


def _req(kind: str, ctx: dict, big: bool) -> tuple[dict, float, int]:
    """One requirement: (requirement, value in hunts' worth — sizes the XP, gold it is worth)."""
    if kind == "serum":
        n = random.randint(2, 3) if big else random.randint(1, 2)
        return {"t": "serum", "k": random.choice(ctx["serums"]), "n": n}, n * SERUM_VALUE, n * ctx["brew"] * GOLD_PER_DNA
    if kind == "creature":
        rarity = random.choice(sorted(ctx["spare"]))
        return {"t": "creature", "r": rarity, "n": 1}, CREATURE_VALUE.get(rarity, 3), CREATURE_GOLD.get(rarity, 2000)
    rarities = sorted(ctx["gear"], key=lambda r: constants.RARITY_ORDER.index(r) if r in constants.RARITY_ORDER else 0)
    rarity = random.choice(rarities[:3])
    n = max(1, min(3 if big else 2, ctx["gear"][rarity]))
    return {"t": "gear", "r": rarity, "n": n}, n * GEAR_VALUE.get(rarity, 1), n * GEAR_GOLD.get(rarity, 100)


def _extra(user: User, big: bool) -> dict | None:
    from game import dungeon

    pool = [({"t": "card", "m": 60 if big else random.choice((15, 30))}, 40),
            ({"t": "chest", "tier": "magical" if big else "golden"}, 25),
            ({"t": "gear", "r": "legendary"}, 30 if big else 10)]
    if big and dungeon.unlocked(user):   # a dungeon key only in the daily special order (≤1 a day)
        pool.append(({"t": "key", "n": 1}, 25))
    items, weights = zip(*pool)
    return dict(random.choices(items, weights)[0])


def make_order(user: User, seq: int, special: bool = False, ctx: dict | None = None) -> dict | None:
    """A new order, or None when the player has nothing a merchant buys."""
    ctx = ctx or _context(user)
    pool = _pool(ctx)
    if not pool:
        return None
    picks: list[str] = []
    for _ in range(min(3 if special else 2, len(pool))):
        keys = [k for k in pool if k not in picks]
        picks.append(random.choices(keys, [pool[k] for k in keys])[0])
    reqs, value, worth = [], 0.0, 0
    for kind in picks:
        r, v, g = _req(kind, ctx, special)
        reqs.append(r)
        value += v
        worth += g
    coins = _nice(worth * (1.3 if special else 1.0))
    xp = min(600 if special else 300, max(20, round(value * (12 if special else 6))))
    extra = _extra(user, special) if special or random.random() < 0.35 else None
    diamonds = 5 if special else (random.randint(2, 4) if random.random() < 0.08 else 0)
    return {"id": seq, "who": random.choice(list(MERCHANTS)), "special": special, "reqs": reqs,
            "reward": {"coins": coins, "xp": xp, "extra": extra, "diamonds": diamonds}}


# ── the board ─────────────────────────────────────────────────────────────────
def _board(user: User, lock: bool = False) -> MerchantBoard:
    qs = MerchantBoard.objects.select_for_update() if lock else MerchantBoard.objects
    row, _ = qs.get_or_create(user_id=user.id)
    return row


def _refresh(user: User, row: MerchantBoard) -> bool:
    """Fill empty or due slots, roll a new day's counters and special order."""
    changed, now, today = False, timezone.now().timestamp(), _today()
    ctx = None
    slots = list(row.slots or [])
    while len(slots) < SLOTS:
        slots.append({"wait_until": 0})
    for i, s in enumerate(slots):
        if "reqs" not in s and s.get("wait_until", 0) <= now:
            ctx = ctx or _context(user)
            order = make_order(user, row.seq + 1, ctx=ctx)
            if order is None:   # nothing to sell: look again in a few minutes
                slots[i] = {"wait_until": now + IDLE_RETRY}
            else:
                row.seq += 1
                slots[i] = order
            changed = True
    if row.day != today:
        row.day, row.done_today, row.diamonds_today = today, 0, 0
        changed = True
    if row.special_day != today:
        ctx = ctx or _context(user)
        special = make_order(user, row.seq + 1, special=True, ctx=ctx)
        if special is not None:   # else tried again on the next visit today
            row.seq += 1
            row.special, row.special_day = special, today
            changed = True
    if changed:
        row.slots = slots
    return changed


def _holdings(user: User) -> dict:
    """What the player could hand over, looked up once per board view."""
    from collections import Counter

    from bio_lab.models import SerumLab
    from game import serums

    row = SerumLab.objects.filter(user_id=user.id).first()
    if row is not None:
        serums._advance(row, serums.lab_level(user), timezone.now())   # counted, not saved
    return {"spare": Counter(c.rarity for c in spare_creatures(user)),
            "gear": Counter(Equipment.objects.filter(owner=user, equipped_on__isnull=True).values_list("rarity", flat=True)),
            "serum": dict(row.stock or {}) if row else {}}


def have(user: User, req: dict, held: dict | None = None) -> int:
    t = req["t"]
    held = held or _holdings(user)
    if t == "serum":
        return int(held["serum"].get(req["k"], 0))
    if t == "creature":
        return held["spare"][req["r"]]
    return held["gear"][req["r"]]


def req_label(req: dict) -> str:
    from game import serums

    t, n = req["t"], req["n"]
    if t == "serum":
        return f"{n} سرم {serums.KINDS[req['k']]['label']}"
    if t == "creature":
        return f"{n} هیولای {RARITY_FA.get(req['r'], req['r'])} (یک ستاره)"
    return f"{n} تجهیزات {RARITY_FA.get(req['r'], req['r'])} (پوشیده‌نشده)"


def extra_label(extra: dict | None) -> str:
    if not extra:
        return ""
    t = extra["t"]
    if t == "card":
        return f"کارت سرعت {constants.SPEEDUP_PLAIN_LABELS.get(extra['m'], str(extra['m']) + ' دقیقه‌ای')}"
    if t == "chest":
        return CHEST_FA.get(extra["tier"], "جعبه")
    if t == "key":
        return "کلید سیاه‌چال"
    return "تجهیزات افسانه‌ای"


def _order_view(user: User, order: dict, slot, held: dict) -> dict:
    reqs = []
    for r in order["reqs"]:
        h = have(user, r, held)
        reqs.append({**r, "label": req_label(r), "have": h, "ok": h >= r["n"]})
    rw = order["reward"]
    return {"slot": slot, "id": order["id"], "who": order["who"], "name": MERCHANTS.get(order["who"], "تاجر"),
            "special": order.get("special", False), "reqs": reqs, "ready": all(r["ok"] for r in reqs),
            "coins": rw["coins"], "xp": rw["xp"], "diamonds": rw["diamonds"],
            "extra": rw.get("extra"), "extra_label": extra_label(rw.get("extra")),
            "gives_creature": any(r["t"] == "creature" for r in order["reqs"])}


def state(user: User) -> dict:
    if not unlocked(user):
        return {"unlocked": False, "unlock_hall": UNLOCK_HALL}
    with transaction.atomic():
        row = _board(user, lock=True)
        if _refresh(user, row):
            row.save()
    user.refresh_from_db(fields=["coins", "dna_fragments"])
    now = timezone.now().timestamp()
    held = _holdings(user)
    slots = []
    for i, s in enumerate(row.slots):
        if "reqs" in s:
            slots.append(_order_view(user, s, i, held))
        else:
            slots.append({"slot": i, "wait": max(0, int(s.get("wait_until", 0) - now))})
    tomorrow = (timezone.localtime() + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    # no order anywhere and nothing a merchant buys → say why the board is empty
    idle = not row.special and not any("reqs" in s for s in row.slots) and not _pool(_context(user))
    return {
        "unlocked": True, "slots": slots, "idle": idle,
        "special": _order_view(user, row.special, "s", held) if row.special else None,
        "special_in": max(0, int((tomorrow - timezone.localtime()).total_seconds())),
        "done_today": row.done_today, "daily": DAILY_ORDERS,
        "diamonds_today": row.diamonds_today, "diamond_cap": DIAMOND_CAP, "done_total": row.done_total,
    }


def _find(row: MerchantBoard, slot, order_id: int) -> dict:
    if slot == "s":
        order = row.special
    else:
        slot = int(slot)
        order = row.slots[slot] if 0 <= slot < len(row.slots or []) else None
    if not order or "reqs" not in order or order.get("id") != order_id:
        raise GameError("این سفارش دیگه روی تابلو نیست؛ تابلو رو دوباره باز کن.")
    return order


def preview_handover(user: User, slot, order_id: int) -> list[tuple[int, str]]:
    """(id, name) of the creature a delivery would hand over — shown before asking to confirm;
    deliver() is then given that id, so the creature that goes is the one the player saw."""
    row = _board(user)
    order = _find(row, slot, order_id)
    out = []
    for r in order["reqs"]:
        if r["t"] == "creature":
            out += [(c.id, f"{c.custom_name or c.name} · سطح {c.level}") for c in spare_creatures(user, r["r"])[: r["n"]]]
    return out


def deliver(user: User, slot, order_id: int, creature_ids: list[int] | None = None) -> dict:
    from bio_lab.repository import lock_row
    from game import serums
    from game.ledger import record_gain

    with transaction.atomic():
        user = lock_row(user) or User.objects.select_for_update().get(id=user.id)
        row = _board(user, lock=True)
        _refresh(user, row)
        order = _find(row, slot, order_id)
        special = slot == "s"
        if not special and row.done_today >= DAILY_ORDERS:
            raise GameError(f"امروز {DAILY_ORDERS} سفارش تحویل دادی؛ تاجرها فردا دوباره سفارش می‌گیرن.")
        for r in order["reqs"]:
            h = have(user, r)
            if h < r["n"]:
                raise GameError(f"برای «{req_label(r)}» کم داری ({h:,} از {r['n']:,}).")
        handed = []
        for r in order["reqs"]:
            t, n = r["t"], r["n"]
            if t == "serum":
                serums.hand_over(user, r["k"], n)
            elif t == "creature":
                spare = spare_creatures(user, r["r"])
                if creature_ids:   # the ones the player confirmed — still spare and of this rarity?
                    gone = [c for c in spare if c.id in set(creature_ids)][:n]
                    if len(gone) < n:
                        raise GameError("هیولایی که تأیید کردی دیگه قابل تحویل نیست؛ تابلو رو دوباره باز کن.")
                else:
                    gone = spare[:n]
                handed += [c.custom_name or c.name for c in gone]
                Equipment.objects.filter(equipped_on__in=gone).update(equipped_on=None)
                Creature.objects.filter(id__in=[c.id for c in gone]).delete()
            else:
                ids = [e.id for e in spare_gear(user, r["r"])[:n]]
                Equipment.objects.filter(id__in=ids, equipped_on__isnull=True).delete()
        rw = order["reward"]
        user.coins += rw["coins"]
        got = {"coins": rw["coins"], "xp": rw["xp"], "extra": None, "diamonds": 0}
        extra = rw.get("extra")
        if extra:
            got["extra"] = _grant_extra(user, row, extra)
        dia = min(rw["diamonds"], DIAMOND_CAP - row.diamonds_today)
        if dia > 0:
            user.diamonds += dia
            row.diamonds_today += dia
            got["diamonds"] = dia
        user.save(update_fields=["coins", "dna_fragments", "diamonds"])
        record_gain(user, "merchant", coins=rw["coins"], diamonds=dia)
        if special:
            row.special = None
        else:
            row.slots[int(slot)] = {"wait_until": (timezone.now() + REFILL).timestamp()}
            row.done_today += 1
        row.done_total += 1
        row.save()
    from game.lab import add_lab_xp

    got["levelup"] = add_lab_xp(user, rw["xp"])
    got["handed"] = handed
    got["name"] = MERCHANTS.get(order["who"], "تاجر")
    return got


def _grant_extra(user: User, row: MerchantBoard, extra: dict) -> str:
    """Grant an order's extra; returns what it turned into, for the result line."""
    t = extra["t"]
    if t == "card":
        from game.buildings import grant_speedup_card

        grant_speedup_card(user, extra["m"])
        return extra_label(extra)
    if t == "key":
        from game import dungeon

        prof = dungeon.profile(user)
        if prof.keys < dungeon.KEY_HARD_CAP:
            prof.keys += 1
            prof.save(update_fields=["keys"])
            return extra_label(extra)
        extra = {"t": "chest", "tier": "golden"}   # a full key ring: a chest instead
        t = "chest"
    if t == "chest":
        from game.arena_chests import grant_chest_contents

        tier = extra["tier"]
        if tier == "magical" and row.diamonds_today + 5 > DIAMOND_CAP:
            tier = "golden"   # a magical chest's diamonds count toward the daily cap
        res = grant_chest_contents(user, tier, user.cup, source="merchant")
        row.diamonds_today += res.get("diamonds", 0)
        bits = [f"{res['coins']:,} طلا", f"{res['dna']:,} DNA"]
        if res.get("creature"):
            bits.append(f"هیولای {res['creature'].name}")
        if res.get("item"):
            bits.append(res["item"].name)
        if res.get("diamonds"):
            bits.append(f"{res['diamonds']} الماس")
        return f"{CHEST_FA.get(tier, 'جعبه')}: " + "، ".join(bits)
    from game.equipment import roll_equipment

    item = roll_equipment(user, "legendary")
    return f"تجهیزات افسانه‌ای: {item.name}"


def reject(user: User, slot, order_id: int) -> dict:
    """Turn an order down: the merchant leaves and a new one comes in an hour."""
    if slot == "s":
        raise GameError("سفارش ویژه رد نمی‌شه؛ تا آخر روز روی تابلو می‌مونه.")
    with transaction.atomic():
        row = _board(user, lock=True)
        _find(row, slot, order_id)
        row.slots[int(slot)] = {"wait_until": (timezone.now() + REFILL).timestamp()}
        row.save()
    return {"wait": int(REFILL.total_seconds())}

"""«آزمایشگاه سرم» — brew-wait-use, like the spells of Clash of Clans.

The building («serum_lab», built like any other) brews every queued serum side by side (each
on its own clock, 2026-11-05 — it used to be one at a time), with DNA paid when it is queued. Finished serums wait on a shelf whose size grows with the
building. A serum is then USED: repair acts at once; shell is armed in the lab for the next
defence; rage, tracker, warrior and giant-slayer are chosen on their own action screen (the
arena attack / hunt card / group «اتک» / boss hit — see MOMENT) and spent by that action.

Each serum also has its own level, 1 to 5, raised inside the lab (gold + DNA + time, one at
a time, never above the building's level). The level sets the size of its effect.

Rules that keep it fair: brewing has no daily cap — the shelf size and the brewing time are the
limit; the heal serum is capped per dungeon run (dungeon.HEALS_PER_RUN). There is no energy
serum: a hunt costs 1 energy, so an uncapped +energy serum paid its own DNA back and made gold
without end (it was removed). Shelf + brewing never exceed the shelf size. The brewing can be
skipped for diamonds (finish_with_diamonds, FINISH_PER_SERUM a serum), but never the shelf size.
Any queued serum can be cancelled for its DNA back; a shelf serum can be thrown away (no refund).
"""

from __future__ import annotations

import datetime

from django.db import transaction
from django.utils import timezone

from bio_lab.models import SerumLab, User
from game.creature import GameError, InsufficientGoldError

LAB = "serum_lab"
PING_DAILY = 2   # «سرم آماده‌ست» DMs a day at most (opt-in)
SHELL_HOURS = 8
MAX_LEVEL = 5

# key → label, short line, the lab level that unlocks it, effect per serum level (1..5)
KINDS: dict[str, dict] = {
    "rage": {"label": "خشم", "where": "حمله‌ی آرنا", "unlock": 1, "fx": [10, 12, 14, 17, 20],
             "text": "+{v}٪ قدرت در یک حمله‌ی آرنا"},
    "tracker": {"label": "ردیاب", "where": "شکار", "unlock": 1, "fx": [1.5, 1.75, 2.0, 2.25, 2.5],
                "text": "غنیمت یک شکارِ برده ×{v}"},
    "warrior": {"label": "جنگاور", "where": "اتک گروهی", "unlock": 2, "fx": [10, 12, 14, 17, 20],
                "text": "+{v}٪ قدرت در یک اتک گروهی"},
    "shell": {"label": "پوسته", "where": f"دفاع بعدی آرنا (تا {SHELL_HOURS} ساعت)", "unlock": 3, "fx": [10, 12, 14, 17, 20],
              "text": "+{v}٪ قدرت مدافعت در حمله‌ی بعدی آرنا که بهت می‌شه"},
    "heal": {"label": "ترمیم", "where": "سیاه‌چال", "unlock": 3, "fx": [30, 35, 40, 45, 50],
             "text": "+{v}٪ جون به یکی از اعضای تیم سیاه‌چال"},
    "slayer": {"label": "غول‌کش", "where": "ضربه به غول", "unlock": 4, "fx": [15, 18, 22, 26, 30],
               "text": "+{v}٪ آسیب در یک ضربه به غول سرگردان"},
}
ORDER = ["rage", "tracker", "warrior", "shell", "heal", "slayer"]
ARMED = ("rage", "tracker", "warrior", "shell", "slayer")     # wait for their moment; heal acts at once
# Picked on the action's own screen (owner's rule, 2026-10-31): a serum is never spent unless the
# player chose it for THIS attack / hunt / hit. The toggle there arms it (off the shelf) and a
# second tap puts it back; the lab no longer arms these. Shell (a defence) is still armed in the
# lab and heal is used inside the dungeon.
MOMENT = {"rage": "صفحه‌ی حمله‌ی آرنا", "tracker": "کارت شکار", "warrior": "پیش‌نمایش اتک گروه",
          "slayer": "صفحه‌ی غول سرگردان"}

# by building level: shelf size and minutes per serum
CAPACITY = {1: 3, 2: 3, 3: 4, 4: 4, 5: 5}   # 3 places, +1 at levels 3 and 5
BREW_MINUTES = {1: 30, 2: 30, 3: 25, 4: 20, 5: 15}
# diamonds to finish one serum that has its whole brewing time left (less as its clock runs, min 1)
FINISH_PER_SERUM = 4
# raising a serum TO level n: (gold, dna, hours)
UPGRADE = {2: (20_000, 400, 2), 3: (60_000, 1_000, 6), 4: (150_000, 2_000, 12), 5: (300_000, 4_000, 24)}


def lab_level(user: User) -> int:
    from game.buildings import building_level

    return building_level(user, LAB)


def power_basis(user: User) -> int:
    """The player's strongest kaiju (gear included) — what serum prices and merchant orders are
    sized by. Not the active creature: that one can be swapped in a tap, which would let a player
    brew with a weak creature selected and deliver/refund with a strong one (two queries)."""
    from bio_lab.models import Creature
    from game.creature import creature_power
    from game.equipment import equipped_items_map

    creatures = list(Creature.objects.filter(owner=user))
    gear = equipped_items_map(creatures)
    return max((creature_power(c, gear.get(c.id, [])) for c in creatures), default=0)


def brew_cost(user: User) -> int:
    """DNA per serum: about two and a half hunts' worth of DNA for this player's power."""
    from game.hunt import HUNT_DNA_PER_POWER, HUNT_LOOT_BONUS

    return max(50, round(power_basis(user) * HUNT_DNA_PER_POWER * HUNT_LOOT_BONUS * 2.5))


def effect(kind: str, level: int) -> float:
    return KINDS[kind]["fx"][max(1, min(MAX_LEVEL, int(level or 1))) - 1]


def effect_text(kind: str, level: int) -> str:
    v = effect(kind, level)
    return KINDS[kind]["text"].format(v=f"{v:g}")


def _today() -> str:
    from game.daily import today_str

    return today_str()


def _row(user: User, lock: bool = False) -> SerumLab:
    if lock:
        row, _ = SerumLab.objects.select_for_update().get_or_create(user_id=user.id)
    else:
        row, _ = SerumLab.objects.get_or_create(user_id=user.id)
    return row


def _advance(row: SerumLab, level: int, now: datetime.datetime) -> bool:
    """Bring the row up to `now`: finished brews move to the shelf (while it has room), a
    finished level-up lands, an expired shell drops. Returns True when anything changed."""
    changed = False
    if row.upgrade_kind and row.upgrade_ends_at and row.upgrade_ends_at <= now:
        row.levels = {**(row.levels or {}), row.upgrade_kind: int((row.levels or {}).get(row.upgrade_kind, 1)) + 1}
        row.upgrade_kind, row.upgrade_ends_at = "", None
        changed = True
    if row.queue and len(row.starts or []) != len(row.queue):
        row.starts = _starts(row, now)
        changed = True
    if level > 0 and row.queue:
        step = _step(level)
        cap = CAPACITY[min(level, MAX_LEVEL)]
        stock = dict(row.stock or {})
        queue, starts = list(row.queue), list(row.starts)
        paid = list(row.paid or [])
        paid += [0] * (len(queue) - len(paid))
        keep_q, keep_s, keep_p = [], [], []
        for kind, start, cost in zip(queue, starts, paid):
            if start + step <= now.timestamp() and sum(stock.values()) < cap:
                stock[kind] = stock.get(kind, 0) + 1
            else:   # still brewing (or done, waiting for a place — only after a hand-over refill)
                keep_q.append(kind); keep_s.append(start); keep_p.append(cost)
        if keep_q != queue:
            row.stock, row.queue, row.starts, row.paid = stock, keep_q, keep_s, keep_p
            changed = True
    if _sync_head(row):
        changed = True
    shell = (row.armed or {}).get("shell")
    if shell and shell.get("until") and shell["until"] <= now.timestamp():
        row.armed = {k: v for k, v in row.armed.items() if k != "shell"}
        changed = True
    return changed


def _step(level: int) -> int:
    return BREW_MINUTES[max(1, min(level, MAX_LEVEL))] * 60


def _starts(row: SerumLab, now: datetime.datetime) -> list[float]:
    """Each queued serum's start. Rows from the one-at-a-time days had only the head's start:
    the head keeps it, the serums that were waiting behind it start now."""
    have = list(row.starts or [])[: len(row.queue)]
    head = row.brew_started_at.timestamp() if row.brew_started_at else now.timestamp()
    fill = [head] if not have else []
    return (have + fill + [now.timestamp()] * len(row.queue))[: len(row.queue)]


def _sync_head(row: SerumLab) -> bool:
    """brew_started_at mirrors the earliest start (kept for older readers); True when it moved."""
    head = (datetime.datetime.fromtimestamp(min(row.starts), tz=datetime.timezone.utc)
            if row.queue and row.starts else None)
    if head != row.brew_started_at:
        row.brew_started_at = head
        return True
    return False


def _lefts(row: SerumLab, level: int, now: datetime.datetime) -> list[int]:
    """Seconds of brewing left for each queued serum, in queue order (0 = done, waiting for room)."""
    step = _step(level)
    return [max(0, int(s + step - now.timestamp())) for s in (row.starts or [])[: len(row.queue)]]


def _drop(row: SerumLab, index: int) -> int:
    """Take queued serum `index` out (with its start and price); returns the DNA paid for it."""
    queue, starts, paid = list(row.queue), list(row.starts or []), list(row.paid or [])
    queue.pop(index)
    if index < len(starts):
        starts.pop(index)
    refund = paid.pop(index) if index < len(paid) else 0
    row.queue, row.starts, row.paid = queue, starts, paid
    _sync_head(row)
    return refund


def _fresh(user: User, lock: bool = True) -> tuple[SerumLab, int]:
    level = lab_level(user)
    row = _row(user, lock=lock)
    if _advance(row, level, timezone.now()):
        row.save()
    return row, level


def _finish_plan(row: SerumLab, level: int, now: datetime.datetime) -> tuple[list[int], int, int]:
    """(queue indexes a diamond finish lands now, the longest wait it skips, its price): every
    brewing serum at once — as many as the shelf has room for, soonest-done first."""
    if level <= 0 or not row.queue:
        return [], 0, 0
    room = max(0, CAPACITY[min(level, MAX_LEVEL)] - sum((row.stock or {}).values()))
    lefts = _lefts(row, level, now)
    idx = sorted(range(len(lefts)), key=lambda i: lefts[i])[:room]
    if not idx:
        return [], 0, 0
    return idx, max(lefts[i] for i in idx), sum(finish_cost(lefts[i], level) for i in idx)


def finish_cost(seconds_left: int, level: int) -> int:
    """One serum's price: FINISH_PER_SERUM for a whole brewing time, less as its clock runs."""
    import math

    if seconds_left <= 0:
        return 0
    return max(1, math.ceil(FINISH_PER_SERUM * seconds_left / _step(level)))


# ── reading ───────────────────────────────────────────────────────────────────
def state(user: User) -> dict:
    """Everything the lab screen shows (bot and Mini App)."""
    with transaction.atomic():
        row, level = _fresh(user)
    now = timezone.now()
    lvl = max(1, min(level, MAX_LEVEL)) if level else 0
    cap = CAPACITY.get(lvl, 0)
    step = BREW_MINUTES.get(lvl, 30) * 60
    lefts = _lefts(row, level, now) if lvl else []
    next_in = min(lefts) if lefts else None
    used = row.used_count if row.used_day == _today() else 0
    fin_idx, fin_secs, fin_cost = _finish_plan(row, level, now)
    kinds = []
    for k in ORDER:
        meta = KINDS[k]
        klvl = int((row.levels or {}).get(k, 1))
        nxt = UPGRADE.get(klvl + 1)
        kinds.append({
            "key": k, "label": meta["label"], "where": meta["where"], "unlock": meta["unlock"],
            "unlocked": level >= meta["unlock"], "level": klvl, "effect": effect_text(k, klvl),
            "next_effect": effect_text(k, klvl + 1) if klvl < MAX_LEVEL else "",
            "all": [effect_text(k, lv) for lv in range(1, MAX_LEVEL + 1)],
            "stock": int((row.stock or {}).get(k, 0)), "queued": row.queue.count(k),
            "armed": k in (row.armed or {}), "moment": MOMENT.get(k, ""),
            "upgrade": {"gold": nxt[0], "dna": nxt[1], "hours": nxt[2], "capped": klvl + 1 > level} if nxt else None,
        })
    shell = (row.armed or {}).get("shell")
    return {
        "level": level, "capacity": cap, "brew_minutes": step // 60, "brew_cost": brew_cost(user) if level else 0,
        "stock_total": sum((row.stock or {}).values()), "queue": list(row.queue), "next_in": next_in,
        "queue_left": lefts, "all_in": max(lefts) if lefts else None,
        "finish": {"count": len(fin_idx), "left": fin_secs, "cost": fin_cost,
                   "per_serum": FINISH_PER_SERUM} if fin_idx and fin_cost else None,
        "used": used, "kinds": kinds,
        "upgrading": {"kind": row.upgrade_kind, "label": KINDS[row.upgrade_kind]["label"],
                      "left": max(0, int((row.upgrade_ends_at - now).total_seconds()))} if row.upgrade_kind else None,
        "armed": sorted((row.armed or {}).keys()),
        "notify": row.notify,
        "shell_left": max(0, int(shell["until"] - now.timestamp())) if shell and shell.get("until") else 0,
    }


def peek(user: User, kind: str) -> float:
    """The multiplier an armed serum WILL give (cards show it), without spending it: 1.0 if none."""
    try:
        row = SerumLab.objects.filter(user_id=user.id).only("armed").first()
    except Exception:  # noqa: BLE001 — a card must never fail over a serum
        return 1.0
    a = (row.armed or {}).get(kind) if row else None
    if not a or (a.get("until") and a["until"] <= timezone.now().timestamp()):
        return 1.0
    return _mult(kind, a.get("lvl", 1))


def _mult(kind: str, level: int) -> float:
    v = effect(kind, level)
    return v if kind == "tracker" else 1 + v / 100


def take(user: User, kind: str) -> float:
    """Spend an armed serum at its moment and return its multiplier (1.0 if none armed).
    Call it inside the caller's transaction (it locks the lab row)."""
    if kind not in ARMED:
        return 1.0
    row = SerumLab.objects.select_for_update().filter(user_id=user.id).first()
    a = (row.armed or {}).get(kind) if row else None
    if not a:
        return 1.0
    row.armed = {k: v for k, v in row.armed.items() if k != kind}
    row.save(update_fields=["armed"])
    if a.get("until") and a["until"] <= timezone.now().timestamp():
        return 1.0
    return _mult(kind, a.get("lvl", 1))


def moment(user: User, kind: str) -> dict | None:
    """What the action screen's serum toggle shows: None when there is nothing to offer (no lab,
    locked, none on the shelf and none chosen). Read-only, like stock_of()."""
    if kind not in MOMENT:
        return None
    try:
        row = SerumLab.objects.filter(user_id=user.id).first()
        if row is None:
            return None
        level = lab_level(user)
        _advance(row, level, timezone.now())
    except Exception:  # noqa: BLE001 — a battle card must never fail over a serum
        return None
    armed = (row.armed or {}).get(kind)
    stock = int((row.stock or {}).get(kind, 0))
    if level < KINDS[kind]["unlock"] or (not armed and stock <= 0):
        return None
    klvl = int(armed.get("lvl", 1)) if armed else int((row.levels or {}).get(kind, 1))
    return {"kind": kind, "label": KINDS[kind]["label"], "on": bool(armed), "stock": stock,
            "level": klvl, "effect": effect_text(kind, klvl), "pct": round((_mult(kind, klvl) - 1) * 100)}


def unarm(user: User, kind: str) -> None:
    """Put a chosen serum back on the shelf (it was never spent)."""
    with transaction.atomic():
        row = _row(user, lock=True)
        if kind not in (row.armed or {}):
            return
        row.armed = {k: v for k, v in row.armed.items() if k != kind}
        row.stock = {**(row.stock or {}), kind: int((row.stock or {}).get(kind, 0)) + 1}
        row.save(update_fields=["armed", "stock"])


def toggle(user: User, kind: str) -> dict | None:
    """The action screen's serum button: choose it for this attack, or put it back."""
    if kind not in MOMENT:
        raise GameError("این سرم اینجا انتخاب نمی‌شه.")
    with transaction.atomic():
        row = _row(user, lock=True)
        on = kind in (row.armed or {})
    if on:
        unarm(user, kind)
    else:
        use(user, kind, _moment=True)
    return moment(user, kind)


# ── acting ────────────────────────────────────────────────────────────────────
def brew(user: User, kind: str, count: int = 1) -> dict:
    """Queue `count` serums of `kind`; the DNA is paid now (shelf + queue never exceed the shelf)."""
    from bio_lab.repository import lock_row

    if kind not in KINDS:
        raise GameError("این سرم وجود نداره.")
    count = max(1, min(int(count), 8))
    with transaction.atomic():
        user = lock_row(user) or User.objects.select_for_update().get(id=user.id)
        row, level = _fresh(user)
        if level <= 0:
            raise GameError("اول «آزمایشگاه سرم» رو توی پایگاه بساز.")
        if level < KINDS[kind]["unlock"]:
            raise GameError(f"سرم «{KINDS[kind]['label']}» از سطح {KINDS[kind]['unlock']} آزمایشگاه سرم باز می‌شه.")
        room = CAPACITY[min(level, MAX_LEVEL)] - sum((row.stock or {}).values()) - len(row.queue)
        if room <= 0:
            raise GameError("قفسه و صف ساخت پره. اول یه سرم مصرف کن یا ساختمون رو ارتقا بده.")
        count = min(count, room)
        cost = brew_cost(user) * count
        if user.dna_fragments < cost:
            raise GameError(f"DNA کافی نداری: برای {count} سرم {cost:,} DNA لازمه.")
        user.dna_fragments -= cost
        user.save(update_fields=["dna_fragments"])
        row.queue = list(row.queue) + [kind] * count
        row.starts = list(row.starts or []) + [timezone.now().timestamp()] * count   # brews side by side
        row.paid = list(row.paid or []) + [cost // count] * count   # refunds give back what was paid
        _sync_head(row)
        row.ping_armed = True
        row.save()
    return {"queued": count, "cost": cost, "state": state(user)}


def cancel_last(user: User) -> dict:
    """Take the last queued (not yet brewing) serum off the queue and refund its DNA."""
    from bio_lab.repository import lock_row

    with transaction.atomic():
        user = lock_row(user) or User.objects.select_for_update().get(id=user.id)
        row, level = _fresh(user)
        if not row.queue:
            raise GameError("سرمی توی صف نیست که بشه لغوش کرد.")
        kind = row.queue[-1]
        refund = _drop(row, len(row.queue) - 1) or brew_cost(user)
        row.save()
        user.dna_fragments += refund
        user.save(update_fields=["dna_fragments"])
    return {"kind": kind, "refund": refund, "state": state(user)}


def finish_with_diamonds(user: User, max_cost: int | None = None) -> dict:
    """Land the queued serums now for diamonds (as many as the shelf holds), priced from the
    brewing time left. `max_cost` is the price the player saw: if it went up meanwhile (a place
    freed up, so more serums would land) nothing is charged."""
    from bio_lab.repository import lock_row

    with transaction.atomic():
        user = lock_row(user) or User.objects.select_for_update().get(id=user.id)
        row, level = _fresh(user)
        if not row.queue:
            raise GameError("سرمی در حال ساخت نیست.")
        now = timezone.now()
        idx, _secs, cost = _finish_plan(row, level, now)
        if not idx:
            raise GameError("قفسه پره؛ اول یه سرم مصرف کن یا دور بریز.")
        if max_cost is not None and cost > max_cost:
            raise GameError(f"قیمت عوض شد: الان {cost} الماس. دوباره بزن.")
        if user.diamonds < cost:
            raise GameError(f"الماس کافی نداری: {cost} الماس لازمه (الان {user.diamonds} داری).")
        user.diamonds -= cost
        user.save(update_fields=["diamonds"])
        stock = dict(row.stock or {})
        for i in sorted(idx, reverse=True):
            kind = row.queue[i]
            _drop(row, i)
            stock[kind] = stock.get(kind, 0) + 1
        row.stock = stock
        row.save()
    return {"count": len(idx), "cost": cost, "state": state(user)}


def remove_queued(user: User, index: int, kind: str) -> dict:
    """Cancel any serum in the brewing queue (the one brewing too) and refund the DNA paid for
    it. `kind` must match what is at `index`, so a queue that moved on since the screen was drawn
    never cancels the wrong serum."""
    from bio_lab.repository import lock_row

    with transaction.atomic():
        user = lock_row(user) or User.objects.select_for_update().get(id=user.id)
        row, level = _fresh(user)
        queue = list(row.queue)
        if not 0 <= index < len(queue) or queue[index] != kind:
            raise GameError("صف ساخت عوض شده (شاید همین الان آماده شد). دوباره نگاه کن.")
        refund = _drop(row, index) or brew_cost(user)
        row.save()
        user.dna_fragments += refund
        user.save(update_fields=["dna_fragments"])
    return {"kind": kind, "label": KINDS[kind]["label"], "refund": refund, "state": state(user)}


def discard(user: User, kind: str, count: int = 1) -> dict:
    """Throw `count` serums of a kind off the shelf to make room (their DNA is not refunded)."""
    if kind not in KINDS:
        raise GameError("این سرم وجود نداره.")
    with transaction.atomic():
        row, _ = _fresh(user)
        have = int((row.stock or {}).get(kind, 0))
        if have <= 0:
            raise GameError(f"سرم «{KINDS[kind]['label']}» روی قفسه نداری.")
        n = max(1, min(int(count), have))
        row.stock = {**row.stock, kind: have - n}
        row.save()
    return {"kind": kind, "label": KINDS[kind]["label"], "count": n, "state": state(user)}


def remove_kind(user: User, kind: str, count: int) -> dict:
    """Cancel `count` queued serums of a kind — the last-started first — with their DNA back."""
    from bio_lab.repository import lock_row

    if kind not in KINDS:
        raise GameError("این سرم وجود نداره.")
    with transaction.atomic():
        user = lock_row(user) or User.objects.select_for_update().get(id=user.id)
        row, _ = _fresh(user)
        where = [i for i, q in enumerate(row.queue) if q == kind]
        if not where:
            raise GameError(f"«{KINDS[kind]['label']}» توی صف ساخت نیست (شاید همین الان آماده شد).")
        n = max(1, min(int(count), len(where)))
        refund = sum(_drop(row, i) or brew_cost(user) for i in sorted(where, reverse=True)[:n])
        row.save()
        user.dna_fragments += refund
        user.save(update_fields=["dna_fragments"])
    return {"kind": kind, "label": KINDS[kind]["label"], "count": n, "refund": refund, "state": state(user)}


def clear_all(user: User) -> dict:
    """«حذف همه»: cancel the whole queue (DNA back) and empty the shelf (no refund). Armed
    serums are not on the shelf and stay armed."""
    from bio_lab.repository import lock_row

    with transaction.atomic():
        user = lock_row(user) or User.objects.select_for_update().get(id=user.id)
        row, _ = _fresh(user)
        queued, shelf = len(row.queue), sum((row.stock or {}).values())
        if not queued and not shelf:
            raise GameError("نه سرمی توی صف داری نه روی قفسه.")
        refund = 0
        for i in range(len(row.queue) - 1, -1, -1):
            refund += _drop(row, i) or brew_cost(user)
        row.stock = {}
        row.save()
        user.dna_fragments += refund
        user.save(update_fields=["dna_fragments"])
    return {"queued": queued, "shelf": shelf, "refund": refund, "state": state(user)}


def use(user: User, kind: str, member_id: int | None = None, _moment: bool = False) -> dict:
    """Use a serum from the shelf. Repair acts now; shell waits for the next defence. The MOMENT
    kinds are only chosen from their action screen (toggle())."""
    from bio_lab.repository import lock_row

    if kind not in KINDS:
        raise GameError("این سرم وجود نداره.")
    label = KINDS[kind]["label"]
    if kind in MOMENT and not _moment:
        raise GameError(f"«{label}» رو موقع استفاده انتخاب کن: توی {MOMENT[kind]} دکمه‌ی سرم رو بزن.")
    with transaction.atomic():
        user = lock_row(user) or User.objects.select_for_update().get(id=user.id)
        row, level = _fresh(user)
        if int((row.stock or {}).get(kind, 0)) <= 0:
            raise GameError(f"سرم «{label}» نداری. اول بسازش.")
        today = _today()
        used = row.used_count if row.used_day == today else 0   # counted for stats only
        if kind in ARMED and kind in (row.armed or {}):
            raise GameError(f"یه «{label}» الان فعاله و منتظر لحظه‌شه.")
        klvl = int((row.levels or {}).get(kind, 1))
        note = ""
        if kind == "heal":
            from game import dungeon

            note = dungeon.heal_member(user, member_id, int(effect(kind, klvl)))
        else:
            until = (timezone.now() + datetime.timedelta(hours=SHELL_HOURS)).timestamp() if kind == "shell" else None
            row.armed = {**(row.armed or {}), kind: {"lvl": klvl, "until": until}}
            note = ("انتخاب شد برای " if _moment else "فعال شد: ") + KINDS[kind]["where"]
        row.stock = {**row.stock, kind: int(row.stock.get(kind, 0)) - 1}
        row.used_day, row.used_count = today, used + 1
        row.save()
    return {"kind": kind, "label": label, "note": note, "state": state(user)}



def set_notify(user: User, on: bool) -> dict:
    """Switch the «سرم آماده‌ست» DM on or off for this player."""
    row = _row(user)
    row.notify = bool(on)
    row.save(update_fields=["notify"])
    return state(user)


def due_pings(now=None) -> list[tuple[int, str, str, None]]:
    """For game/notifications.collect_due: players who opted in, brewed since their last ping,
    and whose lab has stopped (queue done, or paused on a full shelf). Runs inside its transaction."""
    now = now or timezone.now()
    today = _today()
    out = []
    for row in SerumLab.objects.select_for_update().filter(notify=True, ping_armed=True).select_related("user"):
        level = lab_level(row.user)
        _advance(row, level, now)
        if any(_lefts(row, level, now)):   # still brewing
            row.save()
            continue
        row.ping_armed = False
        if row.ping_day != today:
            row.ping_day, row.ping_count = today, 0
        ready = sum((row.stock or {}).values())
        if ready and row.ping_count < PING_DAILY and row.user.notifications_on:
            row.ping_count += 1
            full = " · قفسه پره و ساخت تا مصرف یکی صبر می‌کنه" if row.queue else ""
            out.append((row.user_id, f"🧪 <b>سرم‌هات آماده‌ان!</b>\n{ready} سرم روی قفسه‌ی آزمایشگاه منتظرته{full}.", "serum_ready", None))
        row.save()
    return out


def stock_of(user: User, kind: str) -> int:
    """Serums of this kind on the shelf now (brews finished by now included). Read-only: the
    finished brews are counted, not saved — writing an unlocked row here could overwrite a
    brew() running at the same moment."""
    row = SerumLab.objects.filter(user_id=user.id).first()
    if row is None:
        return 0
    _advance(row, lab_level(user), timezone.now())
    return int((row.stock or {}).get(kind, 0))


def hand_over(user: User, kind: str, count: int) -> None:
    """Take `count` serums off the shelf for something other than using them (a merchant
    order). Not a «use»: the daily cap is untouched. Runs inside the caller's transaction."""
    row, _ = _fresh(user)
    have = int((row.stock or {}).get(kind, 0))
    if have < count:
        raise GameError(f"سرم «{KINDS[kind]['label']}» کافی نداری ({have} از {count}).")
    row.stock = {**row.stock, kind: have - count}
    row.save()

def upgrade(user: User, kind: str) -> dict:
    """Raise one serum a level (gold + DNA, takes real time, one at a time, never above the building)."""
    from bio_lab.repository import lock_row

    if kind not in KINDS:
        raise GameError("این سرم وجود نداره.")
    with transaction.atomic():
        user = lock_row(user) or User.objects.select_for_update().get(id=user.id)
        row, level = _fresh(user)
        if level < KINDS[kind]["unlock"]:
            raise GameError("این سرم هنوز باز نشده.")
        if row.upgrade_kind:
            raise GameError(f"آزمایشگاه داره «{KINDS[row.upgrade_kind]['label']}» رو ارتقا می‌ده؛ صبر کن تموم بشه.")
        cur = int((row.levels or {}).get(kind, 1))
        if cur >= MAX_LEVEL:
            raise GameError("این سرم به بالاترین سطح رسیده.")
        if cur + 1 > level:
            raise GameError(f"برای سطح {cur + 1} این سرم، آزمایشگاه سرم باید سطح {cur + 1} باشه.")
        gold, dna, hours = UPGRADE[cur + 1]
        if user.coins < gold:
            raise InsufficientGoldError(f"طلا کافی نداری: {gold:,} طلا لازمه.", need=gold, have=user.coins)
        if user.dna_fragments < dna:
            raise GameError(f"DNA کافی نداری: {dna:,} DNA لازمه.")
        user.coins -= gold
        user.dna_fragments -= dna
        user.save(update_fields=["coins", "dna_fragments"])
        row.upgrade_kind = kind
        row.upgrade_ends_at = timezone.now() + datetime.timedelta(hours=hours)
        row.save()
    return {"kind": kind, "to": cur + 1, "hours": hours, "state": state(user)}

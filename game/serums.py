"""«آزمایشگاه سرم» — brew-wait-use, like the spells of Clash of Clans.

The building («serum_lab», built like any other) brews one serum at a time from a queue, with
DNA paid when the serum is queued. Finished serums wait on a shelf whose size grows with the
building. A serum is then USED: adrenaline and repair act at once; rage, tracker, shell and
giant-slayer are «armed» and spent by the next arena attack / hunt / defence / boss hit.

Each serum also has its own level, 1 to 5, raised inside the lab (gold + DNA + time, one at
a time, never above the building's level). The level sets the size of its effect.

Rules that keep it fair: at most DAILY_USES uses a day; nothing is ever lost — a full shelf
only pauses the brewing; no serum touches diamonds.
"""

from __future__ import annotations

import datetime

from django.db import transaction
from django.utils import timezone

from bio_lab.models import SerumLab, User
from game.creature import GameError, InsufficientGoldError

LAB = "serum_lab"
DAILY_USES = 6
SHELL_HOURS = 8
MAX_LEVEL = 5

# key → label, short line, the lab level that unlocks it, effect per serum level (1..5)
KINDS: dict[str, dict] = {
    "rage": {"label": "خشم", "where": "حمله‌ی بعدی آرنا", "unlock": 1, "fx": [10, 12, 14, 17, 20],
             "text": "+{v}٪ قدرت در حمله‌ی بعدی آرنا"},
    "tracker": {"label": "ردیاب", "where": "شکار بعدی", "unlock": 1, "fx": [1.5, 1.75, 2.0, 2.25, 2.5],
                "text": "غنیمت شکار بعدی ×{v}"},
    "adrenaline": {"label": "آدرنالین", "where": "همون لحظه", "unlock": 2, "fx": [3, 4, 5, 6, 8],
                   "text": "+{v} انرژی فوری"},
    "shell": {"label": "پوسته", "where": f"دفاع بعدی آرنا (تا {SHELL_HOURS} ساعت)", "unlock": 3, "fx": [10, 12, 14, 17, 20],
              "text": "+{v}٪ قدرت مدافعت در حمله‌ی بعدی آرنا که بهت می‌شه"},
    "heal": {"label": "ترمیم", "where": "سیاه‌چال", "unlock": 3, "fx": [30, 35, 40, 45, 50],
             "text": "+{v}٪ جون به یکی از اعضای تیم سیاه‌چال"},
    "slayer": {"label": "غول‌کش", "where": "ضربه‌ی بعدی به غول", "unlock": 4, "fx": [15, 18, 22, 26, 30],
               "text": "+{v}٪ آسیب در ضربه‌ی بعدی به غول سرگردان"},
}
ORDER = ["rage", "tracker", "adrenaline", "shell", "heal", "slayer"]
ARMED = ("rage", "tracker", "shell", "slayer")     # wait for their moment; the others act at once

# by building level: shelf size and minutes per serum
CAPACITY = {1: 3, 2: 4, 3: 5, 4: 6, 5: 8}
BREW_MINUTES = {1: 30, 2: 30, 3: 25, 4: 20, 5: 15}
# raising a serum TO level n: (gold, dna, hours)
UPGRADE = {2: (20_000, 400, 2), 3: (60_000, 1_000, 6), 4: (150_000, 2_000, 12), 5: (300_000, 4_000, 24)}


def lab_level(user: User) -> int:
    from game.buildings import building_level

    return building_level(user, LAB)


def brew_cost(user: User) -> int:
    """DNA per serum: about two and a half hunts' worth of DNA for this player's power."""
    from game.arena import active_power
    from game.hunt import HUNT_DNA_PER_POWER, HUNT_LOOT_BONUS

    return max(50, round(active_power(user) * HUNT_DNA_PER_POWER * HUNT_LOOT_BONUS * 2.5))


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
    if level > 0 and row.queue:
        step = datetime.timedelta(minutes=BREW_MINUTES[min(level, MAX_LEVEL)])
        cap = CAPACITY[min(level, MAX_LEVEL)]
        stock = dict(row.stock or {})
        queue = list(row.queue)
        started = row.brew_started_at or now
        while queue and sum(stock.values()) < cap and started + step <= now:
            kind = queue.pop(0)
            stock[kind] = stock.get(kind, 0) + 1
            started = started + step
        # a full shelf pauses the brewing: the clock restarts once there is room again
        if queue and sum(stock.values()) >= cap:
            started = None
        # None = paused (full shelf): use() restarts the clock when it frees a place
        new_start = started if queue else None
        if stock != (row.stock or {}) or queue != row.queue or new_start != row.brew_started_at:
            row.stock, row.queue, row.brew_started_at = stock, queue, new_start
            changed = True
    elif not row.queue and row.brew_started_at:
        row.brew_started_at = None
        changed = True
    shell = (row.armed or {}).get("shell")
    if shell and shell.get("until") and shell["until"] <= now.timestamp():
        row.armed = {k: v for k, v in row.armed.items() if k != "shell"}
        changed = True
    return changed


def _fresh(user: User, lock: bool = True) -> tuple[SerumLab, int]:
    level = lab_level(user)
    row = _row(user, lock=lock)
    if _advance(row, level, timezone.now()):
        row.save()
    return row, level


# ── reading ───────────────────────────────────────────────────────────────────
def state(user: User) -> dict:
    """Everything the lab screen shows (bot and Mini App)."""
    with transaction.atomic():
        row, level = _fresh(user)
    now = timezone.now()
    lvl = max(1, min(level, MAX_LEVEL)) if level else 0
    cap = CAPACITY.get(lvl, 0)
    step = BREW_MINUTES.get(lvl, 30) * 60
    next_in = None
    if row.queue and row.brew_started_at and lvl:
        next_in = max(0, int((row.brew_started_at + datetime.timedelta(seconds=step) - now).total_seconds()))
    used = row.used_count if row.used_day == _today() else 0
    kinds = []
    for k in ORDER:
        meta = KINDS[k]
        klvl = int((row.levels or {}).get(k, 1))
        nxt = UPGRADE.get(klvl + 1)
        kinds.append({
            "key": k, "label": meta["label"], "where": meta["where"], "unlock": meta["unlock"],
            "unlocked": level >= meta["unlock"], "level": klvl, "effect": effect_text(k, klvl),
            "next_effect": effect_text(k, klvl + 1) if klvl < MAX_LEVEL else "",
            "stock": int((row.stock or {}).get(k, 0)), "queued": row.queue.count(k),
            "armed": k in (row.armed or {}),
            "upgrade": {"gold": nxt[0], "dna": nxt[1], "hours": nxt[2], "capped": klvl + 1 > level} if nxt else None,
        })
    shell = (row.armed or {}).get("shell")
    return {
        "level": level, "capacity": cap, "brew_minutes": step // 60, "brew_cost": brew_cost(user) if level else 0,
        "stock_total": sum((row.stock or {}).values()), "queue": list(row.queue), "next_in": next_in,
        "used": used, "daily": DAILY_USES, "kinds": kinds,
        "upgrading": {"kind": row.upgrade_kind, "label": KINDS[row.upgrade_kind]["label"],
                      "left": max(0, int((row.upgrade_ends_at - now).total_seconds()))} if row.upgrade_kind else None,
        "armed": sorted((row.armed or {}).keys()),
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
        if not row.queue:
            row.brew_started_at = timezone.now()
        row.queue = list(row.queue) + [kind] * count
        row.save()
    return {"queued": count, "cost": cost, "state": state(user)}


def cancel_last(user: User) -> dict:
    """Take the last queued (not yet brewing) serum off the queue and refund its DNA."""
    from bio_lab.repository import lock_row

    with transaction.atomic():
        user = lock_row(user) or User.objects.select_for_update().get(id=user.id)
        row, level = _fresh(user)
        if len(row.queue) <= (1 if row.brew_started_at else 0):
            raise GameError("سرمی توی صف نیست که بشه لغوش کرد (اونی که در حال ساخته رو نمی‌شه لغو کرد).")
        kind = row.queue[-1]
        row.queue = list(row.queue[:-1])
        row.save()
        refund = brew_cost(user)
        user.dna_fragments += refund
        user.save(update_fields=["dna_fragments"])
    return {"kind": kind, "refund": refund, "state": state(user)}


def use(user: User, kind: str, member_id: int | None = None) -> dict:
    """Use a serum from the shelf. Adrenaline and repair act now; the rest wait for their moment."""
    from bio_lab.repository import lock_row

    if kind not in KINDS:
        raise GameError("این سرم وجود نداره.")
    label = KINDS[kind]["label"]
    with transaction.atomic():
        user = lock_row(user) or User.objects.select_for_update().get(id=user.id)
        row, level = _fresh(user)
        if int((row.stock or {}).get(kind, 0)) <= 0:
            raise GameError(f"سرم «{label}» نداری. اول بسازش.")
        today = _today()
        used = row.used_count if row.used_day == today else 0
        if used >= DAILY_USES:
            raise GameError(f"امروز {DAILY_USES} سرم مصرف کردی؛ سقف روزانه پر شده. فردا دوباره.")
        if kind in ARMED and kind in (row.armed or {}):
            raise GameError(f"یه «{label}» الان فعاله و منتظر لحظه‌شه.")
        klvl = int((row.levels or {}).get(kind, 1))
        note = ""
        if kind == "adrenaline":
            from game.energy import get_max_energy, sync_energy

            cur, top = sync_energy(user), get_max_energy(user)
            if cur >= top:
                raise GameError("انرژیت پره؛ آدرنالین رو بذار برای وقتی که لازمت شد.")
            gain = min(int(effect(kind, klvl)), top - cur)
            user.energy = cur + gain
            user.save(update_fields=["energy", "energy_updated_at"])
            note = f"+{gain} انرژی"
        elif kind == "heal":
            from game import dungeon

            note = dungeon.heal_member(user, member_id, int(effect(kind, klvl)))
        else:
            until = (timezone.now() + datetime.timedelta(hours=SHELL_HOURS)).timestamp() if kind == "shell" else None
            row.armed = {**(row.armed or {}), kind: {"lvl": klvl, "until": until}}
            note = "فعال شد: " + KINDS[kind]["where"]
        row.stock = {**row.stock, kind: int(row.stock.get(kind, 0)) - 1}
        row.used_day, row.used_count = today, used + 1
        if not row.queue:   # room on the shelf again: a paused queue would restart here anyway
            row.brew_started_at = None
        elif row.brew_started_at is None:
            row.brew_started_at = timezone.now()
        row.save()
    return {"kind": kind, "label": label, "note": note, "state": state(user)}


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

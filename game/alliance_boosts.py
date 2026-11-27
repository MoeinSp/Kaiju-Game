"""«بوست‌های اتحاد» — temporary alliance-wide buffs bought from the treasury.

Five keys, each 1M gold / +24h. Buying while active extends the timer (stackable up
to BOOST_MAX_DAYS of remaining time). Leader or deputy buys; every eligible member
gets the effect. Join mid-day → active after Tehran midnight. Expiry is checked live,
so a boost that ends mid-day stops for everyone on the next read.
"""

from __future__ import annotations

import datetime
import time

from django.db import transaction
from django.utils import timezone

from game.creature import GameError

BOOST_COST = 1_000_000
BOOST_HOURS = 24
BOOST_MAX_DAYS = 365  # remaining-time cap: 365 × +24h buys ≈ one year
CACHE_SECONDS = 15

# key → (emoji, title, short description). Effects live where each system reads them.
BOOSTS: dict[str, dict] = {
    "energy_cap": {
        "emoji": "⚡",
        "title": "سقف انرژی",
        "desc": "+۵ سقف انرژی",
        "kind": "flat",
        "value": 5,
    },
    "energy_regen": {
        "emoji": "🔋",
        "title": "بازیابی انرژی",
        "desc": "۱۰٪ بازیابی انرژی سریع‌تر",
        "kind": "regen",
        "value": 1.10,
    },
    "loot": {
        "emoji": "💰",
        "title": "غنیمت شکار و آرنا",
        "desc": "+۱۰٪ طلا و دی‌ان‌ای شکار و آرنا",
        "kind": "mult",
        "value": 1.10,
    },
    "cave": {
        "emoji": "🦇",
        "title": "غار سریع‌تر",
        "desc": "۱۰٪ زمان غار کمتر",
        "kind": "time",
        "value": 0.90,
    },
    "bank": {
        "emoji": "🏦",
        "title": "بانک بزرگ‌تر",
        "desc": "+۱۰٪ سقف واریز و برداشت بانک",
        "kind": "mult",
        "value": 1.10,
    },
}

_CACHE: dict[int, tuple[float, dict[str, datetime.datetime]]] = {}


def _today_start() -> datetime.datetime:
    """Tehran midnight of the current local day (timezone-aware)."""
    return timezone.make_aware(
        datetime.datetime.combine(timezone.localdate(), datetime.time.min)
    )


def member_eligible(user) -> bool:
    """True if this member has been in the alliance since before today's midnight.

    Join mid-day → boosts wait until tomorrow after 00:00 Tehran. Legacy rows with
    no `alliance_joined_at` are treated as eligible (they were already members).
    """
    if not getattr(user, "alliance_id", None):
        return False
    joined = getattr(user, "alliance_joined_at", None)
    if joined is None:
        return True
    return joined < _today_start()


def _load(alliance_id: int) -> dict[str, datetime.datetime]:
    from bio_lab.models import AllianceBoost

    now = timezone.now()
    held = dict(
        AllianceBoost.objects.filter(alliance_id=alliance_id, expires_at__gt=now)
        .values_list("key", "expires_at")
    )
    if len(_CACHE) >= 5_000:
        for key in list(_CACHE)[:1_000]:
            _CACHE.pop(key, None)
    _CACHE[alliance_id] = (time.monotonic() + CACHE_SECONDS, held)
    return held


def invalidate(alliance_id: int | None) -> None:
    if alliance_id is not None:
        _CACHE.pop(int(alliance_id), None)


def active_for_alliance(alliance_id: int | None) -> dict[str, datetime.datetime]:
    """{key: expires_at} of boosts still running on this alliance (no member filter)."""
    if not alliance_id:
        return {}
    aid = int(alliance_id)
    hit = _CACHE.get(aid)
    if hit and hit[0] > time.monotonic():
        # drop anything that expired since the cache filled
        now = timezone.now()
        return {k: exp for k, exp in hit[1].items() if exp > now}
    try:
        return _load(aid)
    except Exception:  # noqa: BLE001 — async handlers call get_max_energy → bonus without run_db
        # serve stale cache if any; otherwise "no boost" beats crashing the card
        if hit:
            now = timezone.now()
            return {k: exp for k, exp in hit[1].items() if exp > now}
        return {}


def active_for_user(user) -> dict[str, datetime.datetime]:
    """Boosts this player actually receives right now (membership + join-day gate)."""
    try:
        if not member_eligible(user):
            return {}
        return active_for_alliance(getattr(user, "alliance_id", None))
    except Exception:  # noqa: BLE001 — same as relics.has: never break the screen that asked
        return {}


def has(user, key: str) -> bool:
    try:
        return key in active_for_user(user)
    except Exception:  # noqa: BLE001
        return False


def bonus(user, key: str) -> int:
    """Flat bonus (energy_cap → +5, else 0)."""
    if not has(user, key):
        return 0
    return int(BOOSTS.get(key, {}).get("value", 0) or 0) if BOOSTS.get(key, {}).get("kind") == "flat" else 0


def mult(user, key: str) -> float:
    """Multiplier for loot/bank (1.0 when inactive). Cave uses time_mult()."""
    if not has(user, key):
        return 1.0
    defn = BOOSTS.get(key) or {}
    if defn.get("kind") not in ("mult", "regen"):
        return 1.0
    return float(defn["value"])


def time_mult(user, key: str = "cave") -> float:
    """Duration scale for cave timers (0.90 when active, else 1.0)."""
    if not has(user, key):
        return 1.0
    defn = BOOSTS.get(key) or {}
    if defn.get("kind") != "time":
        return 1.0
    return float(defn["value"])


def regen_divisor(user) -> float:
    """Divide the energy regen interval by this (1.10 when energy_regen is on)."""
    return mult(user, "energy_regen") if has(user, "energy_regen") else 1.0


@transaction.atomic
def buy(user, key: str) -> dict:
    """Leader/deputy spends treasury gold to activate or extend a boost by +24h."""
    from bio_lab.models import Alliance, AllianceBoost
    from game import alliance as alliance_mod

    if key not in BOOSTS:
        raise GameError("این بوست وجود نداره.")
    if user.alliance_id is None:
        raise GameError("اول باید عضو یه اتحاد باشی.")
    al = Alliance.objects.select_for_update().get(id=user.alliance_id)
    if not alliance_mod._is_manager(user, al):
        raise GameError("فقط رهبر یا قائم‌مقام اتحاد می‌تونه بوست بخره.")
    if al.treasury_gold < BOOST_COST:
        raise GameError(
            f"خزانه کافی نیست. این بوست {BOOST_COST:,} طلا می‌خواد و "
            f"الان {al.treasury_gold:,} طلا توی خزانه‌ست."
        )
    now = timezone.now()
    row = (
        AllianceBoost.objects.select_for_update()
        .filter(alliance=al, key=key)
        .first()
    )
    base = row.expires_at if (row is not None and row.expires_at > now) else now
    extended = base + datetime.timedelta(hours=BOOST_HOURS)
    max_expires = now + datetime.timedelta(days=BOOST_MAX_DAYS)
    if extended > max_expires:
        raise GameError(
            f"سقف تمدید این بوست {BOOST_MAX_DAYS} روزه. "
            "اول صبر کن کمی از زمانش کم بشه، بعد دوباره بخر."
        )
    al.treasury_gold -= BOOST_COST
    al.save(update_fields=["treasury_gold"])
    if row is None:
        AllianceBoost.objects.create(
            alliance=al, key=key, expires_at=extended, bought_by=user,
        )
    else:
        row.expires_at = extended
        row.bought_by = user
        row.bought_at = now
        row.save(update_fields=["expires_at", "bought_by", "bought_at"])
    invalidate(al.id)
    defn = BOOSTS[key]
    return {
        "key": key,
        "title": defn["title"],
        "desc": defn["desc"],
        "emoji": defn["emoji"],
        "cost": BOOST_COST,
        "expires_at": extended,
        "treasury": al.treasury_gold,
        "hours": BOOST_HOURS,
        "extended": base > now,
        "seconds_left": max(0, int((extended - now).total_seconds())),
    }


def panel(user) -> dict | None:
    """Payload for the boost screen (bot + mini-app). None if the player has no alliance."""
    from bio_lab.models import Alliance
    from game import alliance as alliance_mod

    if user.alliance_id is None:
        return None
    al = Alliance.objects.filter(id=user.alliance_id).first()
    if al is None:
        return None
    now = timezone.now()
    active = active_for_alliance(al.id)
    eligible = member_eligible(user)
    items = []
    for key, defn in BOOSTS.items():
        exp = active.get(key)
        on = exp is not None and exp > now
        items.append({
            "key": key,
            "emoji": defn["emoji"],
            "title": defn["title"],
            "desc": defn["desc"],
            "cost": BOOST_COST,
            "active": on,
            "seconds_left": max(0, int((exp - now).total_seconds())) if on else 0,
            # for this viewer: alliance has it, but join-day gate blocks them
            "active_for_me": on and eligible,
        })
    return {
        "name": al.name,
        "treasury": al.treasury_gold,
        "cost": BOOST_COST,
        "hours": BOOST_HOURS,
        "max_days": BOOST_MAX_DAYS,
        "is_manager": alliance_mod._is_manager(user, al),
        "eligible": eligible,
        "joined_today": not eligible,
        "boosts": items,
    }

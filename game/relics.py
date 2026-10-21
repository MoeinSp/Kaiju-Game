"""«آثار باستانی» — temporary account-wide buffs dropped by the dungeon boss (game/dungeon.py).

A relic lasts RELIC_DAYS; dropping the same one again adds RELIC_DAYS more, but the time left
never goes past RELIC_MAX_DAYS (owner, 2026-10-21: "not permanent"). None of them touch combat
power, so the arena balance doesn't move.

`has()` sits on hot paths (the energy cap is read on almost every screen), so each process
keeps a short cache per player; a grant in the same process refreshes it, and other
processes see it within CACHE_SECONDS.
"""

from __future__ import annotations

import datetime
import random
import time

from django.utils import timezone

RELIC_DAYS = 3
RELIC_MAX_DAYS = 14
CACHE_SECONDS = 60

# key → (label, short description). Effects are applied where each system lives (see VALUES).
RELICS: dict[str, tuple[str, str]] = {
    "golden_idol": ("بت طلایی", "+۱۰٪ طلای شکار"),
    "dna_vial": ("شیشه‌ی ژن", "+۱۰٪ DNA شکار"),
    "sand_clock": ("ساعت شنی", "جعبه‌های آرنا ۱۵٪ سریع‌تر باز می‌شن"),
    "heart_stone": ("سنگ قلب", "+۵ سقف انرژی"),
    "compass": ("قطب‌نمای کهن", "+۱ جایگاه اعزام"),
    "war_banner": ("پرچم جنگ", "+۱۰٪ غنیمت آرنا"),
    "titan_fang": ("نیش تایتان", "+۱۰٪ آسیب به غول سرگردان"),
    "shadow_key": ("کلید سایه", "+۱ کلید روزانه‌ی سیاه‌چال"),
}
VALUES = {
    "golden_idol": 1.10, "dna_vial": 1.10, "sand_clock": 0.85, "heart_stone": 5,
    "compass": 1, "war_banner": 1.10, "titan_fang": 1.10, "shadow_key": 1,
}

_CACHE: dict[int, tuple[float, dict[str, datetime.datetime]]] = {}


def _load(user_id: int) -> dict[str, datetime.datetime]:
    from bio_lab.models import UserRelic

    now = timezone.now()
    held = dict(UserRelic.objects.filter(user_id=user_id, expires_at__gt=now).values_list("key", "expires_at"))
    _CACHE[user_id] = (time.monotonic() + CACHE_SECONDS, held)
    return held


def active(user) -> dict[str, datetime.datetime]:
    """{key: expires_at} of the relics this player holds right now."""
    uid = getattr(user, "id", user)
    hit = _CACHE.get(uid)
    held = hit[1] if hit and hit[0] > time.monotonic() else _load(uid)
    now = timezone.now()
    return {k: v for k, v in held.items() if v > now}


def has(user, key: str) -> bool:
    try:
        return key in active(user)
    except Exception:  # noqa: BLE001 — a buff lookup must never break the screen that asked
        return False


def mult(user, key: str) -> float:
    """The multiplier of a %-relic, or 1.0 when the player doesn't hold it."""
    return float(VALUES[key]) if has(user, key) else 1.0


def bonus(user, key: str) -> int:
    """The flat bonus of a count-relic, or 0."""
    return int(VALUES[key]) if has(user, key) else 0


def grant(user, key: str | None = None) -> dict:
    """Give a relic (random when `key` is None). Returns {key, label, desc, expires_at, extended}."""
    from bio_lab.models import UserRelic

    key = key or random.choice(list(RELICS))
    now = timezone.now()
    cap = now + datetime.timedelta(days=RELIC_MAX_DAYS)
    row = UserRelic.objects.select_for_update().filter(user=user, key=key).first()
    extended = bool(row and row.expires_at > now)
    base = row.expires_at if extended else now
    expires = min(cap, base + datetime.timedelta(days=RELIC_DAYS))
    if row is None:
        UserRelic.objects.create(user=user, key=key, expires_at=expires)
    else:
        row.expires_at = expires
        row.times += 1
        row.save(update_fields=["expires_at", "times"])
    _load(user.id)
    label, desc = RELICS[key]
    return {"key": key, "label": label, "desc": desc, "expires_at": expires, "extended": extended}


def album(user) -> list[dict]:
    """Every relic with whether it's active, time left, and how many times it dropped."""
    from bio_lab.models import UserRelic

    now = timezone.now()
    rows = {r.key: r for r in UserRelic.objects.filter(user=user)}
    out = []
    for key, (label, desc) in RELICS.items():
        r = rows.get(key)
        left = max(0, int((r.expires_at - now).total_seconds())) if r else 0
        out.append({"key": key, "label": label, "desc": desc, "active": left > 0, "seconds_left": left,
                    "times": r.times if r else 0})
    return out

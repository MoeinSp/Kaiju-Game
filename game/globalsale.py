"""Store-wide Toman sale («تخفیف همگانی»).

Applies to every real-money checkout: purchase packs, custom gold/DNA/diamond
buy, VIP subscriptions, and ShopItem / paid-offer rows with ``price_toman``.
In-game gold/diamond prices (daily shop, biocrate, …) are never touched.

The pay price is always snapshotted onto ``PurchaseRequest.price_toman`` at
order time, so a sale ending mid-review does not change what the player owed.
"""

from __future__ import annotations

import datetime

from django.utils import timezone

from game.creature import GameError

MAX_PERCENT = 90
MIN_PERCENT = 1
MAX_HOURS = 24 * 30

# process cache — buy screens are built in async handlers; a cold miss just
# means "no sale" for one render until the next sync refresh.
_cache: dict | None = None
_cache_at: float = 0.0
_CACHE_TTL = 15.0


def discounted(price: int, percent: int) -> int:
    """Price after ``percent`` off, rounded to a thousand toman (same as VIP)."""
    if percent <= 0:
        return max(0, int(price))
    return max(0, int(round(int(price) * (100 - int(percent)) / 100 / 1000) * 1000))


def refresh_cache() -> None:
    """Drop the in-memory active-sale snapshot (call after start/stop)."""
    global _cache, _cache_at
    _cache = None
    _cache_at = 0.0


def _load_active():
    from bio_lab.models import GlobalSale

    now = timezone.now()
    return (
        GlobalSale.objects.filter(cancelled=False, starts_at__lte=now, ends_at__gt=now)
        .order_by("-percent", "-id")
        .first()
    )


def active():
    """Cached snapshot only — safe from async. Cold cache = no sale until a sync path warms it."""
    import time

    if _cache is None:
        return None
    if (time.monotonic() - _cache_at) >= _CACHE_TTL:
        return None
    return _cache.get("sale")


def active_sync():
    """Fresh DB read + cache warm. Use inside sync/run_db paths that charge or render."""
    import time

    global _cache, _cache_at
    sale = _load_active()
    _cache = {"sale": sale}
    _cache_at = time.monotonic()
    return sale


def apply(price: int, sale=None) -> int:
    """Final Toman to charge for ``price`` under the active (or given) sale.

    Without an explicit ``sale``, uses the async-safe cache (``active()``). Sync
    checkout paths must call ``active_sync()`` first (or pass the row) so the
    cache is warm before money is quoted.
    """
    if sale is None:
        sale = active()
    if sale is None:
        return max(0, int(price))
    return discounted(int(price), sale.percent)


def view(catalog_price: int, catalog_discount: int = 0, sale=None) -> dict:
    """Display + pay numbers for one Toman item.

    ``catalog_price`` is what the player would pay without a global sale.
    ``catalog_discount`` is any marketing % already baked into that price.
    Sync callers should warm with ``active_sync()`` first; otherwise ``sale`` is
    taken from the cache.
    """
    if sale is False:
        sale = None
    elif sale is None:
        sale = active()
    list_price = max(0, int(catalog_price))
    cat_d = max(0, min(95, int(catalog_discount or 0)))
    marketing_was = (
        round(list_price / (1 - cat_d / 100)) if cat_d and list_price else list_price
    )
    if sale is None:
        return {
            "price": list_price,
            "original": marketing_was if marketing_was > list_price else list_price,
            "discount": cat_d,
            "sale_percent": 0,
            "sale_reason": "",
            "list_price": list_price,
        }
    pay = discounted(list_price, sale.percent)
    original = max(marketing_was, list_price)
    return {
        "price": pay,
        "original": original if original > pay else list_price,
        "discount": sale.percent,
        "sale_percent": sale.percent,
        "sale_reason": (sale.reason or "").strip(),
        "list_price": list_price,
    }


def banner_text(sale=None) -> str:
    """One HTML line for store headers, or empty when nothing is running."""
    sale = active() if sale is None else sale
    if sale is None:
        return ""
    reason = (sale.reason or "").strip()
    left = max(0, int((sale.ends_at - timezone.now()).total_seconds()))
    h, rem = divmod(left, 3600)
    m = rem // 60
    when = f"{h}س و {m}د" if h else f"{m} دقیقه"
    title = f"به مناسبت «{reason}»" if reason else "تخفیف همگانی"
    return f"🔥 <b>{title}: {sale.percent}٪ روی همهٔ خریدهای تومانی</b> · ⏳ {when}"


def start(percent: int, hours: int, reason: str = "", broadcast: bool = True):
    """Cancel any running sale and open a new one. Returns the new row."""
    from bio_lab.models import GlobalSale

    percent = int(percent)
    hours = int(hours)
    if not MIN_PERCENT <= percent <= MAX_PERCENT:
        raise GameError(f"تخفیف باید بین {MIN_PERCENT} تا {MAX_PERCENT} درصد باشه.")
    if not 1 <= hours <= MAX_HOURS:
        raise GameError("مدت نامعتبره.")
    reason = (reason or "").strip()[:64]
    now = timezone.now()
    GlobalSale.objects.filter(cancelled=False, ends_at__gt=now).update(cancelled=True)
    sale = GlobalSale.objects.create(
        percent=percent,
        reason=reason,
        starts_at=now,
        ends_at=now + datetime.timedelta(hours=hours),
        broadcast=bool(broadcast),
    )
    refresh_cache()
    return sale


def stop() -> int:
    """Cancel every running global sale. Returns how many rows were cancelled."""
    from bio_lab.models import GlobalSale

    now = timezone.now()
    n = GlobalSale.objects.filter(cancelled=False, ends_at__gt=now).update(cancelled=True)
    refresh_cache()
    return n


def hours_left(sale) -> int:
    return max(0, int((sale.ends_at - timezone.now()).total_seconds() // 3600))

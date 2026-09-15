"""In-bot purchase flow — buy gold / DNA / diamonds with real money.

The player picks amounts (stepper buttons), the bot quotes a Toman price from the
owner-set per-unit prices (game.botconfig) and shows the owner's card, the player
uploads a receipt photo, and the owner approves or rejects. Approval credits the
resources. All money handling is manual (card-to-card) — the bot only brokers the
request and the owner's decision; it never touches a payment API.
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from bio_lab.models import PurchaseRequest, User
from game import botconfig
from game.creature import GameError

# stepper increments per tap, and a sane ceiling so a mis-tap can't request millions
STEP = {"coins": 10_000, "dna": 1_000, "diamonds": 50}
MAX_UNITS = {"coins": 50_000_000, "dna": 500_000, "diamonds": 1_000_000}
RES_LABEL = {"coins": "طلا", "dna": "DNA", "diamonds": "الماس"}
RES_EMOJI = {"coins": "🪙", "dna": "🧬", "diamonds": "💎"}


def price_for(coins: int, dna: int, diamonds: int) -> int:
    p = botconfig.get_buy_prices()
    return round(coins * p["coins"] + dna * p["dna"] + diamonds * p["diamonds"])


def create_pending(user: User, coins: int, dna: int, diamonds: int) -> PurchaseRequest:
    """Create an awaiting-receipt request for the chosen amounts. Raises if the player
    is receipt-blocked, picked nothing, or picked a resource that isn't for sale."""
    if user.receipt_blocked:
        raise GameError("⛔ دسترسی تو به ثبت رسید خرید مسدود شده. با پشتیبانی در تماس باش.")
    prices = botconfig.get_buy_prices()
    coins, dna, diamonds = max(0, int(coins)), max(0, int(dna)), max(0, int(diamonds))
    for res, amount in (("coins", coins), ("dna", dna), ("diamonds", diamonds)):
        if amount > 0 and prices[res] <= 0:
            raise GameError(f"{RES_LABEL[res]} الان برای فروش نیست — مقدارش رو صفر کن.")
    price = price_for(coins, dna, diamonds)
    if price <= 0:
        raise GameError("اول مقدار چیزی که می‌خوای بخری رو انتخاب کن.")
    minimum = botconfig.get_buy_min()
    if minimum > 0 and price < minimum:
        raise GameError(
            f"حداقل مبلغ خرید {minimum:,} تومان است. الان سبد تو {price:,} تومانه — "
            f"مقدارِ بیشتری انتخاب کن."
        )
    # keep only one live draft per user — drop any older unfinished ones
    PurchaseRequest.objects.filter(user=user, status="awaiting_receipt").delete()
    return PurchaseRequest.objects.create(
        user=user, coins=coins, dna=dna, diamonds=diamonds, price_toman=price, status="awaiting_receipt"
    )


def create_subscription_pending(user: User, tier: str) -> PurchaseRequest:
    """Create an awaiting-receipt request for a 30-day VIP subscription."""
    if user.receipt_blocked:
        raise GameError("⛔ دسترسی تو به ثبت رسید خرید مسدود شده. با پشتیبانی در تماس باش.")
    from game.subscription import SUBSCRIPTION_TIERS
    cfg = SUBSCRIPTION_TIERS.get(tier)
    if not cfg:
        raise GameError("سطح اشتراک نامعتبر است.")
    price = cfg["price_toman"]
    PurchaseRequest.objects.filter(user=user, status="awaiting_receipt").delete()
    return PurchaseRequest.objects.create(
        user=user,
        coins=0,
        dna=0,
        diamonds=0,
        subscription_tier=tier,
        price_toman=price,
        status="awaiting_receipt",
    )


# ── purchase packs (owner-authored fixed-price bundles) ───────────────────────

def _refresh_pack_cache() -> None:
    """Keep botconfig's cached active-pack count in sync after a pack write, so the menu
    (built in async code) shows/hides the buy button without a DB hit."""
    from game import botconfig

    botconfig.refresh_cache()


def pack_original_price(price_toman: int, discount_percent: int) -> int:
    """The struck-through "before discount" price implied by a final price + discount %.
    Returns the final price itself when there's no discount."""
    d = max(0, min(95, int(discount_percent or 0)))
    if d <= 0:
        return int(price_toman)
    return round(int(price_toman) / (1 - d / 100))


def _pack_to_dict(p) -> dict:
    return {
        "id": p.id,
        "title": p.title,
        "emoji": p.emoji or "🎁",
        "coins": p.coins,
        "dna": p.dna,
        "diamonds": p.diamonds,
        "price": p.price_toman,
        "discount": p.discount_percent,
        "original": pack_original_price(p.price_toman, p.discount_percent),
        "active": p.active,
        "sort_order": p.sort_order,
        "contents": pack_contents_summary(p),
    }


def pack_contents_summary(p) -> str:
    """A one-line «🪙 X · 🧬 Y · 💎 Z» summary of a pack's contents (non-zero only)."""
    parts = []
    for res in ("coins", "dna", "diamonds"):
        amount = getattr(p, res)
        if amount:
            parts.append(f"{RES_EMOJI[res]} {amount:,} {RES_LABEL[res]}")
    return " · ".join(parts) or "—"


def list_active_packs() -> list[dict]:
    """Active packs for the player-facing store, ordered by sort_order then id."""
    from bio_lab.models import PurchasePack

    return [_pack_to_dict(p) for p in PurchasePack.objects.filter(active=True)]


def list_all_packs() -> list[dict]:
    """Every pack (active + inactive) for the admin manager."""
    from bio_lab.models import PurchasePack

    return [_pack_to_dict(p) for p in PurchasePack.objects.all()]


def get_pack(pack_id: int) -> dict | None:
    from bio_lab.models import PurchasePack

    p = PurchasePack.objects.filter(id=pack_id).first()
    return _pack_to_dict(p) if p else None


def create_pack(title, coins, dna, diamonds, price_toman, discount_percent, emoji="🎁"):
    """Create a pack. Raises GameError on invalid input (empty contents / non-positive
    price). Returns the new pack's dict."""
    from bio_lab.models import PurchasePack

    title = (title or "").strip()[:64]
    if not title:
        raise GameError("عنوان پک نمی‌تونه خالی باشه.")
    coins, dna, diamonds = max(0, int(coins)), max(0, int(dna)), max(0, int(diamonds))
    if coins == 0 and dna == 0 and diamonds == 0:
        raise GameError("پک باید حداقل یکی از طلا / DNA / الماس رو داشته باشه.")
    price_toman = int(price_toman)
    if price_toman <= 0:
        raise GameError("قیمت پک باید بزرگ‌تر از صفر باشه.")
    discount_percent = max(0, min(95, int(discount_percent or 0)))
    last = PurchasePack.objects.order_by("-sort_order").first()
    sort_order = (last.sort_order + 1) if last else 0
    p = PurchasePack.objects.create(
        title=title, emoji=(emoji or "🎁")[:8], coins=coins, dna=dna, diamonds=diamonds,
        price_toman=price_toman, discount_percent=discount_percent, sort_order=sort_order,
    )
    _refresh_pack_cache()
    return _pack_to_dict(p)


def update_pack(pack_id, title, coins, dna, diamonds, price_toman, discount_percent, emoji="🎁"):
    from bio_lab.models import PurchasePack

    p = PurchasePack.objects.filter(id=pack_id).first()
    if p is None:
        raise GameError("این پک پیدا نشد.")
    title = (title or "").strip()[:64]
    if not title:
        raise GameError("عنوان پک نمی‌تونه خالی باشه.")
    coins, dna, diamonds = max(0, int(coins)), max(0, int(dna)), max(0, int(diamonds))
    if coins == 0 and dna == 0 and diamonds == 0:
        raise GameError("پک باید حداقل یکی از طلا / DNA / الماس رو داشته باشه.")
    price_toman = int(price_toman)
    if price_toman <= 0:
        raise GameError("قیمت پک باید بزرگ‌تر از صفر باشه.")
    p.title = title
    p.emoji = (emoji or "🎁")[:8]
    p.coins, p.dna, p.diamonds = coins, dna, diamonds
    p.price_toman = price_toman
    p.discount_percent = max(0, min(95, int(discount_percent or 0)))
    p.save(update_fields=["title", "emoji", "coins", "dna", "diamonds",
                          "price_toman", "discount_percent"])
    return _pack_to_dict(p)


def toggle_pack(pack_id: int) -> dict:
    from bio_lab.models import PurchasePack

    p = PurchasePack.objects.filter(id=pack_id).first()
    if p is None:
        raise GameError("این پک پیدا نشد.")
    p.active = not p.active
    p.save(update_fields=["active"])
    _refresh_pack_cache()
    return _pack_to_dict(p)


def delete_pack(pack_id: int) -> bool:
    from bio_lab.models import PurchasePack

    deleted, _ = PurchasePack.objects.filter(id=pack_id).delete()
    _refresh_pack_cache()
    return deleted > 0


def create_pack_pending(user: User, pack_id: int) -> PurchaseRequest:
    """Create an awaiting-receipt request for a fixed-price pack. Raises if the player
    is receipt-blocked or the pack is gone/inactive."""
    from bio_lab.models import PurchasePack

    if user.receipt_blocked:
        raise GameError("⛔ دسترسی تو به ثبت رسید خرید مسدود شده. با پشتیبانی در تماس باش.")
    p = PurchasePack.objects.filter(id=pack_id, active=True).first()
    if p is None:
        raise GameError("این پک دیگه در دسترس نیست.")
    PurchaseRequest.objects.filter(user=user, status="awaiting_receipt").delete()
    return PurchaseRequest.objects.create(
        user=user, coins=p.coins, dna=p.dna, diamonds=p.diamonds,
        price_toman=p.price_toman, status="awaiting_receipt",
    )


def attach_receipt(req_id: int, user_id: int, file_id: str) -> PurchaseRequest | None:
    """Bind the uploaded receipt photo to the request and move it to 'pending' review.
    Returns the request, or None if it's gone / not this user's / not awaiting a receipt."""
    req = PurchaseRequest.objects.filter(id=req_id, user_id=user_id).first()
    if req is None or req.status != "awaiting_receipt":
        return None
    req.receipt_file_id = file_id
    req.status = "pending"
    req.save(update_fields=["receipt_file_id", "status"])
    return req


@transaction.atomic
def approve(req_id: int) -> dict:
    """Credit the requested resources and mark the request approved. Idempotent-safe:
    a request that isn't 'pending' any more is reported, not double-granted."""
    req = PurchaseRequest.objects.select_for_update().filter(id=req_id).first()
    if req is None:
        raise GameError("این درخواست پیدا نشد.")
    if req.status != "pending":
        raise GameError(f"این درخواست قبلاً رسیدگی شده (وضعیت: {req.status}).")
    user = User.objects.select_for_update().get(id=req.user_id)
    if req.subscription_tier:
        from game.subscription import activate_subscription
        activate_subscription(user, req.subscription_tier, days=30)
    user.coins += req.coins
    user.dna_fragments += req.dna
    user.diamonds += req.diamonds
    user.save(update_fields=["coins", "dna_fragments", "diamonds"])
    req.status = "approved"
    req.reviewed_at = timezone.now()
    req.save(update_fields=["status", "reviewed_at"])
    from game.ledger import record_gain

    record_gain(user, "purchase", coins=req.coins, dna=req.dna, diamonds=req.diamonds)
    return {
        "user_id": user.id,
        "coins": req.coins,
        "dna": req.dna,
        "diamonds": req.diamonds,
        "subscription_tier": req.subscription_tier,
        "price": req.price_toman,
        "channel_chat_id": req.channel_chat_id,
        "channel_message_id": req.channel_message_id,
    }


@transaction.atomic
def reject(req_id: int) -> dict:
    req = PurchaseRequest.objects.select_for_update().filter(id=req_id).first()
    if req is None:
        raise GameError("این درخواست پیدا نشد.")
    if req.status != "pending":
        raise GameError(f"این درخواست قبلاً رسیدگی شده (وضعیت: {req.status}).")
    req.status = "rejected"
    req.reviewed_at = timezone.now()
    req.save(update_fields=["status", "reviewed_at"])
    return {
        "user_id": req.user_id,
        "price": req.price_toman,
        "coins": req.coins,
        "dna": req.dna,
        "diamonds": req.diamonds,
        "subscription_tier": req.subscription_tier,
        "channel_chat_id": req.channel_chat_id,
        "channel_message_id": req.channel_message_id,
    }


def set_channel_message(req_id: int, chat_id: int, message_id: int) -> None:
    """Remember the purchase-report message posted to the channel so approve/reject can
    edit it to reflect the final status."""
    PurchaseRequest.objects.filter(id=req_id).update(
        channel_chat_id=chat_id, channel_message_id=message_id
    )


def set_receipt_block(user_id: int, blocked: bool) -> User:
    user = User.objects.filter(id=user_id).first()
    if user is None:
        raise GameError("این کاربر پیدا نشد.")
    user.receipt_blocked = blocked
    user.save(update_fields=["receipt_blocked"])
    return user


def daily_purchase_report(day) -> dict:
    """Build the owner's daily sales report for a single local (Tehran) calendar day.

    Only APPROVED purchases count — a sale is "made" the moment the owner approves the
    receipt, so we key off ``reviewed_at`` (the approval time), and rejected / pending /
    still-awaiting-receipt requests are deliberately excluded. Returns fully pre-rendered
    rows + aggregates so the async handler never touches the DB while formatting.

    ``day`` is a ``datetime.date`` in the game timezone.
    """
    import html as _html

    from bio_lab.repository import display_name

    qs = (
        PurchaseRequest.objects.filter(status="approved", reviewed_at__date=day)
        .select_related("user")
        .order_by("reviewed_at")
    )
    rows: list[dict] = []
    total_toman = 0
    sub_counts: dict[str, int] = {}
    res_totals = {"coins": 0, "dna": 0, "diamonds": 0}
    for req in qs:
        total_toman += req.price_toman or 0
        if req.subscription_tier:
            sub_counts[req.subscription_tier] = sub_counts.get(req.subscription_tier, 0) + 1
        else:
            res_totals["coins"] += req.coins
            res_totals["dna"] += req.dna
            res_totals["diamonds"] += req.diamonds
        local_dt = timezone.localtime(req.reviewed_at) if req.reviewed_at else None
        rows.append(
            {
                "time": local_dt.strftime("%H:%M") if local_dt else "—",
                "name": _html.escape(display_name(req.user)),
                "user_id": req.user_id,
                "summary": request_summary(req),
                "price": req.price_toman or 0,
                "is_sub": bool(req.subscription_tier),
            }
        )
    return {
        "day": day,
        "count": len(rows),
        "total_toman": total_toman,
        "sub_counts": sub_counts,
        "res_totals": res_totals,
        "rows": rows,
    }


def request_summary(req: PurchaseRequest) -> str:
    """A one-line-per-resource summary of what a request buys (only non-zero items)."""
    if getattr(req, "subscription_tier", None):
        from game.subscription import SUBSCRIPTION_TIERS
        sub = SUBSCRIPTION_TIERS.get(req.subscription_tier)
        name = sub["name"] if sub else req.subscription_tier
        badge = sub["badge"] if sub else "⭐"
        return f"{badge} {name} (۳۰ روزه)"
    parts = []
    for res in ("coins", "dna", "diamonds"):
        amount = getattr(req, res if res != "coins" else "coins")
        if amount:
            parts.append(f"{RES_EMOJI[res]} {amount:,} {RES_LABEL[res]}")
    return " · ".join(parts) or "—"

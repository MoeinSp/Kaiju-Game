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
    # keep only one live draft per user — drop any older unfinished ones
    PurchaseRequest.objects.filter(user=user, status="awaiting_receipt").delete()
    return PurchaseRequest.objects.create(
        user=user, coins=coins, dna=dna, diamonds=diamonds, price_toman=price, status="awaiting_receipt"
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
    user.coins += req.coins
    user.dna_fragments += req.dna
    user.diamonds += req.diamonds
    user.save(update_fields=["coins", "dna_fragments", "diamonds"])
    req.status = "approved"
    req.reviewed_at = timezone.now()
    req.save(update_fields=["status", "reviewed_at"])
    from game.ledger import record_gain

    record_gain(user, "purchase", coins=req.coins, dna=req.dna, diamonds=req.diamonds)
    return {"user_id": user.id, "coins": req.coins, "dna": req.dna, "diamonds": req.diamonds,
            "price": req.price_toman}


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
    return {"user_id": req.user_id, "price": req.price_toman}


def set_receipt_block(user_id: int, blocked: bool) -> User:
    user = User.objects.filter(id=user_id).first()
    if user is None:
        raise GameError("این کاربر پیدا نشد.")
    user.receipt_blocked = blocked
    user.save(update_fields=["receipt_blocked"])
    return user


def request_summary(req: PurchaseRequest) -> str:
    """A one-line-per-resource summary of what a request buys (only non-zero items)."""
    parts = []
    for res in ("coins", "dna", "diamonds"):
        amount = getattr(req, res if res != "coins" else "coins")
        if amount:
            parts.append(f"{RES_EMOJI[res]} {amount:,} {RES_LABEL[res]}")
    return " · ".join(parts) or "—"

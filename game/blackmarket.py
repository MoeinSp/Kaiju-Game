"""Midnight Black Market (بازار سیاه و مزایده‌های نیمه‌شب).

Auctions run with rare items, genetic crates, diamond packs, or high-tier creatures.
Players place bids; outbid players are instantly refunded.
"""

from __future__ import annotations

import datetime
from django.db import transaction
from django.utils import timezone

from bio_lab.models import BlackMarketAuction, User
from bio_lab.repository import lab_display
from game.creature import GameError
from game.emoji import get_emoji


def get_min_bid_increment(current_bid: int, currency: str = "coins") -> int:
    """Calculate dynamic minimum bid increment based on the auction's current price."""
    if currency == "coins":
        if current_bid < 100_000:
            return 5_000
        elif current_bid < 500_000:
            return 25_000
        elif current_bid < 1_000_000:
            return 50_000
        elif current_bid < 5_000_000:
            return 100_000
        elif current_bid < 10_000_000:
            return 250_000
        else:
            return 500_000
    else:  # diamonds
        if current_bid < 100:
            return 10
        elif current_bid < 500:
            return 25
        elif current_bid < 1_000:
            return 50
        elif current_bid < 5_000:
            return 100
        else:
            return 250


def get_active_auctions() -> list[BlackMarketAuction]:
    """Get active non-settled auctions."""
    _settle_expired_auctions()
    _ensure_daily_auctions()
    return list(BlackMarketAuction.objects.filter(is_settled=False, ends_at__gt=timezone.now()).order_by("id"))


def _ensure_daily_auctions() -> None:
    """Create default daily auctions if none are active."""
    now = timezone.now()
    active_count = BlackMarketAuction.objects.filter(is_settled=False, ends_at__gt=now).count()
    if active_count > 0:
        return

    # Midnight expiration
    tomorrow = now.date() + datetime.timedelta(days=1)
    ends_at = timezone.make_aware(datetime.datetime.combine(tomorrow, datetime.time(23, 59, 59)))

    # Auction 1: 500 Diamonds
    BlackMarketAuction.objects.create(
        title="💎 محموله ۵۰۰ تایی الماس خالص",
        item_type="diamonds",
        item_payload={"amount": 500},
        bid_currency="coins",
        min_bid=50000,
        current_bid=50000,
        ends_at=ends_at,
    )

    # Auction 2: 10 Biocrate Tickets
    BlackMarketAuction.objects.create(
        title="🎫 بسته ۱۰ عددی بلیط باکس ژنتیکی",
        item_type="tickets",
        item_payload={"amount": 10},
        bid_currency="coins",
        min_bid=30000,
        current_bid=30000,
        ends_at=ends_at,
    )

    # Auction 3: Legendary Creature Egg (VIP)
    BlackMarketAuction.objects.create(
        title="👑 🦖 تخم کایجوی افسانه‌ای ۳ ستاره (VIP)",
        item_type="creature",
        item_payload={"rarity": "legendary", "star": 3, "level": 1},
        bid_currency="diamonds",
        min_bid=200,
        current_bid=200,
        ends_at=ends_at,
    )


@transaction.atomic
def place_bid(user: User, auction_id: int, bid_amount: int) -> dict:
    """Place a bid on an auction. Refunds previous bidder and deducts currency from new bidder."""
    now = timezone.now()
    auction = BlackMarketAuction.objects.select_for_update().filter(id=auction_id, is_settled=False).first()
    if auction is None or auction.ends_at <= now:
        raise GameError("این مزایده به پایان رسیده یا نامعتبر است.")

    user = User.objects.select_for_update().get(id=user.id)

    # 1. VIP Gate: creature/diamond lots require active Silver or Gold subscription
    is_vip_lot = (auction.bid_currency == "diamonds" or auction.item_type == "creature" or "(VIP)" in auction.title)
    if is_vip_lot:
        from game.subscription import is_subscription_active
        if not is_subscription_active(user):
            raise GameError("👑 این مزایده VIP است و فقط دارندگان «اشتراک نقره‌ای» یا «اشتراک طلایی» می‌توانند در آن شرکت کنند!")

    # 2. Anti-Monopoly Gate: user can hold top bid on at most 1 active auction at a time
    other_active = BlackMarketAuction.objects.filter(
        is_settled=False,
        ends_at__gt=now,
        highest_bidder_id=user.id,
    ).exclude(id=auction.id).first()
    if other_active:
        raise GameError(
            f"⚠️ شما در حال حاضر بالاترین پیشنهاد را روی «{other_active.title}» دارید!\n"
            "برای حفظ تعادل بازار و جلوگیری از انحصار، هر بازیکن همزمان می‌تواند بالاترین پیشنهاد ۱ مزایده را داشته باشد."
        )

    if auction.highest_bidder is None:
        min_required = auction.min_bid
        step = get_min_bid_increment(auction.min_bid, auction.bid_currency)
    else:
        step = get_min_bid_increment(auction.current_bid, auction.bid_currency)
        min_required = auction.current_bid + step

    if bid_amount < min_required:
        curr_label = "طلا" if auction.bid_currency == "coins" else "الماس"
        raise GameError(
            f"حداقل پیشنهاد بعدی باید {min_required:,} {curr_label} باشد.\n"
            f"(حداقل افزایش با توجه به قیمت فعلی: +{step:,} {curr_label})"
        )

    prev_bidder = auction.highest_bidder
    prev_amount = auction.current_bid
    outbid_info = None

    # Handle bidding on own top bid vs outbidding someone else
    if prev_bidder is not None and prev_bidder.id == user.id:
        # User is increasing their OWN leading bid -> only charge the difference
        cost = bid_amount - prev_amount
        if cost > 0:
            if auction.bid_currency == "coins":
                if user.coins < cost:
                    raise GameError(f"طلای کافی برای افزایش پیشنهاد نداری! (موجودی: {user.coins:,} طلا، نیاز: {cost:,})")
                user.coins -= cost
                user.save(update_fields=["coins"])
            else:
                if user.diamonds < cost:
                    raise GameError(f"الماس کافی برای افزایش پیشنهاد نداری! (موجودی: {user.diamonds:,} الماس، نیاز: {cost:,})")
                user.diamonds -= cost
                user.save(update_fields=["diamonds"])
    else:
        # New or different bidder -> charge full bid_amount from user
        if auction.bid_currency == "coins":
            if user.coins < bid_amount:
                raise GameError(f"طلای کافی نداری! (موجودی: {user.coins:,})")
            user.coins -= bid_amount
            user.save(update_fields=["coins"])
        else:
            if user.diamonds < bid_amount:
                raise GameError(f"الماس کافی نداری! (موجودی: {user.diamonds:,})")
            user.diamonds -= bid_amount
            user.save(update_fields=["diamonds"])

        # Refund previous highest bidder if different user
        if prev_bidder is not None:
            prev_user = User.objects.select_for_update().filter(id=prev_bidder.id).first()
            if prev_user:
                if auction.bid_currency == "coins":
                    prev_user.coins += prev_amount
                    prev_user.save(update_fields=["coins"])
                else:
                    prev_user.diamonds += prev_amount
                    prev_user.save(update_fields=["diamonds"])

                outbid_info = {
                    "user_id": prev_user.id,
                    "auction_id": auction.id,
                    "auction_title": auction.title,
                    "refunded_amount": prev_amount,
                    "currency": auction.bid_currency,
                    "new_bid": bid_amount,
                }

    auction.highest_bidder = user
    auction.highest_bidder_name = lab_display(user)
    auction.current_bid = bid_amount
    auction.save(update_fields=["highest_bidder", "highest_bidder_name", "current_bid"])

    return {
        "auction": auction,
        "bid_amount": bid_amount,
        "bid_currency": auction.bid_currency,
        "outbid_info": outbid_info,
    }


def settle_expired_auctions() -> int:
    """Settle finished auctions and grant items to highest bidders."""
    now = timezone.now()
    expired = BlackMarketAuction.objects.filter(is_settled=False, ends_at__lte=now)
    count = 0
    for auc in expired:
        with transaction.atomic():
            auc = BlackMarketAuction.objects.select_for_update().get(id=auc.id)
            if auc.is_settled:
                continue
            if auc.highest_bidder is not None:
                winner = User.objects.select_for_update().get(id=auc.highest_bidder.id)
                _deliver_auction_item(winner, auc)
            auc.is_settled = True
            auc.save(update_fields=["is_settled"])
            count += 1
    return count


_settle_expired_auctions = settle_expired_auctions


def _deliver_auction_item(user: User, auction: BlackMarketAuction) -> None:
    """Deliver the won auction reward to the user."""
    p = auction.item_payload or {}
    itype = auction.item_type
    if itype == "diamonds":
        user.diamonds += p.get("amount", 0)
        user.save(update_fields=["diamonds"])
    elif itype == "tickets":
        user.biocrate_tickets = (user.biocrate_tickets or 0) + p.get("amount", 0)
        user.save(update_fields=["biocrate_tickets"])
    elif itype == "coins":
        user.coins += p.get("amount", 0)
        user.save(update_fields=["coins"])
    elif itype in ("dna", "material"):
        user.dna_fragments += p.get("amount", 0)
        user.save(update_fields=["dna_fragments"])
    elif itype == "speedup":
        from bio_lab.models import SpeedupCard
        mins = p.get("minutes", 60)
        cnt = p.get("count", 1)
        card, _ = SpeedupCard.objects.get_or_create(owner=user, minutes=mins)
        card.count += cnt
        card.save(update_fields=["count"])
    elif itype == "equipment":
        from bio_lab.models import Equipment
        from game import constants
        slot = p.get("slot", "weapon")
        rarity = p.get("rarity", "rare")
        level = p.get("level", 1)
        name = p.get("name") or f"تجهیزات مزایده ({constants.RARITY_LABELS.get(rarity, rarity)})"
        Equipment.objects.create(
            owner=user,
            name=name,
            slot=slot,
            rarity=rarity,
            level=level,
        )
    elif itype == "creature":
        from bio_lab.models import Creature
        from game.creature import base_share_for_rating
        rarity = p.get("rarity", "rare")
        star = p.get("star", 1)
        lvl = p.get("level", 1)
        elem = p.get("element", "fire")
        custom_name = p.get("name") or f"کایجوی بازار سیاه {star}⭐"
        share = max(10, 30 * star)
        Creature.objects.create(
            owner=user,
            name=custom_name,
            rarity=rarity,
            star_level=star,
            level=lvl,
            element=elem,
            base_hp=share, base_atk=share, base_def=share, base_spd=share,
        )

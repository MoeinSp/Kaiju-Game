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


PERSIAN_WEEKDAYS = {
    0: "دوشنبه",
    1: "سه‌شنبه",
    2: "چهارشنبه",
    3: "پنج‌شنبه",
    4: "جمعه",
    5: "شنبه",
    6: "یک‌شنبه",
}


def format_persian_deadline(dt: datetime.datetime) -> str:
    """Format an aware datetime into Persian deadline string e.g. 'شنبه ساعت 22:30 شب'."""
    local_dt = timezone.localtime(dt)
    day_name = PERSIAN_WEEKDAYS.get(local_dt.weekday(), "")
    time_str = local_dt.strftime("%H:%M")
    return f"{day_name} ساعت {time_str} شب"


def format_time_remaining(seconds: float | int) -> str:
    """Format remaining seconds into Persian string e.g. '۱ ساعت و ۲۴ دقیقه' or '۳۵ دقیقه'."""
    s = max(0, int(seconds))
    if s <= 0:
        return "پایان یافته"
    days = s // 86400
    hours = (s % 86400) // 3600
    mins = (s % 3600) // 60
    secs = s % 60

    parts = []
    if days > 0:
        parts.append(f"{days} روز")
    if hours > 0:
        parts.append(f"{hours} ساعت")
    if mins > 0:
        parts.append(f"{mins} دقیقه")
    if not parts or (days == 0 and hours == 0 and mins < 5):
        parts.append(f"{secs} ثانیه")
    return " و ".join(parts)


def get_next_blackmarket_deadline() -> datetime.datetime:
    """Calculate the next 22:30 Tehran time deadline."""
    now = timezone.now()
    now_local = timezone.localtime(now)
    today_2230 = now_local.replace(hour=22, minute=30, second=0, microsecond=0)
    if now_local < today_2230:
        return today_2230
    tomorrow_local = now_local + datetime.timedelta(days=1)
    return tomorrow_local.replace(hour=22, minute=30, second=0, microsecond=0)


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
    return list(BlackMarketAuction.objects.filter(is_settled=False, ends_at__gt=timezone.now()).select_related("highest_bidder").order_by("id"))


def _ensure_daily_auctions() -> None:
    """Create default daily auctions if none are active."""
    now = timezone.now()
    active_count = BlackMarketAuction.objects.filter(is_settled=False, ends_at__gt=now).count()
    if active_count > 0:
        return

    # 22:30 Tehran time deadline
    ends_at = get_next_blackmarket_deadline()

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


def validate_bid_preview(user: User, auction_id: int, bid_amount: int) -> dict:
    """Validate a bid preview before confirmation. Raises GameError on failure."""
    now = timezone.now()
    auction = BlackMarketAuction.objects.filter(id=auction_id, is_settled=False, ends_at__gt=now).select_related("highest_bidder").first()
    if auction is None:
        raise GameError("این مزایده به پایان رسیده یا پیدا نشد.")

    is_vip_lot = (auction.bid_currency == "diamonds" or auction.item_type == "creature" or "(VIP)" in auction.title)
    if is_vip_lot:
        from game.subscription import is_subscription_active
        if not is_subscription_active(user):
            raise GameError("👑 این مزایده VIP است و فقط دارندگان «اشتراک نقره‌ای» یا «اشتراک طلایی» می‌توانند در آن شرکت کنند!")

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

    if auction.highest_bidder_id is None:
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

    if prev_bidder is not None and prev_bidder.id == user.id:
        cost = bid_amount - prev_amount
        is_own_increase = True
    else:
        cost = bid_amount
        is_own_increase = False

    if auction.bid_currency == "coins":
        if user.coins < cost:
            raise GameError(f"طلای کافی نداری! (موجودی: {user.coins:,} طلا، نیاز: {cost:,})")
    else:
        if user.diamonds < cost:
            raise GameError(f"الماس کافی نداری! (موجودی: {user.diamonds:,} الماس، نیاز: {cost:,})")

    return {
        "auction": auction,
        "bid_amount": bid_amount,
        "cost": cost,
        "currency": auction.bid_currency,
        "is_own_increase": is_own_increase,
        "prev_bidder_name": auction.highest_bidder_name,
        "prev_amount": prev_amount,
    }


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

    if auction.highest_bidder_id is None:
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


def settle_and_collect_winner_notifications() -> list[tuple[int, str]]:
    """Settle all expired auctions and return a list of (user_telegram_id, message_text) for winners."""
    settle_expired_auctions()

    unnotified = BlackMarketAuction.objects.filter(
        is_settled=True,
        winner_notified=False,
        highest_bidder__isnull=False,
    ).select_related("highest_bidder")

    notifications = []
    for auc in unnotified:
        with transaction.atomic():
            auc = BlackMarketAuction.objects.select_for_update().get(id=auc.id)
            if auc.winner_notified or auc.highest_bidder is None:
                continue
            winner = auc.highest_bidder
            curr = "طلا" if auc.bid_currency == "coins" else "الماس"
            text = (
                "🎉 <b>تبریک! شما برنده مزایده بازار سیاه شدید!</b>\n\n"
                f"🏷 <b>نام آیتم:</b> «<b>{auc.title}</b>»\n"
                f"💵 <b>مبلغ نهایی ثبت شده:</b> <b>{auc.current_bid:,}</b> {curr}\n\n"
                "🎁 <i>جایزه این مزایده به طور خودکار به حساب / انبار شما واریز گردید.</i>\n"
                "✨ جهت شرکت در مزایده‌های جدید، به منوی «⏳ بازار سیاه» سر بزنید."
            )
            notifications.append((winner.id, text))
            auc.winner_notified = True
            auc.save(update_fields=["winner_notified"])

    # Ensure fresh daily auctions are available if all expired
    _ensure_daily_auctions()
    return notifications



def _deliver_auction_item(user: User, auction: BlackMarketAuction) -> None:
    """Deliver the won auction reward to the user."""
    from game import constants
    p = auction.item_payload or {}
    itype = auction.item_type

    if itype == "diamonds":
        user.diamonds += p.get("amount", 0)
        user.save(update_fields=["diamonds"])
    elif itype in ("tickets", "biocrate_tickets"):
        user.biocrate_tickets = (user.biocrate_tickets or 0) + p.get("amount", 0)
        user.save(update_fields=["biocrate_tickets"])
    elif itype in ("coins", "gold"):
        user.coins += p.get("amount", 0)
        user.save(update_fields=["coins"])
    elif itype in ("dna", "material", "dna_fragments"):
        user.dna_fragments += p.get("amount", 0)
        user.save(update_fields=["dna_fragments"])
    elif itype in ("speedup", "speedup_card"):
        from bio_lab.models import SpeedupCard
        mins = p.get("minutes", 60)
        cnt = p.get("count", 1)
        card, _ = SpeedupCard.objects.get_or_create(owner=user, minutes=mins)
        card.count += cnt
        card.save(update_fields=["count"])
    elif itype == "energy":
        from game import energy
        amt = p.get("amount", 50)
        energy.add_energy(user, amt)
    elif itype == "equipment":
        from bio_lab.models import Equipment
        slot = p.get("slot", "weapon")
        rarity = p.get("rarity", "rare")
        level = p.get("level", 1)
        name = p.get("name") or f"تجهیزات مزایده ({constants.RARITY_LABELS.get(rarity, rarity)})"
        template_key = p.get("template_key") or f"bm_{slot}_{rarity}"
        Equipment.objects.create(
            owner=user,
            template_key=template_key,
            name=name,
            slot=slot,
            rarity=rarity,
            level=level,
        )
    elif itype == "egg":
        from bio_lab.models import Egg
        rarity = p.get("rarity", "legendary")
        elem = p.get("element") or constants.random_element()
        name = p.get("name") or constants.random_species_name(elem)
        mins = max(0, p.get("minutes", 0))
        Egg.objects.create(
            owner=user,
            base_rarity=rarity,
            upgrade_chance=1.0,
            fallback_rarity=rarity,
            parent_a_name=name,
            parent_a_element=elem,
            parent_b_name=name,
            parent_b_element=elem,
            inherit_level=p.get("level", 1),
            finishes_at=timezone.now() + datetime.timedelta(minutes=mins),
        )
    elif itype == "creature":
        from bio_lab.models import Creature
        rarity = p.get("rarity", "legendary")
        star = max(1, min(5, p.get("star", 1)))
        lvl = max(1, p.get("level", 1))
        elem = p.get("element") or constants.random_element()
        species_name = p.get("name") or constants.random_species_name(elem)
        mult = constants.RARITY_STAT_MULTIPLIER.get(rarity, 1.0)
        
        base_hp = round(constants.STARTER_BASE_HP * mult) + (lvl - 1) * constants.LEVEL_UP_HP
        base_atk = round(constants.STARTER_BASE_ATK * mult) + (lvl - 1) * constants.LEVEL_UP_ATK
        base_def = round(constants.STARTER_BASE_DEF * mult) + (lvl - 1) * constants.LEVEL_UP_DEF
        base_spd = round(constants.STARTER_BASE_SPD * mult) + (lvl - 1) * constants.LEVEL_UP_SPD
        
        Creature.objects.create(
            owner=user,
            name=species_name,
            rarity=rarity,
            star_level=star,
            level=lvl,
            element=elem,
            base_hp=base_hp,
            base_atk=base_atk,
            base_def=base_def,
            base_spd=base_spd,
            is_active=False,
        )
    elif itype in ("subscription", "vip"):
        from game.subscription import activate_subscription
        tier = p.get("tier", "silver")
        days = p.get("days", 30)
        activate_subscription(user, tier=tier, days=days)
    elif itype == "builder":
        user.builder_slots = max(2, user.builder_slots)
        user.save(update_fields=["builder_slots"])


def admin_list_auctions(only_active: bool = True) -> list[BlackMarketAuction]:
    """List auctions for admin panel. By default returns only active and future auctions."""
    _settle_expired_auctions()
    now = timezone.now()
    if only_active:
        return list(
            BlackMarketAuction.objects.filter(is_settled=False, ends_at__gt=now)
            .select_related("highest_bidder")
            .order_by("ends_at")
        )
    return list(
        BlackMarketAuction.objects.all()
        .select_related("highest_bidder")
        .order_by("-id")[:50]
    )


def admin_get_auction(auction_id: int) -> BlackMarketAuction | None:
    """Fetch single auction with bidder for admin panel views."""
    return BlackMarketAuction.objects.filter(id=auction_id).select_related("highest_bidder").first()


def admin_create_auction(
    title: str,
    item_type: str,
    item_payload: dict,
    bid_currency: str,
    min_bid: int,
    ends_at: datetime.datetime,
) -> BlackMarketAuction:
    """Create a new auction from admin panel."""
    return BlackMarketAuction.objects.create(
        title=title,
        item_type=item_type,
        item_payload=item_payload,
        bid_currency=bid_currency,
        min_bid=min_bid,
        current_bid=min_bid,
        ends_at=ends_at,
    )


def admin_delete_auction(auction_id: int) -> tuple[bool, str]:
    """Delete / cancel auction, refunding the bidder if any."""
    with transaction.atomic():
        auction = BlackMarketAuction.objects.select_for_update().filter(id=auction_id).first()
        if not auction:
            return False, "مزایده پیدا نشد."
        if auction.highest_bidder and not auction.is_settled:
            bidder = User.objects.select_for_update().get(id=auction.highest_bidder.id)
            if auction.bid_currency == "coins":
                bidder.coins += auction.current_bid
                bidder.save(update_fields=["coins"])
            else:
                bidder.diamonds += auction.current_bid
                bidder.save(update_fields=["diamonds"])
        title = auction.title
        auction.delete()
        return True, f"مزایده «{title}» با موفقیت حذف شد و مبالغ واریزی بازگردانده شد."


def admin_extend_auction(auction_id: int, hours: int) -> tuple[bool, str]:
    """Extend or set deadline for an auction."""
    with transaction.atomic():
        auction = BlackMarketAuction.objects.select_for_update().filter(id=auction_id).first()
        if not auction:
            return False, "مزایده پیدا نشد."
        now = timezone.now()
        base_time = max(auction.ends_at, now)
        auction.ends_at = base_time + datetime.timedelta(hours=hours)
        auction.is_settled = False
        auction.save(update_fields=["ends_at", "is_settled"])
        return True, f"زمان پایان مزایده تا {format_persian_deadline(auction.ends_at)} تمدید شد."



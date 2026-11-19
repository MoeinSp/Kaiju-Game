"""The trade hall is the bank.

Outgoing gold (a withdrawal, or a transfer to someone else) shares one daily
budget: 20% of the hall's capacity. Incoming gold from another player lands in
the bank. An account younger than a week can receive at most 30,000 a day.
Gold that has been withdrawn is ordinary free gold and a group attack can loot it.
"""

from __future__ import annotations

from django.utils import timezone

from bio_lab.models import User
from game import constants
from game.creature import GameError


def capacity(level: int) -> int:
    return constants.trade_hall_gold_cap(level)


def daily_budget(level: int) -> int:
    return constants.bank_daily_withdraw(level)


def _age_days(user) -> float:
    if not user.created_at:
        return constants.BANK_NEW_ACCOUNT_DAYS
    return (timezone.now() - user.created_at).total_seconds() / 86400.0


def is_young(user) -> bool:
    return _age_days(user) < constants.BANK_NEW_ACCOUNT_DAYS


def _used(user, action: str) -> int:
    from game.daily import get_daily_count

    return get_daily_count(user, action)


def _add(user, action: str, amount: int) -> None:
    from game.daily import record_action_bulk

    record_action_bulk(user, action, amount)


def withdraw_room(user, level: int) -> int:
    return max(0, daily_budget(level) - _used(user, "bank_withdrawn"))


def receive_room(user, level: int) -> int:
    """How much more gold this bank can accept right now."""
    room = max(0, capacity(level) - int(user.bank_gold or 0))
    if is_young(user):
        room = min(room, max(0, constants.BANK_NEW_ACCOUNT_RECEIVE_CAP - _used(user, "bank_received")))
    return room


def lootable_gold(user) -> int:
    """Free-wallet gold. A withdrawal from the bank is the same gold and can be looted."""
    return max(0, int(user.coins or 0))


def apply_group_loot(user, gold: int) -> None:
    """Remove looted gold from the free wallet."""
    gold = max(0, int(gold))
    user.coins = max(0, int(user.coins or 0) - gold)


def settle_interest(user) -> int:
    """Pay 2% of the bank into the free wallet, once per Tehran day.

    The midnight job is the only caller. The balance is whatever is in the bank
    at that moment, and a second call the same day pays nothing.
    """
    from django.db.models import F

    today = timezone.localdate()
    if getattr(user, "bank_interest_on", None) == today:
        return 0
    row = (
        User.objects.filter(pk=user.pk)
        .exclude(bank_interest_on=today)
        .values("bank_gold")
        .first()
    )
    if row is None:
        fresh = User.objects.get(pk=user.pk)
        user.coins = fresh.coins
        user.bank_gold = fresh.bank_gold
        user.bank_interest_on = fresh.bank_interest_on
        return 0
    payout = int(row["bank_gold"] or 0) * constants.BANK_INTEREST_PERCENT // 100
    changes = {"coins": F("coins") + payout, "bank_interest_on": today}
    if payout > 0:
        changes["bank_interest_notice"] = F("bank_interest_notice") + payout
        changes["bank_interest_notice_days"] = F("bank_interest_notice_days") + 1
    updated = User.objects.filter(pk=user.pk).exclude(bank_interest_on=today).update(**changes)
    fresh = User.objects.get(pk=user.pk)
    user.coins = fresh.coins
    user.bank_gold = fresh.bank_gold
    user.bank_interest_on = fresh.bank_interest_on
    user.bank_interest_notice = fresh.bank_interest_notice
    user.bank_interest_notice_days = fresh.bank_interest_notice_days
    return payout if updated else 0


def pay_all_interest() -> int:
    """Midnight pass: every bank is stamped for today, and gold on hand earns 2%."""
    today = timezone.localdate()
    paid = 0
    ids = User.objects.filter(bank_gold__gt=0).exclude(bank_interest_on=today).values_list("id", flat=True)
    for uid in ids.iterator(chunk_size=200):
        paid += settle_interest(User.objects.get(pk=uid))
    User.objects.filter(bank_gold__lte=0).exclude(bank_interest_on=today).update(bank_interest_on=today)
    return paid


def deposit(user, amount: int, *, level: int) -> None:
    """Put `amount` into the bank. The caller saves `user`."""
    amount = int(amount)
    if amount <= 0:
        return
    cap = capacity(level)
    if cap <= 0:
        raise GameError("بانک ساخته نشده. اول «بانک طلا» را از ساختمان‌ها بساز.")
    if int(user.bank_gold or 0) + amount > cap:
        raise GameError(
            f"بانک ظرفیت ندارد. گنجایش {cap:,} طلاست و الان {int(user.bank_gold or 0):,} طلا داخلش است."
        )
    if is_young(user):
        used = _used(user, "bank_received")
        cap_in = constants.BANK_NEW_ACCOUNT_RECEIVE_CAP
        if used + amount > cap_in:
            left = max(0, cap_in - used)
            raise GameError(
                f"اکانت زیر {constants.BANK_NEW_ACCOUNT_DAYS} روز امروز حداکثر {cap_in:,} طلا می‌تواند در بانک بنشیند. "
                f"امروز {used:,} نشسته و {left:,} جا مانده."
            )
        _add(user, "bank_received", amount)
    user.bank_gold = int(user.bank_gold or 0) + amount


def take_for_transfer(user, amount: int, *, level: int) -> None:
    """Remove `amount` from the bank and count it against today's withdraw budget."""
    amount = int(amount)
    if capacity(level) <= 0:
        raise GameError("بانک ساخته نشده. اول «بانک طلا» را از ساختمان‌ها بساز.")
    room = withdraw_room(user, level)
    if amount > room:
        raise GameError(
            f"سقف خروج طلا از بانک امروز {daily_budget(level):,} است و {room:,} طلا از آن مانده."
        )
    if int(user.bank_gold or 0) < amount:
        raise GameError(f"داخل بانک {int(user.bank_gold or 0):,} طلاست.")
    user.bank_gold -= amount
    _add(user, "bank_withdrawn", amount)


def withdraw_amount(user, level: int, amount: int) -> int:
    """Move `amount` from the bank into the free wallet, inside today's budget."""
    amount = int(amount)
    if amount <= 0:
        raise GameError("مقدار باید بیشتر از صفر باشد.")
    take_for_transfer(user, amount, level=level)
    user.coins = int(user.coins or 0) + amount
    user.save(update_fields=["bank_gold", "coins"])
    return amount


def deposit_from_wallet(user, level: int, amount: int) -> int:
    """Move free-wallet gold into this player's own bank. No receive cap: it is already theirs."""
    amount = int(amount)
    if amount <= 0:
        raise GameError("مقدار باید بیشتر از صفر باشد.")
    cap = capacity(level)
    if cap <= 0:
        raise GameError("بانک ساخته نشده. از ساختمان‌ها «بانک طلا» را بساز.")
    room = cap - int(user.bank_gold or 0)
    if amount > room:
        raise GameError(f"بانک فقط {max(0, room):,} طلا جا دارد.")
    if int(user.coins or 0) < amount:
        raise GameError(f"طلای آزاد {int(user.coins or 0):,} است.")
    user.coins -= amount
    user.bank_gold = int(user.bank_gold or 0) + amount
    user.save(update_fields=["coins", "bank_gold"])
    return amount


def withdraw_to_wallet(user, level: int) -> int:
    """Move today's remaining budget, or whatever is in the bank, into the free wallet."""
    room = withdraw_room(user, level)
    amount = min(int(user.bank_gold or 0), room)
    if amount <= 0:
        if int(user.bank_gold or 0) <= 0:
            raise GameError("بانک خالی است.")
        raise GameError("سقف برداشت امروز تمام شده. فردا دوباره می‌توانی برداشت کنی.")
    return withdraw_amount(user, level, amount)


def interest_notice_text(gold: int, days: int) -> str:
    """The line shown the next time the player opens the bank. Empty when nothing is waiting."""
    gold, days = int(gold or 0), int(days or 0)
    if gold <= 0:
        return ""
    if days > 1:
        return f"طی <b>{days}</b> روز، <b>{gold:,}</b> طلا به موجودی شما اضافه شد."
    return f"<b>{gold:,}</b> طلا به موجودی شما اضافه شد."


def take_interest_notice(user) -> tuple[int, int]:
    """Read and clear the unseen interest receipt. One open of the bank consumes it."""
    gold = int(getattr(user, "bank_interest_notice", 0) or 0)
    days = int(getattr(user, "bank_interest_notice_days", 0) or 0)
    if gold <= 0 and days <= 0:
        return 0, 0
    User.objects.filter(pk=user.pk).update(bank_interest_notice=0, bank_interest_notice_days=0)
    user.bank_interest_notice = 0
    user.bank_interest_notice_days = 0
    return gold, days


def snapshot(user, level: int) -> dict:
    cap = capacity(level)
    daily = daily_budget(level)
    used = _used(user, "bank_withdrawn") if cap else 0
    young = is_young(user)
    received = _used(user, "bank_received") if young else 0
    return {
        "gold": int(user.bank_gold or 0),
        "cap": cap,
        "daily": daily,
        "left": max(0, daily - used),
        "wallet": int(user.coins or 0),
        "young": young,
        "receive_left": max(0, constants.BANK_NEW_ACCOUNT_RECEIVE_CAP - received) if young else None,
        "notice": int(getattr(user, "bank_interest_notice", 0) or 0),
        "notice_days": int(getattr(user, "bank_interest_notice_days", 0) or 0),
    }


def pay_seller_from_earned(buyer, seller, price: int, *, seller_level: int) -> None:
    """Buyer pays from the free wallet. Seller's bank receives the same amount."""
    from game.wallet import spend_wallet

    price = int(price)
    if price <= 0:
        return
    if not spend_wallet(buyer, price):
        raise GameError(
            f"این خرید {price:,} طلا می‌خواهد. موجودی آزادت {int(buyer.coins or 0):,} است."
        )
    deposit(seller, price, level=seller_level)

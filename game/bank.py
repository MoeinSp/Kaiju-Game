"""The trade hall is the bank.

Outgoing gold (a withdrawal, or a transfer to someone else) shares one daily
budget: 20% of the hall's capacity. Incoming gold from another player lands in
the bank. An account younger than a week can receive at most 30,000 a day.
Gold that left the bank in the last 24 hours is not lootable in a group attack.
"""

from __future__ import annotations

import datetime

from django.utils import timezone

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


def _note_out(user, amount: int) -> None:
    now = timezone.now()
    window = datetime.timedelta(hours=constants.BANK_LOOT_PROTECT_HOURS)
    if user.bank_out_at and now - user.bank_out_at < window:
        user.bank_out_amount = int(user.bank_out_amount or 0) + amount
    else:
        user.bank_out_amount = amount
        user.bank_out_at = now


def recent_bank_gold(user) -> int:
    """Gold that moved from the bank into the wallet during the last 24 hours."""
    if not user.bank_out_at or not user.bank_out_amount:
        return 0
    window = datetime.timedelta(hours=constants.BANK_LOOT_PROTECT_HOURS)
    if timezone.now() - user.bank_out_at >= window:
        return 0
    return min(int(user.bank_out_amount), int(user.coins or 0))


def lootable_gold(user) -> int:
    return max(0, int(user.coins or 0) - recent_bank_gold(user))


def apply_group_loot(user, gold: int) -> None:
    """Remove looted gold. The caller already left the last 24h of bank withdrawals out of `gold`."""
    gold = max(0, int(gold))
    user.coins = max(0, int(user.coins or 0) - gold)
    if user.bank_out_amount:
        user.bank_out_amount = min(int(user.bank_out_amount), int(user.coins))


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


def withdraw_to_wallet(user, level: int) -> int:
    """Move today's remaining budget, or whatever is in the bank, into the free wallet."""
    room = withdraw_room(user, level)
    amount = min(int(user.bank_gold or 0), room)
    if amount <= 0:
        if int(user.bank_gold or 0) <= 0:
            raise GameError("بانک خالی است.")
        raise GameError("سقف برداشت امروز تمام شده. فردا دوباره می‌توانی برداشت کنی.")
    user.bank_gold -= amount
    user.coins = int(user.coins or 0) + amount
    _note_out(user, amount)
    _add(user, "bank_withdrawn", amount)
    user.save(update_fields=["bank_gold", "coins", "bank_out_amount", "bank_out_at"])
    return amount


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

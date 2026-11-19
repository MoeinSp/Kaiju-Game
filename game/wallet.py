"""Free-wallet gold. There is one pile: `coins`.

A bank withdrawal is added to that same pile. Buildings, kaiju, gear and boxes
all spend it. Gold still inside the bank is a different place, not a second kind
of wallet gold.
"""

from __future__ import annotations


def spend_wallet(user, amount: int) -> bool:
    """Spend free-wallet gold. Returns False when the wallet is short."""
    amount = int(amount)
    if amount < 0:
        raise ValueError(amount)
    if amount == 0:
        return True
    if int(user.coins or 0) < amount:
        return False
    user.coins = int(user.coins) - amount
    return True

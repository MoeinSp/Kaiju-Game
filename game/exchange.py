"""«مبادله» — an always-on, two-way gold ↔ DNA converter.

Deliberately lossy on the round-trip: buying DNA costs more gold per unit than
selling it returns, so it's a convenience for smoothing a shortfall, never an
arbitrage loop. Only fixed packages exist (no free-form amounts), so the whole UI
is inline buttons with a final confirm, and the swap itself is a single atomic,
row-locked, balance-checked update — you can only ever convert what you actually
hold, verified at the moment of confirmation (spam-safe by construction).
"""

from __future__ import annotations

from django.db import transaction

from bio_lab.models import User
from game.creature import GameError

# gold per 1 DNA in each direction. BUY > SELL, so a full round-trip loses gold.
GOLD_PER_DNA_BUY = 100   # spend this much gold to gain 1 DNA
GOLD_PER_DNA_SELL = 50   # gain this much gold for 1 DNA sold

# fixed package sizes (in DNA) for each direction
BUY_PACKS = [10, 50, 200]
SELL_PACKS = [10, 50, 200]


def buy_gold_cost(dna: int) -> int:
    return int(dna) * GOLD_PER_DNA_BUY


def sell_gold_gain(dna: int) -> int:
    return int(dna) * GOLD_PER_DNA_SELL


def describe(direction: str, idx: int) -> dict:
    """Resolve a package to {direction, idx, dna, gold}. `gold` is the gold spent
    (buy) or gained (sell). Raises GameError on a bad direction/index."""
    if direction == "buy":
        packs = BUY_PACKS
    elif direction == "sell":
        packs = SELL_PACKS
    else:
        raise GameError("جهت مبادله نامعتبره.")
    if idx < 0 or idx >= len(packs):
        raise GameError("این بسته‌ی مبادله وجود نداره.")
    dna = packs[idx]
    gold = buy_gold_cost(dna) if direction == "buy" else sell_gold_gain(dna)
    return {"direction": direction, "idx": idx, "dna": dna, "gold": gold}


@transaction.atomic
def exchange(user: User, direction: str, idx: int) -> dict:
    """Perform one package exchange atomically. Locks the user row, RE-checks the
    balance under the lock, then moves both currencies in a single save. Returns
    {direction, idx, dna, gold, new_coins, new_dna}."""
    pack = describe(direction, idx)  # validates direction/idx
    u = User.objects.select_for_update().get(id=user.id)
    dna, gold = pack["dna"], pack["gold"]

    if direction == "buy":
        if u.coins < gold:
            raise GameError(f"طلا کافی نداری! این مبادله {gold:,} طلا می‌خواد (الان {u.coins:,} داری).")
        u.coins -= gold
        u.dna_fragments += dna
    else:  # sell
        if u.dna_fragments < dna:
            raise GameError(f"DNA کافی نداری! این مبادله {dna} DNA می‌خواد (الان {u.dna_fragments} داری).")
        u.dna_fragments -= dna
        u.coins += gold

    u.save(update_fields=["coins", "dna_fragments"])
    return {**pack, "new_coins": u.coins, "new_dna": u.dna_fragments}

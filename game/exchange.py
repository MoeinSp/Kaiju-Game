"""«مبادله» — a two-way gold ↔ DNA converter (DM and, scoped, in groups).

Two directions:
* buy_dna  — pay gold, get DNA  (GOLD_PER_DNA_BUY gold per 1 DNA)
* buy_gold — pay DNA, get gold  (GOLD_PER_DNA_SELL gold per 1 DNA)

Buying DNA costs more gold per unit than selling it returns, so a full round-trip
loses value — it's a convenience, not an arbitrage loop. Amounts are always a whole
number of DNA (the atomic unit), so the math is exact and never rounds. Preset
sizes plus a free-form custom amount are both supported. The swap is a single
atomic, row-locked, balance-checked update — funds are re-checked at the instant of
confirmation, so a stale or spammed button can never over-convert.
"""

from __future__ import annotations

from django.db import transaction

from bio_lab.models import User
from game.creature import GameError

ENABLED = True

# gold per 1 DNA in each direction. BUY > SELL, so a full round-trip loses gold.
GOLD_PER_DNA_BUY = 50    # buy_dna:  spend 50 gold to gain 1 DNA
GOLD_PER_DNA_SELL = 25   # buy_gold: gain 25 gold for 1 DNA spent

PRESET_DNA = [10, 50, 200]     # quick-pick sizes (in DNA) offered for both directions
MAX_EXCHANGE_DNA = 1_000_000   # sane upper bound on a single custom exchange

DIRECTIONS = ("buy_dna", "buy_gold")


def buy_gold_cost(dna: int) -> int:
    """Gold you PAY to buy `dna` DNA."""
    return int(dna) * GOLD_PER_DNA_BUY


def sell_gold_gain(dna: int) -> int:
    """Gold you GET for selling `dna` DNA."""
    return int(dna) * GOLD_PER_DNA_SELL


def describe(direction: str, dna: int) -> dict:
    """Resolve a would-be exchange of `dna` DNA in `direction` to
    {direction, dna, gold} (gold = gold paid for buy_dna / gold gained for buy_gold).
    Raises GameError on a bad direction or a non-positive/oversized amount."""
    if direction not in DIRECTIONS:
        raise GameError("جهت مبادله نامعتبره.")
    dna = int(dna)
    if dna <= 0:
        raise GameError("تعداد باید بزرگ‌تر از صفر باشه.")
    if dna > MAX_EXCHANGE_DNA:
        raise GameError(f"حداکثر {MAX_EXCHANGE_DNA:,} DNA در هر مبادله.")
    gold = buy_gold_cost(dna) if direction == "buy_dna" else sell_gold_gain(dna)
    return {"direction": direction, "dna": dna, "gold": gold}


@transaction.atomic
def exchange(user: User, direction: str, dna: int) -> dict:
    """Perform one exchange atomically: lock the user row, RE-check the balance under
    the lock, then move both currencies in a single save. Returns
    {direction, dna, gold, new_coins, new_dna}."""
    pack = describe(direction, dna)  # validates
    u = User.objects.select_for_update().get(id=user.id)
    dna, gold = pack["dna"], pack["gold"]

    if direction == "buy_dna":  # pay gold, get DNA
        if u.coins < gold:
            raise GameError(f"طلا کافی نداری! این مبادله {gold:,} طلا می‌خواد (الان {u.coins:,} داری).")
        u.coins -= gold
        u.dna_fragments += dna
    else:  # buy_gold: pay DNA, get gold
        if u.dna_fragments < dna:
            raise GameError(f"DNA کافی نداری! این مبادله {dna:,} DNA می‌خواد (الان {u.dna_fragments:,} داری).")
        u.dna_fragments -= dna
        u.coins += gold

    u.save(update_fields=["coins", "dna_fragments"])
    return {**pack, "new_coins": u.coins, "new_dna": u.dna_fragments}

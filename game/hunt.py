import random

from bio_lab.models import Creature, User
from game import constants
from game.combat import resolve_duel
from game import lab
from game.creature import add_xp, effective_stats

WILD_NAMES = ["Ferabeast", "Grimhide", "Rustclaw", "Mossfang", "Duskrunner"]

# each tier scales the wild creature's stats and its payout together, so picking a
# tougher target is a real risk/reward decision rather than a free upgrade.
# `stat_mult` sizes the opponent (difficulty); `reward_mult` scales the loot.
# stat_mult sizes the wild creature RELATIVE TO THE PLAYER'S STRONGEST kaiju (the
# benchmark, see spawn_wild_creature) — never to whichever creature happens to be
# active. So a hunt is winnable if you bring your strongest kaiju with the RIGHT
# element: normal (0.95×) is a reliable win with element advantage and a coin-flip
# without it, and strong (1.05×) is hard-but-possible — only the strongest kaiju with
# the right element clears it, which is why its loot is much bigger.
HUNT_TIERS = {
    "weak": {"label": "🟢 ضعیف", "stat_mult": 0.80, "reward_mult": 0.6},
    "normal": {"label": "🟡 هم‌سطح", "stat_mult": 0.95, "reward_mult": 1.0},
    "strong": {"label": "🔴 قوی", "stat_mult": 1.05, "reward_mult": 2.8},
}

# Hunt loot scales PURELY with the player creature's power — the stronger your kaiju,
# the more it earns, independent of cup or anything else. Tier only sizes the target
# (a risk/reward difficulty knob).
HUNT_COIN_PER_POWER = 0.20   # doubled
HUNT_DNA_PER_POWER = 0.012   # doubled
HUNT_XP_WIN = 25
HUNT_XP_LOSE = 8
# «بعدی» (searching for a better target) costs a little gold, scaled by power, so
# hunting a good loot is a small deliberate spend rather than free infinite rerolls.
# Halved from 0.012/5 — searching for a hunt should be cheap (arena keeps its own).
HUNT_SCOUT_COST_PER_POWER = 0.006
HUNT_SCOUT_COST_MIN = 3


def _player_power(creature: Creature) -> int:
    from game.creature import creature_power
    from game.equipment import get_equipped_items

    return creature_power(creature, get_equipped_items(creature))


def strongest_power(user: User) -> int:
    """The power of the player's STRONGEST kaiju (gear included), across their whole
    collection. Kept for callers that genuinely want the collection ceiling; hunts use
    hunt_benchmark_power() instead so an unavailable creature doesn't inflate difficulty."""
    from game.creature import creature_power
    from game.equipment import get_equipped_items

    best = 0
    for c in Creature.objects.filter(owner=user):
        best = max(best, creature_power(c, get_equipped_items(c)))
    return max(20, best)


def hunt_benchmark_power(user: User) -> int:
    """The benchmark a hunt is sized against: the strongest kaiju the player can
    ACTUALLY field for this hunt right now — the active creature or one of their team
    (the swap picker's choices), excluding any that are busy (mining or breeding).

    This fixes the unfair case where the collection's strongest kaiju is locked away —
    not selected and not in the team, or in the team but busy in a mine/cave — so it
    can't be brought to the fight. Sizing the wild against a creature the player can't
    deploy made hunts unwinnable; the benchmark now tracks what's genuinely available,
    and falls back to the strongest non-busy creature if the team set is all locked."""
    from game.creature import creature_power
    from game.equipment import get_equipped_items
    from game.workers import busy_creature_ids
    from bio_lab.repository import get_active_creature, team_choices

    busy = busy_creature_ids(user)
    # fieldable = the active creature + the team roster the swap picker offers
    fieldable: dict[int, Creature] = {}
    active = get_active_creature(user)
    if active is not None:
        fieldable[active.id] = active
    for c in team_choices(user):
        fieldable.setdefault(c.id, c)

    def _best(creatures) -> int:
        b = 0
        for c in creatures:
            if c.id in busy:
                continue
            b = max(b, creature_power(c, get_equipped_items(c)))
        return b

    best = _best(fieldable.values())
    if best == 0:
        # everything fieldable is busy (or no active/team at all) — fall back to the
        # strongest creature that isn't locked, so difficulty still tracks something real
        best = _best(Creature.objects.filter(owner=user))
    return max(20, best)


def scout_cost(creature: Creature) -> int:
    return max(HUNT_SCOUT_COST_MIN, round(_player_power(creature) * HUNT_SCOUT_COST_PER_POWER))


def hunt_coin_range(power: int, tier: str) -> tuple[int, int]:
    """Coin loot for a hunt — scales ONLY with the player creature's power (× tier)."""
    mult = HUNT_TIERS[tier]["reward_mult"]
    base = max(0, power) * HUNT_COIN_PER_POWER
    return (round(base * mult * 0.85), round(base * mult * 1.15))


def hunt_dna_range(player_power: int, tier: str) -> tuple[int, int]:
    mult = HUNT_TIERS[tier]["reward_mult"]
    base = max(0, player_power) * HUNT_DNA_PER_POWER
    return (round(base * mult * 0.7), round((base + 1) * mult * 1.3))


def spawn_wild_creature(benchmark_power: int, tier: str = "normal", seed: int | None = None) -> Creature:
    """Builds an unsaved (ephemeral) Creature scaled to `benchmark_power` (the player's
    STRONGEST kaiju) and the difficulty tier, purely to reuse the combat-math functions —
    never persisted, so its id stays None. `seed` makes a scouted target reproducible AND
    keeps it fixed while the player swaps which creature fights it."""
    from game.creature import base_share_for_rating

    rng = random.Random(seed)
    cfg = HUNT_TIERS[tier]
    variance = rng.uniform(0.92, 1.08) * cfg["stat_mult"]
    target_power = max(20, round(max(20, benchmark_power) * variance))
    share = base_share_for_rating(target_power)
    return Creature(
        name=rng.choice(WILD_NAMES),
        element=rng.choice(constants.ELEMENTS),
        rarity="common",
        level=1,
        base_hp=share, base_atk=share, base_def=share, base_spd=share,
    )


def scout_one(user: User, player_creature: Creature) -> dict:
    """A single previewable opponent — the player searches again ("بعدی") until they
    like what they see. Carries the seed so resolve_hunt rebuilds the exact opponent. The
    wild is sized to the player's STRONGEST kaiju, so it stays the same if they switch
    creatures to fight it."""
    from game.creature import creature_power

    tier = random.choice(list(HUNT_TIERS))
    seed = random.randrange(1_000_000)
    wild = spawn_wild_creature(hunt_benchmark_power(user), tier, seed)
    return {
        "tier": tier,
        "seed": seed,
        "name": wild.name,
        "element": wild.element,
        # canonical power metric (same as profile/arena), so «قدرت حریف» is comparable
        # to the player's own shown power — not a different, smaller stat-sum.
        "power": creature_power(wild),
        "reward_mult": HUNT_TIERS[tier]["reward_mult"],
    }


def rebuild_target(user: User, tier: str, seed: int) -> dict:
    """Rebuild the EXACT same scouted opponent from its (tier, seed) — used when the
    player swaps which creature fights it, so the target stays identical."""
    from game.creature import creature_power

    wild = spawn_wild_creature(hunt_benchmark_power(user), tier, seed)
    return {
        "tier": tier, "seed": seed, "name": wild.name, "element": wild.element,
        "power": creature_power(wild), "reward_mult": HUNT_TIERS[tier]["reward_mult"],
    }


def estimated_reward(tier: str, power: int = 0) -> tuple[int, int]:
    """(min_coins, max_coins) shown while scouting — scales with the creature's power."""
    return hunt_coin_range(power, tier)


AUTO_HUNT_LOOT_MULT = 0.5  # auto-hunt pays HALF the gold/DNA of a manual hunt


def resolve_hunt(user: User, player_creature: Creature, tier: str = "normal",
                 seed: int | None = None, loot_mult: float = 1.0) -> dict:
    """Plays out one solo PvE encounter and applies rewards. Caller handles energy. The
    wild is sized to the player's STRONGEST kaiju (fixed by the seed), so whichever
    creature they bring fights the SAME opponent — switching to the right element is the
    strategy, not a way to shrink the target. `loot_mult` scales the gold/DNA payout —
    auto-hunt passes AUTO_HUNT_LOOT_MULT (0.5) so it earns half of a manual hunt."""
    wild = spawn_wild_creature(hunt_benchmark_power(user), tier, seed)
    winner, log_text = resolve_duel(player_creature, wild)
    won = winner is player_creature
    reward_mult = HUNT_TIERS[tier]["reward_mult"]
    power = _player_power(player_creature)

    if won:
        coins = round(random.randint(*hunt_coin_range(power, tier)) * loot_mult)
        dna = round(random.randint(*hunt_dna_range(power, tier)) * loot_mult)
        xp_gain = round(HUNT_XP_WIN * reward_mult)
    else:
        coins = 0
        dna = 0
        xp_gain = HUNT_XP_LOSE

    user.coins += coins
    user.dna_fragments += dna
    user.save(update_fields=["coins", "dna_fragments"])
    if coins or dna:
        from game.ledger import record_gain

        record_gain(user, "hunt", coins=coins, dna=dna)
    levels = add_xp(player_creature, xp_gain)
    player_creature.save()
    lab_up = lab.award(user, "hunt_win" if won else "hunt_loss")

    return {
        "won": won,
        "lab_up": lab_up,
        "log_text": log_text,
        "wild_name": wild.name,
        "tier": tier,
        "coins": coins,
        "dna": dna,
        "xp": xp_gain,
        "levels": levels,
    }

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
HUNT_TIERS = {
    "weak": {"label": "🟢 ضعیف", "stat_mult": 0.8, "reward_mult": 0.5},
    "normal": {"label": "🟡 هم‌سطح", "stat_mult": 1.0, "reward_mult": 1.0},
    "strong": {"label": "🔴 قوی", "stat_mult": 1.4, "reward_mult": 1.8},
}

# Hunt coin loot is pegged to the ARENA reward at the same cup: a «هم‌سطح» hunt pays
# ~80% of what a same-cup bot raid pays, so when a player's cup stalls they can still
# earn a solid trickle by hunting instead. (weak ≈ 40% of arena, strong ≈ 144%.)
HUNT_ARENA_LOOT_FRACTION = 0.80
HUNT_DNA_PER_POWER = 0.006
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


def scout_cost(creature: Creature) -> int:
    return max(HUNT_SCOUT_COST_MIN, round(_player_power(creature) * HUNT_SCOUT_COST_PER_POWER))


def hunt_coin_range(cup: int, tier: str) -> tuple[int, int]:
    """Coin loot for a hunt, pegged to the arena bot-loot at this cup × 80% × tier."""
    mult = HUNT_TIERS[tier]["reward_mult"]
    base = constants.arena_fake_loot(max(0, cup)) * HUNT_ARENA_LOOT_FRACTION
    return (round(base * mult * 0.85), round(base * mult * 1.15))


def hunt_dna_range(player_power: int, tier: str) -> tuple[int, int]:
    mult = HUNT_TIERS[tier]["reward_mult"]
    base = max(0, player_power) * HUNT_DNA_PER_POWER
    return (round(base * mult * 0.7), round((base + 1) * mult * 1.3))


def spawn_wild_creature(player_creature: Creature, tier: str = "normal", seed: int | None = None) -> Creature:
    """Builds an unsaved (ephemeral) Creature scaled to the player's POWER and the
    chosen difficulty tier, purely to reuse the combat-math functions — never
    persisted, so its id stays None. `seed` makes a scouted target reproducible."""
    from game.creature import base_share_for_rating

    rng = random.Random(seed)
    cfg = HUNT_TIERS[tier]
    variance = rng.uniform(0.9, 1.1) * cfg["stat_mult"]
    target_power = max(20, round(_player_power(player_creature) * variance))
    share = base_share_for_rating(target_power)
    return Creature(
        name=rng.choice(WILD_NAMES),
        element=rng.choice(constants.ELEMENTS),
        rarity="common",
        level=max(1, player_creature.level),
        base_hp=share, base_atk=share, base_def=share, base_spd=share,
    )


def scout_one(player_creature: Creature) -> dict:
    """A single previewable opponent — the player searches again ("بعدی") until they
    like what they see. Carries the seed so resolve_hunt rebuilds the exact opponent."""
    from game.creature import creature_power

    tier = random.choice(list(HUNT_TIERS))
    seed = random.randrange(1_000_000)
    wild = spawn_wild_creature(player_creature, tier, seed)
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


def estimated_reward(tier: str, cup: int = 0) -> tuple[int, int]:
    """(min_coins, max_coins) shown while scouting — pegged to arena loot at this cup."""
    return hunt_coin_range(cup, tier)


def resolve_hunt(user: User, player_creature: Creature, tier: str = "normal", seed: int | None = None) -> dict:
    """Plays out one solo PvE encounter and applies rewards. Caller handles energy."""
    wild = spawn_wild_creature(player_creature, tier, seed)
    winner, log_text = resolve_duel(player_creature, wild)
    won = winner is player_creature
    reward_mult = HUNT_TIERS[tier]["reward_mult"]
    power = _player_power(player_creature)

    if won:
        coins = random.randint(*hunt_coin_range(user.cup, tier))
        dna = random.randint(*hunt_dna_range(power, tier))
        xp_gain = round(HUNT_XP_WIN * reward_mult)
    else:
        coins = 0
        dna = 0
        xp_gain = HUNT_XP_LOSE

    user.coins += coins
    user.dna_fragments += dna
    user.save(update_fields=["coins", "dna_fragments"])
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

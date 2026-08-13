import random

from bio_lab.models import Creature, User
from game import constants
from game.combat import resolve_duel
from game.creature import add_xp, effective_stats

WILD_NAMES = ["Ferabeast", "Grimhide", "Rustclaw", "Mossfang", "Duskrunner"]

# each tier scales the wild creature's stats and its payout together, so picking a
# tougher target is a real risk/reward decision rather than a free upgrade
HUNT_TIERS = {
    "weak": {"label": "🟢 ضعیف", "stat_mult": 0.8, "reward_mult": 0.6},
    "normal": {"label": "🟡 هم‌سطح", "stat_mult": 1.0, "reward_mult": 1.0},
    "strong": {"label": "🔴 قوی", "stat_mult": 1.35, "reward_mult": 1.8},
}

HUNT_COIN_REWARD = (15, 35)
HUNT_DNA_REWARD = (0, 4)
HUNT_XP_WIN = 25
HUNT_XP_LOSE = 8


def spawn_wild_creature(player_creature: Creature, tier: str = "normal", seed: int | None = None) -> Creature:
    """Builds an unsaved (ephemeral) Creature scaled to the player's level and the
    chosen difficulty tier, purely to reuse the combat-math functions — never
    persisted, so its id stays None. `seed` makes a scouted target reproducible:
    the preview and the actual fight must roll the same opponent."""
    rng = random.Random(seed)
    cfg = HUNT_TIERS[tier]
    variance = rng.uniform(0.9, 1.1) * cfg["stat_mult"]
    level_factor = max(1, player_creature.level)
    return Creature(
        name=rng.choice(WILD_NAMES),
        element=rng.choice(constants.ELEMENTS),
        rarity="common",
        level=level_factor,
        base_hp=round((constants.STARTER_BASE_HP + level_factor * 4) * variance),
        base_atk=round((constants.STARTER_BASE_ATK + level_factor * 1.0) * variance),
        base_def=round((constants.STARTER_BASE_DEF + level_factor * 1.0) * variance),
        base_spd=round((constants.STARTER_BASE_SPD + level_factor * 0.6) * variance),
    )


def scout_targets(player_creature: Creature) -> list[dict]:
    """Three previewable opponents, one per tier. Each carries the seed used to
    generate it so resolve_hunt can rebuild the exact same creature on commit."""
    targets = []
    for tier in HUNT_TIERS:
        seed = random.randrange(1_000_000)
        wild = spawn_wild_creature(player_creature, tier, seed)
        wild_stats = effective_stats(wild)
        targets.append(
            {
                "tier": tier,
                "seed": seed,
                "name": wild.name,
                "element": wild.element,
                "power": wild_stats["hp"] + wild_stats["atk"] + wild_stats["def"] + wild_stats["spd"],
                "reward_mult": HUNT_TIERS[tier]["reward_mult"],
            }
        )
    return targets


def estimated_reward(tier: str) -> tuple[int, int]:
    """(min_coins, max_coins) shown while scouting, so the payout is visible upfront."""
    mult = HUNT_TIERS[tier]["reward_mult"]
    return round(HUNT_COIN_REWARD[0] * mult), round(HUNT_COIN_REWARD[1] * mult)


def resolve_hunt(user: User, player_creature: Creature, tier: str = "normal", seed: int | None = None) -> dict:
    """Plays out one solo PvE encounter and applies rewards. Caller handles energy."""
    wild = spawn_wild_creature(player_creature, tier, seed)
    winner, log_text = resolve_duel(player_creature, wild)
    won = winner is player_creature
    reward_mult = HUNT_TIERS[tier]["reward_mult"]

    if won:
        coins = round(random.randint(*HUNT_COIN_REWARD) * reward_mult)
        dna = round(random.randint(*HUNT_DNA_REWARD) * reward_mult)
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

    return {
        "won": won,
        "log_text": log_text,
        "wild_name": wild.name,
        "tier": tier,
        "coins": coins,
        "dna": dna,
        "xp": xp_gain,
        "levels": levels,
    }

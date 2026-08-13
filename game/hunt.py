import random

from bio_lab.models import Creature, User
from game import constants
from game.combat import resolve_duel
from game.creature import add_xp

WILD_NAMES = ["Ferabeast", "Grimhide", "Rustclaw", "Mossfang", "Duskrunner"]

HUNT_COIN_REWARD = (15, 35)
HUNT_DNA_REWARD = (0, 4)
HUNT_XP_WIN = 25
HUNT_XP_LOSE = 8


def spawn_wild_creature(player_creature: Creature) -> Creature:
    """Builds an unsaved (ephemeral) Creature scaled to the player's level, purely
    to reuse the combat-math functions — never persisted, so its id stays None."""
    variance = random.uniform(0.8, 1.15)
    level_factor = max(1, player_creature.level)
    return Creature(
        name=random.choice(WILD_NAMES),
        element=constants.random_element(),
        rarity="common",
        level=level_factor,
        base_hp=round((constants.STARTER_BASE_HP + level_factor * 4) * variance),
        base_atk=round((constants.STARTER_BASE_ATK + level_factor * 1.0) * variance),
        base_def=round((constants.STARTER_BASE_DEF + level_factor * 1.0) * variance),
        base_spd=round((constants.STARTER_BASE_SPD + level_factor * 0.6) * variance),
    )


def resolve_hunt(user: User, player_creature: Creature) -> dict:
    """Plays out one solo PvE encounter and applies rewards. Caller handles energy."""
    wild = spawn_wild_creature(player_creature)
    winner, log_text = resolve_duel(player_creature, wild)
    won = winner is player_creature

    if won:
        coins = random.randint(*HUNT_COIN_REWARD)
        dna = random.randint(*HUNT_DNA_REWARD)
        xp_gain = HUNT_XP_WIN
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
        "coins": coins,
        "dna": dna,
        "xp": xp_gain,
        "levels": levels,
    }

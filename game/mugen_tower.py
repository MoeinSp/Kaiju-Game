"""Mugen Tower (برج موگن - 無限の塔) - Infinite Roguelike Tower.

Floors scale infinitely with challenging elemental guardians, milestone boss rewards,
and a global climbing leaderboard.
"""

from __future__ import annotations

import math
import random

from bio_lab.models import Creature, User
from game import constants, lab
from game.combat import resolve_duel
from game.creature import add_xp, effective_stats


MUGEN_GUARDIAN_TITLES = [
    "نگهبان سایه",
    "روح شمشیرزن",
    "غول صخره‌ای",
    "اژدهای جهنمی",
    "کالبد باستانی",
    "تایتان بلورین",
    "ارباب رعد",
    "اهریمن اعماق",
    "پادشاه خاکستر",
    "امپراتور موگن",
]

MUGEN_ENERGY_COST = 5


def floor_guardian_power(floor: int) -> int:
    """Power curve for floor N: smooth progression starting from ~80 to thousands."""
    return max(50, round(50 + (floor ** 1.32) * 40))


def floor_guardian(floor: int) -> Creature:
    """Build ephemeral Creature for floor N guardian."""
    from game.creature import base_share_for_rating

    p = floor_guardian_power(floor)
    share = base_share_for_rating(p)
    element = constants.ELEMENTS[(floor - 1) % len(constants.ELEMENTS)]
    title_idx = (floor - 1) % len(MUGEN_GUARDIAN_TITLES)
    name = f"{MUGEN_GUARDIAN_TITLES[title_idx]} (طبقه {floor})"

    rarity = "common"
    if floor >= 50:
        rarity = "mythic"
    elif floor >= 30:
        rarity = "legendary"
    elif floor >= 15:
        rarity = "epic"
    elif floor >= 5:
        rarity = "rare"

    return Creature(
        name=name,
        element=element,
        rarity=rarity,
        level=floor,
        base_hp=share,
        base_atk=share,
        base_def=share,
        base_spd=share,
    )


def floor_rewards(floor: int) -> dict:
    """Reward calculation for clearing floor N (Boss vs Regular Floor balance)."""
    is_boss = (floor % 10 == 0)

    if is_boss:
        tier = floor // 10
        # Tickets requested by user:
        # Floors 10 to 50 -> 5 tickets
        # Floors 60 to 90 -> 20 tickets
        # Floor 100 -> 50 tickets
        if tier <= 5:
            tickets = 5
        elif tier <= 9:
            tickets = 20
        else: # tier == 10 (floor 100)
            tickets = 50

        diamonds = tier * 10
        if floor == 100:
            diamonds = 150

        # Massive Gold & DNA spike for defeating milestone boss
        coins = round(20000 + (floor ** 2.7) * 2.3)
        dna = round(200 + (floor ** 2.2) * 2.1)
        xp_gain = 100 + floor * 15
        lab_xp = 50 + floor * 10
    else:
        tickets = 0
        diamonds = 0
        # Regular floors: moderate progression rewards
        coins = round(2000 + (floor ** 1.8) * 16)
        dna = round(20 + (floor ** 1.4) * 2.5)
        xp_gain = 30 + floor * 3
        lab_xp = 15 + floor * 2

    return {
        "coins": coins,
        "dna": dna,
        "diamonds": diamonds,
        "tickets": tickets,
        "xp": xp_gain,
        "lab_xp": lab_xp,
    }


def get_mugen_status(user: User) -> dict:
    """Summary of user's Mugen tower status, next floor boss, and leaderboard."""
    floor = getattr(user, "mugen_tower_floor", 1) or 1
    guardian = floor_guardian(floor)
    power = floor_guardian_power(floor)
    rewards = floor_rewards(floor)

    # Top 10 climbers
    leaderboard = (
        User.objects.filter(mugen_tower_floor__gt=1)
        .order_by("-mugen_tower_floor")[:10]
        .values("id", "username", "first_name", "lab_name", "mugen_tower_floor")
    )

    return {
        "floor": floor,
        "guardian_name": guardian.name,
        "guardian_element": guardian.element,
        "guardian_power": power,
        "guardian_rarity": guardian.rarity,
        "rewards": rewards,
        "leaderboard": list(leaderboard),
    }


def fight_mugen_floor(user: User, player_creature: Creature) -> dict:
    """Fight the current floor guardian in Mugen Tower."""
    from game.energy import spend_energy
    from game.equipment import get_equipped_items
    from game.creature import creature_power
    from game.ledger import record_gain
    from game import research

    floor = getattr(user, "mugen_tower_floor", 1) or 1
    spend_energy(user, MUGEN_ENERGY_COST, "برج موگن")

    research.attach_research(user, player_creature)
    guardian = floor_guardian(floor)
    winner, log_text = resolve_duel(player_creature, guardian)
    won = winner is player_creature
    rew = floor_rewards(floor)
    player_power = creature_power(player_creature, get_equipped_items(player_creature))

    if won:
        user.mugen_tower_floor = floor + 1
        user.coins += rew["coins"]
        user.dna_fragments += rew["dna"]
        if rew["diamonds"]:
            user.diamonds += rew["diamonds"]
        if rew["tickets"]:
            user.biocrate_tickets = (user.biocrate_tickets or 0) + rew["tickets"]
        user.save(update_fields=["mugen_tower_floor", "coins", "dna_fragments", "diamonds", "biocrate_tickets"])

        record_gain(
            user, "mugen_tower",
            coins=rew["coins"],
            dna=rew["dna"],
            diamonds=rew["diamonds"],
        )

        add_xp(player_creature, rew["xp"])
        player_creature.save()
        lab.add_lab_xp(user, rew["lab_xp"])
    else:
        # Small consolation XP on loss
        add_xp(player_creature, 10)
        player_creature.save()

    return {
        "won": won,
        "floor": floor,
        "guardian_name": guardian.name,
        "guardian_element": guardian.element,
        "guardian_power": floor_guardian_power(floor),
        "player_power": player_power,
        "log_text": log_text,
        "rewards": rew if won else None,
        "next_floor": floor + 1 if won else floor,
    }

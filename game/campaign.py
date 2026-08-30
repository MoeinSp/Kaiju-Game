"""PvE campaign — an endless ladder of scaling 3v3 stages, the game's single-player
progression spine.

Each stage pits your team (game/teambattle) against a scaled enemy squad. Clearing
a stage advances you one step and pays a one-time first-clear reward; every 5th
stage is a tougher boss with a bigger prize. You can only attempt the next
uncleared stage, so the campaign is a natural difficulty wall that keeps pushing
you to build a stronger, deeper roster — which is the whole point of having a team.

Stages are generated from the stage number (no table), and enemy creatures are
ephemeral (never saved). Rewards flow through the same lab-XP / Battle-Pass hooks
as everything else.
"""

from __future__ import annotations

import random

from bio_lab.models import Creature, User
from game import constants, lab
from game.creature import GameError
from game.energy import spend_energy
from game.teambattle import resolve, team_power

MAX_STAGE = 50
ENERGY_COST = 1
BOSS_EVERY = 5

_ENEMY_NAMES = ["مهاجم", "دیو بیابان", "نگهبان تاریک", "شبح", "غول سنگی", "مار آتشین", "روح یخی", "گرگ توفان"]


def is_boss(stage: int) -> bool:
    return stage % BOSS_EVERY == 0


def enemy_team(stage: int) -> list[Creature]:
    """Three ephemeral enemy creatures scaled to the stage. Deterministic per stage
    so a stage always presents the same challenge."""
    rng = random.Random(stage * 7919)
    # much steeper enemy scaling — the campaign was too easy. Level climbs faster
    # with the stage AND every level adds far more stat, so enemy power grows
    # several times quicker than before.
    level = max(1, round(stage * 1.6))
    boss = is_boss(stage)
    # rarity climbs with depth, so late stages hit harder even at the same level
    tier_idx = min(len(constants.RARITY_ORDER) - 1, stage // 6)
    rarity = constants.RARITY_ORDER[tier_idx]
    rmult = constants.RARITY_STAT_MULTIPLIER[rarity] * (1.25 if boss else 1.0)
    team = []
    for i in range(3):
        element = constants.ELEMENTS[(stage + i) % len(constants.ELEMENTS)]
        team.append(
            Creature(
                name=rng.choice(_ENEMY_NAMES),
                element=element,
                rarity=rarity,
                level=level,
                base_hp=round((constants.STARTER_BASE_HP + level * 10) * rmult),
                base_atk=round((constants.STARTER_BASE_ATK + level * 2.3) * rmult),
                base_def=round((constants.STARTER_BASE_DEF + level * 2.0) * rmult),
                base_spd=round((constants.STARTER_BASE_SPD + level * 1.2) * rmult),
            )
        )
    return team


def first_clear_reward(stage: int) -> dict:
    reward = {"coins": 100 + stage * 40}
    if is_boss(stage):
        reward["diamonds"] = 10 + (stage // BOSS_EVERY) * 2
        reward["speedup"] = 30
    elif stage % 3 == 0:
        reward["dna"] = 8 + stage
    return reward


def _grant(user: User, reward: dict) -> None:
    from game.battlepass import _grant as grant

    grant(user, reward)


def status(user: User) -> dict:
    cleared = user.campaign_stage
    nxt = cleared + 1
    return {
        "cleared": cleared,
        "next_stage": nxt if nxt <= MAX_STAGE else None,
        "max_stage": MAX_STAGE,
        "next_is_boss": nxt <= MAX_STAGE and is_boss(nxt),
        "next_reward": first_clear_reward(nxt) if nxt <= MAX_STAGE else {},
        "enemy_power": team_power(enemy_team(nxt)) if nxt <= MAX_STAGE else 0,
    }


def attempt(user: User, team_creatures: list[Creature]) -> dict:
    """Fight the next uncleared stage with the player's team. Spends energy. On a
    win, advances the campaign and pays the first-clear reward."""
    stage = user.campaign_stage + 1
    if stage > MAX_STAGE:
        raise GameError("کل دانجن رو تموم کردی! 🏆 منتظر مراحل جدید باش.")
    if not team_creatures:
        raise GameError("اول از «⚔️ تیم من» حداقل یه هیولا توی تیمت بذار.")

    spend_energy(user, ENERGY_COST, "دانجن")  # raises if not enough
    user.save(update_fields=["energy", "energy_updated_at"])

    enemies = enemy_team(stage)
    result = resolve(team_creatures, enemies, seed=random.randrange(1_000_000))
    won = result["winner"] == "a"

    reward = {}
    if won:
        user.campaign_stage = stage
        user.save(update_fields=["campaign_stage"])
        reward = first_clear_reward(stage)
        _grant(user, reward)
        lab.award(user, "campaign_win")  # also feeds the Battle Pass

    return {
        "won": won,
        "stage": stage,
        "is_boss": is_boss(stage),
        "reward": reward,
        "log": result["log"],
        "survivors": result["survivors_a"],
        "cleared_all": won and stage == MAX_STAGE,
    }

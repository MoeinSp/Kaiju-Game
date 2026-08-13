import random
from dataclasses import dataclass

from bio_lab.models import Creature
from game import constants
from game.creature import effective_stats
from game.emoji import get_emoji
from game.equipment import get_equipped_items

MAX_ROUNDS = 12
CRIT_MULTIPLIER = 1.5


@dataclass
class Fighter:
    creature: Creature
    stats: dict[str, float]
    hp: int


def resolve_duel(creature_a: Creature, creature_b: Creature) -> tuple[Creature, str]:
    """Simulates an automatic duel and returns (winner_creature, battle_log_text).
    Runs in sync context (called from run_db-wrapped code), so it's safe to fetch
    equipped items here directly rather than requiring the caller to pass them."""
    fa = Fighter(creature_a, effective_stats(creature_a, get_equipped_items(creature_a)), 0)
    fa.hp = fa.stats["hp"]
    fb = Fighter(creature_b, effective_stats(creature_b, get_equipped_items(creature_b)), 0)
    fb.hp = fb.stats["hp"]

    log = [
        f"{get_emoji('battle')} <b>{fa.creature.name}</b> {constants.element_label(fa.creature.element)}"
        f"  ⚔️  <b>{fb.creature.name}</b> {constants.element_label(fb.creature.element)}",
        "",
    ]

    round_num = 0
    while fa.hp > 0 and fb.hp > 0 and round_num < MAX_ROUNDS:
        round_num += 1
        order = sorted([fa, fb], key=lambda f: f.stats["spd"] + random.uniform(0, 3), reverse=True)
        for attacker, defender in ((order[0], order[1]), (order[1], order[0])):
            if attacker.hp <= 0 or defender.hp <= 0:
                continue
            _attack(attacker, defender, log)

    winner = _decide_winner(fa, fb)
    log.append("")
    log.append(
        f"<i>{fa.creature.name} {max(fa.hp, 0)}❤️  ·  {fb.creature.name} {max(fb.hp, 0)}❤️  ·  {round_num} راند</i>"
    )
    log.append(f"{get_emoji('trophy')} <b>برنده: {winner.creature.name}</b>")
    return winner.creature, "\n".join(log)


def _attack(attacker: Fighter, defender: Fighter, log: list[str]) -> None:
    """Appends exactly one line per attack — poison and lifesteal are folded into
    that same line as suffixes rather than getting their own lines, which used to
    triple the length of every log."""
    mult = constants.element_multiplier(attacker.creature.element, defender.creature.element)
    base = max(1.0, attacker.stats["atk"] - defender.stats["def"] * 0.5)
    is_crit = random.random() < attacker.stats["crit_rate"]
    dmg = round(base * mult * random.uniform(0.85, 1.15) * (CRIT_MULTIPLIER if is_crit else 1.0))
    defender.hp -= dmg

    suffixes = []
    if is_crit:
        suffixes.append("💥")
    if mult > 1:
        suffixes.append("🔺مؤثر")
    elif mult < 1:
        suffixes.append("🔻کم‌اثر")

    if attacker.stats["lifesteal"] > 0:
        healed = round(dmg * attacker.stats["lifesteal"])
        if healed > 0:
            attacker.hp = min(attacker.stats["hp"], attacker.hp + healed)
            suffixes.append(f"{get_emoji('lifesteal')}+{healed}")

    if attacker.stats["poison"] > 0 and defender.hp > 0:
        poison_dmg = attacker.stats["poison"]
        defender.hp -= poison_dmg
        suffixes.append(f"{get_emoji('poison')}+{poison_dmg}")

    suffix_txt = f"  <i>{' '.join(suffixes)}</i>" if suffixes else ""
    log.append(f"{attacker.creature.name} ➜ {defender.creature.name}  <b>−{dmg}</b>{suffix_txt}")


def _decide_winner(fa: Fighter, fb: Fighter) -> Fighter:
    if fa.hp <= 0 and fb.hp > 0:
        return fb
    if fb.hp <= 0 and fa.hp > 0:
        return fa
    ratio_a = fa.hp / fa.stats["hp"]
    ratio_b = fb.hp / fb.stats["hp"]
    return fa if ratio_a >= ratio_b else fb

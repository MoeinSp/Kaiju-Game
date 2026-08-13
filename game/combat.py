import random
from dataclasses import dataclass

from bio_lab.models import Creature
from game import constants
from game.creature import effective_stats
from game.emoji import get_emoji

MAX_ROUNDS = 12
CRIT_CHANCE = 0.1
CRIT_MULTIPLIER = 1.5


@dataclass
class Fighter:
    creature: Creature
    stats: dict[str, int]
    hp: int


def resolve_duel(creature_a: Creature, creature_b: Creature) -> tuple[Creature, str]:
    """Simulates an automatic duel and returns (winner_creature, battle_log_text)."""
    fa = Fighter(creature_a, effective_stats(creature_a), 0)
    fa.hp = fa.stats["hp"]
    fb = Fighter(creature_b, effective_stats(creature_b), 0)
    fb.hp = fb.stats["hp"]

    log = [
        f"{get_emoji('battle')} <b>{fa.creature.name}</b> ({constants.element_label(fa.creature.element)}) "
        f"در برابر <b>{fb.creature.name}</b> ({constants.element_label(fb.creature.element)})\n"
    ]

    round_num = 0
    while fa.hp > 0 and fb.hp > 0 and round_num < MAX_ROUNDS:
        round_num += 1
        order = sorted([fa, fb], key=lambda f: f.stats["spd"] + random.uniform(0, 3), reverse=True)
        for attacker, defender in ((order[0], order[1]), (order[1], order[0])):
            if attacker.hp <= 0 or defender.hp <= 0:
                continue
            _attack(attacker, defender, log)
        log.append(f"<i>— راند {round_num}: {fa.creature.name} {max(fa.hp, 0)}HP | {fb.creature.name} {max(fb.hp, 0)}HP</i>")

    winner = _decide_winner(fa, fb)
    log.append(f"\n{get_emoji('trophy')} <b>برنده: {winner.creature.name}!</b>")
    return winner.creature, "\n".join(log)


def _attack(attacker: Fighter, defender: Fighter, log: list[str]) -> None:
    mult = constants.element_multiplier(attacker.creature.element, defender.creature.element)
    base = max(1.0, attacker.stats["atk"] - defender.stats["def"] * 0.5)
    is_crit = random.random() < CRIT_CHANCE
    dmg = round(base * mult * random.uniform(0.85, 1.15) * (CRIT_MULTIPLIER if is_crit else 1.0))
    defender.hp -= dmg

    crit_txt = " 💥کریتیکال!" if is_crit else ""
    elem_txt = " (مؤثر بود!)" if mult > 1 else (" (کم‌اثر بود)" if mult < 1 else "")
    log.append(f"{attacker.creature.name} به {defender.creature.name} {dmg} دمیج زد{crit_txt}{elem_txt}")

    if attacker.stats["poison"] > 0 and defender.hp > 0:
        poison_dmg = attacker.stats["poison"]
        defender.hp -= poison_dmg
        log.append(
            f"{get_emoji('poison')} زهر {attacker.creature.name} {poison_dmg} دمیج اضافه به "
            f"{defender.creature.name} زد"
        )


def _decide_winner(fa: Fighter, fb: Fighter) -> Fighter:
    if fa.hp <= 0 and fb.hp > 0:
        return fb
    if fb.hp <= 0 and fa.hp > 0:
        return fa
    ratio_a = fa.hp / fa.stats["hp"]
    ratio_b = fb.hp / fb.stats["hp"]
    return fa if ratio_a >= ratio_b else fb

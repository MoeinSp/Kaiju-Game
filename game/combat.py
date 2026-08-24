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
    hp: float
    dmg_dealt: float = 0.0
    crits: int = 0


def resolve_duel(creature_a: Creature, creature_b: Creature) -> tuple[Creature, str]:
    """Simulates an automatic duel and returns (winner_creature, battle_log_text).

    The log is a compact scoreboard, not a blow-by-blow: the old version printed up
    to 24 «A ➜ B −5» lines that read as noise. Now it's the matchup, an HP bar and
    total damage for each side, and the outcome — the numbers people actually want."""
    fa = Fighter(creature_a, effective_stats(creature_a, get_equipped_items(creature_a)), 0)
    fa.hp = fa.stats["hp"]
    fb = Fighter(creature_b, effective_stats(creature_b, get_equipped_items(creature_b)), 0)
    fb.hp = fb.stats["hp"]

    round_num = 0
    while fa.hp > 0 and fb.hp > 0 and round_num < MAX_ROUNDS:
        round_num += 1
        order = sorted([fa, fb], key=lambda f: f.stats["spd"] + random.uniform(0, 3), reverse=True)
        for attacker, defender in ((order[0], order[1]), (order[1], order[0])):
            if attacker.hp <= 0 or defender.hp <= 0:
                continue
            _attack(attacker, defender)

    winner = _decide_winner(fa, fb)
    mult = constants.element_multiplier(fa.creature.element, fb.creature.element)
    if mult > 1:
        edge = f"🔺 برتری عنصری با <b>{fa.creature.name}</b>"
    elif mult < 1:
        edge = f"🔺 برتری عنصری با <b>{fb.creature.name}</b>"
    else:
        edge = "⚖️ عنصرها خنثی‌ان"

    log = [
        f"{get_emoji('battle')} <b>{fa.creature.name}</b> {constants.element_label(fa.creature.element)}"
        f"  ⚔️  <b>{fb.creature.name}</b> {constants.element_label(fb.creature.element)}",
        edge,
        "",
        _scoreline(fa),
        _scoreline(fb),
        "",
        f"{get_emoji('trophy')} <b>برنده: {winner.creature.name}</b>  <i>· {round_num} راند</i>",
        constants.element_advantage_chain(),
    ]
    return winner.creature, "\n".join(log)


def _scoreline(f: Fighter) -> str:
    hp = max(0, round(f.hp))
    maxhp = max(1, round(f.stats["hp"]))
    bar = constants.render_bar(hp, maxhp, width=10)
    crit = f" · 💥{f.crits}" if f.crits else ""
    return f"{bar} {hp}/{maxhp}❤️  <b>{f.creature.name}</b> <i>(زد: {round(f.dmg_dealt)}{crit})</i>"


def _attack(attacker: Fighter, defender: Fighter) -> None:
    """Resolves one hit and folds the result into the attacker's running totals —
    the log no longer prints a line per hit."""
    mult = constants.element_multiplier(attacker.creature.element, defender.creature.element)
    base = max(1.0, attacker.stats["atk"] - defender.stats["def"] * 0.5)
    is_crit = random.random() < attacker.stats["crit_rate"]
    dmg = round(base * mult * random.uniform(0.85, 1.15) * (CRIT_MULTIPLIER if is_crit else 1.0))
    defender.hp -= dmg
    attacker.dmg_dealt += dmg
    if is_crit:
        attacker.crits += 1

    if attacker.stats["lifesteal"] > 0:
        healed = round(dmg * attacker.stats["lifesteal"])
        if healed > 0:
            attacker.hp = min(attacker.stats["hp"], attacker.hp + healed)

    if attacker.stats["poison"] > 0 and defender.hp > 0:
        poison_dmg = attacker.stats["poison"]
        defender.hp -= poison_dmg
        attacker.dmg_dealt += poison_dmg


def _decide_winner(fa: Fighter, fb: Fighter) -> Fighter:
    if fa.hp <= 0 and fb.hp > 0:
        return fb
    if fb.hp <= 0 and fa.hp > 0:
        return fa
    ratio_a = fa.hp / fa.stats["hp"]
    ratio_b = fb.hp / fb.stats["hp"]
    return fa if ratio_a >= ratio_b else fb

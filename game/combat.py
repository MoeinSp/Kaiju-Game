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
    """Simulates a duel, returning (winner, compact_log). For the full blow-by-blow
    too, call resolve_duel_detailed()."""
    winner, compact, _detail = resolve_duel_detailed(creature_a, creature_b)
    return winner, compact


def resolve_duel_detailed(creature_a: Creature, creature_b: Creature) -> tuple[Creature, str, str]:
    """Like resolve_duel but also returns a detailed blow-by-blow log (one line per
    hit) for the optional «جزییات حمله» view. The compact log is the default — the
    old always-on blow-by-blow read as 24 lines of noise."""
    fa = Fighter(creature_a, effective_stats(creature_a, get_equipped_items(creature_a)), 0)
    fa.hp = fa.stats["hp"]
    fb = Fighter(creature_b, effective_stats(creature_b, get_equipped_items(creature_b)), 0)
    fb.hp = fb.stats["hp"]

    blow_by_blow: list[str] = []
    round_num = 0
    while fa.hp > 0 and fb.hp > 0 and round_num < MAX_ROUNDS:
        round_num += 1
        blow_by_blow.append(f"<b>راند {round_num}</b>")
        order = sorted([fa, fb], key=lambda f: f.stats["spd"] + random.uniform(0, 3), reverse=True)
        for attacker, defender in ((order[0], order[1]), (order[1], order[0])):
            if attacker.hp <= 0 or defender.hp <= 0:
                continue
            _attack(attacker, defender, blow_by_blow)

    winner = _decide_winner(fa, fb)
    mult = constants.element_multiplier(fa.creature.element, fb.creature.element)
    if mult > 1:
        edge = f"🔺 برتری عنصری با <b>{fa.creature.name}</b>"
    elif mult < 1:
        edge = f"🔺 برتری عنصری با <b>{fb.creature.name}</b>"
    else:
        edge = "⚖️ عنصرها خنثی‌ان"

    header = (
        f"{get_emoji('battle')} <b>{fa.creature.name}</b> {constants.element_label(fa.creature.element)}"
        f"  ⚔️  <b>{fb.creature.name}</b> {constants.element_label(fb.creature.element)}"
    )
    compact = "\n".join([
        header, edge, "",
        _scoreline(fa), _scoreline(fb), "",
        f"{get_emoji('trophy')} <b>برنده: {winner.creature.name}</b>  <i>· {round_num} راند</i>",
        constants.element_advantage_chain(),
    ])
    detail = "\n".join([
        f"🔍 <b>جزییات نبرد</b>\n{header}\n{edge}\n",
        "\n".join(blow_by_blow),
        f"\n{get_emoji('trophy')} <b>برنده: {winner.creature.name}</b>  <i>· {round_num} راند</i>",
    ])
    return winner.creature, compact, detail


def _scoreline(f: Fighter) -> str:
    hp = max(0, round(f.hp))
    maxhp = max(1, round(f.stats["hp"]))
    bar = constants.render_bar(hp, maxhp, width=10)
    crit = f" · 💥{f.crits}" if f.crits else ""
    return f"{bar} {hp}/{maxhp}❤️  <b>{f.creature.name}</b> <i>(زد: {round(f.dmg_dealt)}{crit})</i>"


def _attack(attacker: Fighter, defender: Fighter, detail: list[str]) -> None:
    """Resolves one hit: folds the result into the attacker's running totals (for the
    compact scoreboard) and appends one blow-by-blow line to `detail`."""
    mult = constants.element_multiplier(attacker.creature.element, defender.creature.element)
    base = max(1.0, attacker.stats["atk"] - defender.stats["def"] * 0.5)
    is_crit = random.random() < attacker.stats["crit_rate"]
    dmg = round(base * mult * random.uniform(0.85, 1.15) * (CRIT_MULTIPLIER if is_crit else 1.0))
    defender.hp -= dmg
    attacker.dmg_dealt += dmg
    if is_crit:
        attacker.crits += 1

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
        attacker.dmg_dealt += poison_dmg
        suffixes.append(f"{get_emoji('poison')}+{poison_dmg}")

    suffix_txt = f"  <i>{' '.join(suffixes)}</i>" if suffixes else ""
    detail.append(f"{attacker.creature.name} ➜ {defender.creature.name}  <b>−{dmg}</b>{suffix_txt}")


def _decide_winner(fa: Fighter, fb: Fighter) -> Fighter:
    if fa.hp <= 0 and fb.hp > 0:
        return fb
    if fb.hp <= 0 and fa.hp > 0:
        return fa
    ratio_a = fa.hp / fa.stats["hp"]
    ratio_b = fb.hp / fb.stats["hp"]
    return fa if ratio_a >= ratio_b else fb

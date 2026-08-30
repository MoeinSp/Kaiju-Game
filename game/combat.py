import random
from dataclasses import dataclass

from bio_lab.models import Creature
from game import constants
from game.creature import combat_rating, effective_stats
from game.emoji import get_emoji
from game.equipment import get_equipped_items

MAX_ROUNDS = 12
CRIT_MULTIPLIER = 1.5
# Real, per-fight variance. Combat used to be fully deterministic (a fixed per-matchup
# seed + a ±3% wobble) so a given pairing ALWAYS resolved the same way — which made the
# shown win-% a lie: the fight was already decided, yet the player saw "65%". Now every
# fight rolls fresh, so the stronger side USUALLY wins (you can analyse and decide) but
# a close matchup genuinely can go either way, and a rematch can flip. The spread is
# tuned (see win_chance_pct's calibration) so a clear power lead is ~90% and parity is
# ~50%, never a guaranteed 0/100.
DMG_VARIANCE = (0.85, 1.15)

# Per-fight "form": once per duel each fighter rolls an overall offensive multiplier in
# [1-FORM_SWING, 1+FORM_SWING]. This is what turns the fight from a near-step-function
# (where any power edge is decisive, because atk+def+hp+spd all compound) into a smooth,
# analysable curve: a fighter can have a good or bad day. The swing width is tuned so
# parity ≈ 50/50 and a large power lead ≈ 90% — see win_chance_pct, which is calibrated
# to the win-rate this produces.
FORM_SWING = 0.38


@dataclass
class Fighter:
    creature: Creature
    stats: dict[str, float]
    hp: float
    dmg_dealt: float = 0.0
    crits: int = 0
    form: float = 1.0


def resolve_duel(creature_a: Creature, creature_b: Creature) -> tuple[Creature, str]:
    """Simulates a duel, returning (winner, compact_log). For the full blow-by-blow
    too, call resolve_duel_detailed()."""
    winner, compact, _detail = resolve_duel_detailed(creature_a, creature_b)
    return winner, compact


def resolve_duel_detailed(creature_a: Creature, creature_b: Creature) -> tuple[Creature, str, str]:
    """Like resolve_duel but also returns a detailed blow-by-blow log (one line per
    hit) for the optional «جزییات حمله» view. The compact log is the default — the
    old always-on blow-by-blow read as 24 lines of noise."""
    rng = random.Random()  # fresh each fight — outcomes are probabilistic, not fixed
    fa = Fighter(creature_a, effective_stats(creature_a, get_equipped_items(creature_a)), 0)
    fa.hp = fa.stats["hp"]
    fb = Fighter(creature_b, effective_stats(creature_b, get_equipped_items(creature_b)), 0)
    fb.hp = fb.stats["hp"]
    fa.form = rng.uniform(1 - FORM_SWING, 1 + FORM_SWING)
    fb.form = rng.uniform(1 - FORM_SWING, 1 + FORM_SWING)

    blow_by_blow: list[str] = []
    round_num = 0
    while fa.hp > 0 and fb.hp > 0 and round_num < MAX_ROUNDS:
        round_num += 1
        blow_by_blow.append(f"<b>راند {round_num}</b>")
        # faster attacks first; the tiny seeded tiebreak is deterministic
        order = sorted([fa, fb], key=lambda f: (f.stats["spd"], rng.random()), reverse=True)
        for attacker, defender in ((order[0], order[1]), (order[1], order[0])):
            if attacker.hp <= 0 or defender.hp <= 0:
                continue
            _attack(attacker, defender, blow_by_blow, rng)

    winner = _decide_winner(fa, fb)
    mult = constants.element_multiplier(fa.creature.element, fb.creature.element)
    if mult > 1:
        edge = f"🔺 برتری عنصری با <b>{fa.creature.name}</b>"
    elif mult < 1:
        edge = f"🔺 برتری عنصری با <b>{fb.creature.name}</b>"
    else:
        edge = "⚖️ عنصرها خنثی‌ان"

    # attacker on top, defender below — labelled, one per line, so it's clear who's who
    header = (
        f"🗡 <b>حمله‌کننده:</b> <b>{fa.creature.name}</b> {constants.element_label(fa.creature.element)}\n"
        f"🛡 <b>دفاع‌کننده:</b> <b>{fb.creature.name}</b> {constants.element_label(fb.creature.element)}"
    )
    compact = "\n".join([
        header, edge, "",
        _scoreline(fa), _scoreline(fb), "",
        f"{get_emoji('trophy')} <b>برنده: {winner.creature.name}</b>  <i>· {round_num} راند</i>",
        "",
        constants.element_advantage_lines(),
    ])
    detail = "\n".join([
        f"🔍 <b>جزییات نبرد</b>\n{header}\n{edge}\n",
        "\n".join(blow_by_blow),
        f"\n{get_emoji('trophy')} <b>برنده: {winner.creature.name}</b>  <i>· {round_num} راند</i>",
    ])
    return winner.creature, compact, detail


def resolve_duel_report(creature_a: Creature, creature_b: Creature) -> dict:
    """Structured result for rich battle cards — winner, round count, and each side's
    final/max HP + element + crits. Rolls fresh like resolve_duel (probabilistic), so
    it's one independent play-out of the matchup, not a fixed outcome."""
    rng = random.Random()  # fresh each fight — outcomes are probabilistic, not fixed
    fa = Fighter(creature_a, effective_stats(creature_a, get_equipped_items(creature_a)), 0)
    fa.hp = fa.stats["hp"]
    fb = Fighter(creature_b, effective_stats(creature_b, get_equipped_items(creature_b)), 0)
    fb.hp = fb.stats["hp"]
    fa.form = rng.uniform(1 - FORM_SWING, 1 + FORM_SWING)
    fb.form = rng.uniform(1 - FORM_SWING, 1 + FORM_SWING)
    round_num = 0
    while fa.hp > 0 and fb.hp > 0 and round_num < MAX_ROUNDS:
        round_num += 1
        order = sorted([fa, fb], key=lambda f: (f.stats["spd"], rng.random()), reverse=True)
        for attacker, defender in ((order[0], order[1]), (order[1], order[0])):
            if attacker.hp <= 0 or defender.hp <= 0:
                continue
            _attack(attacker, defender, [], rng)
    winner = _decide_winner(fa, fb)

    def _side(f: Fighter) -> dict:
        return {
            "name": f.creature.name, "element": f.creature.element,
            "hp": max(0, round(f.hp)), "max_hp": max(1, round(f.stats["hp"])), "crits": f.crits,
        }

    return {
        "winner": winner.creature, "rounds": round_num,
        "a": _side(fa), "b": _side(fb),
        "mult": constants.element_multiplier(fa.creature.element, fb.creature.element),
    }


def _scoreline(f: Fighter) -> str:
    """HP bar + name. The old "(زد: N)" damage-dealt total was cryptic, so it's gone;
    a crit count only shows when there were crits."""
    hp = max(0, round(f.hp))
    maxhp = max(1, round(f.stats["hp"]))
    bar = constants.render_bar(hp, maxhp, width=10)
    crit = f"  💥{f.crits}" if f.crits else ""
    return f"{bar} {hp}/{maxhp}❤️  <b>{f.creature.name}</b>{crit}"


def _attack(attacker: Fighter, defender: Fighter, detail: list[str], rng: random.Random) -> None:
    """Resolves one hit: folds the result into the attacker's running totals (for the
    compact scoreboard) and appends one blow-by-blow line to `detail`. All randomness
    comes from the seeded `rng`, so the fight is deterministic per matchup."""
    mult = constants.element_multiplier(attacker.creature.element, defender.creature.element)
    base = max(1.0, attacker.stats["atk"] * attacker.form - defender.stats["def"] * 0.5)
    is_crit = rng.random() < attacker.stats["crit_rate"]
    dmg = round(base * mult * rng.uniform(*DMG_VARIANCE) * (CRIT_MULTIPLIER if is_crit else 1.0))
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


def _power(f: Fighter) -> float:
    # same combat-accurate rating used everywhere else, so the timeout tiebreak
    # agrees with the power number the player was shown
    return combat_rating(f.stats)


def _decide_winner(fa: Fighter, fb: Fighter) -> Fighter:
    if fa.hp <= 0 and fb.hp > 0:
        return fb
    if fb.hp <= 0 and fa.hp > 0:
        return fa
    # both alive (timeout): higher remaining-HP ratio wins; if that ties, the
    # stronger creature does — so the outcome is never a coin flip.
    ratio_a = fa.hp / max(1.0, fa.stats["hp"])
    ratio_b = fb.hp / max(1.0, fb.stats["hp"])
    if abs(ratio_a - ratio_b) > 1e-9:
        return fa if ratio_a > ratio_b else fb
    return fa if _power(fa) >= _power(fb) else fb

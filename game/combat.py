import random
from dataclasses import dataclass

from bio_lab.models import Creature
from bio_lab.repository import creature_name
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


def _simulate(creature_a: Creature, creature_b: Creature) -> tuple[Fighter, Fighter, Fighter, int, list[str]]:
    """Play out one probabilistic duel. Returns (fa, fb, winner, rounds, blow_by_blow).
    Single source of truth so every result view (compact, detailed, structured) is the
    same fight rather than three copies of the loop that could drift apart."""
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
        blow_by_blow.append(f"• <b>راند {round_num}</b>")
        # faster attacks first; the tiny seeded tiebreak is deterministic
        order = sorted([fa, fb], key=lambda f: (f.stats["spd"], rng.random()), reverse=True)
        for attacker, defender in ((order[0], order[1]), (order[1], order[0])):
            if attacker.hp <= 0 or defender.hp <= 0:
                continue
            _attack(attacker, defender, blow_by_blow, rng)

    winner = _decide_winner(fa, fb)
    return fa, fb, winner, round_num, blow_by_blow


def _side(f: Fighter) -> dict:
    return {
        "name": creature_name(f.creature), "element": f.creature.element,
        "hp": max(0, round(f.hp)), "max_hp": max(1, round(f.stats["hp"])), "crits": f.crits,
    }


def _hp_report_line(side: dict) -> str:
    """One creature's health at the end of the fight: bar + percent + exact HP + crits."""
    hp, maxhp = side["hp"], side["max_hp"]
    icon = "💀" if hp <= 0 else "❤️"
    pct = round(100 * max(hp, 0) / max(1, maxhp))
    bar = constants.render_bar(hp, maxhp, width=10)
    crit = f"  💥 {side['crits']} ضربه" if side["crits"] else ""
    return f"{icon} <b>{side['name']}</b>: [{bar}] {pct}% ({hp:,}/{maxhp:,} HP){crit}"


def battle_report(sa: dict, sb: dict, winner_name: str, rounds: int, mult: float,
                  *, victor_line: str | None = None, reward_block: str | None = None) -> str:
    """The unified end-of-battle text, shared by every mode (hunt, group boss, PvP,
    arena) so they all read identically. `victor_line` adds a «پیروز میدان» mention
    for player-vs-player fights; `reward_block` is inserted just before the element
    cycle so loot always sits in the same place. Attacker is `sa`, defender is `sb`."""
    div = "──────────────"
    a_lbl = constants.element_label(sa["element"])
    b_lbl = constants.element_label(sb["element"])
    if mult > 1:
        match = f"⚖️ <b>تطابق عناصر:</b> برتری با <b>{sa['name']}</b> (ضریب آسیب فعال)"
    elif mult < 1:
        match = f"⚖️ <b>تطابق عناصر:</b> برتری با <b>{sb['name']}</b> (ضریب آسیب فعال)"
    else:
        match = "⚖️ <b>تطابق عناصر:</b> خنثی (بدون ضریب آسیب)"
    lines = [
        f"🗡 <b>مهاجم:</b> <b>{sa['name']}</b> [{a_lbl}]",
        f"🛡 <b>مدافع:</b> <b>{sb['name']}</b> [{b_lbl}]",
        match,
        "", div, "",
        "📊 <b>وضعیت سلامت در پایان نبرد:</b>",
        "",
        _hp_report_line(sa),
        _hp_report_line(sb),
        "", div, "",
        f"{get_emoji('trophy')} <b>فاتح نبرد:</b> <b>{winner_name}</b> (در {rounds} راند)",
    ]
    if victor_line:
        lines.append(f"👑 <b>پیروز میدان:</b> {victor_line} 🎉")
    if reward_block:
        lines += ["", div, "", reward_block]
    lines += ["", div, "", constants.element_cycle_block()]
    return "\n".join(lines)


def _detail_text(fa: Fighter, fb: Fighter, winner: Fighter, rounds: int, blow_by_blow: list[str]) -> str:
    div = "──────────────"
    a_emoji = get_emoji(constants.ELEMENT_EMOJI_KEYS[fa.creature.element])
    b_emoji = get_emoji(constants.ELEMENT_EMOJI_KEYS[fb.creature.element])
    return "\n".join([
        "🔍 <b>گزارش نبرد</b>",
        f"🗡 <b>{creature_name(fa.creature)}</b> {a_emoji} vs 🛡 <b>{creature_name(fb.creature)}</b> {b_emoji}",
        div,
        "",
        "\n".join(blow_by_blow),
        "",
        div,
        f"{get_emoji('trophy')} <b>برنده: {creature_name(winner.creature)}</b> (در {rounds} راند)",
    ])


def resolve_battle(creature_a: Creature, creature_b: Creature) -> dict:
    """Rich core result for callers that want to slot rewards into the shared report
    themselves: structured sides, winner, round count, element multiplier, and the
    ready-made compact / detail strings. Attacker is `creature_a`."""
    fa, fb, winner, rounds, blow = _simulate(creature_a, creature_b)
    sa, sb = _side(fa), _side(fb)
    mult = constants.element_multiplier(fa.creature.element, fb.creature.element)
    return {
        "winner": winner.creature, "winner_name": creature_name(winner.creature),
        "rounds": rounds, "mult": mult, "a": sa, "b": sb,
        "compact": battle_report(sa, sb, winner.creature.name, rounds, mult),
        "detail": _detail_text(fa, fb, winner, rounds, blow),
    }


def resolve_duel(creature_a: Creature, creature_b: Creature) -> tuple[Creature, str]:
    """Simulates a duel, returning (winner, compact_log). For the full blow-by-blow
    too, call resolve_duel_detailed()."""
    r = resolve_battle(creature_a, creature_b)
    return r["winner"], r["compact"]


def resolve_duel_detailed(creature_a: Creature, creature_b: Creature) -> tuple[Creature, str, str]:
    """Like resolve_duel but also returns a detailed blow-by-blow log (one line per
    hit) for the optional «جزییات حمله» view."""
    r = resolve_battle(creature_a, creature_b)
    return r["winner"], r["compact"], r["detail"]


def resolve_duel_report(creature_a: Creature, creature_b: Creature) -> dict:
    """Structured result for rich battle cards — winner, round count, and each side's
    final/max HP + element + crits. Rolls fresh like resolve_duel (probabilistic), so
    it's one independent play-out of the matchup, not a fixed outcome."""
    fa, fb, winner, rounds, _blow = _simulate(creature_a, creature_b)
    return {
        "winner": winner.creature, "rounds": rounds,
        "a": _side(fa), "b": _side(fb),
        "mult": constants.element_multiplier(fa.creature.element, fb.creature.element),
    }


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

    crit_tag = " 💥" if is_crit else ""
    if mult > 1:
        eff = " (بسیار مؤثر)"
    elif mult < 1:
        eff = " (کم‌اثر)"
    else:
        eff = ""

    extra = ""
    if attacker.stats["lifesteal"] > 0:
        healed = round(dmg * attacker.stats["lifesteal"])
        if healed > 0:
            attacker.hp = min(attacker.stats["hp"], attacker.hp + healed)
            extra += f" {get_emoji('lifesteal')}+{healed}"

    if attacker.stats["poison"] > 0 and defender.hp > 0:
        poison_dmg = attacker.stats["poison"]
        defender.hp -= poison_dmg
        attacker.dmg_dealt += poison_dmg
        extra += f" {get_emoji('poison')}+{poison_dmg}"

    # nested per-hit line: «   • هما به تیشتر: −12 💥 (بسیار مؤثر)»
    detail.append(
        f"   • {creature_name(attacker.creature)} به {creature_name(defender.creature)}: <b>−{dmg}</b>{crit_tag}{eff}{extra}"
    )


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

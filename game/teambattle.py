"""3v3 team battle engine — a gauntlet auto-battler that makes a deep roster matter.

Separate from combat.py (which stays 1v1 for hunt/duel/raid). Here each side
fields up to three creatures; the front fighters trade blows, and when one faints
the next steps up, until one team is wiped. Two things reward thoughtful
team-building:

* **Element traits** — each creature carries a passive from its element (fire hits
  harder, water is bulkier, earth takes less, electric crits more), so *which*
  creatures you field changes the outcome.
* **Element synergy** — a team whose three creatures share one element gets a small
  team-wide attack bonus, rewarding a committed theme over a random three.

Used by the PvE campaign (game/campaign.py). Deterministic under a given seed so
the preview and the real fight agree and tests are stable.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from bio_lab.models import Creature
from game import constants
from game.creature import effective_stats
from game.emoji import get_emoji
from game.equipment import get_equipped_items

MAX_TURNS = 60
CRIT_MULTIPLIER = 1.5

# passive trait each element grants a fighter, applied when stats are built
ELEMENT_TRAIT = {
    "fire": {"atk_mult": 1.25},        # aggressor
    "water": {"hp_mult": 1.25},        # bulwark
    "earth": {"dmg_taken_mult": 0.82}, # tank
    "electric": {"crit_bonus": 0.15},  # striker
}
SYNERGY_ATK_BONUS = 0.10  # all three share an element → +10% atk team-wide


@dataclass
class _Fighter:
    creature: Creature
    stats: dict
    hp: int
    dmg_taken_mult: float = 1.0
    alive: bool = True


def _build_side(creatures: list[Creature], synergy: bool) -> list[_Fighter]:
    fighters = []
    for c in creatures:
        # enemy/campaign creatures are ephemeral (no pk) — never query equipment for them
        items = get_equipped_items(c) if c.pk else []
        stats = dict(effective_stats(c, items))
        trait = ELEMENT_TRAIT.get(c.element, {})
        stats["atk"] = stats["atk"] * trait.get("atk_mult", 1.0) * (1 + SYNERGY_ATK_BONUS if synergy else 1)
        stats["hp"] = round(stats["hp"] * trait.get("hp_mult", 1.0))
        stats["crit_rate"] = stats["crit_rate"] + trait.get("crit_bonus", 0.0)
        fighters.append(_Fighter(creature=c, stats=stats, hp=stats["hp"], dmg_taken_mult=trait.get("dmg_taken_mult", 1.0)))
    return fighters


def _same_element(creatures: list[Creature]) -> bool:
    return len(creatures) == 3 and len({c.element for c in creatures}) == 1


def _hit(rng: random.Random, attacker: _Fighter, defender: _Fighter, log: list[str]) -> None:
    mult = constants.element_multiplier(attacker.creature.element, defender.creature.element)
    base = max(1.0, attacker.stats["atk"] - defender.stats["def"] * 0.5)
    is_crit = rng.random() < attacker.stats["crit_rate"]
    dmg = round(
        base * mult * rng.uniform(0.85, 1.15) * (CRIT_MULTIPLIER if is_crit else 1.0) * defender.dmg_taken_mult
    )
    dmg = max(1, dmg)
    defender.hp -= dmg
    tags = []
    if is_crit:
        tags.append("💥")
    if mult > 1:
        tags.append("🔺")
    elif mult < 1:
        tags.append("🔻")
    tag_txt = f" <i>{''.join(tags)}</i>" if tags else ""
    log.append(f"{attacker.creature.name} ➜ {defender.creature.name} <b>−{dmg}</b>{tag_txt}")
    if defender.hp <= 0:
        defender.alive = False
        log.append(f"☠️ <b>{defender.creature.name}</b> از پا افتاد!")


def resolve(team_a: list[Creature], team_b: list[Creature], seed: int | None = None) -> dict:
    """Fight two teams. Returns {winner: 'a'|'b', log, survivors_a, survivors_b}."""
    rng = random.Random(seed)
    fa = _build_side(team_a, _same_element(team_a))
    fb = _build_side(team_b, _same_element(team_b))

    log: list[str] = []
    ia = ib = 0
    turn = 0
    while ia < len(fa) and ib < len(fb) and turn < MAX_TURNS:
        turn += 1
        a, b = fa[ia], fb[ib]
        # faster fighter strikes first
        first, second = (a, b) if a.stats["spd"] >= b.stats["spd"] else (b, a)
        _hit(rng, first, second, log)
        if second.alive:
            _hit(rng, second, first, log)
        if not fa[ia].alive:
            ia += 1
        if not fb[ib].alive:
            ib += 1

    a_alive = sum(1 for f in fa if f.alive)
    b_alive = sum(1 for f in fb if f.alive)
    if a_alive != b_alive:
        winner = "a" if a_alive > b_alive else "b"
    else:
        # timeout / simultaneous wipe → compare remaining HP fraction
        ha = sum(max(0, f.hp) for f in fa)
        hb = sum(max(0, f.hp) for f in fb)
        winner = "a" if ha >= hb else "b"
    return {"winner": winner, "log": log, "survivors_a": a_alive, "survivors_b": b_alive}


def team_power(creatures: list[Creature]) -> int:
    """A quick strength number for a team, for previews/matchmaking."""
    synergy = _same_element(creatures)
    total = 0
    for f in _build_side(creatures, synergy):
        total += round(f.stats["hp"] + f.stats["atk"] + f.stats["def"] + f.stats["spd"])
    return total


def battle_summary(log: list[str], max_lines: int = 16) -> str:
    """Trim a battle log so a long gauntlet still fits in one Telegram message."""
    if len(log) <= max_lines:
        return "\n".join(log)
    head = log[: max_lines - 3]
    return "\n".join(head + ["<i>…</i>"] + log[-2:])

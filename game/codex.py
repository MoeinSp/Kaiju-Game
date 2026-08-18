"""Codex / دانشنامه — the "gotta collect 'em all" completion drive.

There are exactly 20 species (game/constants.SPECIES). The Codex tracks which a
player has discovered and hands out milestone rewards for breadth (5/10/15/20
discovered) and for completing each element's full set of 5.

Discovery is persistent (a CodexEntry per species) so it survives fusing or
releasing the creature — but it's *recorded lazily* from currently-owned creatures
every time the Codex is opened, so there's no signal magic and no hook scattered
through every creature-creation path. In practice nothing is ever lost: fusion
keeps the species (the result has the same name), so a species only leaves your
roster if you delete every copy, and by then you've almost certainly viewed it.

Milestone reward claims reuse AchievementClaim with ``codex_`` keys — a generic
(user, key) "already collected" table, which is all a one-time claim needs.
"""

from __future__ import annotations

from django.db import transaction

from bio_lab.models import AchievementClaim, CodexEntry, Creature, User
from game import constants

# breadth milestones: N species discovered -> reward
COUNT_MILESTONES = {
    5: {"dna": 30},
    10: {"diamonds": 20},
    15: {"diamonds": 40},
    20: {"diamonds": 100, "speedup": 720},  # the full dex
}
# completing all 5 species of one element -> reward
ELEMENT_COMPLETE_REWARD = {"diamonds": 15}

_CLAIM_PREFIX = "codex_"


def discover_owned(user: User) -> None:
    """Record every species the player currently owns into their Codex."""
    owned = set(
        Creature.objects.filter(owner=user).values_list("name", flat=True)
    )
    known = set(CodexEntry.objects.filter(user=user).values_list("species", flat=True))
    to_add = [s for s in owned if s in constants.SPECIES and s not in known]
    if to_add:
        CodexEntry.objects.bulk_create(
            [CodexEntry(user=user, species=s) for s in to_add], ignore_conflicts=True
        )


def _reward_text(reward: dict) -> str:
    parts = []
    if reward.get("coins"):
        parts.append(f"{reward['coins']} طلا")
    if reward.get("dna"):
        parts.append(f"{reward['dna']} DNA")
    if reward.get("diamonds"):
        parts.append(f"{reward['diamonds']} 💎")
    if reward.get("speedup"):
        parts.append(f"کارت {reward['speedup']}د")
    return " + ".join(parts) or "—"


def _milestone_keys_met(discovered: set[str]) -> dict[str, dict]:
    """All milestone keys currently met, mapped to their reward."""
    met: dict[str, dict] = {}
    count = len(discovered)
    for n, reward in COUNT_MILESTONES.items():
        if count >= n:
            met[f"{_CLAIM_PREFIX}count_{n}"] = reward
    for element, names in constants.SPECIES_NAMES.items():
        if all(name in discovered for name in names):
            met[f"{_CLAIM_PREFIX}element_{element}"] = dict(ELEMENT_COMPLETE_REWARD)
    return met


def status(user: User) -> dict:
    """Full Codex view: per-element species with discovered flags, counts, and how
    many milestone rewards are ready to claim."""
    discover_owned(user)
    discovered = set(CodexEntry.objects.filter(user=user).values_list("species", flat=True))
    claimed = set(
        AchievementClaim.objects.filter(user=user, key__startswith=_CLAIM_PREFIX).values_list(
            "key", flat=True
        )
    )
    elements = []
    for element, names in constants.SPECIES_NAMES.items():
        species = [{"name": n, "found": n in discovered} for n in names]
        elements.append(
            {
                "element": element,
                "label": constants.element_label(element),
                "species": species,
                "complete": all(s["found"] for s in species),
            }
        )
    met = _milestone_keys_met(discovered)
    claimable = sum(1 for k in met if k not in claimed)
    return {
        "elements": elements,
        "discovered": len(discovered),
        "total": len(constants.SPECIES),
        "claimable": claimable,
        "count_milestones": COUNT_MILESTONES,
    }


@transaction.atomic
def claim(user: User) -> dict:
    """Claim every met-but-unclaimed Codex milestone. Returns {'claimed': int,
    'reward': {...totals...}}."""
    from game.battlepass import _grant  # shared reward-granting helper

    discover_owned(user)
    discovered = set(CodexEntry.objects.filter(user=user).values_list("species", flat=True))
    claimed = set(
        AchievementClaim.objects.filter(user=user, key__startswith=_CLAIM_PREFIX).values_list(
            "key", flat=True
        )
    )
    met = _milestone_keys_met(discovered)
    totals: dict[str, int] = {}
    n = 0
    for key, reward in met.items():
        if key in claimed:
            continue
        AchievementClaim.objects.create(user=user, key=key)
        _grant(user, reward)
        for k, v in reward.items():
            totals[k] = totals.get(k, 0) + v
        n += 1
    return {"claimed": n, "reward": totals}

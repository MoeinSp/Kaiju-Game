"""«تیم دفاع» — up to two creatures that defend a player in the arena and in group «اتک».

When someone attacks (a search, a held card, a revenge), `pick_defender` chooses, among the
defense team, the creature that does best against the ATTACKER's creature under the game's
one duel rule (constants.duel_attacker_wins: higher effective power wins, ×1.20 for the
element-advantaged side, a tie goes to the attacker):

  1. one that WINS, if any — the one with the widest margin;
  2. otherwise the one that loses by the least.

The pick is re-made every time the attacker's creature changes («تعویض»), and once more at the
fight itself: the defender is ALWAYS the team's best answer to the creature that really
attacks (owner's rule, 2026-11-06). When the pick changes, the attacker is told why
(`swap_note`) — the new face is another member of the same defense team.

A creature busy elsewhere (mine, mission, cave) doesn't defend. A creature may be in both the
normal Team and the defense team. No defense team (or every member busy) → the active
creature defends, exactly as before.
"""

from __future__ import annotations

from bio_lab.models import Creature, DefenseTeam, User
from game import constants
from game.creature import GameError, creature_power

MAX_SLOTS = 2   # was 3 until 2026-11-06; older teams keep their two strongest (member_ids)


def _slots(team: DefenseTeam) -> list[int]:
    return [cid for cid in (team.slot1_id, team.slot2_id, team.slot3_id) if cid is not None]


def member_ids(user: User) -> list[int]:
    """The defense team, pruned to creatures the player still owns (fused / transferred ones
    drop out and the cleanup is saved)."""
    team = DefenseTeam.objects.filter(owner=user).first()
    if team is None:
        return []
    ids = _slots(team)
    owned = {c.id: c for c in Creature.objects.filter(owner=user, id__in=ids)}
    valid = [cid for cid in ids if cid in owned]
    if len(valid) > MAX_SLOTS:   # a team picked under the old cap of 3: keep its strongest two
        strongest = set(sorted(valid, key=lambda cid: power_of(user, owned[cid]), reverse=True)[:MAX_SLOTS])
        valid = [cid for cid in valid if cid in strongest]
    if valid != ids:
        team.slot1_id, team.slot2_id, team.slot3_id = (valid + [None, None, None])[:3]
        team.save(update_fields=["slot1", "slot2", "slot3", "updated_at"])
    return valid


def swap_note(old_name: str, new_name: str, new_element: str | None, plain: bool = False) -> str:
    """What the attacker reads when the defender changes under their eyes (a swap of theirs made
    another member of the target's defense team the better answer). `plain`: the Mini App —
    no HTML, no emoji."""
    if plain:   # the bare word: no Premium emoji tag in the Mini App
        elem = constants.ELEMENT_WORDS.get(new_element, "") if new_element else ""
    else:
        elem = constants.element_label(new_element) if new_element else ""
    elem_tag = f" ({elem})" if elem else ""
    if plain:
        return (f"مدافع حریف عوض شد: «{new_name}»{elem_tag} هم عضو تیم دفاع همین حریفه و در برابر هیولای تو "
                f"بهتر دفاع می‌کنه، برای همین جای «{old_name}» اومد. ربات همیشه بهترین مدافع تیم دفاع رو می‌فرسته.")
    return (f"🛡 <b>مدافع حریف عوض شد!</b>\n<blockquote>«{new_name}»{elem_tag} هم عضو <b>تیم دفاع</b> همین حریفه "
            f"و در برابر هیولای تو بهتر دفاع می‌کنه، برای همین جای «{old_name}» اومد وسط.\n"
            f"<i>ربات همیشه بهترین مدافع تیم دفاع رو در برابر هیولای مهاجم می‌فرسته.</i></blockquote>")


def toggle(user: User, creature_id: int) -> list[int]:
    """Add the creature to the defense team, or take it out. Returns the new member ids."""
    if not Creature.objects.filter(id=creature_id, owner=user).exists():
        raise GameError("این موجود توی کلکسیون تو نیست.")
    team, _ = DefenseTeam.objects.select_for_update().get_or_create(owner=user)
    members = member_ids(user)
    if creature_id in members:
        members = [m for m in members if m != creature_id]
    else:
        if len(members) >= MAX_SLOTS:
            raise GameError(f"تیم دفاع پره! (حداکثر {MAX_SLOTS} هیولا) — اول یکی رو بردار.")
        members.append(creature_id)
    team.refresh_from_db()
    team.slot1_id, team.slot2_id, team.slot3_id = (members + [None, None, None])[:3]
    team.save(update_fields=["slot1", "slot2", "slot3", "updated_at"])
    return members


def power_of(owner: User, creature: Creature) -> int:
    """The creature's fighting power with gear and the owner's research — what the duel uses."""
    from game import research
    from game.equipment import get_equipped_items

    research.attach_research(owner, creature)
    return creature_power(creature, get_equipped_items(creature))


def _available(owner: User) -> list[Creature]:
    """Defense-team members that are free to fight; falls back to the active creature."""
    from game.workers import creature_status

    ids = member_ids(owner)
    pool = []
    if ids:
        by_id = {c.id: c for c in Creature.objects.filter(owner=owner, id__in=ids)}
        pool = [by_id[i] for i in ids if i in by_id and (by_id[i].is_active or creature_status(owner, by_id[i]) is None)]
    if not pool:
        active = Creature.objects.filter(owner=owner, is_active=True).first()
        pool = [active] if active is not None else []
    return pool


def pick_defender(owner: User, attacker_creature: Creature | None = None, attacker_power: int | None = None,
                  prefer_id: int | None = None) -> tuple[Creature | None, int]:
    """(creature, power) that defends `owner` against `attacker_creature`. `prefer_id` keeps
    the defender already shown on a card, as long as it can still defend."""
    c, p = _pick_defender(owner, attacker_creature, attacker_power, prefer_id)
    if c is not None:
        from game import serums

        p = round(p * serums.peek(owner, "shell"))   # «سرم پوسته»: every card shows what attack() will fight
    return c, p


def _pick_defender(owner: User, attacker_creature: Creature | None, attacker_power: int | None,
                   prefer_id: int | None) -> tuple[Creature | None, int]:
    pool = _available(owner)
    if not pool:
        return None, 0
    if prefer_id is not None:
        for c in pool:
            if c.id == prefer_id:
                return c, power_of(owner, c)
    powered = [(c, power_of(owner, c)) for c in pool]
    if attacker_creature is None or len(powered) == 1:
        return max(powered, key=lambda cp: cp[1])
    if attacker_power is None:
        attacker_power = power_of(attacker_creature.owner, attacker_creature)
    a_elem = attacker_creature.element

    def score(cp):
        c, p = cp
        ea, eb = constants.effective_duel_powers(attacker_power, a_elem, p, c.element)
        return (eb > ea, eb - ea, p)

    return max(powered, key=score)

"""«تیم حمله» — the up-to-three creatures a player fights with in the campaign and the dungeon.

The rules live here so the bot («تیم من», bot/handlers/team.py) and the Mini App (the dungeon's
team sheet) pick a team the same way.
"""

from __future__ import annotations

from django.db import transaction

from bio_lab.models import Creature, Team, User
from game.creature import GameError

MAX_SLOTS = 3


def prune(team: Team, owned_ids: set[int]) -> list[int]:
    """Drop any slot pointing to a creature the player no longer owns (transferred, fused,
    devoured…) — a dangling slot used to count toward the cap, so a 2-creature team read as
    full. Saves the cleanup and returns the valid member ids in slot order."""
    valid = [cid for cid in (team.slot1_id, team.slot2_id, team.slot3_id) if cid in owned_ids]
    cleaned = (valid + [None, None, None])[:3]
    if [team.slot1_id, team.slot2_id, team.slot3_id] != cleaned:
        team.slot1_id, team.slot2_id, team.slot3_id = cleaned
        team.save(update_fields=["slot1", "slot2", "slot3", "updated_at"])
    return valid


def member_ids(user: User) -> list[int]:
    team, _ = Team.objects.get_or_create(owner=user)
    return prune(team, set(Creature.objects.filter(owner=user).values_list("id", flat=True)))


def toggle(user: User, creature_id: int) -> list[int]:
    """Add the creature to the team, or take it out if it is in. Returns the new member ids."""
    if not Creature.objects.filter(id=creature_id, owner=user).exists():
        raise GameError("این موجود توی کلکسیون تو نیست.")
    Team.objects.get_or_create(owner=user)
    with transaction.atomic():   # the row lock keeps two quick taps from both passing the cap
        team = Team.objects.select_for_update().get(owner=user)
        members = prune(team, set(Creature.objects.filter(owner=user).values_list("id", flat=True)))
        if creature_id in members:
            members = [m for m in members if m != creature_id]
        else:
            if len(members) >= MAX_SLOTS:
                raise GameError("تیم پره! (حداکثر ۳ هیولا) — اول یکی رو بردار.")
            members.append(creature_id)
        team.slot1_id, team.slot2_id, team.slot3_id = (members + [None, None, None])[:3]
        team.save(update_fields=["slot1", "slot2", "slot3", "updated_at"])
    return members

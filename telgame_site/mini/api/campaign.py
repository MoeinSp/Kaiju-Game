"""«دانجن» — the 50-stage PvE ladder (game/campaign.py), for the Mini App.

Same gates and fight as bot/handlers/campaign.py: hall level 4, the attack Team (up to 3),
one energy a fight, win by team power ≥ enemy power. Once every stage is cleared the screen
points at «سیاه‌چال» (the roguelite) — the campaign itself has nothing left.
"""

from __future__ import annotations

from bio_lab.models import Team, User
from game import campaign, constants
from game.energy import sync_energy
from game.teambattle import team_power
from telgame_site.mini.api.hunt import hall_gate
from telgame_site.mini.core import (
    asset_img, clean, creature_img, creature_list, endpoint, need_int, species_img,
)

GATE = "campaign"


def _team_creatures(user: User):
    team = Team.objects.filter(owner=user).first()
    return team.creatures() if team else []


def _enemy_row(c) -> dict:
    from game.creature import creature_power

    return {
        "name": clean(c.name), "element": c.element, "rarity": c.rarity, "level": c.level,
        "power": creature_power(c),
        "img": species_img(c.name, c.element, c.rarity, 1),
    }


def _state(user: User) -> dict:
    from game.media import get_feature_image_path

    st = campaign.status(user)
    members = _team_creatures(user)
    enemies = campaign.enemy_team(st["next_stage"]) if st["next_stage"] else []
    reward = st.get("next_reward") or {}
    return {
        "cleared": st["cleared"], "next_stage": st["next_stage"], "max_stage": st["max_stage"],
        "next_is_boss": st["next_is_boss"], "enemy_power": st["enemy_power"],
        "enemies": [_enemy_row(c) for c in enemies],
        "reward": {k: int(reward.get(k, 0) or 0) for k in ("coins", "dna", "diamonds", "speedup")
                   if reward.get(k)},
        "energy": sync_energy(user), "energy_cost": campaign.ENERGY_COST,
        "team_power": team_power(members) if members else 0,
        "has_team": bool(members),
        "art": asset_img(get_feature_image_path("campaign")),
        "dungeon_open": user.campaign_stage >= campaign.MAX_STAGE,
        "unlock_dungeon_at": campaign.MAX_STAGE,
    }


@endpoint()
def panel(request, user):
    hall_gate(user, GATE)
    return _state(user)


@endpoint("POST")
def fight(request, user, data):
    """Fight the next uncleared stage with the attack team (bot: camp_fight)."""
    hall_gate(user, GATE)
    members = _team_creatures(user)
    result = campaign.attempt(user, members)
    out = _state(User.objects.get(id=user.id))
    out["result"] = {
        "won": result["won"], "stage": result["stage"], "is_boss": result["is_boss"],
        "reward": {k: int(result["reward"].get(k, 0) or 0)
                   for k in ("coins", "dna", "diamonds", "speedup") if result["reward"].get(k)},
        "survivors": result["survivors"], "cleared_all": result["cleared_all"],
        "log": [clean(line) for line in result["log"]],
    }
    return out


def _team(user: User) -> dict:
    """Same attack-team picker the dungeon uses — campaign fights with that roster."""
    from game import attack_team

    members = attack_team.member_ids(user)
    rows = creature_list(user)
    for r in rows:
        r["in_team"] = r["id"] in members
    rows.sort(key=lambda r: (not r["in_team"], -r.get("power", 0)))
    return {"members": members, "max": attack_team.MAX_SLOTS, "creatures": rows}


@endpoint()
def team(request, user):
    hall_gate(user, GATE)
    return _team(user)


@endpoint("POST")
def team_toggle(request, user, data):
    from game import attack_team

    hall_gate(user, GATE)
    attack_team.toggle(user, need_int(data, "creature_id", 1))
    return _team(user)


routes = [
    ("", panel),
    ("fight/", fight),
    ("team/", team),
    ("team/toggle/", team_toggle),
]

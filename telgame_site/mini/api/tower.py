"""Mugen Tower («برج موگن»): the current floor and its guardian, the floor's prize, the next
milestone boss, the fight, the fighter swap and the top climbers — a thin adapter over
game/mugen_tower.py in the order bot/handlers/mugen_tower.py uses it.

Nothing to carry between requests: the floor lives on the player's row and the guardian of a
floor is fixed (game.mugen_tower.floor_guardian), so every request re-reads it. A fight can't be
replayed for a second prize: fight_mugen_floor() locks the player's row, spends the energy and
moves `mugen_tower_floor` forward in one transaction — a repeated request simply fights the
NEXT floor (and pays its 3 energy), exactly as a second tap does in the bot.

Gate: main hall 5 (SECTION_HALL_REQ["mugen_tower"]), with the bot's exemption for the player
whose active story quest points at the tower.
"""

from __future__ import annotations

from bio_lab.models import Creature, User
from game import mugen_tower
from telgame_site.mini.api.hunt import battle_log, fighter, hall_gate, plain_name, swap_to
from telgame_site.mini.core import active_creature, asset_img, creature_dict, endpoint, need_int, species_img

GATE = "mugen_tower"
LEADERBOARD_SIZE = 15   # bot: mugen_lb_callback


def _guardian(floor: int) -> dict:
    """The guardian of a floor (fixed per floor) as plain data."""
    name, element, rarity = mugen_tower.get_floor_guardian_info(floor)
    return {
        "floor": floor, "name": name, "element": element, "rarity": rarity,
        "power": mugen_tower.floor_guardian_power(floor),
        "boss": floor % 10 == 0,
        "img": species_img(name, element, rarity, 1, floor),
    }


def _rewards(floor: int) -> dict:
    rew = mugen_tower.floor_rewards(floor)
    return {k: rew[k] for k in ("coins", "dna", "diamonds", "tickets", "xp")}


def _leaderboard(user: User) -> list[dict]:
    rows = (
        User.objects.filter(mugen_tower_floor__gt=1).order_by("-mugen_tower_floor")[:LEADERBOARD_SIZE]
        .values("id", "username", "first_name", "lab_name", "mugen_tower_floor")
    )
    return [
        {"rank": rank, "floor": row["mugen_tower_floor"], "me": row["id"] == user.id,
         "name": plain_name(row["lab_name"] or row["first_name"] or row["username"] or f"Player {row['id']}")}
        for rank, row in enumerate(rows, start=1)
    ]


def _state(user: User) -> dict:
    from game.energy import sync_energy
    from game.media import get_feature_image_path

    status = mugen_tower.get_mugen_status(user)
    floor = status["floor"]
    creature, gear, _power = fighter(user)
    milestone = floor if floor % 10 == 0 else (floor // 10 + 1) * 10
    out = {
        "floor": floor, "cleared": floor - 1,
        "guardian": _guardian(floor), "rewards": _rewards(floor),
        "milestone": None,
        "me": creature_dict(creature, gear),
        "energy": sync_energy(user), "energy_cost": mugen_tower.MUGEN_ENERGY_COST,
        "art": asset_img(get_feature_image_path("mugen_tower")),
        "leaderboard": _leaderboard(user),
    }
    if milestone != floor:
        out["milestone"] = {"guardian": _guardian(milestone), "rewards": _rewards(milestone), "away": milestone - floor}
    return out


@endpoint()
def panel(request, user):
    hall_gate(user, GATE)
    return _state(user)


@endpoint("POST")
def fight(request, user, data):
    """Fight the guardian of the current floor (3 energy). Same call as the bot's «نبرد با نگهبان»."""
    hall_gate(user, GATE)
    creature = active_creature(user)
    level_before = creature.level
    res = mugen_tower.fight_mugen_floor(user, creature)  # locks the rows, spends energy, pays
    level = Creature.objects.values_list("level", flat=True).get(id=creature.id)
    guardian = _guardian(res["floor"])
    out = _state(User.objects.get(id=user.id))
    out["result"] = {
        "won": res["won"], "floor": res["floor"], "next_floor": res["next_floor"],
        "guardian": guardian, "player_power": res["player_power"],
        "rewards": {k: res["rewards"][k] for k in ("coins", "dna", "diamonds", "tickets", "xp")} if res["won"] else None,
        "level": level, "level_before": level_before, "levels": max(0, level - level_before),
        "log": battle_log(res["log_text"]),
    }
    return out


@endpoint("POST")
def swap(request, user, data):
    """«انتخاب کایجو»: another team creature becomes the active one (the picker is hunt/team/)."""
    hall_gate(user, GATE)
    swap_to(user, need_int(data, "creature_id", 1))
    return _state(user)


@endpoint()
def leaderboard(request, user):
    """«برترین فاتحان» on its own (not gated in the bot either)."""
    return {"leaderboard": _leaderboard(user), "floor": user.mugen_tower_floor or 1}


routes = [
    ("", panel),
    ("fight/", fight),
    ("swap/", swap),
    ("leaderboard/", leaderboard),
]

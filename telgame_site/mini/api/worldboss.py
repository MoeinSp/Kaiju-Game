"""World boss («غول سرگردان»): the shared boss of the whole server — a thin adapter over
game/worldboss.py in the order bot/handlers/worldboss.py uses it (`_panel_sync`, `_hit_sync`).

Nothing to carry between requests: the boss, the player's hits and damage are rows
(WorldBoss / WorldBossHit). A hit can't be replayed: game.worldboss.hit() locks the player
and the boss, spends 1 energy and counts the hit (3 per boss) in one transaction — a repeated
request is simply the next hit, or a refusal once the three are used.

The EXACT time of the next boss is a surprise in the bot («زمان دقیقش غافل‌گیریه»), so the
panel only names the window it falls in — the timestamp never leaves the server.
"""

from __future__ import annotations

import html

from django.db.models import Q
from django.utils import timezone

from bio_lab.models import User, WorldBoss, WorldBossHit
from game import constants, worldboss
from telgame_site.mini.api.hunt import missions_out, swap_to
from telgame_site.mini.core import asset_img, clean, creature_dict, endpoint, need_int, species_img

BOSS_RARITY = "mythic"  # only picks the art/frame of the boss card


def _name(value) -> str:
    return html.unescape(clean(value))


def _next_window(moment) -> dict | None:
    """«امروز/فردا بین ساعت X تا Y» — the same reading as the bot's panel."""
    if moment is None:
        return None
    local = timezone.localtime(moment)
    window = next((w for w in worldboss.SPAWN_WINDOWS if w[0] <= local.hour < w[1]), None)
    if window is None:
        return None
    return {"today": local.date() == timezone.localdate(), "from": window[0], "to": window[1]}


def _rules() -> dict:
    from game.arena_chests import ARENA_CHEST_TIERS

    return {
        "hits": worldboss.HITS_PER_PLAYER, "energy_cost": worldboss.ENERGY_COST,
        "minutes": worldboss.DURATION_MINUTES,
        "windows": [{"from": a, "to": b} for a, b in worldboss.SPAWN_WINDOWS],
        "chest_all": _name(ARENA_CHEST_TIERS[worldboss.KILL_CHEST_ALL]["name"]),
        "chest_top": _name(ARENA_CHEST_TIERS[worldboss.KILL_CHEST_TOP]["name"]),
        "top_share_pct": int(round(worldboss.TOP_SHARE * 100)),
        "top3_diamonds": list(worldboss.TOP3_DIAMONDS), "killer_diamonds": worldboss.KILLER_DIAMONDS,
        "advantage_pct": int(round((constants.ELEMENT_ADVANTAGE_POWER_FACTOR - 1) * 100)),
    }


def _outcome(status: str) -> str:
    """How a finished boss ended. A boss whose window ran out stays «active» in the table
    until the 5-minute job settles it — game.worldboss._settle turns exactly that into an
    escape, so that is what it already is for the player."""
    return "dead" if status == WorldBoss.DEAD else "escaped"


def _last_fight(user: User) -> dict | None:
    """The player's own part in the LAST boss — shown while no boss is up, so the end of a
    fight reads as a result. The boss is sized to ESCAPE on a normal day (about half its HP
    is dealt): an escape is the usual ending, and every hit was already paid on the spot."""
    prev = worldboss.last_boss()
    if prev is None:
        return None
    entry = worldboss.my_entry(prev, user)
    damage = entry.damage if entry else 0
    out = {
        "id": prev.id, "outcome": _outcome(prev.status), "element": prev.element,
        # the same reading as the «فرار کرد» DM of game.worldboss._settle
        "hp_left_pct": round(100 * max(0, prev.current_hp) / max(1, prev.max_hp)),
        "max_hp": prev.max_hp, "dealt": prev.max_hp - max(0, prev.current_hp),
        "my_damage": damage, "my_hits": entry.hits if entry else 0, "my_rank": None,
        "settled": bool(prev.settled), "killer": prev.killer_id == user.id,
    }
    if damage > 0:
        # same order as the settlement: damage, then who got there first
        ahead = WorldBossHit.objects.filter(boss=prev).filter(
            Q(damage__gt=damage) | Q(damage=damage, id__lt=entry.id)).count()
        out["my_rank"] = ahead + 1
    return out


def _live(user: User, data: dict | None = None) -> dict:
    """The part that changes while the screen is open (the light poll)."""
    data = data or worldboss.panel_state(user)
    boss = data["boss"]
    out = {"active": boss is not None, "energy": data["energy"], "max_energy": data["max_energy"], "has_creature": data["has_creature"]}
    if boss is None:
        last = data.get("last")
        out["next"] = _next_window(data["next_spawn_at"])
        out["last"] = None if not last else {"name": _name(last["name"]), "outcome": _outcome(last["status"]),
                                             "fighters": last["fighters"]}
        return out
    out.update({
        "boss": {
            "id": boss.id, "name": _name(boss.name), "element": boss.element, "rarity": BOSS_RARITY,
            "hp": boss.current_hp, "max_hp": boss.max_hp, "seconds_left": data["seconds_left"],
            "img": species_img(boss.name, boss.element, BOSS_RARITY, 5, 100),
        },
        "hits_left": data["hits_left"], "my_damage": data["my_damage"], "fighters": data["fighters"],
        "advantage": data["advantage"],
        "top": [{"rank": r["rank"], "name": _name(r["name"]), "damage": r["damage"], "me": r["user_id"] == user.id} for r in data["top"]],
    })
    return out


def _state(user: User) -> dict:
    from bio_lab.repository import get_active_creature
    from game import research
    from game.creature import creature_power
    from game.equipment import get_equipped_items
    from game.media import get_feature_image_path

    data = worldboss.panel_state(user)
    out = _live(user, data)
    out["rules"] = _rules()
    out["art"] = asset_img(get_feature_image_path("worldboss"))
    out["me"] = None
    if data["boss"] is None and out.get("last"):
        out["last"].update(_last_fight(user) or {})
    creature = get_active_creature(user)
    if creature is not None:
        research.attach_research(user, creature)
        gear = get_equipped_items(creature)
        out["me"] = creature_dict(creature, gear)
        if data["boss"] is not None:
            # what ONE hit pays: game.worldboss.hit_reward() of the damage this creature deals
            # before the random swing (its power, with the element bonus when it has it)
            power = creature_power(creature, gear)
            about = round(power * (constants.ELEMENT_ADVANTAGE_POWER_FACTOR if data["advantage"] else 1.0))
            coins, dna = worldboss.hit_reward(about)
            out["per_hit"] = {"damage": about, "coins": coins, "dna": dna,
                              "swing_pct": int(round((worldboss.DAMAGE_SWING[1] - 1) * 100))}
    return out


@endpoint()
def panel(request, user):
    return _state(user)


@endpoint()
def live(request, user):
    """The light poll: HP, time left, fighters, the top list, my hits."""
    return _live(user)


@endpoint("POST")
def hit(request, user, data):
    """One hit (1 energy) — the bot's `_hit_sync`: hit → record_action → check_missions."""
    from game.daily import check_missions, record_action

    res = worldboss.hit(user)
    record_action(user, "worldboss_hit")  # festival coins (and any mission on it)
    completed = check_missions(user, "worldboss_hit")
    out = _state(User.objects.get(id=user.id))
    out["result"] = {
        "damage": res["damage"], "coins": res["coins"], "dna": res["dna"], "advantage": bool(res["advantage"]),
        "killed": bool(res["killed"]), "hits_left": res["hits_left"], "my_damage": res["my_damage"],
        "boss_name": _name(res["boss"].name), "boss_hp": res["boss"].current_hp, "boss_max_hp": res["boss"].max_hp,
        "missions": missions_out(completed),
    }
    return out


@endpoint("POST")
def swap(request, user, data):
    """Bring another team creature (the element decides the +20% damage). The picker is hunt/team/."""
    swap_to(user, need_int(data, "creature_id", 1))
    return _state(User.objects.get(id=user.id))


routes = [
    ("", panel),
    ("live/", live),
    ("hit/", hit),
    ("swap/", swap),
]

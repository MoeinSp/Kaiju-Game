"""«جام آخر هفته» — the weekly knockout cup. A thin adapter over game.tournament, the same
calls the bot's screens make (bot/handlers/tournament.py): panel_state / register /
unregister / creature_choices / set_creature.

Nothing here plays a match or pays a prize: the draw and the three rounds are run by
game.tournament.tick() from the bot's periodic job, exactly as for bot players. The app only
reads the state and changes the player's own entry, so there is no client-held state at all.
"""

import html

from django.db.models import Q

from bio_lab.models import Tournament, TournamentMatch
from bio_lab.repository import creature_name, lab_display
from game import arena_chests as AC
from game import tournament as T
from telgame_site.mini.core import GameError, asset_img, clean, creature_dict, creature_img, endpoint, need_int


def _txt(value) -> str:
    """Game label → plain text (lab_display() is HTML-escaped and may carry a badge tag)."""
    return html.unescape(clean(value))


def _prizes() -> list[dict]:
    """T.PRIZES as data: the chest a place wins (its contents are rolled by
    arena_chests.grant_chest_contents at the winner's league) plus the diamonds on top."""
    from game.media import get_arena_chest_image_path

    out = []
    for place, (tier, diamonds) in sorted(T.PRIZES.items()):
        cfg = AC.ARENA_CHEST_TIERS[tier]
        out.append({"place": place, "title": _txt(T.PLACE_NAMES[place]), "tier": tier, "chest": cfg["name"],
                    "diamonds": diamonds, "img": asset_img(get_arena_chest_image_path(tier, "locked"))})
    return out


def _schedule() -> dict:
    """The week's fixed moments, as seconds from now (negative = already passed)."""
    from django.utils import timezone

    now = timezone.localtime()

    def left(moment) -> int:
        return int((moment - now).total_seconds())

    return {
        "reg_open_in": left(T.reg_open_at(now)), "draw_in": left(T.draw_at(now)),
        "rounds": [{"round": r, "name": T.ROUND_NAMES[r], "in": left(T.round_at(r, now)), "hour": T.ROUND_AT[r][1]}
                   for r in sorted(T.ROUND_AT)],
        "group_size": T.GROUP_SIZE,
    }


def _fielded(entry) -> dict | None:
    """The creature an entry fields right now, with art. T._power is the game's own «who
    plays and how strong» (it falls back to the active creature if the chosen one is gone),
    so the number is the one the round will be decided with."""
    creature, power, element = T._power(entry)
    if creature is None:
        return None
    return {"id": creature.id, "name": _txt(creature_name(creature)), "species": creature.name, "element": element,
            "rarity": creature.rarity, "star": creature.star_level, "level": creature.level, "power": power,
            "img": creature_img(creature)}


def _history(entry) -> list[dict]:
    """The matches this entry has already played (TournamentMatch keeps the fielded power and
    element of both sides at the moment the round ran)."""
    rows = (TournamentMatch.objects.filter(tournament_id=entry.tournament_id, winner__isnull=False)
            .filter(Q(a=entry) | Q(b=entry)).select_related("a__user", "b__user").order_by("round"))
    out = []
    for m in rows:
        mine_is_a = m.a_id == entry.id
        foe = m.b if mine_is_a else m.a
        out.append({
            "round": m.round, "name": T.ROUND_NAMES.get(m.round, ""), "won": m.winner_id == entry.id,
            "bye": foe is None,
            "foe": _txt(lab_display(foe.user)) if foe is not None else None,
            "my_power": m.power_a if mine_is_a else m.power_b, "my_element": (m.element_a if mine_is_a else m.element_b) or None,
            "foe_power": m.power_b if mine_is_a else m.power_a, "foe_element": (m.element_b if mine_is_a else m.element_a) or None,
        })
    return out


def _panel(user) -> dict:
    from game.media import get_feature_image_path

    d = T.panel_state(user)  # the bot's _panel_sync
    out = {
        "status": d["status"], "registration_open": d["registration_open"],
        "draw_in": d["draw_in"], "next_reg_in": d["next_reg_in"],
        "players": d.get("players", 0), "entered": d["entered"],
        "alive": d.get("alive"), "place": d.get("place") or 0,
        "place_title": None, "group": d.get("group") or 0, "round": d.get("round") or 0,
        "my": None, "match": None, "champion": _txt(d["champion"]) if d.get("champion") else None,
        "can_change": False, "history": [],
        "prizes": _prizes(), "schedule": _schedule(),
        "round_names": {str(k): v for k, v in T.ROUND_NAMES.items()},
        "img": asset_img(get_feature_image_path("tournament")),
    }
    if not d["entered"]:
        return out
    entry = T.my_entry(user)
    if entry is None:  # withdrew between the two reads
        out["entered"] = False
        return out
    out["my"] = _fielded(entry)
    # the place only means something once the player is out or the cup is over
    if d["status"] == Tournament.FINISHED or not d.get("alive"):
        out["place_title"] = _txt(T.PLACE_NAMES.get(d.get("place") or 5))
    # the same condition set_creature() enforces
    out["can_change"] = bool(d.get("alive")) and d["status"] != Tournament.FINISHED
    if "match_round" in d:
        match = {"round": d["match_round"], "name": T.ROUND_NAMES[d["match_round"]], "in": d["round_in"], "foe": None}
        if d.get("foe") is not None:
            row = T._next_match_for(entry)
            foe_entry = None if row is None else (row.b if row.a_id == entry.id else row.a)
            fielded = _fielded(foe_entry) if foe_entry is not None else None
            match["foe"] = {"name": _txt(d["foe"]["name"]), "creature": fielded or {
                "id": None, "name": _txt(d["foe"]["creature"]), "species": "", "element": d["foe"]["element"] or None,
                "rarity": None, "star": None, "level": None, "power": d["foe"]["power"], "img": None}}
        out["match"] = match
    if entry.group_no:
        out["history"] = _history(entry)
    return out


@endpoint()
def home(request, user):
    return _panel(user)


@endpoint("POST")
def register(request, user, data):
    T.register(user)
    return _panel(user)


@endpoint("POST")
def unregister(request, user, data):
    T.unregister(user)
    return _panel(user)


@endpoint()
def choices(request, user):
    """«کدوم هیولا برای دور بعد بازی کنه؟» — strongest first (bot: _choices_sync)."""
    entry = T.my_entry(user)
    if entry is None:
        raise GameError("توی جام این هفته نیستی.")
    from game.equipment import equipped_items_map

    # creature_choices() already attached the research and ranked by card power; the cards
    # only need the gear of the 30 it kept (one query — creature_list would redo all of it
    # and add the three «who is busy» queries this picker has no use for)
    creatures = [c for c, _ in T.creature_choices(user)]
    gear = equipped_items_map(creatures)
    rows = [creature_dict(c, gear[c.id]) for c in creatures]
    for row in rows:
        row["active"] = False  # a busy / inactive creature may be fielded (set_creature has no such rule)
    return {"creatures": rows, "current": entry.creature_id}


@endpoint("POST")
def set_creature(request, user, data):
    T.set_creature(user, need_int(data, "creature_id", 1))
    return _panel(user)


routes = [
    ("", home),
    ("register/", register),
    ("unregister/", unregister),
    ("choices/", choices),
    ("creature/", set_creature),
]

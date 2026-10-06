"""Profile, collection, equipment list and the cup leaderboard (read-only)."""

from bio_lab.models import Creature, Equipment, User
from game import constants
from telgame_site.mini.core import creature_dict, creature_list, endpoint, equipment_img, meta

LEADERBOARD_SIZE = 50


@endpoint()
def me(request, user):
    from bio_lab.repository import get_active_creature
    from game import lab
    from game.buildings import main_hall_level
    from game.subscription import get_subscription_info

    league = constants.league_for_cup(user.cup)
    active = get_active_creature(user)
    progress = lab.lab_progress(user)
    sub = get_subscription_info(user)
    return {
        "meta": meta(),
        "id": user.id,
        "lab_name": user.lab_name or "آزمایشگاه",
        "lab_level": lab.lab_level(user),
        "lab_progress": {"into": progress["into"], "span": progress["span"], "ratio": round(progress["ratio"], 4)},
        "league": {"name": league["name"], "key": league["key"]},
        "cup_rank": User.objects.filter(is_banned=False, cup__gt=user.cup).count() + 1,
        "hall_level": main_hall_level(user),
        "creatures": Creature.objects.filter(owner=user).count(),
        "equipment": Equipment.objects.filter(owner=user).count(),
        "tower_floor": max(0, (user.mugen_tower_floor or 1) - 1),
        "streak": user.login_streak,
        "subscription": sub.get("tier_name") if sub.get("is_active") else None,
        "active": creature_dict(active) if active else None,
    }


@endpoint()
def creatures(request, user):
    out = creature_list(user)
    out.sort(key=lambda d: (-int(d["active"]), -d["power"], d["id"]))
    return {"creatures": out}


@endpoint()
def equipment(request, user):
    from bio_lab.repository import creature_name
    from game.equipment import bonus_text, equipment_power
    from telgame_site.mini.core import clean

    order = {r: i for i, r in enumerate(constants.RARITY_ORDER)}
    out = []
    for it in Equipment.objects.filter(owner=user).select_related("equipped_on"):
        out.append({
            "id": it.id, "name": it.name, "slot": it.slot, "rarity": it.rarity, "level": it.level,
            "power": equipment_power(it), "bonus": clean(bonus_text(it)),
            "on": creature_name(it.equipped_on) if it.equipped_on_id else None,
            "on_id": it.equipped_on_id,
            "img": equipment_img(it),
        })
    out.sort(key=lambda d: (-order.get(d["rarity"], 0), -d["level"], d["id"]))
    return {"equipment": out}


@endpoint()
def leaderboard(request, user):
    top = list(
        User.objects.filter(is_banned=False, cup__gt=0).order_by("-cup", "id")
        .values("id", "lab_name", "cup")[:LEADERBOARD_SIZE]
    )
    rows = []
    for rank, row in enumerate(top, start=1):
        league = constants.league_for_cup(row["cup"])
        rows.append({"rank": rank, "name": row["lab_name"] or "آزمایشگاه", "cup": row["cup"],
                     "league": league["key"], "league_name": league["name"], "me": row["id"] == user.id})
    return {
        "rows": rows,
        "me": {"rank": User.objects.filter(is_banned=False, cup__gt=user.cup).count() + 1, "cup": user.cup,
               "name": user.lab_name or "آزمایشگاه"},
    }


routes = [
    ("me/", me),
    ("creatures/", creatures),
    ("equipment/", equipment),
    ("leaderboard/", leaderboard),
]

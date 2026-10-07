"""Profile, collection, equipment list and the cup leaderboard (read-only)."""

from bio_lab.models import Creature, Equipment, User
from game import constants
from telgame_site.mini.core import clean, creature_dict, creature_list, endpoint, equipment_img, hub_badges, meta

LEADERBOARD_SIZE = 50


def _counts(user) -> dict:
    """Cup rank + collection sizes in ONE query (they were three)."""
    from django.db.models import F, Func, IntegerField, OuterRef, Subquery

    def count(queryset):
        return Subquery(queryset.order_by().annotate(n=Func(F("pk"), function="COUNT")).values("n"),
                        output_field=IntegerField())

    row = User.objects.filter(pk=user.pk).values_list(
        count(User.objects.filter(is_banned=False, cup__gt=OuterRef("cup"))),
        count(Creature.objects.filter(owner_id=OuterRef("pk"))),
        count(Equipment.objects.filter(owner_id=OuterRef("pk"))),
    ).first() or (0, 0, 0)
    return {"ahead": row[0] or 0, "creatures": row[1] or 0, "equipment": row[2] or 0}


@endpoint()
def me(request, user):
    from bio_lab.repository import get_active_creature
    from game import lab, research
    from game.buildings import main_hall_level
    from game.subscription import get_subscription_info

    league = constants.league_for_cup(user.cup)
    active = get_active_creature(user)
    if active is not None:
        # same numbers as the collection: without this the card's power depended on whether
        # THIS worker process happened to have the player's research in its memory
        research.attach_research(user, [active])
    progress = lab.lab_progress(user)
    sub = get_subscription_info(user)
    counts = _counts(user)
    out = {
        "id": user.id,
        "lab_name": user.lab_name or "آزمایشگاه",
        "lab_level": lab.lab_level(user),
        "lab_progress": {"into": progress["into"], "span": progress["span"], "ratio": round(progress["ratio"], 4)},
        "league": {"name": league["name"], "key": league["key"]},
        "cup_rank": counts["ahead"] + 1,
        "hall_level": main_hall_level(user),
        "creatures": counts["creatures"],
        "equipment": counts["equipment"],
        "tower_floor": max(0, (user.mugen_tower_floor or 1) - 1),
        "streak": user.login_streak,
        "subscription": sub.get("tier_name") if sub.get("is_active") else None,
        "active": creature_dict(active) if active else None,
        "badges": hub_badges(user),
    }
    # the label tables are written into the shell page; the app asks for `?meta=0`. They stay
    # the default so a copy of the app that was opened before a deploy keeps working.
    if request.GET.get("meta") != "0":
        out["meta"] = meta()
    return out


@endpoint()
def creatures(request, user):
    out = creature_list(user)
    out.sort(key=lambda d: (-int(d["active"]), -d["power"], d["id"]))
    return {"creatures": out}


@endpoint()
def equipment(request, user):
    from bio_lab.repository import creature_name
    from game.equipment import bonus_text, equipment_power

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

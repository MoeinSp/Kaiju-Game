"""Events and progression: the monthly festival, the weekly event/rule, the season pass and
the achievements. Thin adapters over game.festival / game.events / game.battlepass /
game.achievements — the same calls the bot's handlers make."""

from __future__ import annotations

import datetime
import html

from django.db import transaction
from django.utils import timezone

from bio_lab.models import Creature, Equipment
from bio_lab.repository import lock_row
from game import constants
from telgame_site.mini.core import (
    GameError, asset_img, clean, creature_list, endpoint, equipment_img, need_str,
)

# Mirrors bot.handlers.private.SECTION_HALL_REQ for the two gated sections of this module
# (importing that module would load every bot handler into the web process).
_HALL_REQ = {"events": 3, "battlepass": 2}


def _gate(user, action: str) -> None:
    """Same rule as bot.gates.hall_gated: locked below the main-hall level, except for the
    one section the player's current story quest points at."""
    from game import story
    from game.buildings import main_hall_level

    req = _HALL_REQ[action]
    level = main_hall_level(user)
    if level < req and story.active_cta_action(user) != action:
        raise GameError(f"این بخش از سطح {req} «تالار مِهر» باز می‌شه (الان سطح {level}). اول تالار مِهرت رو ارتقا بده.")


def _text(value) -> str:
    """Game text (may be HTML-escaped and carry emoji/badges) → plain text."""
    return html.unescape(clean(value))


def _reward(reward: dict, total: bool = False) -> dict:
    """A game reward dict (coins / dna / diamonds / speedup minutes) for the front end.
    `total` = a sum over several rewards: its speed-up minutes may be several cards."""
    out = {k: int(reward[k]) for k in ("coins", "dna", "diamonds") if reward.get(k)}
    if reward.get("speedup"):
        out["speedup"] = int(reward["speedup"])
        if not total:
            out["speedup_label"] = constants.speedup_plain_label(reward["speedup"])
    return out


def _feature_img(name: str):
    from game.media import get_feature_image_path

    return asset_img(get_feature_image_path(name))


# ── monthly festival ──────────────────────────────────────────────────────────
def _theme(th: dict) -> dict:
    return {"title": th["title"], "element": th["element"], "element_label": constants.ELEMENT_WORDS[th["element"]]}


# The four blocks of the bot's festival shop (bot.handlers.festival._SECTIONS) and the
# picture each item gets in the app. Presentation only: prices and limits come from the game.
_FEST_GROUP = {"golden": "box", "magical": "box", "grand": "grand"}
_FEST_ART = {
    "capsule": "drops/drop_capsule.jpg", "dna": "drops/drop_vein.jpg", "gold": "features/feat_gold_shop.jpg",
    "golden": "chests/golden_locked.jpg", "magical": "chests/magical_locked.jpg", "vip_chest": "chests/magical_open.jpg",
    "grand": "drops/drop_egg.jpg", "vip_mythic": "drops/drop_jackpot.jpg",
}


def _fest_shop(shop: dict) -> list[dict]:
    """game.festival.shop_state(...)["items"] for the front end."""
    from game import festival

    out = []
    for it in shop["items"]:
        key = it["key"]
        limit = festival.SHOP[key][3]
        out.append({
            "key": key, "title": _text(it["title"]).replace("( ", "("), "cost": it["cost"], "left": it["left"],
            "limit": limit, "bought": limit - it["left"], "vip": it["vip"], "locked": it["locked"],
            "group": "vip" if it["vip"] else _FEST_GROUP.get(key, "res"),
            "img": asset_img(_FEST_ART[key]) if key in _FEST_ART else None,
        })
    return out


def _fest_source(row: dict, label: str | None = None) -> dict:
    """One line of game.festival.today_progress: coins per action, how many are left today."""
    return {"key": row.get("key"), "label": label or row["label"], "unit": row["unit"], "cap": row["cap"],
            "done": row["done"], "left": row["left"], "earned": row.get("earned"), "max": row.get("max")}


@endpoint()
def festival_panel(request, user):
    from game import festival

    st = festival.status(user)
    vip = st["vip"]   # subscribers earn more per action — every number is what THIS player gets
    out = {
        "active": st["active"],
        "banner": _feature_img("festival"),
        "theme": _theme(st["theme"]),
        "seconds_left": st["seconds_left"],
        "seconds_until_next": st["seconds_until_next"],
        "start_day": festival.FESTIVAL_START_DAY,
        "end_day": festival.FESTIVAL_END_DAY,
        "days": festival.FESTIVAL_DAYS,
        "prizes": [{"max_rank": r, "diamonds": d} for r, d in festival.RANK_PRIZES],
        "grand_cost": festival.SHOP["grand"][2],
        "vip": vip,
        "vip_bonus_pct": int(festival.VIP_COIN_BONUS * 100),
        "mythic_cost": festival.SHOP["vip_mythic"][2],
        "mission_daily": festival.unit_value(festival.MISSION_DAILY_COINS, vip),
        "mission_weekly": festival.unit_value(festival.MISSION_WEEKLY_COINS, vip),
        "day_max": festival.day_max(vip),
    }
    if not st["active"]:
        # the theme of the festival that starts next (it belongs to the month it starts in)
        from game.battlepass import gregorian_to_jalali

        start = (timezone.localtime() + datetime.timedelta(seconds=st["seconds_until_next"] + 3600)).date()
        jy, jm, _jd = gregorian_to_jalali(start.year, start.month, start.day)
        out["theme"] = _theme(festival.theme(f"{jy}-{jm:02d}"))
        out["earn"] = [{"key": a, "label": festival.EARN_LABELS[a], "unit": festival.unit_value(per, vip), "cap": cap,
                        "max": festival.unit_value(per, vip) * cap}
                       for a, (per, cap) in festival.EARN.items()]
        return out

    # «سکه‌ی امروز»: game.festival.today_progress (already computed by status) — the same
    # numbers as the bot's screen (bot.handlers.festival._how_text)
    today = st["today"]
    out.update({
        "coins": st["coins"],
        "earned": st["earned"],
        "rank": st["rank"],
        "today": {
            "earned": today["earned"], "left": today["left"], "max": today["max"],
            "rows": [_fest_source(r) for r in today["rows"]],
            "daily": _fest_source(today["daily"], "هر مأموریت روزانه"),
            "weekly": _fest_source(today["weekly"], "هر مأموریت هفتگی"),
        },
        "top": [
            {"rank": r["rank"], "name": _text(r["name"]), "earned": r["earned"],
             "prize": festival.rank_prize(r["rank"]), "me": r["user_id"] == user.id}
            for r in festival.leaderboard(st["key"])
        ],
        "shop": _fest_shop(festival.shop_state(user)),
    })
    return out


@endpoint("POST")
def festival_buy(request, user, data):
    """Bot: _buy_sync — festival.buy, then the shop again (sent back so the screen redraws
    without another request)."""
    from game import festival
    from game.equipment import equipment_power

    key = need_str(data, "key", choices=tuple(festival.SHOP))
    last_creature = Creature.objects.filter(owner=user).order_by("-id").values_list("id", flat=True).first() or 0
    last_item = Equipment.objects.filter(owner=user).order_by("-id").values_list("id", flat=True).first() or 0
    res = festival.buy(user, key)
    # what a box / the grand prize dropped, for the reveal
    new_creatures = list(Creature.objects.filter(owner=user, id__gt=last_creature))
    new_items = list(Equipment.objects.filter(owner=user, id__gt=last_item))
    try:
        shop = festival.shop_state(user)
    except GameError:        # the festival ended in the same second: the purchase still stands
        shop = None
    return {
        "key": key,
        "title": _text(res["title"]),
        "cost": res["cost"],
        "got": _text(res["got"]),
        "coins_left": res["coins_left"],
        "coins": shop["coins"] if shop else res["coins_left"],
        "shop": _fest_shop(shop) if shop else None,
        "creatures": creature_list(user, new_creatures) if new_creatures else [],
        "items": [{"id": it.id, "name": it.name, "slot": it.slot, "rarity": it.rarity, "level": it.level,
                   "power": equipment_power(it), "img": equipment_img(it)} for it in new_items],
    }


# ── weekly event / rule ───────────────────────────────────────────────────────
def _event_dict(ev: dict, element: str | None) -> dict:
    rule = ev.get("rule")
    return {
        "key": ev["key"], "title": clean(ev["title"]), "desc": clean(ev["desc"]),
        "xp_mult": ev.get("xp_mult", 1), "pass_mult": ev.get("pass_mult", 1), "reward_mult": ev.get("reward_mult", 1),
        "rule": rule,
        "element": element if rule == "hunt_element" else None,
        "element_label": constants.ELEMENT_WORDS[element] if rule == "hunt_element" and element else None,
    }


def _rotation(count: int = 4) -> list[dict]:
    """The coming weeks. game.events only answers «what is live NOW», so this indexes the
    same EVENTS table by ISO week — and is dropped entirely if it ever disagrees with the
    game about the current week (so a changed rotation can never be shown wrong)."""
    from game import events

    now = events._now_local()

    def at(offset: int):
        week = (now + datetime.timedelta(days=7 * offset)).isocalendar().week
        return (events.EVENTS[week % len(events.EVENTS)],
                constants.ELEMENTS[(week // len(events.EVENTS)) % len(constants.ELEMENTS)])

    ev0, el0 = at(0)
    if ev0["key"] != events.current_event()["key"] or el0 != events.week_element():
        return []
    left = events.seconds_left()
    out = []
    for k in range(1, count + 1):
        ev, el = at(k)
        row = _event_dict(ev, el)
        row["starts_in"] = left + (k - 1) * 7 * 86400
        out.append(row)
    return out


def _week_state(user) -> dict:
    from game import events

    st = events.status(user)
    return {
        "banner": _feature_img("events"),
        "event": _event_dict(st["event"], events.week_element()),
        "rule_line": clean(events.rule_line()),
        "seconds_left": st["seconds_left"],
        "day": st["day"],
        "today_reward": _reward(st["today_reward"]),
        "can_claim": st["can_claim"],
        "days": [{"day": d, "reward": _reward(events.daily_reward(d))} for d in range(1, 8)],
        "upcoming": _rotation(),
    }


@endpoint()
def week_panel(request, user):
    _gate(user, "events")
    return _week_state(user)


@endpoint("POST")
def week_claim(request, user, data):
    from game import events

    _gate(user, "events")
    reward = events.claim_daily(user)
    if reward is None:
        raise GameError("جایزه‌ی امروزو قبلاً گرفتی.")
    # claim_daily locked and updated `user` in place, so the panel is rebuilt from it
    return {"reward": _reward(reward), "state": _week_state(user)}


# ── season pass ───────────────────────────────────────────────────────────────
def _pass_state(user) -> dict:
    from game import battlepass

    st = battlepass.status(user)
    today = timezone.localtime(timezone.now()).date()
    jy, jm, _jd = battlepass.gregorian_to_jalali(today.year, today.month, today.day)
    return {
        "banner": _feature_img("battlepass"),
        "month": battlepass.SHAMSI_MONTH_NAMES[jm] if 1 <= jm <= 12 else "",
        "year": jy,
        "points": st["points"], "tier": st["tier"], "max_tier": st["max_tier"],
        "into": st["into"], "span": st["span"],
        "premium": st["premium"], "premium_cost": st["premium_cost"],
        "free_claimed": st["free_claimed"], "premium_claimed": st["premium_claimed"],
        "has_claimable": st["has_claimable"],
        "seconds_left": battlepass.seconds_until_period_end(),
        "tiers": [{"tier": t, "free": _reward(battlepass.free_reward(t)), "premium": _reward(battlepass.premium_reward(t))}
                  for t in range(1, st["max_tier"] + 1)],
    }


@endpoint()
def pass_panel(request, user):
    _gate(user, "battlepass")
    return _pass_state(user)


@endpoint("POST")
def pass_claim(request, user, data):
    from game import battlepass

    _gate(user, "battlepass")
    result = battlepass.claim(user)
    if not result["tiers"]:
        raise GameError("چیزی برای دریافت نیست.")
    return {"tiers": result["tiers"], "reward": _reward(result["reward"], total=True), "state": _pass_state(user)}


@endpoint("POST")
def pass_premium(request, user, data):
    from game import battlepass

    _gate(user, "battlepass")
    battlepass.buy_premium(user)
    return {"premium": True, "cost": battlepass.PREMIUM_COST_DIAMONDS, "state": _pass_state(user)}


# ── achievements ──────────────────────────────────────────────────────────────
def _achievements_state(user) -> dict:
    from game import achievements

    view = achievements.evaluate(user)
    return {
        "banner": _feature_img("achievements"),
        "done": view["done"], "total": view["total"], "claimable": view["claimable"],
        "items": [
            {"key": i["ach"].key, "title": clean(i["ach"].title), "desc": clean(i["ach"].desc),
             "reward": _reward(i["ach"].reward), "current": i["current"], "target": i["target"],
             "earned": i["earned"], "claimed": i["claimed"]}
            for i in view["items"]
        ],
    }


@endpoint()
def achievements_panel(request, user):
    return _achievements_state(user)


@endpoint("POST")
def achievements_claim(request, user, data):
    from game import achievements

    # the game claims every ready achievement at once (there is no single-claim function);
    # the row lock makes a double tap wait instead of racing the unique claim rows
    with transaction.atomic():
        lock_row(user)
        result = achievements.claim_all(user)
    if not result["claimed"]:
        raise GameError("چیزی برای دریافت نیست.")
    return {
        "claimed": [{"key": a.key, "title": clean(a.title)} for a in result["claimed"]],
        "reward": _reward(result["reward"], total=True),
        "state": _achievements_state(user),
    }


routes = [
    ("festival/", festival_panel),
    ("festival/buy/", festival_buy),
    ("week/", week_panel),
    ("week/claim/", week_claim),
    ("pass/", pass_panel),
    ("pass/claim/", pass_claim),
    ("pass/premium/", pass_premium),
    ("achievements/", achievements_panel),
    ("achievements/claim/", achievements_claim),
]

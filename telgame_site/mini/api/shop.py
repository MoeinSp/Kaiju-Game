"""Economy: monster boxes, the daily shop (+ special items, gem kaiju, shields, gold packs),
the gold/DNA exchange, gear recycling, the energy refill and the read-only VIP page.

Every view is an adapter over the functions the bot's handlers call (bot/handlers/lootbox.py,
shop.py, exchange.py, equip_exchange.py, energy.py, subscription.py)."""

from __future__ import annotations

import json

from game import constants
from telgame_site.mini.core import (
    GameError, asset_img, clean, creature_list, endpoint, equipment_img, need_int, need_str,
    pack, species_img, unpack,
)

BOX_COUNTS = (1, 10)          # the bot's «باز کردن (۱×)» / «باز کردن (۱۰×)» buttons
FREE_BOX_TIERS = ("bronze", "silver")
SHOWN_OFFER_MAX_AGE = 6 * 3600


# ── shared helpers (also used by api/market.py) ───────────────────────────────
def hall_state(user, action: str) -> dict:
    """{req, level, locked} for a section of SECTION_HALL_REQ — the same rule as
    bot.gates.hall_gated (the active story quest's section is let through)."""
    from bot.handlers.private import SECTION_HALL_REQ
    from game import story
    from game.buildings import main_hall_level

    req = SECTION_HALL_REQ.get(action)
    level = main_hall_level(user)
    locked = req is not None and level < req and story.active_cta_action(user) != action
    return {"req": req or 0, "level": level, "locked": locked}


def hall_gate(user, action: str) -> None:
    st = hall_state(user, action)
    if st["locked"]:
        raise GameError(
            f"این بخش از سطح {st['req']} «تالار مِهر» باز می‌شه (الان سطح {st['level']}). "
            "اول تالار مِهرت رو ارتقا بده."
        )


def feature_img(name: str):
    from game.media import get_feature_image_path

    return asset_img(get_feature_image_path(name))


def item_dict(it) -> dict:
    from game.equipment import equipment_power

    return {"id": it.id, "name": it.name, "slot": it.slot, "rarity": it.rarity, "level": it.level,
            "power": equipment_power(it), "img": equipment_img(it)}


def _rolls(user, rolls: list[dict]) -> list[dict]:
    creatures = [r["creature"] for r in rolls if r["kind"] == "creature"]
    by_id = {d["id"]: d for d in creature_list(user, creatures)} if creatures else {}
    out = []
    for r in rolls:
        if r["kind"] == "creature":
            out.append({"kind": "creature", "rarity": r["rarity"], "creature": by_id[r["creature"].id]})
        else:
            out.append({"kind": "equipment", "rarity": r["rarity"], "item": item_dict(r["item"])})
    return out


def _best_index(summary: dict) -> int:
    for i, r in enumerate(summary["rolls"]):
        if r is summary["best"]:
            return i
    return 0


# ── boxes ─────────────────────────────────────────────────────────────────────
def _free_tiers(user) -> list[str]:
    from game.lootbox import can_claim_free_diamond_box

    return [t for t in FREE_BOX_TIERS if can_claim_free_diamond_box(user, t)]


@endpoint()
def badge(request, user):
    return {"free_boxes": len(_free_tiers(user))}


@endpoint()
def boxes(request, user):
    from game.lootbox import BULK_OPEN, BULK_PAY, batch_open_cost

    gold = []
    for tier in constants.BIOCRATE_TIER_ORDER:
        cfg = constants.BIOCRATE_TIERS[tier]
        cc = cfg["creature_chance"]
        weights = cfg["weights"]
        total = sum(weights.values())
        ew = cfg.get("equip_weights", constants.LOOTBOX_RARITY_WEIGHTS)
        et = sum(ew.values())
        gold.append({
            "tier": tier, "label": clean(cfg["label"]), "gold": cfg["gold"], "dna": cfg["dna"],
            # the same numbers, formatted the same way, as the bot's box detail screen
            "equip_total": f"{(1 - cc) * 100:g}", "creature_total": f"{cc * 100:g}",
            "equip": [{"rarity": r, "pct": f"{(1 - cc) * w / et * 100:.2g}"} for r, w in ew.items()],
            "creature": [{"rarity": r, "pct": f"{cc * w / total * 100:.2g}"} for r, w in weights.items()],
            "cost": {str(n): batch_open_cost(user, tier, n) for n in BOX_COUNTS},
        })
    free = _free_tiers(user)
    diamond = []
    for tier, cfg in constants.DIAMOND_BOX_TIERS.items():
        diamond.append({
            "tier": tier, "label": clean(cfg["label"]), "cost": cfg["cost_diamonds"],
            "bulk_cost": BULK_PAY * cfg["cost_diamonds"],
            "daily_free": tier in FREE_BOX_TIERS, "free": tier in free,
            "odds": [{"rarity": r, "pct": f"{w:g}"} for r, w in cfg["weights"].items()],
        })
    return {
        "tickets": user.biocrate_tickets, "gold": gold, "diamond": diamond,
        "bulk_pay": BULK_PAY, "bulk_open": BULK_OPEN, "free_boxes": len(free),
        "img_gold": feature_img("biocrate"), "img_diamond": feature_img("diamond_box"),
    }


@endpoint("POST")
def box_open(request, user, data):
    """Gold «باکس ژنتیکی»: 1 or 10 boxes, tickets first (bot: bc_open)."""
    from game.lootbox import open_biocrate_batch

    tier = need_str(data, "tier", constants.BIOCRATE_TIERS)
    count = need_int(data, "count")
    if count not in BOX_COUNTS:
        raise GameError("تعداد نامعتبره.")
    summary = open_biocrate_batch(user, tier, count)
    return {
        "box": "gold", "tier": tier, "label": clean(constants.BIOCRATE_TIERS[tier]["label"]),
        "rolls": _rolls(user, summary["rolls"]), "best": _best_index(summary),
        "by_rarity": summary["by_rarity"], "opened": summary["opened"],
        "from_tickets": summary["from_tickets"], "paid_boxes": summary["paid_boxes"],
        "gold_spent": summary["gold_spent"], "dna_spent": summary["dna_spent"],
        "tickets_left": summary["tickets_left"],
    }


@endpoint("POST")
def box_diamond(request, user, data):
    """One diamond «باکس هیولا». `free: true` = the daily free one (never charges)."""
    from game.lootbox import open_diamond_box

    tier = need_str(data, "tier", constants.DIAMOND_BOX_TIERS)
    result = open_diamond_box(user, tier, require_free=bool(data.get("free")))
    cfg = constants.DIAMOND_BOX_TIERS[tier]
    return {
        "box": "diamond", "tier": tier, "label": clean(cfg["label"]),
        "rolls": _rolls(user, [result]), "best": 0, "opened": 1,
        "is_free": bool(result.get("is_free")),
        "diamonds_spent": 0 if result.get("is_free") else cfg["cost_diamonds"],
    }


@endpoint("POST")
def box_diamond_bulk(request, user, data):
    from game.lootbox import BULK_PAY, open_diamond_box_bulk

    tier = need_str(data, "tier", constants.DIAMOND_BOX_TIERS)
    summary = open_diamond_box_bulk(user, tier)
    cfg = constants.DIAMOND_BOX_TIERS[tier]
    return {
        "box": "diamond", "tier": tier, "label": clean(cfg["label"]),
        "rolls": _rolls(user, summary["rolls"]), "best": _best_index(summary),
        "by_rarity": summary["by_rarity"], "opened": summary["opened"], "paid": summary["paid"],
        "is_free": False, "diamonds_spent": BULK_PAY * cfg["cost_diamonds"],
    }


# ── daily shop ────────────────────────────────────────────────────────────────
def _offer_group(key: str) -> str:
    # the bot's three sections: food (XP capsules), speedup cards, the rest
    return "food" if key.startswith("cap_") else "speed" if key.startswith("speedup") else "other"


def _summary(contents) -> str:
    from game import itemshop

    try:
        return clean(itemshop.content_summary(contents or []))
    except (KeyError, TypeError):
        return ""


@endpoint()
def store(request, user):
    from bio_lab.models import ShopItemPurchase
    from bot.handlers.shop import is_quantity_offer
    from game import gemkaiju, itemshop, shop
    from game.arena import group_shield_remaining_seconds, shield_remaining_seconds

    offers = []
    for o in shop.offers_with_remaining(user):
        offers.append({
            "key": o["key"], "title": clean(o["title"]), "group": _offer_group(o["key"]),
            "price": o["price"], "base": o["cost"], "currency": o["currency"],
            "featured": bool(o.get("featured")), "limit": int(o.get("limit", 0) or 0),
            "remaining": o["remaining"], "qty": is_quantity_offer(o["key"]),
            "gets": _summary(o.get("contents")),
            # what the bot keeps in user_data: the price this player was SHOWN (shop.buy
            # never charges more than it)
            "token": pack(user, "shop_offer", {"k": o["key"], "p": o["price"], "c": o["currency"]}),
        })

    special = itemshop.list_items(active_only=True)
    bought = dict(ShopItemPurchase.objects.filter(user=user, item__in=special).values_list("item_id", "count")) if special else {}
    items = []
    for it in special:
        try:
            contents = json.loads(it.contents_json)
        except (ValueError, TypeError):
            contents = []
        items.append({
            "id": it.id, "title": clean(it.title), "gets": _summary(contents),
            "price_coins": it.price_coins, "price_diamonds": it.price_diamonds,
            "max_per_user": it.max_per_user or 0, "bought": bought.get(it.id, 0),
        })

    gem = gemkaiju.gem_offer(user)
    if gem:
        gem = {**gem, "img": species_img(gem["name"], gem["element"], gem["rarity"])}

    gate = hall_state(user, "shield_shop")
    shield = {
        "locked": gate["locked"], "req": gate["req"],
        "attack_cost_hours": constants.SHIELD_ATTACK_COST_HOURS,
        "arena": {"left": shield_remaining_seconds(user), "tiers": _shield_tiers(constants.SHIELD_SHOP_TIERS)},
        "group": {"left": group_shield_remaining_seconds(user), "tiers": _shield_tiers(constants.GROUP_SHIELD_SHOP_TIERS)},
    }
    return {
        "offers": offers, "items": items, "gem": gem, "shield": shield,
        "gold_packs": [{"idx": i, "gold": p["gold"], "diamonds": p["diamonds"]} for i, p in enumerate(shop.GOLD_PACKS)],
        "img": feature_img("shop"), "img_shield": feature_img("shield_shop"), "img_gold": feature_img("gold_shop"),
    }


def _shield_tiers(table: dict) -> list[dict]:
    return [{"tier": k, "hours": c["hours"], "diamonds": c["diamonds"], "label": clean(c["label"])} for k, c in table.items()]


@endpoint("POST")
def store_buy(request, user, data):
    from bot.handlers.shop import is_quantity_offer
    from game import shop

    shown = unpack(user, "shop_offer", data.get("token"), max_age=SHOWN_OFFER_MAX_AGE)
    key = str(shown.get("k"))
    count = need_int(data, "count", 1, 100_000) if data.get("count") is not None else 1
    if count > 1 and not is_quantity_offer(key):
        raise GameError("این آفر رو فقط تکی می‌شه خرید.")
    offer = shop.buy(user, key, count=count, shown_price=shown.get("p"), shown_currency=shown.get("c"))
    return {
        "title": clean(offer["title"]), "count": offer["count"], "currency": offer["currency"],
        "total_price": offer["total_price"], "notes": [clean(n) for n in offer["notes"]],
    }


@endpoint("POST")
def store_item(request, user, data):
    from game import itemshop

    result = itemshop.buy(user, need_int(data, "id", 1))
    return {"title": clean(result["title"]), "notes": [clean(n) for n in result["notes"]]}


@endpoint("POST")
def store_gem(request, user, data):
    from game import gemkaiju

    result = gemkaiju.buy_gem_kaiju(user)
    return {"creature": creature_list(user, [result["creature"]])[0], "price": result["price"]}


@endpoint("POST")
def store_shield(request, user, data):
    from game.arena import buy_group_shield, buy_shield

    hall_gate(user, "shield_shop")
    kind = need_str(data, "kind", ("arena", "group"))
    tier = need_str(data, "tier")
    result = (buy_shield if kind == "arena" else buy_group_shield)(user, tier)
    return {"kind": kind, "left": result["remaining"]}


@endpoint("POST")
def store_gold(request, user, data):
    from game import shop

    bought = shop.buy_gold_pack(user, need_int(data, "idx"))
    return {"gold": bought["gold"], "diamonds": bought["diamonds"]}


# ── exchange (gold ↔ DNA) ─────────────────────────────────────────────────────
def _exchange_open(user):
    from game import exchange as ex

    hall_gate(user, "exchange")
    if not ex.ENABLED:
        raise GameError("مبادله فعلاً غیرفعاله.")
    return ex


@endpoint()
def exchange_panel(request, user):
    ex = _exchange_open(user)
    recycle = hall_state(user, "equip_exchange")
    return {
        "coins": user.coins, "dna": user.dna_fragments,
        "rate_buy": ex.GOLD_PER_DNA_BUY, "rate_sell": ex.GOLD_PER_DNA_SELL, "max_dna": ex.MAX_EXCHANGE_DNA,
        # quick picks: DNA amounts when buying DNA, gold amounts (→ whole DNA) when buying gold
        "presets": {
            "buy_dna": [ex.describe("buy_dna", amt) for amt in ex.PRESET_DNA],
            "buy_gold": [ex.describe("buy_gold", ex.dna_for_gold(g)) for g in ex.PRESET_GOLD],
        },
        "recycle": {"locked": recycle["locked"], "req": recycle["req"]},
        "img": feature_img("exchange"),
    }


@endpoint()
def exchange_preview(request, user):
    """?direction=buy_dna|buy_gold&amount=N — N is what the player wants to RECEIVE (DNA for
    buy_dna, gold for buy_gold, like the bot's «عدد دلخواه»)."""
    ex = _exchange_open(user)
    direction = need_str(request.GET, "direction", ex.DIRECTIONS)
    amount = need_int(request.GET, "amount", 1, 10**12)
    deal = ex.describe(direction, ex.dna_for_gold(amount) if direction == "buy_gold" else amount)
    if direction == "buy_dna":
        enough = user.coins >= deal["gold"]
    else:
        enough = user.dna_fragments >= deal["dna"]
    return {**deal, "enough": enough}


@endpoint("POST")
def exchange_do(request, user, data):
    ex = _exchange_open(user)
    result = ex.exchange(user, need_str(data, "direction", ex.DIRECTIONS), need_int(data, "dna", 1))
    return {"direction": result["direction"], "dna": result["dna"], "gold": result["gold"]}


# ── gear recycling (legendary / mythic gear → genetic-box tickets) ────────────
@endpoint()
def recycle_panel(request, user):
    from game.equipment import exchangeable_equipment, ticket_value

    hall_gate(user, "equip_exchange")
    items = []
    for it in exchangeable_equipment(user):
        row = item_dict(it)
        row["tickets"] = ticket_value(it)
        items.append(row)
    return {
        "tickets": user.biocrate_tickets, "items": items,
        "values": [{"rarity": r, "tickets": v} for r, v in constants.EQUIP_TICKET_VALUE.items()],
        "img": feature_img("equip_exchange"),
    }


@endpoint("POST")
def recycle_do(request, user, data):
    from game.equipment import exchange_for_tickets

    hall_gate(user, "equip_exchange")
    raw = data.get("ids")
    if not isinstance(raw, list) or not raw or len(raw) > 2000:
        raise GameError("چیزی انتخاب نکردی.")
    try:
        ids = [int(i) for i in raw]
    except (TypeError, ValueError):
        raise GameError("درخواست ناقصه.")
    result = exchange_for_tickets(user, ids)  # filters by owner / unequipped / ticket-worthy
    return {"tickets": result["tickets"], "count": result["count"], "total": result["total"]}


# ── energy ────────────────────────────────────────────────────────────────────
def _refill_cost() -> int:
    from game import botconfig

    # the price is owner-tunable and read from an in-memory cache the BOT warms at start-up;
    # this is another process, so reload it before showing or charging it
    botconfig.refresh_cache()
    return botconfig.get_energy_refill_cost()


@endpoint()
def energy_panel(request, user):
    from game.energy import get_energy_regen_interval_seconds, get_max_energy, seconds_until_next_point, sync_energy
    from game.subscription import is_subscription_active

    energy, max_energy = sync_energy(user), get_max_energy(user)  # in memory only, not saved
    return {
        "energy": energy, "max_energy": max_energy,
        "next_in": 0 if energy >= max_energy else seconds_until_next_point(user),
        "regen_seconds": round(get_energy_regen_interval_seconds(user)),
        "cost": _refill_cost(), "diamonds": user.diamonds,
        "subscriber": is_subscription_active(user),
        "base_max": constants.MAX_ENERGY, "sub_max": constants.MAX_ENERGY_SUBSCRIBER,
    }


@endpoint("POST")
def energy_refill(request, user, data):
    from game.energy import refill_energy

    _refill_cost()
    result = refill_energy(user)
    return {"cost": result["cost"], "energy": result["energy"]}


# ── VIP (read-only; buying happens in the bot) ────────────────────────────────
@endpoint()
def vip(request, user):
    from game.subscription import SUBSCRIPTION_TIERS, get_subscription_info

    info = get_subscription_info(user)
    return {
        "active": info["is_active"], "tier": info["tier"], "tier_name": info["tier_name"],
        "until": int(info["until"].timestamp()) if info["is_active"] and info["until"] else None,
        "days_left": info["days_left"], "hours_left": info["hours_left"],
        "tiers": [{
            "key": cfg["key"], "name": cfg["name"], "price_toman": cfg["price_toman"], "days": cfg["duration_days"],
            "perks": [clean(p) for p in cfg["perks"]],
        } for cfg in SUBSCRIPTION_TIERS.values()],
        "img": feature_img("subscription"),
    }


routes = [
    ("badge/", badge),
    ("boxes/", boxes),
    ("boxes/open/", box_open),
    ("boxes/diamond/", box_diamond),
    ("boxes/diamond_bulk/", box_diamond_bulk),
    ("store/", store),
    ("store/buy/", store_buy),
    ("store/item/", store_item),
    ("store/gem/", store_gem),
    ("store/shield/", store_shield),
    ("store/gold/", store_gold),
    ("exchange/", exchange_panel),
    ("exchange/preview/", exchange_preview),
    ("exchange/do/", exchange_do),
    ("recycle/", recycle_panel),
    ("recycle/do/", recycle_do),
    ("energy/", energy_panel),
    ("energy/refill/", energy_refill),
    ("vip/", vip),
]

"""«بازار سیاه» — the nightly auctions (adapter over game/blackmarket.py, the way
bot/handlers/blackmarket.py drives it: list → bid preview → confirmed bid)."""

from __future__ import annotations

from django.utils import timezone

from game import blackmarket as bm
from telgame_site.mini.api.shop import feature_img, hall_gate
from telgame_site.mini.core import clean, endpoint, need_int

MAX_BID = 10**12


def _name(raw) -> str:
    # highest_bidder_name is lab_display(): HTML-escaped lab name + the badge's <tg-emoji> tag
    import html

    return html.unescape(clean(raw))


def _lot(a, user, subscriber: bool) -> dict:
    # the next valid bid, exactly as validate_bid_preview / the bot's custom-bid prompt compute it
    if a.highest_bidder_id is None:
        step = bm.get_min_bid_increment(a.min_bid, a.bid_currency)
        next_bid = a.min_bid
    else:
        step = bm.get_min_bid_increment(a.current_bid, a.bid_currency)
        next_bid = a.current_bid + step
    # display only (the game functions enforce it): diamond / creature lots are VIP-only
    is_vip = a.bid_currency == "diamonds" or a.item_type == "creature" or "(VIP)" in a.title
    return {
        "id": a.id, "title": clean(a.title), "type": a.item_type, "currency": a.bid_currency,
        "current_bid": a.current_bid, "min_bid": a.min_bid, "has_bid": a.highest_bidder_id is not None,
        "bidder": _name(a.highest_bidder_name) if a.highest_bidder_id is not None else "",
        "mine": a.highest_bidder_id == user.id,
        "step": step, "next_bid": next_bid,
        "ends_at": int(a.ends_at.timestamp()), "deadline": clean(bm.format_persian_deadline(a.ends_at)),
        "vip": is_vip, "vip_locked": is_vip and not subscriber,
    }


def _night_of(auctions) -> dict | None:
    """Name the night only when the lots on sale really are that night's (same rule as the bot)."""
    titles = {a.title for a in auctions}
    night = next((n for n in bm.PROGRAMME if titles and titles <= {lot[0] for lot in n["lots"]}), None)
    return {"key": night["key"], "title": night["title"]} if night else None


@endpoint()
def panel(request, user):
    from game.subscription import is_subscription_active

    hall_gate(user, "blackmarket")
    auctions = bm.get_active_auctions()  # settles finished lots / opens tonight's, like the bot
    subscriber = is_subscription_active(user)
    soonest = min((a.ends_at for a in auctions), default=None)
    return {
        "night": _night_of(auctions),
        "lots": [_lot(a, user, subscriber) for a in auctions],
        "ends_at": int(soonest.timestamp()) if soonest else None,
        "deadline": clean(bm.format_persian_deadline(soonest)) if soonest else "",
        "subscriber": subscriber,
        "upcoming": [{
            "weekday": bm.PERSIAN_WEEKDAYS.get(day.weekday(), ""), "key": night["key"], "title": night["title"],
            # (title, item_type, payload, bid currency, opening bid) — same VIP rule as _lot
            "lots": [{
                "title": clean(lot[0]), "type": lot[1], "currency": lot[3], "min_bid": lot[4],
                "vip": lot[3] == "diamonds" or lot[1] == "creature" or "(VIP)" in lot[0],
            } for lot in night["lots"]],
        } for day, night in bm.upcoming(4)],
        "img": feature_img("blackmarket"),
    }


@endpoint()
def preview(request, user):
    """?id=<lot>&amount=<bid> — the bot's confirm screen: what would be charged, who is outbid."""
    hall_gate(user, "blackmarket")
    lot_id = need_int(request.GET, "id", 1)
    amount = need_int(request.GET, "amount", 1, MAX_BID)
    p = bm.validate_bid_preview(user, lot_id, amount)
    auction = p["auction"]
    return {
        "id": auction.id, "title": clean(auction.title), "bid_amount": p["bid_amount"], "cost": p["cost"],
        "currency": p["currency"], "is_own_increase": p["is_own_increase"],
        "prev_name": _name(p["prev_bidder_name"]) if auction.highest_bidder_id is not None else "",
        "prev_amount": p["prev_amount"],
        "ends_at": int(auction.ends_at.timestamp()), "deadline": clean(bm.format_persian_deadline(auction.ends_at)),
        "left": max(0, int((auction.ends_at - timezone.now()).total_seconds())),
    }


@endpoint("POST")
def bid(request, user, data):
    from game.subscription import is_subscription_active

    hall_gate(user, "blackmarket")
    lot_id = need_int(data, "id", 1)
    amount = need_int(data, "amount", 1, MAX_BID)
    res = bm.place_bid(user, lot_id, amount)
    # the «someone outbid you» message is sent by the bot process; the web process cannot
    auction = res["auction"]
    return {
        "bid_amount": res["bid_amount"], "currency": res["bid_currency"],
        "lot": _lot(auction, auction.highest_bidder, is_subscription_active(auction.highest_bidder)),
    }


routes = [
    ("", panel),
    ("preview/", preview),
    ("bid/", bid),
]

"""Mini App: redeem paid-offer «اساطیری دلخواه» tickets (game/creature_pick.py)."""

from __future__ import annotations

from game import constants, creature_pick
from telgame_site.mini.core import clean, creature_dict, endpoint, need_int


@endpoint()
def home(request, user):
    left = creature_pick.pending_count(user)
    element = (request.GET.get("element") or "").strip() or None
    if element and element not in constants.ELEMENTS:
        element = None
    page = max(0, int(request.GET.get("page") or 0))
    data = creature_pick.options(element, page) if left else {
        "element": element, "page": 0, "pages": 1, "total": 0, "items": [],
    }
    return {
        "left": left,
        "element": data["element"],
        "page": data["page"],
        "pages": data["pages"],
        "items": [
            {
                "idx": it["idx"],
                "name": clean(it["name"]),
                "element": it["element"],
                "element_label": clean(constants.ELEMENT_WORDS[it["element"]]),
            }
            for it in data["items"]
        ],
        "elements": [
            {"key": el, "label": clean(constants.ELEMENT_WORDS[el])}
            for el in constants.ELEMENTS
        ],
    }


@endpoint("POST")
def redeem(request, user, data):
    res = creature_pick.redeem(user, need_int(data, "idx", min=0))
    return {
        "left": res["left"],
        "creature": creature_dict(res["creature"]),
        "name": clean(res["name"]),
        "element": res["element"],
        "rarity": res["rarity"],
    }


routes = [
    ("", home),
    ("redeem/", redeem),
]

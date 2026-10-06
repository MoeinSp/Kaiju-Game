"""Mini App — phase 1: read-only screens (profile, collection, equipment, leaderboard).

The Mini App is a second FRONT END over the same game: every number here comes from the
same functions the bot's screens use (creature_power, effective_stats, league_for_cup, …),
so the two can never disagree. Nothing in this module changes game state.

Auth: every /app/api/ call carries Telegram's signed `initData` in the `X-Tg-Init-Data`
header; `verify_init_data` proves which player is asking. There is no session and no
cookie. With DJANGO_DEBUG on (a developer's machine only) `?dev_user=<id>` stands in for
it, so the pages can be opened in a plain browser.

Images: creature/equipment art is 200 KB–1 MB per file, far too heavy for a grid of
hundreds, so `/app/img/…` serves ~320 px JPEG thumbnails made once and cached on disk.
An <img> can't send a header, so each image URL carries a short HMAC of its id instead.
"""

from __future__ import annotations

import hashlib
import hmac
from functools import wraps
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.http import require_GET

from bio_lab.models import Creature, Equipment, User
from config import BOT_TOKEN
from game import constants
from telgame_site.miniapp_views import _DIR, verify_init_data

THUMB_SIZE = 320
LEADERBOARD_SIZE = 50
_THUMB_DIR_NAME = "thumbs"


# ── auth ──────────────────────────────────────────────────────────────────────
def _player(request) -> User | None:
    data = verify_init_data(request.headers.get("X-Tg-Init-Data", ""))
    user_id = None
    if data and isinstance(data.get("user"), dict):
        user_id = data["user"].get("id")
    elif settings.DEBUG and request.GET.get("dev_user", "").isdigit():
        user_id = int(request.GET["dev_user"])
    if not user_id:
        return None
    return User.objects.filter(id=user_id, is_banned=False).first()


def api(view):
    """JSON endpoint for an authenticated player: 401 without valid initData, 404 when the
    player hasn't started the game yet (no row / no creature)."""
    @require_GET
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        user = _player(request)
        if user is None:
            return JsonResponse({"error": "unauthorized"}, status=401)
        if not Creature.objects.filter(owner=user).exists():
            return JsonResponse({"error": "not_started"}, status=404)
        resp = JsonResponse(view(request, user, *args, **kwargs), json_dumps_params={"ensure_ascii": False})
        resp["Cache-Control"] = "no-store"
        return resp
    return wrapped


def _img_key(kind: str, obj_id: int) -> str:
    return hmac.new(BOT_TOKEN.encode(), f"miniapp-img:{kind}:{int(obj_id)}".encode(), hashlib.sha256).hexdigest()[:12]


def _img_url(kind: str, obj_id: int) -> str:
    return f"/app/img/{kind}/{obj_id}.jpg?k={_img_key(kind, obj_id)}"


def _emoji(label: str) -> str:
    """«🔥 آتش» → «🔥» (the tables keep emoji and word together)."""
    return label.split(" ", 1)[0]


# ── serialisers ───────────────────────────────────────────────────────────────
def _creature_dict(c: Creature, gear: list, busy: set[int]) -> dict:
    from bio_lab.repository import creature_name
    from game.creature import creature_power, effective_stats

    stats = effective_stats(c, gear)
    return {
        "id": c.id,
        "name": creature_name(c),
        "species": c.name,
        "element": c.element,
        "rarity": c.rarity,
        "star": c.star_level,
        "level": c.level,
        "power": creature_power(c, gear),
        "hp": round(stats["hp"]), "atk": round(stats["atk"]), "def": round(stats["def"]), "spd": round(stats["spd"]),
        "active": c.is_active,
        "busy": c.id in busy,
        "gear": [{"name": g.name, "slot": g.slot, "rarity": g.rarity, "level": g.level} for g in gear],
        "img": _img_url("c", c.id),
    }


def _meta() -> dict:
    """The label tables the front end needs — sent once, so wording lives in ONE place."""
    return {
        "elements": {k: {"label": constants.ELEMENT_WORDS[k], "emoji": _emoji(v)} for k, v in constants.ELEMENT_LABELS.items()},
        "rarities": {k: {"label": v.split(" ", 1)[1], "emoji": _emoji(v)} for k, v in constants.RARITY_LABELS.items()},
        "rarity_order": list(constants.RARITY_ORDER),
        "slots": {k: {"label": v.split(" ", 1)[1], "emoji": _emoji(v)} for k, v in constants.EQUIPMENT_SLOT_LABELS.items()},
    }


# ── endpoints ─────────────────────────────────────────────────────────────────
@api
def me(request, user: User) -> dict:
    from bio_lab.repository import get_active_creature
    from game import lab
    from game.buildings import main_hall_level
    from game.energy import get_max_energy, sync_energy
    from game.equipment import get_equipped_items
    from game.subscription import get_subscription_info

    league = constants.league_for_cup(user.cup)
    active = get_active_creature(user)
    progress = lab.lab_progress(user)
    sub = get_subscription_info(user)
    return {
        "meta": _meta(),
        "lab_name": user.lab_name or "آزمایشگاه",
        "lab_level": lab.lab_level(user),
        "lab_progress": {"into": progress["into"], "span": progress["span"], "ratio": round(progress["ratio"], 4)},
        "coins": user.coins, "dna": user.dna_fragments, "diamonds": user.diamonds,
        "energy": sync_energy(user), "max_energy": get_max_energy(user),
        "cup": user.cup,
        "league": {"name": league["name"], "emoji": league["emoji"]},
        "cup_rank": User.objects.filter(is_banned=False, cup__gt=user.cup).count() + 1,
        "hall_level": main_hall_level(user),
        "creatures": Creature.objects.filter(owner=user).count(),
        "equipment": Equipment.objects.filter(owner=user).count(),
        "tower_floor": max(0, (user.mugen_tower_floor or 1) - 1),
        "streak": user.login_streak,
        "subscription": sub.get("tier_name") if sub.get("is_active") else None,
        "active": _creature_dict(active, get_equipped_items(active), set()) if active else None,
    }


@api
def creatures(request, user: User) -> dict:
    from game import research
    from game.equipment import equipped_items_map
    from game.workers import busy_creature_ids

    rows = list(Creature.objects.filter(owner=user))
    research.attach_research(user, rows)       # the same research buffs the bot's cards show
    gear = equipped_items_map(rows)            # ONE query for everyone's gear
    busy = busy_creature_ids(user)
    out = [_creature_dict(c, gear[c.id], busy) for c in rows]
    out.sort(key=lambda d: (-int(d["active"]), -d["power"], d["id"]))
    return {"creatures": out}


@api
def equipment(request, user: User) -> dict:
    from bio_lab.repository import creature_name
    from game.equipment import bonus_text, equipment_power

    items = Equipment.objects.filter(owner=user).select_related("equipped_on")
    order = {r: i for i, r in enumerate(constants.RARITY_ORDER)}
    out = []
    for it in items:
        out.append({
            "id": it.id, "name": it.name, "slot": it.slot, "rarity": it.rarity, "level": it.level,
            "power": equipment_power(it), "bonus": bonus_text(it),
            "on": creature_name(it.equipped_on) if it.equipped_on_id else None,
            "img": _img_url("e", it.id),
        })
    out.sort(key=lambda d: (-order.get(d["rarity"], 0), -d["level"], d["id"]))
    return {"equipment": out}


@api
def leaderboard(request, user: User) -> dict:
    top = list(
        User.objects.filter(is_banned=False, cup__gt=0).order_by("-cup", "id")
        .values("id", "lab_name", "cup")[:LEADERBOARD_SIZE]
    )
    rows = []
    for rank, row in enumerate(top, start=1):
        league = constants.league_for_cup(row["cup"])
        rows.append({"rank": rank, "name": row["lab_name"] or "آزمایشگاه", "cup": row["cup"],
                     "league": league["emoji"], "me": row["id"] == user.id})
    return {
        "rows": rows,
        "me": {"rank": User.objects.filter(is_banned=False, cup__gt=user.cup).count() + 1, "cup": user.cup,
               "name": user.lab_name or "آزمایشگاه"},
    }


# ── images ────────────────────────────────────────────────────────────────────
def _thumb(source: str) -> Path | None:
    """A cached ~320 px JPEG of `source` (made on first request)."""
    from PIL import Image

    from game.media import CACHE_DIR

    src = Path(source)
    if not src.exists():
        return None
    out_dir = CACHE_DIR / _THUMB_DIR_NAME
    out_dir.mkdir(parents=True, exist_ok=True)
    stat = src.stat()
    name = hashlib.md5(f"{src}:{stat.st_mtime_ns}:{stat.st_size}:{THUMB_SIZE}".encode()).hexdigest()[:20] + ".jpg"
    out = out_dir / name
    if not out.exists():
        with Image.open(src) as im:
            im = im.convert("RGB")
            im.thumbnail((THUMB_SIZE, THUMB_SIZE))
            im.save(out, "JPEG", quality=78, optimize=True)
    return out


@require_GET
def image(request, kind: str, obj_id: int):
    from game.media import get_creature_image_path, get_equipment_image_path

    if kind not in ("c", "e") or not hmac.compare_digest(request.GET.get("k", ""), _img_key(kind, obj_id)):
        raise Http404
    obj = (Creature if kind == "c" else Equipment).objects.filter(id=obj_id).first()
    if obj is None:
        raise Http404
    source = get_creature_image_path(obj) if kind == "c" else get_equipment_image_path(obj)
    thumb = _thumb(source) if source else None
    if thumb is None:
        raise Http404
    resp = FileResponse(open(thumb, "rb"), content_type="image/jpeg")
    # the URL is stable per object but the art changes when it levels/stars up → short cache
    resp["Cache-Control"] = "private, max-age=3600"
    return resp


@xframe_options_exempt  # Telegram Web / Desktop show Mini Apps inside an iframe
@require_GET
def app_page(request):
    resp = HttpResponse((_DIR / "app.html").read_text(encoding="utf-8"), content_type="text/html; charset=utf-8")
    resp["Cache-Control"] = "no-store"
    return resp

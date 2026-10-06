"""Mini App — the shared back-end layer every feature module builds on.

The Mini App is a second FRONT END over the same game. A feature module in
`telgame_site/mini/api/` is a thin adapter: it calls the very functions the bot's handlers
call (game.hunt, game.arena, game.buildings, …) and turns their results into JSON. It never
re-implements a rule, a price or a reward — if the bot and the app could disagree about a
number, the module is wrong.

HOW TO WRITE A FEATURE MODULE  (one file: telgame_site/mini/api/<name>.py)

    from telgame_site.mini.core import endpoint, GameError

    @endpoint()                       # GET  — read-only
    def panel(request, user):
        return {"items": [...]}

    @endpoint("POST")                 # POST — changes game state
    def do_thing(request, user, data):          # `data` = the parsed JSON body (a dict)
        thing_id = need_int(data, "id")
        result = game.something.do(user, thing_id)   # raises GameError on a broken rule
        return {"result": ...}

    routes = [                         # mounted under /app/api/<name>/…
        ("", panel),                   # GET  /app/api/<name>/
        ("do/", do_thing),             # POST /app/api/<name>/do/
    ]

What `endpoint` does for you:
  * authenticates the player from Telegram's signed initData (header X-Tg-Init-Data); 401
    otherwise. `user` is a fresh bio_lab.models.User. Players without a creature get 404.
  * parses the JSON body for POST and passes it as `data`.
  * turns `GameError` into HTTP 400 `{"error": "<plain Persian text>", "code": ...}` with
    code "energy" for EnergyError and "gold" for InsufficientGoldError; everything else is a
    logged 500. So: just let game functions raise.
  * adds `"res": {...}` (the player's CURRENT gold / DNA / diamonds / energy / cup, re-read
    from the database) to every successful response, so the app's top bar is always right
    without a second request.

Rules for modules:
  * A view is plain synchronous Django code — call the ORM directly (unlike the bot, there
    is no event loop here). State-changing game functions already lock the rows they touch;
    call them the same way the bot's `*_sync` helpers do (copy their sequence: record_action
    → check_missions etc. — those side effects are part of the action).
  * Never trust an id from the client: always filter by `owner=user` / `user=user`.
  * Text from the game (GameError messages, labels with emoji or <tg-emoji>/<b> tags) must
    go through `clean()` before it is sent — the app renders plain text and draws its own
    icons; it shows NO emoji.
  * Images: use `creature_img(c)` / `equipment_img(e)` / `asset_img("features/x.jpg")`.
  * Serialise creatures with `creature_dict` so every screen shows the same fields.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import time
from functools import wraps
from pathlib import Path

from django.conf import settings
from django.http import JsonResponse

from bio_lab.models import Creature, Equipment, User
from config import BOT_TOKEN
from game import constants
from game.creature import GameError, InsufficientGoldError  # noqa: F401 — re-exported for modules
from game.energy import EnergyError
from telgame_site.miniapp_views import verify_init_data

logger = logging.getLogger(__name__)

MINI_DIR = Path(__file__).resolve().parent
STATIC_DIR = MINI_DIR / "static"

_TAG = re.compile(r"<[^>]+>")
# emoji + variation selectors + ZWJ: the app shows none (it has its own icon set)
_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U00002B00-\U00002BFF\U0001F1E6-\U0001F1FF"
    "←-⇿⌀-⏿■-◿⤀-⥿〰〽㊗㊙️‍⃣]+"
)


def clean(text) -> str:
    """Game text → plain text for the app: HTML/<tg-emoji> tags and emoji removed, spaces
    tidied. Use it on GameError messages and on any label that comes from the game."""
    out = _EMOJI.sub("", _TAG.sub("", str(text or "")))
    out = re.sub(r"[ \t]+", " ", out)
    out = re.sub(r" *\n *", "\n", out).strip(" \n·-—:|")
    return out


# ── request helpers ───────────────────────────────────────────────────────────
def need_int(data: dict, key: str, minimum: int | None = None, maximum: int | None = None) -> int:
    """A required integer from the POST body; raises GameError (→ 400) when missing/invalid."""
    try:
        value = int(data.get(key))
    except (TypeError, ValueError):
        raise GameError("درخواست ناقصه.")
    if (minimum is not None and value < minimum) or (maximum is not None and value > maximum):
        raise GameError("مقدار نامعتبره.")
    return value


def need_str(data: dict, key: str, choices=None, max_len: int = 64) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value or len(value) > max_len or (choices is not None and value not in choices):
        raise GameError("درخواست ناقصه.")
    return value


def own_creature(user: User, creature_id: int) -> Creature:
    creature = Creature.objects.filter(id=creature_id, owner=user).first()
    if creature is None:
        raise GameError("این هیولا توی کلکسیون تو نیست.")
    return creature


def own_equipment(user: User, equip_id: int) -> Equipment:
    item = Equipment.objects.filter(id=equip_id, owner=user).first()
    if item is None:
        raise GameError("این تجهیزات توی کوله‌ی تو نیست.")
    return item


# ── auth ──────────────────────────────────────────────────────────────────────
def player(request) -> User | None:
    data = verify_init_data(request.headers.get("X-Tg-Init-Data", ""))
    user_id = None
    if data and isinstance(data.get("user"), dict):
        user_id = data["user"].get("id")
    elif settings.DEBUG and request.GET.get("dev_user", "").isdigit():
        user_id = int(request.GET["dev_user"])  # developer machine only
    if not user_id:
        return None
    return User.objects.filter(id=user_id, is_banned=False).first()


def resources(user: User) -> dict:
    """The top bar. Always re-read from the DB: the view's `user` may be stale after an action."""
    from game.energy import get_max_energy, seconds_until_next_point, sync_energy

    fresh = User.objects.get(id=user.id)
    energy, max_energy = sync_energy(fresh), get_max_energy(fresh)
    return {
        "coins": fresh.coins, "dna": fresh.dna_fragments, "diamonds": fresh.diamonds,
        "energy": energy, "max_energy": max_energy,
        "energy_in": 0 if energy >= max_energy else seconds_until_next_point(fresh),
        "cup": fresh.cup,
    }


_last_flush = 0.0


def _track(user: User, name: str) -> None:
    """Mini App use counts as activity in the admin stats (game/metrics.py). The web process
    has no job queue, so it flushes its own in-memory counters about once a minute."""
    global _last_flush
    try:
        from game import metrics

        metrics.mark(user.id, True)
        metrics.click(f"app:{name}")
        if time.time() - _last_flush > 60:
            _last_flush = time.time()
            metrics.flush()
    except Exception:  # noqa: BLE001 — statistics must never break a request
        logger.exception("mini app metrics failed")


def endpoint(method: str = "GET"):
    """Decorator for a feature view — see the module docstring."""
    method = method.upper()

    def deco(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if request.method != method:
                return JsonResponse({"error": "method"}, status=405)
            user = player(request)
            if user is None:
                return JsonResponse({"error": "unauthorized"}, status=401)
            if not Creature.objects.filter(owner=user).exists():
                return JsonResponse({"error": "not_started"}, status=404)
            data = {}
            if method == "POST":
                try:
                    data = json.loads(request.body.decode("utf-8") or "{}")
                except (ValueError, UnicodeDecodeError):
                    return JsonResponse({"error": "bad body"}, status=400)
                if not isinstance(data, dict):
                    return JsonResponse({"error": "bad body"}, status=400)
            _track(user, f"{view.__module__.rsplit('.', 1)[-1]}.{view.__name__}")
            try:
                payload = view(request, user, data, *args, **kwargs) if method == "POST" else view(request, user, *args, **kwargs)
            except GameError as exc:
                code = "energy" if isinstance(exc, EnergyError) else "gold" if isinstance(exc, InsufficientGoldError) else "rule"
                return _json({"error": clean(exc) or "این کار الان ممکن نیست.", "code": code, "res": _safe_res(user)}, 400)
            except Exception:  # noqa: BLE001
                logger.exception("mini app endpoint %s failed", view.__name__)
                return _json({"error": "یه مشکلی پیش اومد. دوباره امتحان کن.", "code": "server"}, 500)
            payload = dict(payload or {})
            payload.setdefault("res", _safe_res(user))
            return _json(payload)

        wrapped.csrf_exempt = True  # authenticated by signed initData, not by a cookie
        return wrapped
    return deco


def _safe_res(user: User) -> dict | None:
    try:
        return resources(user)
    except Exception:  # noqa: BLE001
        return None


def _json(payload: dict, status: int = 200) -> JsonResponse:
    resp = JsonResponse(payload, status=status, json_dumps_params={"ensure_ascii": False})
    resp["Cache-Control"] = "no-store"
    return resp


# ── signed client-held state ──────────────────────────────────────────────────
def pack(user: User, kind: str, data: dict) -> str:
    """Give the client a piece of server state to hold (a scouted hunt target, the arena
    opponent being looked at, …) as an opaque token it cannot alter. The web server runs
    several worker processes, so NOTHING may be kept in a module-level dict between two
    requests — the bot's `context.user_data` has no equivalent here; this is it.

        token = pack(user, "arena_opp", {"id": 5, "power": 1200, "loot": 300})
        …next request…
        opp = unpack(user, "arena_opp", data["token"], max_age=600)   # GameError if forged/expired
    """
    import base64

    body = base64.urlsafe_b64encode(json.dumps({"d": data, "t": int(time.time())}, separators=(",", ":")).encode()).decode()
    mac = hmac.new(BOT_TOKEN.encode(), f"miniapp-state:{user.id}:{kind}:{body}".encode(), hashlib.sha256).hexdigest()[:24]
    return f"{body}.{mac}"


def unpack(user: User, kind: str, token, max_age: int = 900) -> dict:
    import base64

    try:
        body, mac = str(token).rsplit(".", 1)
        expected = hmac.new(BOT_TOKEN.encode(), f"miniapp-state:{user.id}:{kind}:{body}".encode(), hashlib.sha256).hexdigest()[:24]
        if not hmac.compare_digest(mac, expected):
            raise ValueError
        payload = json.loads(base64.urlsafe_b64decode(body.encode()))
        if max_age and time.time() - int(payload["t"]) > max_age:
            raise GameError("این صفحه قدیمی شده؛ دوباره بازش کن.")
        return payload["d"]
    except GameError:
        raise
    except Exception:  # noqa: BLE001
        raise GameError("درخواست نامعتبره؛ صفحه رو دوباره باز کن.")


# ── images ────────────────────────────────────────────────────────────────────
def _sig(*parts) -> str:
    return hmac.new(BOT_TOKEN.encode(), ("miniapp-img:" + ":".join(str(p) for p in parts)).encode(), hashlib.sha256).hexdigest()[:12]


def creature_img(creature) -> str:
    return f"/app/img/c/{creature.id}.jpg?k={_sig('c', creature.id)}"


def equipment_img(item) -> str:
    return f"/app/img/e/{item.id}.jpg?k={_sig('e', item.id)}"


def asset_img(rel_path: str | None) -> str | None:
    """URL of a thumbnail of a file under assets/images/ (e.g. "features/feat_arena.jpg",
    or an absolute path returned by game.media.get_*_image_path). None if it doesn't exist."""
    from game.media import ASSETS_DIR

    if not rel_path:
        return None
    path = Path(rel_path)
    if path.is_absolute():
        try:
            rel = path.resolve().relative_to(ASSETS_DIR.resolve()).as_posix()
        except ValueError:
            return None
    else:
        rel = path.as_posix()
        path = ASSETS_DIR / rel
    if ".." in rel or not path.exists():
        return None
    return f"/app/img/a/{rel}?k={_sig('a', rel)}"


def species_img(name: str, element: str = "fire", rarity: str = "common", star: int = 1, level: int = 1) -> str | None:
    """Art for a creature that doesn't exist as a row (a wild hunt target, a bot's creature,
    an egg's parent): same picture the bot would show for that species/rarity/star."""
    from types import SimpleNamespace

    from game.media import get_creature_image_path

    return asset_img(get_creature_image_path(
        SimpleNamespace(name=name, element=element, rarity=rarity, star_level=star, level=level, species=name)
    ))


# ── serialisers ───────────────────────────────────────────────────────────────
def gear_dict(g: Equipment) -> dict:
    return {"id": g.id, "name": g.name, "slot": g.slot, "rarity": g.rarity, "level": g.level}


def creature_dict(c: Creature, gear: list | None = None, busy: set[int] | None = None) -> dict:
    """THE creature shape of the app. `gear` = its equipped items (pass it when you already
    have it — e.g. from game.equipment.equipped_items_map — to avoid a query per creature)."""
    from bio_lab.repository import creature_name
    from game.creature import creature_power, effective_stats
    from game.equipment import get_equipped_items

    if gear is None:
        gear = get_equipped_items(c)
    stats = effective_stats(c, gear)
    return {
        "id": c.id, "name": creature_name(c), "species": c.name,
        "element": c.element, "rarity": c.rarity, "star": c.star_level, "level": c.level,
        "power": creature_power(c, gear),
        "hp": round(stats["hp"]), "atk": round(stats["atk"]), "def": round(stats["def"]), "spd": round(stats["spd"]),
        "active": c.is_active, "busy": bool(busy and c.id in busy),
        "gear": [gear_dict(g) for g in gear],
        "img": creature_img(c),
    }


def creature_list(user: User, creatures=None) -> list[dict]:
    """All (or the given) creatures of a player as dicts — research buffs attached and gear
    fetched in ONE query, exactly like the bot's collection screens."""
    from game import research
    from game.equipment import equipped_items_map
    from game.workers import busy_creature_ids

    rows = list(Creature.objects.filter(owner=user)) if creatures is None else list(creatures)
    research.attach_research(user, rows)
    gear = equipped_items_map(rows)
    busy = busy_creature_ids(user)
    return [creature_dict(c, gear[c.id], busy) for c in rows]


def active_creature(user: User) -> Creature:
    from bio_lab.repository import get_active_creature

    creature = get_active_creature(user)
    if creature is None:
        raise GameError("اول یه هیولای فعال انتخاب کن.")
    return creature


def meta() -> dict:
    """Label tables for the front end (sent once with /app/api/profile/me/)."""
    def word(label: str) -> str:
        return label.split(" ", 1)[1] if " " in label else label

    return {
        "elements": {k: {"label": constants.ELEMENT_WORDS[k]} for k in constants.ELEMENT_LABELS},
        "rarities": {k: {"label": word(v)} for k, v in constants.RARITY_LABELS.items()},
        "rarity_order": list(constants.RARITY_ORDER),
        "slots": {k: {"label": word(v)} for k, v in constants.EQUIPMENT_SLOT_LABELS.items()},
        "strong_against": constants.ELEMENT_STRONG_AGAINST,
    }

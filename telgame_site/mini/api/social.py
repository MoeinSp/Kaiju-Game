"""Alliance (read-mostly), the alliance rankings, the guide and the settings (notification
switches, lab rename). Thin adapters over game.alliance / game.raid / game.guide and the
profile helpers of the bot."""

from __future__ import annotations

import html
import re

from django.db import transaction

from bio_lab.models import Alliance, User
from bio_lab.repository import display_name, lab_name_taken, lock_row
from game import constants
from telgame_site.mini.core import GameError, asset_img, clean, endpoint, need_int, need_str

# Mirrors bot.handlers.private.SECTION_HALL_REQ for the boards of this module (importing
# that module would load every bot handler into the web process).
_HALL_REQ = {"rank": 4, "alliance_league": 5, "raid_rank": 5}

# Mirrors bot.handlers.private.LAB_NAME_MAX_LEN / _INVISIBLE_CHARS / _clean_lab_name.
LAB_NAME_MAX_LEN = 32
_INVISIBLE_CHARS = dict.fromkeys(
    [0x200B, 0x200D, 0x200E, 0x200F, 0x2060, 0x2061, 0x2062, 0x2063, 0x2064, 0xFEFF, 0x00AD, 0x061C,
     0x202A, 0x202B, 0x202C, 0x202D, 0x202E, 0x2066, 0x2067, 0x2068, 0x2069, 0x3164, 0x115F, 0x1160]
)


def _text(value) -> str:
    """Game text (HTML-escaped names with a VIP badge, labels with emoji) → plain text."""
    return html.unescape(clean(value))


def _feature_img(name: str):
    from game.media import get_feature_image_path

    return asset_img(get_feature_image_path(name))


def _unlocked(user, action: str) -> tuple[bool, int]:
    """(open?, required hall level) — same rule as bot.gates.hall_gated."""
    from game import story
    from game.buildings import main_hall_level

    req = _HALL_REQ[action]
    return main_hall_level(user) >= req or story.active_cta_action(user) == action, req


# ── alliance ──────────────────────────────────────────────────────────────────
def _alliance_dict(user, al: Alliance) -> dict:
    from game import alliance as alliance_mod
    from game.raid import _joined_alliance_today, alliance_raid_members, get_active_boss

    info = alliance_mod.alliance_info(al)
    cups = dict(User.objects.filter(alliance_id=al.id).values_list("id", "cup"))
    members = [
        {"id": r["id"], "name": _text(r["name"]), "power": r["power"], "cup": cups.get(r["id"], 0),
         "role": "leader" if r["is_leader"] else "deputy" if r["is_deputy"] else "member", "me": r["id"] == user.id}
        for r in alliance_mod.member_roster(al)
    ]
    binfo = alliance_mod.buildings_info(al)
    buildings = [
        {"key": b["key"], "title": b["title"], "desc": b["desc"], "level": b["level"], "max_level": b["max_level"],
         "maxed": b["maxed"], "cost": b["cost"], "effect": b["effect"], "next_effect": b["next_effect"]}
        for b in binfo["buildings"]
    ]
    view = alliance_mod.war_view(user)
    war = None
    if view is not None:
        war = {
            "my_name": view["my_name"], "foe_name": view["foe_name"],
            "my_score": view["my_score"], "foe_score": view["foe_score"],
            "seconds_left": view["remaining_seconds"], "ended": view["ended"],
            "rallied": view["already_rallied"], "my_contribution": view["my_contribution"],
            "my_participants": view["my_participants"], "foe_participants": view["foe_participants"],
            "contributors": [{"name": _text(c["name"]), "power": c["power"], "me": c["user_id"] == user.id}
                             for c in view["contributors"]],
        }
    boss = get_active_boss(al.id)
    others = len(members) - 1
    return {
        "id": al.id,
        "name": info["name"],
        "role": alliance_mod._role_of(al, user.id),
        "leader": _text(display_name(info["leader"])) if info["leader"] else None,
        "deputy": _text(display_name(info["deputy"])) if info["deputy"] else None,
        "member_count": info["member_count"],
        "capacity": info["capacity"],
        "power": info["power"],
        "treasury": info["treasury_gold"],
        "members": members,
        "buildings": buildings,
        "vault_income": binfo["vault_income"],
        "war": war,
        "war_points": alliance_mod.perks_info(al)["war_points"],
        "war_week_bonus": alliance_mod.WAR_WINNER_TREASURY_BONUS,
        "raid": {
            "level": al.raid_level,
            "boss": ({"name": boss.name, "level": boss.level, "element": boss.element,
                      "hp": max(0, boss.current_hp), "max_hp": boss.max_hp} if boss else None),
            "top": [{"rank": m["rank"], "name": _text(m["name"]), "damage": m["damage"], "me": m["user_id"] == user.id}
                    for m in alliance_raid_members(al.id, 10)],
        },
        "joined_today": _joined_alliance_today(user),
        # what leaving does (same cases as the bot's confirm screen)
        "leave": {
            "last_member": others <= 0,
            "is_leader": al.leader_id == user.id,
            "heir": "deputy" if (al.deputy_id and al.deputy_id != user.id) else "member",
        },
    }


@endpoint()
def alliance_panel(request, user):
    from config import BOT_USERNAME
    from game import alliance as alliance_mod

    al = Alliance.objects.filter(id=user.alliance_id).first() if user.alliance_id else None
    return {
        "banner": _feature_img("alliance"),
        "alliance": _alliance_dict(user, al) if al is not None else None,
        "create_cost": alliance_mod.ALLIANCE_CREATE_COST,
        "bot": BOT_USERNAME or None,
        "coins": user.coins,
    }


@endpoint("POST")
def alliance_deposit(request, user, data):
    from game.alliance import deposit_treasury

    amount = need_int(data, "amount", minimum=1, maximum=2_000_000_000)
    al = deposit_treasury(user, amount)
    return {"amount": amount, "treasury": al.treasury_gold, "name": al.name}


@endpoint("POST")
def alliance_leave(request, user, data):
    from game.alliance import leave_alliance

    leave_alliance(user)
    return {"left": True}


# ── alliance rankings ─────────────────────────────────────────────────────────
_BOARDS = ("power", "treasury", "raid", "war")


def _grant_dict(reward: dict | None) -> dict | None:
    return {k: v for k, v in reward.items() if v} if reward else None


@endpoint()
def ranks(request, user):
    """One board per request (`?board=`): power/league, treasury, raid level, weekly war."""
    from game import alliance as alliance_mod

    board = request.GET.get("board") or "power"
    if board not in _BOARDS:
        raise GameError("درخواست ناقصه.")
    mine = user.alliance_id
    out = {"board": board, "boards": list(_BOARDS), "in_alliance": mine is not None, "locked": False, "rows": []}

    if board == "power":
        # «برترین اتحادها» is open to everyone; the league rewards belong to the gated
        # «لیگ اتحادها» panel, so they are attached only when that panel is unlocked.
        league_open, req = _unlocked(user, "alliance_league")
        out.update({"banner": _feature_img("alliance_league"), "league_open": league_open, "league_hall": req})
        for i, r in enumerate(alliance_mod.top_alliances(10), start=1):
            out["rows"].append({
                "rank": i, "name": r["alliance"].name, "value": r["power"], "members": r["member_count"],
                "me": r["alliance"].id == mine,
                "reward": _grant_dict(alliance_mod.ALLIANCE_LEAGUE_REWARD_BY_RANK.get(i)) if league_open else None,
            })
        return out

    if board == "treasury":
        is_open, req = _unlocked(user, "rank")
        out.update({"banner": _feature_img("rank"), "hall": req})
        if not is_open:
            out["locked"] = True
            return out
        for i, r in enumerate(alliance_mod.top_alliances_by_treasury(limit=10), start=1):
            out["rows"].append({
                "rank": i, "name": r["alliance"].name, "value": r["treasury"], "members": r["member_count"],
                "me": r["alliance"].id == mine,
                "reward": ({"coins": alliance_mod.DAILY_TREASURY_REWARD_BY_RANK[i]}
                           if i in alliance_mod.DAILY_TREASURY_REWARD_BY_RANK else None),
            })
        out["daily_rewards"] = [{"rank": k, "coins": v} for k, v in sorted(alliance_mod.DAILY_TREASURY_REWARD_BY_RANK.items())]
        # «رتبه‌ی اتحاد تو» exactly as the bot's /rank computes it
        out["total"] = Alliance.objects.count()
        if mine:
            ids = list(Alliance.objects.order_by("-treasury_gold", "id").values_list("id", flat=True))
            out["my_rank"] = next((i for i, aid in enumerate(ids, start=1) if aid == mine), None)
        return out

    if board == "raid":
        from game.raid import RAID_WEEKLY_REWARD_BY_RANK, alliance_raid_ranking

        is_open, req = _unlocked(user, "raid_rank")
        out.update({"banner": _feature_img("raid_rank"), "hall": req})
        if not is_open:
            out["locked"] = True
            return out
        for r in alliance_raid_ranking(limit=10):
            out["rows"].append({
                "rank": r["rank"], "name": r["alliance"].name, "value": r["raid_level"], "members": r["member_count"],
                "me": r["alliance"].id == mine,
                "reward": _grant_dict(RAID_WEEKLY_REWARD_BY_RANK.get(r["rank"])),
            })
        return out

    # weekly war points (the «جنگ هفتگی» board inside the alliance menu — not gated)
    out.update({"banner": _feature_img("alliance"), "bonus": alliance_mod.WAR_WINNER_TREASURY_BONUS})
    for i, r in enumerate(alliance_mod.war_leaderboard(), start=1):
        out["rows"].append({"rank": i, "name": r["name"], "value": r["war_points"], "members": None,
                            "me": r["id"] == mine, "reward": None})
    return out


# ── guide ─────────────────────────────────────────────────────────────────────
_ARROWS = re.compile("[➔⟵⟶]")
_ARROW_MARK = "@@arrow@@"


def _guide_text(value) -> str:
    """clean() drops every emoji and arrow; the guide's «a ⟵ b» arrows carry meaning, so
    they are kept as a plain arrow."""
    out = clean(_ARROWS.sub(f" {_ARROW_MARK} ", str(value or "")))
    out = re.sub(rf"(?m)^\s*{_ARROW_MARK}\s*|\s*{_ARROW_MARK}\s*$", "", out)
    return re.sub(rf"\s*{_ARROW_MARK}\s*", " ← ", out)


@endpoint()
def guide_panel(request, user):
    from game import guide

    topics = []
    for group, table in (("concepts", guide.CONCEPTS), ("dm", guide.DM_SECTIONS)):
        for key, (kind, title, blurb, rows) in table.items():
            topics.append({
                "id": f"{group}:{key}", "key": key, "group": group, "kind": kind,
                "title": _guide_text(title), "blurb": _guide_text(blurb),
                "rows": [{"h": _guide_text(h), "b": _guide_text(b)} for h, b in rows],
            })
    return {"banner": _feature_img("guide"), "topics": topics}


# ── settings ──────────────────────────────────────────────────────────────────
def _notify_categories() -> dict:
    """{key: (title, description)} — the bot's own table; mirrored only if the bot package
    cannot be imported in this process."""
    try:
        from bot.handlers.notify import NOTIFY_CATEGORIES

        return NOTIFY_CATEGORIES
    except Exception:  # noqa: BLE001 — python-telegram-bot missing in a web-only image
        return {
            "timers": ("تایمرها", "تخم، ساختمان، اعزام، جعبه و پر شدن انرژی"),
            "attacks": ("حمله‌ها", "وقتی به آزمایشگاهت حمله می‌شه"),
            "events": ("رویدادها", "غول سرگردان، جام، جشنواره و رویداد هفته"),
            "reminders": ("یادآوری‌ها", "باکس رایگان، جایزه‌ی روزانه و باکس مأموریت"),
        }


def _settings_dict(user) -> dict:
    cats = _notify_categories()
    off = {c for c in (user.notify_off or "").split(",") if c in cats}
    cost = constants.lab_rename_cost(user.lab_renames)
    return {
        "notifications_on": user.notifications_on,
        "categories": [{"key": k, "title": clean(t), "desc": clean(d), "on": k not in off} for k, (t, d) in cats.items()],
        "lab_name": user.lab_name,
        "rename_cost": cost,
        "rename_free": cost == 0,
        "name_max": LAB_NAME_MAX_LEN,
        "diamonds": user.diamonds,
    }


@endpoint()
def settings_panel(request, user):
    return _settings_dict(user)


@endpoint("POST")
def settings_notify(request, user, data):
    """{key: "all" | <category>, on: bool} — the switches of the bot's «اعلان‌ها» screen."""
    cats = _notify_categories()
    key = need_str(data, "key", choices=("all", *cats))
    on = data.get("on")
    if not isinstance(on, bool):
        raise GameError("درخواست ناقصه.")
    with transaction.atomic():
        lock_row(user)
        if key == "all":
            user.notifications_on = on
            user.save(update_fields=["notifications_on"])
        else:
            off = {c for c in (user.notify_off or "").split(",") if c in cats}
            (off.discard if on else off.add)(key)
            user.notify_off = ",".join(sorted(off))
            user.save(update_fields=["notify_off"])
    return _settings_dict(user)


def _clean_lab_name(name) -> str:
    text = " ".join(str(name).translate(_INVISIBLE_CHARS).split())
    text = re.sub(r"(?<![^\W\d_])‌|‌(?![^\W\d_])", "", text).strip()
    return text[:LAB_NAME_MAX_LEN]


@endpoint("POST")
def settings_rename(request, user, data):
    """Rename the lab — the bot's _rename_lab_check_sync / _rename_lab_sync rules.
    {name, dry: true} only validates and returns the cleaned name and the price;
    {name, cost} performs it (`cost` = the price the player confirmed)."""
    raw = data.get("name")
    if not isinstance(raw, str) or not raw.strip() or len(raw) > LAB_NAME_MAX_LEN:
        raise GameError(f"اسم باید بین ۱ تا {LAB_NAME_MAX_LEN} کاراکتر باشه.")
    dry = data.get("dry") is True
    with transaction.atomic():
        if not dry:
            lock_row(user)
        cleaned = _clean_lab_name(raw)
        if not cleaned:
            raise GameError("اسم نمی‌تونه خالی باشه")
        if user.lab_name is None:
            raise GameError("اول با /start اسم آزمایشگاهت رو بذار")
        if cleaned.casefold() == user.lab_name.casefold():
            raise GameError("این همون اسم فعلیته")
        if lab_name_taken(cleaned, exclude_user_id=user.id):
            raise GameError("این اسم آزمایشگاه قبلاً گرفته شده")
        cost = constants.lab_rename_cost(user.lab_renames)
        if user.diamonds < cost:
            raise GameError(f"الماس کافی نداری! تغییر اسم {cost} الماس می‌خواد")
        if dry:
            return {"dry": True, "name": cleaned, "cost": cost}
        if "cost" in data and data.get("cost") != cost:
            raise GameError("هزینه‌ی تغییر اسم عوض شده؛ دوباره امتحان کن.")
        user.diamonds -= cost
        user.lab_name = cleaned
        user.lab_renames += 1
        user.save(update_fields=["diamonds", "lab_name", "lab_renames"])
    out = _settings_dict(user)
    out.update({"renamed": True, "name": cleaned, "cost": cost})
    return out


routes = [
    ("alliance/", alliance_panel),
    ("alliance/deposit/", alliance_deposit),
    ("alliance/leave/", alliance_leave),
    ("ranks/", ranks),
    ("guide/", guide_panel),
    ("settings/", settings_panel),
    ("settings/notify/", settings_notify),
    ("settings/rename/", settings_rename),
]

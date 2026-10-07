"""Hunt («شکار»): scout a wild target, attack it, swap the fighter, random encounters and
auto-hunt — a thin adapter over game/hunt.py, in the order bot/handlers/private.py uses
(`_hunt_scout_sync`, `_hunt_go_sync`, `_hunt_swap_pick_sync`, `_autohunt_sync`, `henc:`).

STATELESS TARGETS. The bot carries the scouted target in callback_data (tier:seed) and keeps
the open encounter in a per-process dict. Here the target travels as a signed token
(core.pack) holding tier + seed (the wild is rebuilt with game.hunt.rebuild_target) or the
encounter type, plus a NONCE: today's date and today's «hunt» and «app_hunt_enc» counters
of the player (DailyActionLog). Every action that spends a token advances one of those
counters inside the same transaction that holds the player's row lock:

  * attack     → record_action(user, "hunt")           (what the bot does anyway)
  * auto-hunt  → record_action_bulk(user, "hunt", n)   (ditto)
  * encounter  → the «app_hunt_enc» counter (+1, written here; it has no mission/festival
                 meaning — it exists only to make an encounter card one-shot)

so a token is good for exactly one of them: a second request with the same token (or with
any other token scouted before that action) sees a different nonce and is refused. A scout
(«حریف بعدی») is never free — it charges gold on every call, exactly like the bot — so
there is nothing to replay there.
"""

from __future__ import annotations

import html
import re

from django.db import transaction
from django.db.models import F

from bio_lab.models import DailyActionLog, User
from game import constants, hunt
from telgame_site.mini.core import (
    GameError, InsufficientGoldError, active_creature, asset_img, clean, creature_dict, creature_list,
    endpoint, need_int, need_str, pack, species_img, unpack,
)

TOKEN_KIND = "hunt_target"
TOKEN_MAX_AGE = 24 * 3600          # the nonce is what really expires a card; this is a backstop
ENC_COUNTER = "app_hunt_enc"       # DailyActionLog action used only as the encounter one-shot counter
STALE_MSG = "این کارت شکار دیگه معتبر نیست؛ یه حریف تازه پیدا کن."

# the buttons of each encounter card — same choices, labels and order as
# bot.handlers.private._hunt_scout_keyboard (the action keys are game.hunt's)
ENCOUNTER_BUTTONS = {
    "chest": (("open", "باز کردن صندوق", "main"), ("leave", "رها کردن", "skip")),
    "thief": (("fight", "حمله به دزد", "main"), ("leave", "صرف‌نظر", "skip")),
    "fork": (("crystal", "غار کریستالی", "main"), ("volcano", "دشت آتشفشانی", "main")),
    "spring": (("drink", "نوشیدن از چشمه", "main"), ("leave", "عبور", "skip")),
}
_BAR = re.compile(r"\s*\[[■□█░ ]*\]")


# ── shared with tower.py / worldboss.py ───────────────────────────────────────
def fighter(user: User):
    """(active creature with the owner's research attached, its gear, its power) — the
    power every card of this area shows and the one the duel is decided with."""
    from game import research
    from game.creature import creature_power
    from game.equipment import get_equipped_items

    creature = active_creature(user)
    research.attach_research(user, creature)  # also refreshes this process's research cache
    gear = get_equipped_items(creature)
    return creature, gear, creature_power(creature, gear)


def team(user: User) -> list[dict]:
    """The creatures the bot's «تعویض موجود» picker offers (bio_lab.repository.team_choices),
    with the reason a busy one can't be picked."""
    from bio_lab.repository import team_choices
    from game.workers import creature_status

    rows = team_choices(user)
    out = creature_list(user, rows)  # research + gear + the busy set, a fixed number of queries
    for row, c in zip(out, rows):
        # creature_list already knows WHO is busy (game.workers.busy_creature_ids); only those
        # are asked for the reason, instead of three queries for every team member
        busy = creature_status(user, c) if (row["busy"] and not c.is_active) else None
        row["busy"] = bool(busy)
        row["busy_why"] = clean(busy) if busy else ""
    return out


def swap_to(user: User, creature_id: int) -> None:
    """Make one of the team creatures the active one (the bot's swap pick)."""
    from bio_lab.repository import team_choices
    from game.creature import set_active_creature

    if creature_id not in {c.id for c in team_choices(user)}:
        raise GameError("این هیولا توی تیم تو نیست.")
    set_active_creature(user, creature_id)  # raises when it is busy


def missions_out(completed: list[dict]) -> list[dict]:
    """Completed missions (game.daily.check_missions) as plain data — the same parts
    bot.utils.mission_reward_text prints."""
    out = []
    for m in completed or []:
        extras = []
        if m.get("capsule"):
            tier, count = m["capsule"]
            extras.append(f"{count} کپسول {constants.XP_CAPSULES[tier]['label']}")
        if m.get("speedup"):
            extras.append(f"کارت سرعت {constants.speedup_plain_label(m['speedup'])}")
        out.append({
            "label": clean(m.get("label")), "weekly": bool(m.get("weekly")),
            "coins": m.get("coins", 0), "dna": m.get("dna", 0), "diamonds": m.get("diamonds", 0),
            "points": m.get("points", 0), "extras": extras,
        })
    return out


def battle_log(text) -> str:
    """The game's compact battle report as plain text (bars removed — the app draws its own)."""
    lines = (_BAR.sub("", ln).replace("[ ", "[").strip() for ln in clean(text).split("\n"))
    return "\n".join(line for line in lines if line.strip("─━-—= "))


def plain_name(text) -> str:
    return html.unescape(str(text or ""))


def hall_gate(user: User, action: str) -> None:
    """The bot's main-hall gate for a section (bot.handlers.private.menu_callback /
    bot.gates.hall_gated): locked below SECTION_HALL_REQ[action], except for the section
    the player's active story quest points at."""
    req = hall_requirement(action)
    if req is None:
        return
    from game import story
    from game.buildings import main_hall_level

    level = main_hall_level(user)
    if level < req and action != story.active_cta_action(user):
        raise GameError(f"این بخش از سطح {req} «تالار مِهر» باز می‌شه (الان سطح {level}). اول تالار مِهرت رو ارتقا بده.")


def hall_requirement(action: str) -> int | None:
    try:
        from bot.handlers.private import SECTION_HALL_REQ
    except Exception:  # noqa: BLE001 — the bot package isn't importable here: mirror its table
        SECTION_HALL_REQ = {"mugen_tower": 5}
    return SECTION_HALL_REQ.get(action)


def section_open(user: User, action: str) -> bool:
    try:
        hall_gate(user, action)
    except GameError:
        return False
    return True


# ── the one-shot nonce ────────────────────────────────────────────────────────
def _nonce(user: User) -> str:
    from game.daily import today_str

    day = today_str()
    counts = dict(
        DailyActionLog.objects.filter(user=user, day=day, action__in=("hunt", ENC_COUNTER)).values_list("action", "count")
    )
    return f"{day}:{counts.get('hunt', 0)}:{counts.get(ENC_COUNTER, 0)}"


def _bump_encounter_counter(user: User) -> None:
    from game.daily import today_str

    DailyActionLog.objects.get_or_create(user=user, action=ENC_COUNTER, day=today_str(), defaults={"count": 0})
    DailyActionLog.objects.filter(user=user, action=ENC_COUNTER, day=today_str()).update(count=F("count") + 1)


def _read_token(user: User, token, kind: str | None = None) -> dict:
    """The signed card, refused unless it is still the player's CURRENT one (nonce)."""
    card = unpack(user, TOKEN_KIND, token, max_age=TOKEN_MAX_AGE)
    if not isinstance(card, dict) or card.get("n") != _nonce(user) or (kind and card.get("k") != kind):
        raise GameError(STALE_MSG)
    if card.get("k") == "t":
        if card.get("tier") not in hunt.HUNT_TIERS or not isinstance(card.get("seed"), int):
            raise GameError(STALE_MSG)
    elif card.get("k") != "e" or card.get("enc") not in hunt.ENCOUNTERS:
        raise GameError(STALE_MSG)
    return card


# ── serialisers ───────────────────────────────────────────────────────────────
def _target_out(creature, power: int, target: dict) -> dict:
    """A wild target with the exact prize of a win — the number the bot's card shows
    (hunt_reward_roll with the scouted seed and this week's element rule)."""
    from game import events

    coins, dna = hunt.hunt_reward_roll(power, target["tier"], target.get("seed"), events.hunt_loot_mult(creature.element))
    return {
        "kind": "target", "tier": target["tier"], "tier_label": clean(hunt.HUNT_TIERS[target["tier"]]["label"]),
        "name": target["name"], "element": target["element"], "rarity": "common", "power": target["power"],
        "img": species_img(target["name"], target["element"], "common", 1, 1),
        "reward": {"coins": coins, "dna": dna},
    }


def _encounter_out(enc_type: str) -> dict:
    from game.media import get_feature_image_path

    info = hunt.ENCOUNTERS[enc_type]
    return {
        "kind": "encounter", "type": enc_type, "title": clean(info["title"]), "desc": clean(info["desc"]),
        "img": asset_img(get_feature_image_path("encounter_cave" if enc_type == "fork" else f"encounter_{enc_type}")),
        "actions": [{"key": k, "label": label, "style": style} for k, label, style in ENCOUNTER_BUTTONS[enc_type]],
    }


def _state(user: User, card: dict | None = None, token: str | None = None, target: dict | None = None) -> dict:
    """Everything the hunt screen shows. `card` = the open (already validated) card;
    `target` = the wild it stands for when the caller already has it (a fresh scout)."""
    from game.energy import sync_energy
    from game.media import get_feature_image_path

    creature, gear, power = fighter(user)
    out = {
        "me": creature_dict(creature, gear),
        "scout_cost": hunt.scout_cost(creature, power=power),
        "energy_cost": constants.HUNT_ENERGY_COST,
        "energy": sync_energy(user),
        "art": asset_img(get_feature_image_path("hunt")),
        "target": None, "token": None,
    }
    if card is not None:
        out["token"] = token
        if card["k"] == "e":
            out["target"] = _encounter_out(card["enc"])
        else:
            out["target"] = _target_out(creature, power, target or hunt.rebuild_target(user, card["tier"], card["seed"]))
    return out


# ── views ─────────────────────────────────────────────────────────────────────
@endpoint()
def panel(request, user):
    """The hunt screen. `?t=<token>` re-opens the card the client still holds (after a
    reload or a trip to another screen) — read-only, nothing is charged."""
    token = request.GET.get("t") or ""
    card = None
    stale = False
    if token:
        try:
            card = _read_token(user, token)
        except GameError:
            stale = True
    out = _state(user, card, token if card else None)
    out["stale"] = stale
    return out


@endpoint("POST")
def scout(request, user, data):
    """Find a target («شکار» / «حریف بعدی» / «شکار دوباره»). Costs gold on EVERY call."""
    creature, _gear, power = fighter(user)
    cost = hunt.scout_cost(creature, power=power)
    paid = User.objects.filter(id=user.id, coins__gte=cost).update(coins=F("coins") - cost)
    if not paid:
        raise InsufficientGoldError(f"برای جستجوی حریف {cost:,} طلا لازمه (الان {user.coins:,} داری).", cost, user.coins)
    target = hunt.scout_one(user, creature)
    # scout_one() remembers an encounter in a per-process dict; this worker may never see the
    # follow-up request, so don't leave it behind — the signed card below is the record.
    hunt._PENDING_ENCOUNTERS.pop(user.id, None)
    nonce = _nonce(user)
    if target.get("is_encounter"):
        card = {"k": "e", "enc": target["enc_type"], "n": nonce}
    else:
        card = {"k": "t", "tier": target["tier"], "seed": int(target["seed"]), "n": nonce}
    # the wild scout_one() just built IS the card: no second rebuild (it re-prices the whole team)
    out = _state(user, card, pack(user, TOKEN_KIND, card), None if card["k"] == "e" else target)
    out["paid"] = cost
    return out


@endpoint("POST")
def swap(request, user, data):
    """Another team creature fights the SAME target (the card and its token don't change)."""
    creature_id = need_int(data, "creature_id", 1)
    token = data.get("token") or ""
    card, stale = None, False
    if token:
        try:
            card = _read_token(user, token)
        except GameError:
            stale = True
    swap_to(user, creature_id)
    out = _state(user, card, token if card else None)
    out["stale"] = stale
    return out


@endpoint()
def team_view(request, user):
    """The «تعویض موجود» picker: the team (or the strongest few), busy ones marked."""
    return {"team": team(user)}


@endpoint("POST")
def attack(request, user, data):
    from bio_lab.repository import get_active_creature
    from game.creature import creature_power
    from game.daily import check_missions, record_action
    from game.energy import spend_energy
    from game.equipment import get_equipped_items

    token = need_str(data, "token", max_len=600)
    with transaction.atomic():
        # the row lock serialises two taps; the second one then fails the nonce check
        user = User.objects.select_for_update().get(id=user.id)
        card = _read_token(user, token, "t")
        creature = get_active_creature(user)
        if creature is None:
            raise GameError("اول یه هیولای فعال انتخاب کن.")
        level_before = creature.level

        spend_energy(user, constants.HUNT_ENERGY_COST, "شکار")
        user.save(update_fields=["energy", "energy_updated_at"])

        result = hunt.resolve_hunt(user, creature, card["tier"], card["seed"])
        record_action(user, "hunt")  # ← advances the nonce: this card is spent
        completed = check_missions(user, "hunt")
    # resolve_hunt() fought with this very object (research attached, XP added): one gear
    # query gives the card of the fighter as it is now
    gear = get_equipped_items(creature)
    return {
        "won": result["won"], "coins": result["coins"], "dna": result["dna"], "xp": result["xp"],
        "levels": result["levels"], "level": creature.level, "level_before": level_before,
        "lab_up": result["lab_up"] or None,
        "log": battle_log(result["log_text"]),
        # name/element/power/art of the wild are on the card the client attacked (same tier + seed)
        "enemy": {"name": result["wild_name"], "tier": card["tier"],
                  "tier_label": clean(hunt.HUNT_TIERS[card["tier"]]["label"])},
        "missions": missions_out(completed),
        "me": creature_dict(creature, gear),
        "scout_cost": hunt.scout_cost(creature, power=creature_power(creature, gear)),
    }


@endpoint("POST")
def encounter(request, user, data):
    """One choice on a random-encounter card. Pays (or costs) once: see the module docstring."""
    from bio_lab.repository import get_active_creature

    token = need_str(data, "token", max_len=600)
    action = need_str(data, "action", max_len=16)
    with transaction.atomic():
        user = User.objects.select_for_update().get(id=user.id)
        card = _read_token(user, token, "e")
        enc_type = card["enc"]
        creature = get_active_creature(user)
        level_before = creature.level if creature else 0
        # game.hunt guards an encounter with a per-process dict filled by scout_one(); the
        # scout may have been served by another worker, so the signed card + nonce above is
        # the real guard and the dict is only primed for this one call.
        hunt._PENDING_ENCOUNTERS[user.id] = enc_type
        try:
            res = hunt.resolve_encounter_action(user, creature, enc_type, action)
        finally:
            hunt._PENDING_ENCOUNTERS.pop(user.id, None)
        _bump_encounter_counter(user)  # ← advances the nonce: this card is spent
    level = creature.level if creature else 0
    me, gear, power = fighter(user)
    return {
        "me": creature_dict(me, gear), "scout_cost": hunt.scout_cost(me, power=power),
        "type": enc_type, "action": action, "success": bool(res.get("success")), "text": clean(res.get("msg")),
        "coins": res.get("coins", 0), "dna": res.get("dna", 0), "diamonds": res.get("diamonds", 0),
        "energy": res.get("energy", 0), "xp": res.get("xp", 0),
        "levels": max(0, level - level_before), "level": level,
        "img": _encounter_out(enc_type)["img"],
    }


def _auto_state(user: User) -> dict:
    from game.energy import get_max_energy, sync_energy
    from game.subscription import get_subscription_tier

    creature, gear, _power = fighter(user)
    energy, per = sync_energy(user), max(1, constants.HUNT_ENERGY_COST)
    return {
        "me": creature_dict(creature, gear),
        "energy": energy, "max_energy": get_max_energy(user), "energy_cost": per,
        "all": energy, "half": max(per, energy // 2), "can": energy >= per,
        "loot_pct": int(round(hunt.AUTO_HUNT_LOOT_MULT * 100)),
        "win_floor_pct": int(round(hunt.AUTO_HUNT_WIN_FLOOR * 100)),
        "subscription": get_subscription_tier(user),
    }


@endpoint()
def auto_panel(request, user):
    """The auto-hunt prompt: live energy and the bot's quick amounts (همه / نصف / دلخواه)."""
    return _auto_state(user)


@endpoint("POST")
def auto_run(request, user, data):
    from bio_lab.repository import get_active_creature
    from game.daily import check_missions, record_action_bulk
    from game.energy import get_max_energy, spend_energy, sync_energy

    amount = need_int(data, "energy", 1, 10_000)
    with transaction.atomic():
        user = User.objects.select_for_update().get(id=user.id)
        creature = get_active_creature(user)
        if creature is None:
            raise GameError("اول یه هیولای فعال انتخاب کن.")
        level_before = creature.level
        sync_energy(user)
        max_en = get_max_energy(user)
        per = max(1, constants.HUNT_ENERGY_COST)
        hunts = min(amount, user.energy) // per  # clamp to what is really there, like the bot
        if hunts <= 0:
            from game.energy import EnergyError

            raise EnergyError(f"انرژی کافی نداری (الان {user.energy}/{max_en}).")
        spend_energy(user, hunts * per, "شکار خودکار")
        user.save(update_fields=["energy", "energy_updated_at"])

        res = hunt.resolve_auto_hunt(user, creature, hunts)
        record_action_bulk(user, "hunt", hunts)  # counts toward missions; also spends any open card
        completed = check_missions(user, "hunt")
    return {
        "hunts": res["hunts"], "wins": res["wins"], "losses": res["losses"],
        "coins": res["coins"], "dna": res["dna"], "xp": res["xp"],
        "levels": res["levels"], "level": creature.level, "level_before": level_before,
        "lab_up": bool(res["lab_up"]),
        "sub_bonus_pct": res["sub_bonus_pct"], "sub_name": res["sub_name"],
        "base_coins": res["base_coins"], "base_dna": res["base_dna"],
        "bonus_coins": res["bonus_coins"], "bonus_dna": res["bonus_dna"],
        "energy_left": user.energy, "max_energy": max_en,
        "missions": missions_out(completed),
        # the prompt for the next batch, so «شکار خودکار مجدد» needs no second request
        "panel": _auto_state(user),
    }


@endpoint()
def hub(request, user):
    """Tiny status for the battle hub tiles, in ONE request: is a world boss up, is the tower
    open for this player, how many arena chests are ready to open."""
    from game import worldboss
    from telgame_site.mini.api.arena import ready_chest_count

    return {"boss": worldboss.current_boss() is not None, "tower_open": section_open(user, "mugen_tower"),
            "ready_chests": ready_chest_count(user)}


routes = [
    ("", panel),
    ("scout/", scout),
    ("swap/", swap),
    ("team/", team_view),
    ("attack/", attack),
    ("encounter/", encounter),
    ("auto/", auto_panel),
    ("auto/run/", auto_run),
    ("hub/", hub),
]

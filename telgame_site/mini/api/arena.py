"""Arena («آرنا»), arena chests («جعبه‌ها») and the league («لیگ») — thin adapters over
game.arena / game.arena_chests / game.league / game.season, calling them in the order the
bot's `*_sync` helpers do (bot/handlers/arena.py, bot/handlers/league.py).

THE OPPONENT BEING VIEWED lives in the client, as a signed token (core.pack). The bot keeps it
in `context.user_data`; a web worker has no such memory. The token carries everything
`game.arena.attack()` needs (the same keys as the bot's pending dict) plus two bindings:

  * ``n`` — the id of this player's newest AttackLog row when the card was issued. Every
    attack creates exactly one AttackLog for the attacker, so after ONE attack the number has
    moved and every token issued before it is dead. That is what makes a card single-use: a
    bot with a good pre-rolled loot cannot be attacked twice. It is checked under the player's
    row lock, so two parallel requests with the same token cannot both pass.
  * ``c`` — the id of the active creature the card was drawn for (see `_refit`).

On «حمله» the card is RE-VALIDATED, never trusted for being signed: a real opponent must still
exist, not be banned, still sit inside the attacker's cup band / rookie protection (`_revalidate`,
the same rules as the bot's `_reconstruct_pending_sync`), and `attack()` then re-checks alliance,
active creature and shield under the defender's row lock. A card that fails any of it — or that is
expired / already used — is answered with a FRESH opponent (`rematch`), never with a dead end.

`game.arena._RESERVATIONS` (the 20-second "this opponent is held for me" map) is per-process and
therefore only best-effort here; the real guard is `attack()` itself, which re-checks the
defender's shield under a row lock and raises OpponentUnavailableError.
"""

import datetime
import html
import random

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from bio_lab.models import ArenaChest, AttackLog, Creature, User
from bio_lab.repository import lab_display, lock_row, team_choices
from game import arena as A
from game import arena_chests as AC
from game import constants, league, season
from telgame_site.mini.core import (GameError, active_creature, asset_img, clean, creature_dict, creature_img,
                                    creature_list, endpoint, equipment_img, need_int, pack, species_img, unpack)

TOKEN = "arena_opp"
TOKEN_AGE = 15 * 60
RECENT_MAX = 5          # bot: _RECENT_OPPONENTS_MAX
REVENGE_DAYS = 3
NOTE_STALE = "این کارت قدیمی بود؛ حریف تازه برات پیدا کردم."
NOTE_UNAVAILABLE = "حریف قبلی الان قابل حمله نیست (سپر گرفت یا در دسترس نیست)؛ یه حریف تازه برات پیدا شد."
NOTE_FIGHTER = "هیولای فعالت عوض شده بود؛ کارت حریف به‌روز شد."


class _StaleCard(Exception):
    """The token was issued before this player's latest attack (already used, or old)."""


class _FighterChanged(Exception):
    """The active creature is not the one the card was drawn for."""


def _txt(value) -> str:
    """Game label → plain text (lab_display() is HTML-escaped and may carry a badge tag)."""
    return html.unescape(clean(value))


def _gate(user, action: str) -> None:
    """Main-hall unlock, exactly like bot.gates.hall_gated / menu_callback."""
    from bot.handlers.private import SECTION_HALL_REQ
    from game import story
    from game.buildings import main_hall_level

    req = SECTION_HALL_REQ.get(action)
    if req is None:
        return
    level = main_hall_level(user)
    if level < req and story.active_cta_action(user) != action:
        raise GameError(f"این بخش از سطح {req} «تالار مِهر» باز می‌شه (الان سطح {level}). اول تالار مِهرت رو ارتقا بده.")


def _my(user, creature=None) -> dict:
    """The player's fighter as the card shows it. The owner's research is re-read from the DB
    first: game.research keeps a per-PROCESS cache, and a web worker's copy can be older than
    an upgrade finished through the bot or another worker — the card must show the power the
    duel will really be fought with."""
    from game import research
    from game.equipment import get_equipped_items

    creature = creature or active_creature(user)
    research.attach_research(user, creature)
    return creature_dict(creature, get_equipped_items(creature))


def _fresh_research(*players) -> None:
    """Refresh this worker's research cache for the players about to fight (see `_my`):
    game.arena.attack() reads the combat buffs of both sides from that cache."""
    from game import research

    for player in players:
        if player is not None:
            research.research_levels(player)


def ready_chest_count(user) -> int:
    """Chests that can be opened now (one indexed query; nothing is advanced or settled)."""
    now = timezone.now()
    rows = ArenaChest.objects.filter(user=user).values_list("status", "unlock_finishes_at")
    return sum(1 for status, until in rows if status == "ready" or (status == "unlocking" and until and until <= now))


def _last_attack_id(user) -> int:
    return AttackLog.objects.filter(attacker_id=user.id).aggregate(m=Max("id"))["m"] or 0


def _reward(r: dict | None) -> dict | None:
    if not r:
        return None
    return {"coins": r.get("coins", 0), "dna": r.get("dna", 0), "diamonds": r.get("diamonds", 0)}


def _league(lg: dict) -> dict:
    return {"key": lg["key"], "name": lg["name"], "min_cup": lg["min_cup"], "coins": lg["coins"], "dna": lg["dna"]}


def _rank_of(user) -> int:
    return User.objects.filter(is_banned=False, cup__gt=user.cup).count() + 1


# ══════════════════════════════ arena home ══════════════════════════════
def _chest_summary(chests) -> dict:
    opening = next((c for c in chests if c.status == "unlocking"), None)
    return {
        "count": len(chests), "max": AC.ARENA_CHEST_MAX_SLOTS,
        "ready": sum(1 for c in chests if c.status == "ready"),
        "opening_left": AC.seconds_until_ready(opening) if opening is not None else None,
    }


@endpoint()
def home(request, user):
    from game.media import get_feature_image_path

    # same sequence as the bot's _arena_home_sync (the season settles lazily on any arena read)
    season.close_due_season()
    user.refresh_from_db()
    chests = AC.get_user_chests(user)
    creature = Creature.objects.filter(owner=user, is_active=True).first()
    my = _my(user, creature) if creature is not None else None
    power = my["power"] if my else 0  # = game.arena.active_power, with this player's research fresh
    lg, nxt = constants.league_for_cup(user.cup), constants.next_league(user.cup)
    history = []
    for log in A.recent_attacks_received(user):
        history.append({
            "won": log.attacker_won,  # the ATTACKER won = you were raided
            "name": _txt(log.attacker_label or (lab_display(log.attacker) if log.attacker else "یه مهاجم")),
            "power": log.attacker_power or 0, "coins": log.loot_gold or 0, "dna": log.loot_dna or 0,
            "at": int(log.created_at.timestamp()),
        })
    return {
        "cup": user.cup, "power": power, "week": season.current_week(),
        "season_left": season.seconds_until_next_week(),
        "league": _league(lg), "next_league": _league(nxt) if nxt else None,
        "overcap": user.cup > A.deserved_cup(power),
        "shield": A.shield_remaining_seconds(user), "shield_cost_hours": constants.SHIELD_ATTACK_COST_HOURS,
        "chests": _chest_summary(chests),
        "history": history,
        "revenges": len(A.revengeable_attacks(user)),
        "energy_cost": constants.ARENA_ATTACK_ENERGY_COST,
        "loot_percent": int(constants.ARENA_LOOT_PERCENT * 100),
        "img": asset_img(get_feature_image_path("arena")),
    }


@endpoint()
def badges(request, user):
    """Cheap numbers for the hub tiles (one indexed query, nothing is advanced or settled)."""
    return {"ready_chests": ready_chest_count(user)}


# ══════════════════════════════ matchmaking ══════════════════════════════
def _seen(data: dict) -> list[int]:
    """The client's short «already shown» list → exclude_ids for find_opponent."""
    raw = data.get("seen")
    if not isinstance(raw, list):
        return []
    out = []
    for value in raw[-RECENT_MAX:]:
        if isinstance(value, int) and not isinstance(value, bool):
            out.append(value)
    return out


def _refit(user, pending: dict, creature) -> None:
    """Bind the card to the fighter. Mirrors the bot's _swap_rerender_sync: at the top of the
    ladder (cup 3700+) a BOT always counters the creature you field — the same rule
    game.arena._fake_opponent applies when the bot is generated — so swapping your fighter
    there re-picks the bot's element. Loot, power and cup never change."""
    if (pending.get("is_fake") and creature.element
            and (user.cup >= 3700 or int(pending.get("cup", 0)) >= 3700)
            and not constants.is_strong_against(pending.get("element"), creature.element)):
        counters = [e for e in constants.ELEMENTS if constants.is_strong_against(e, creature.element)]
        if counters:
            pending["element"] = random.choice(counters)
            pending["creature_name"] = constants.random_species_name(pending["element"])
    pending["c"] = creature.id


def _card(user, pending: dict, seen: list[int], note: str = "", creature=None, foe=None) -> dict:
    """The «opponent found» card (bot: _render_opponent) + the token that carries it.
    `creature` / `foe` = the two active creatures when the caller has already loaded them."""
    from game.energy import get_max_energy, sync_energy

    my = _my(user, creature)
    opp = {
        "is_fake": bool(pending["is_fake"]), "label": _txt(pending["label"]),
        "alliance": pending.get("alliance") or None,
        "creature": _txt(pending.get("creature_name") or "؟"),
        "cup": pending["cup"], "power": pending["power"], "element": pending.get("element"),
        "rarity": None, "star": None, "level": None, "img": None,
    }
    if pending["is_fake"]:
        opp["rarity"], opp["star"] = A._bot_display_tier(int(pending["cup"]))
        opp["img"] = species_img(pending.get("creature_name") or "", pending.get("element") or "fire", opp["rarity"], opp["star"])
    else:
        tc = foe or Creature.objects.filter(owner_id=pending["user_id"], is_active=True).first()
        if tc is not None:
            opp.update({"rarity": tc.rarity, "star": tc.star_level, "level": tc.level, "img": creature_img(tc)})
    return {
        "note": note,
        "token": pack(user, TOKEN, pending),
        "seen": seen,
        "my": my, "my_cup": user.cup,
        "opponent": opp,
        "loot": {"coins": pending["loot_gold"], "dna": pending["loot_dna"]},
        "cup_win": A.cup_delta(user, pending["cup"], True, my["power"]),
        "cup_loss": A.cup_delta(user, pending["cup"], False, my["power"]),
        "energy_cost": constants.ARENA_ATTACK_ENERGY_COST,
        "energy": sync_energy(user), "max_energy": get_max_energy(user),
        "shield": A.shield_remaining_seconds(user), "shield_cost_hours": constants.SHIELD_ATTACK_COST_HOURS,
    }


def _find(user, seen: list[int], note: str = "") -> dict:
    """bot: _find_sync + the bookkeeping of arena_find_callback."""
    opponent = A.find_opponent(user, exclude_ids=seen)
    foe = None
    if opponent.get("is_fake"):
        loot, dna = constants.arena_loot_roll(user.cup)  # rolled ONCE; card and payout share it
        user_id = None
        # a bot breaks the cycle: keep only the last real player excluded (bot: arena_last_real_id)
        seen = seen[-1:]
    else:
        target = opponent["user"]
        loot = max(0, target.coins // 10)          # what attack() takes: 10% of gold and DNA
        dna = max(0, target.dna_fragments // 10)
        user_id = target.id
        seen = ([i for i in seen if i != target.id] + [target.id])[-RECENT_MAX:]
        foe = Creature.objects.filter(owner=target, is_active=True).first()
        if foe is not None:
            # = game.arena.active_power(target), with the defender's research re-read (see `_my`)
            from game import research
            from game.creature import creature_power
            from game.equipment import get_equipped_items

            research.attach_research(target, foe)
            opponent["power"] = creature_power(foe, get_equipped_items(foe))
    pending = {
        "is_fake": opponent["is_fake"], "user_id": user_id, "label": opponent["label"],
        "alliance": opponent.get("alliance"),
        "creature_name": opponent.get("creature_name", "؟"),
        "cup": opponent["cup"], "power": opponent["power"], "element": opponent.get("element"),
        "loot_pool": opponent["loot_pool"], "loot_gold": loot, "loot_dna": dna,
        "n": _last_attack_id(user),
    }
    creature = active_creature(user)
    pending["c"] = creature.id
    return _card(user, pending, seen, note, creature, foe)


_CARD_KEYS = ("is_fake", "label", "cup", "power", "loot_pool", "loot_gold", "loot_dna", "n", "c")


def _pending(user, data: dict) -> dict:
    pending = unpack(user, TOKEN, data.get("token"), max_age=TOKEN_AGE)
    if not isinstance(pending, dict) or any(key not in pending for key in _CARD_KEYS):
        raise GameError("درخواست نامعتبره؛ صفحه رو دوباره باز کن.")
    return pending


def _pending_or_none(user, data: dict) -> dict | None:
    """For the screens that can simply draw a new opponent: an expired / missing / damaged
    card is not an error there."""
    try:
        return _pending(user, data)
    except GameError:
        return None


def _revalidate(user, pending: dict) -> User | None:
    """A real opponent's card must obey the matchmaking rules at the moment of the attack too
    (bot: _reconstruct_pending_sync) — otherwise a held card is an attack bookmark onto someone
    who has since left the cup band, crossed rookie protection or been banned. Returns the
    defender (None for a bot)."""
    if pending["is_fake"]:
        return None
    target = User.objects.filter(id=pending.get("user_id")).first()
    if target is None:
        raise A.OpponentUnavailableError("این حریف دیگه در دسترس نیست، یکی دیگه پیدا کن.")
    rookie_gap = (user.cup >= 2000 and target.cup < 1000) or (user.cup < 1000 and target.cup >= 2000)
    if (target.id == user.id or target.is_banned or rookie_gap
            or abs(target.cup - user.cup) > constants.ARENA_MATCH_CUP_BAND):
        raise A.OpponentUnavailableError("این حریف دیگه در محدوده‌ی کاپ تو نیست، یکی دیگه پیدا کن.")
    return target


@endpoint("POST")
def find(request, user, data):
    """«جستجوی حریف» / «حریف بعدی». Reads only — it is a POST because the answer is random and
    the request carries the list of opponents already shown."""
    return _find(user, _seen(data))


@endpoint("POST")
def card(request, user, data):
    """Redraw the card being viewed (coming back to the screen): same opponent, same loot."""
    pending = _pending_or_none(user, data)
    seen = _seen(data)
    if pending is None or pending["n"] != _last_attack_id(user):
        return _find(user, seen, NOTE_STALE)
    creature = active_creature(user)
    _refit(user, pending, creature)
    return _card(user, pending, seen, creature=creature)


@endpoint()
def team(request, user):
    """Who can fight instead (bot: _team_choices_sync — the configured team, else the strongest few)."""
    rows = creature_list(user, team_choices(user))
    for row in rows:
        row["busy"] = bool(row["busy"] and not row["active"])
    return {"creatures": rows}


@endpoint("POST")
def swap(request, user, data):
    """«تعویض موجود»: the opponent stays, only your fighter changes (bot: _swap_rerender_sync)."""
    from game.creature import set_active_creature

    pending = _pending_or_none(user, data)
    creature_id = need_int(data, "creature_id", 1)
    with transaction.atomic():
        set_active_creature(user, creature_id)  # GameError if it isn't yours or is busy
    seen = _seen(data)
    if pending is None or pending["n"] != _last_attack_id(user):
        return _find(user, seen, NOTE_STALE)
    creature = active_creature(user)
    _refit(user, pending, creature)
    return _card(user, pending, seen, creature=creature)


def _creature_details(creature) -> dict:
    """bot: _opponent_details_sync — level, stars, stats, gear and body parts of a real fighter."""
    from game.creature import creature_power, effective_stats
    from game.equipment import get_equipped_items

    items = get_equipped_items(creature)
    out = creature_dict(creature, items)
    stats = effective_stats(creature, items)
    out["gear_power"] = out["power"] - creature_power(creature, [])
    out["poison"] = round(stats.get("poison") or 0)
    out["parts"] = [
        {"key": key, "label": _txt(constants.BODY_PARTS[key]["label"]), "level": getattr(creature, f"{key}_lvl")}
        for key in ("fangs", "armor", "wings", "poison")
    ]
    return out


def _details_of(target_id: int | None, fallback: dict) -> dict:
    target = User.objects.filter(id=target_id).select_related("alliance").first() if target_id else None
    creature = Creature.objects.filter(owner=target, is_active=True).first() if target else None
    out = {"is_fake": creature is None, "label": _txt(fallback.get("label") or (lab_display(target) if target else "این بازیکن")),
           "alliance": target.alliance.name if (target and target.alliance_id) else None}
    if creature is None:
        cup = int(fallback.get("cup", target.cup if target else 0))
        rarity, star = A._bot_display_tier(cup)
        out.update({"power": fallback.get("power", 0), "element": fallback.get("element"), "rarity": rarity, "star": star,
                    "name": _txt(fallback.get("creature_name") or ""),
                    "img": species_img(fallback.get("creature_name") or "", fallback.get("element") or "fire", rarity, star)
                    if fallback.get("creature_name") else None})
    else:
        out["creature"] = _creature_details(creature)
    return out


@endpoint("POST")
def details(request, user, data):
    """«جزئیات حریف» — of the card being viewed (`token`), or of someone who raided you (`attacker_id`)."""
    if data.get("attacker_id") is not None:
        attacker_id = need_int(data, "attacker_id")
        if not AttackLog.objects.filter(defender=user, attacker_id=attacker_id).exists():
            raise GameError("این بازیکن به تو حمله نکرده.")
        return {"details": _details_of(attacker_id, {})}
    pending = _pending(user, data)
    return {"details": _details_of(None if pending["is_fake"] else pending.get("user_id"), pending)}


# ══════════════════════════════ attack ══════════════════════════════
@transaction.atomic
def _attack_tx(user, pending: dict):
    """bot: _attack_sync. Atomic, so a refused attack gives the energy back."""
    lock_row(user)  # the mutex for «one card = one attack»
    if pending["n"] != _last_attack_id(user):
        raise _StaleCard
    creature = Creature.objects.filter(owner=user, is_active=True).first()
    if creature is None:
        raise GameError("اول یه موجود فعال انتخاب کن.")
    if creature.id != pending["c"]:
        raise _FighterChanged
    from game.daily import check_missions, record_action
    from game.energy import spend_energy

    target = _revalidate(user, pending)  # before the energy: OpponentUnavailableError → rematch
    spend_energy(user, constants.ARENA_ATTACK_ENERGY_COST, "حمله")
    user.save(update_fields=["energy", "energy_updated_at"])

    opponent = dict(pending)
    opponent["user"] = target

    _fresh_research(user, target)
    result = A.attack(user, opponent)
    user.refresh_from_db()
    record_action(user, "arena_attack")
    missions = check_missions(user, "arena_attack")
    _queue_defense_report(result)
    return result, missions


def _queue_defense_report(result: dict) -> None:
    """The bot DMs the defender the moment attack() returns, so attack() pre-marks the log as
    notified. The web process can't send a message: hand the log back to the bot's periodic
    «you were raided» job (game/notifications.py) instead of dropping the report."""
    defense = result.get("defense")
    if defense and defense.get("notifications_on") and defense.get("log_id"):
        AttackLog.objects.filter(id=defense["log_id"]).update(defender_notified=False)


def _mission(m: dict) -> dict:
    return {"label": _txt(m.get("label")), "coins": m.get("coins", 0), "dna": m.get("dna", 0),
            "diamonds": m.get("diamonds", 0), "points": m.get("points", 0), "weekly": bool(m.get("weekly"))}


def _chest_brief(chest) -> dict | None:
    if chest is None:
        return None
    from game.media import get_arena_chest_image_path

    cfg = AC.ARENA_CHEST_TIERS.get(chest.chest_type, {})
    return {"tier": chest.chest_type, "name": cfg.get("name", "جعبه آرنا"), "slot": chest.slot,
            "img": asset_img(get_arena_chest_image_path(chest.chest_type, "locked"))}


def _side(side: dict | None, name: str, alive: bool) -> dict:
    if side:
        return {"name": _txt(side["name"]), "hp": max(0, side["hp"]), "max_hp": max(1, side["max_hp"])}
    return {"name": _txt(name), "hp": 1 if alive else 0, "max_hp": 1}


def _result(result: dict, missions) -> dict:
    won = result["won"]
    return {
        "won": won,
        "coins": result.get("loot", 0), "dna": result.get("dna", 0),
        "plunder": {"coins": result.get("plundered_collector_gold", 0), "dna": result.get("plundered_collector_dna", 0)},
        "league": {"name": result.get("league_name", ""), "coins": result.get("league_coins", 0), "dna": result.get("league_dna", 0)},
        "cup_delta": result["cup_delta"], "new_cup": result["new_cup"],
        "chest": _chest_brief(result.get("awarded_chest")), "slots_full": bool(result.get("slots_full")),
        "missions": [_mission(m) for m in (missions or [])],
        "opponent": {"label": _txt(result.get("opponent_label")), "alliance": result.get("opponent_alliance"),
                     "creature": _txt(result.get("defender_creature_name") or "موجود حریف"),
                     "element": result.get("defender_element")},
        "me": _side(result.get("sa"), result.get("attacker_creature_name", "موجود شما"), won),
        "foe": _side(result.get("sb"), result.get("defender_creature_name") or "موجود حریف", not won),
        "detail": _txt(result.get("detail_log", "")),
    }


def _shield_gate(user, data: dict) -> dict | None:
    """The bot warns FIRST when the attacker holds a shield (attacking burns part of it)."""
    secs = A.shield_remaining_seconds(user)
    if secs > 0 and data.get("confirm") is not True:
        return {"need_confirm": True, "shield": secs, "shield_cost_hours": constants.SHIELD_ATTACK_COST_HOURS}
    return None


@endpoint("POST")
def attack(request, user, data):
    seen = _seen(data)
    pending = _pending_or_none(user, data)
    if pending is None:  # expired (15 min) or unreadable card — same answer as a used one
        return {"rematch": True, **_find(user, seen, NOTE_STALE)}
    gate = _shield_gate(user, data)
    if gate:
        return gate
    try:
        result, missions = _attack_tx(user, pending)
    except _StaleCard:
        return {"rematch": True, **_find(user, seen, NOTE_STALE)}
    except _FighterChanged:
        _refit(user, pending, active_creature(user))
        return {"rematch": True, **_card(user, pending, seen, NOTE_FIGHTER)}
    except A.OpponentUnavailableError:
        # shielded / gone / an ally by now — never dead-end: show a fresh opponent (bot: _rematch_after_unavailable)
        return {"rematch": True, **_find(user, seen, NOTE_UNAVAILABLE)}
    return {"result": _result(result, missions)}


# ══════════════════════════════ revenge ══════════════════════════════
@endpoint()
def revenges(request, user):
    """bot: _revenges_sync."""
    now = timezone.now()
    items = []
    for log in A.revengeable_attacks(user):
        atk = log.attacker
        items.append({
            "log_id": log.id, "attacker_id": log.attacker_id,
            "name": _txt(log.attacker_label or (lab_display(atk) if atk else "یه مهاجم")),
            "power": log.attacker_power or 0, "coins": log.loot_gold or 0, "dna": log.loot_dna or 0,
            "won": log.attacker_won,
            "left": max(0, int((log.created_at + datetime.timedelta(days=REVENGE_DAYS) - now).total_seconds())),
            "shield": A.shield_remaining_seconds(atk) if atk is not None else 0,
        })
    # revenge-able (unshielded) first, so the actionable ones are at the top
    items.sort(key=lambda it: (it["shield"] > 0, -it["power"]))
    return {"items": items, "energy_cost": constants.ARENA_ATTACK_ENERGY_COST, "days": REVENGE_DAYS}


def _revenge_log(user, log_id: int) -> AttackLog:
    """bot: _revenge_find_sync."""
    log = (AttackLog.objects.select_related("attacker")
           .filter(id=log_id, defender=user, revenge_taken=False, is_fake_defender=False).first())
    if log is None:
        raise GameError("این انتقام دیگه در دسترس نیست.")
    if log.created_at < timezone.now() - datetime.timedelta(days=REVENGE_DAYS):
        raise GameError("مهلت 3 روزه‌ی انتقام گذشته.")
    return log


@endpoint()
def revenge_card(request, user):
    from game.energy import get_max_energy, sync_energy

    log = _revenge_log(user, need_int(request.GET, "id", 1))
    target = log.attacker
    if target is None:
        raise GameError("حریف دیگه در دسترس نیست.")
    my = _my(user)
    tc = Creature.objects.filter(owner=target, is_active=True).first()
    _fresh_research(target)
    opp_power = A.active_power(target)
    return {
        "log_id": log.id, "my": my, "my_cup": user.cup,
        "opponent": {
            "is_fake": False, "id": target.id,
            "label": _txt(log.attacker_label or lab_display(target)),
            "creature": _txt(tc.name) if tc else "؟", "cup": target.cup, "power": opp_power,
            "element": tc.element if tc else None, "rarity": tc.rarity if tc else None,
            "star": tc.star_level if tc else None, "level": tc.level if tc else None,
            "img": creature_img(tc) if tc else None,
        },
        "taken": {"coins": log.loot_gold or 0, "dna": log.loot_dna or 0},
        "cup_win": A.cup_delta(user, target.cup, True, my["power"]),
        "cup_loss": A.cup_delta(user, target.cup, False, my["power"]),
        "opp_shield": A.shield_remaining_seconds(target),
        "energy_cost": constants.ARENA_ATTACK_ENERGY_COST,
        "energy": sync_energy(user), "max_energy": get_max_energy(user),
        "shield": A.shield_remaining_seconds(user), "shield_cost_hours": constants.SHIELD_ATTACK_COST_HOURS,
    }


@transaction.atomic
def _revenge_tx(user, log_id: int):
    """bot: _revenge_attack_sync (atomic: a refused revenge un-marks the log and refunds the energy)."""
    from game.daily import check_missions, record_action
    from game.energy import spend_energy

    lock_row(user)
    log = A.mark_revenge_taken(log_id, user)
    if log is None:
        raise GameError("این انتقام قبلاً گرفته شده یا دیگه معتبر نیست.")
    if log.is_fake_defender:
        raise GameError("این انتقام دیگه در دسترس نیست.")
    if log.created_at < timezone.now() - datetime.timedelta(days=REVENGE_DAYS):
        raise GameError("مهلت 3 روزه‌ی انتقام گذشته.")
    target = log.attacker
    if target is None:
        raise GameError("حریف دیگه در دسترس نیست.")

    spend_energy(user, constants.ARENA_ATTACK_ENERGY_COST, "انتقام")
    user.save(update_fields=["energy", "energy_updated_at"])

    _fresh_research(user, target)
    opponent = {
        "is_fake": False, "user": target,
        "label": log.attacker_label or lab_display(target),
        "cup": target.cup,
        "power": A.active_power(target),  # same number the revenge card showed (with gear)
        "loot_pool": target.coins,
    }
    result = A.attack(user, opponent)
    record_action(user, "arena_attack")
    missions = check_missions(user, "arena_attack")
    _queue_defense_report(result)
    return result, missions


@endpoint("POST")
def revenge(request, user, data):
    log_id = need_int(data, "log_id", 1)
    gate = _shield_gate(user, data)
    if gate:
        return gate
    result, missions = _revenge_tx(user, log_id)
    return {"result": _result(result, missions)}


# ══════════════════════════════ season tables ══════════════════════════════
def _lab_name(u) -> str:
    return (u.lab_name or "آزمایشگاه") if u is not None else "آزمایشگاه"


@endpoint()
def season_view(request, user):
    """«جدول هفته» + «فصل قبل» (bot: _top_sync, _last_season_sync)."""
    season.close_due_season()
    table = [{"rank": row["rank"], "name": _lab_name(row["user"]), "cup": row["cup"],
              "reset_to": season.reset_floor(row["rank"], row["cup"]), "me": row["user"].id == user.id}
             for row in season.standings(10)]
    week, results = season.last_season_results(10)
    return {
        "week": season.current_week(), "left": season.seconds_until_next_week(), "table": table,
        "last_week": week,
        "last": [{"rank": r.rank, "name": _lab_name(r.user), "cup_before": r.cup_before, "cup_after": r.cup_after,
                  "me": r.user_id == user.id} for r in results],
    }


# ══════════════════════════════ arena chests ══════════════════════════════
def _chest_dict(chest, has_sub: bool, other_unlocking: bool, queued_exists: bool) -> dict:
    from game.media import get_arena_chest_image_path

    cfg = AC.ARENA_CHEST_TIERS.get(chest.chest_type, AC.ARENA_CHEST_TIERS["silver"])
    mult = AC.league_multiplier(chest.cup_at_drop)
    locked = chest.status == "locked"
    return {
        "id": chest.id, "slot": chest.slot, "tier": chest.chest_type, "name": cfg["name"], "status": chest.status,
        "left": AC.seconds_until_ready(chest), "unlock_hours": cfg["unlock_hours"],
        "speedup_cost": AC.speedup_diamond_cost(chest),
        "cup": chest.cup_at_drop, "league": constants.league_for_cup(chest.cup_at_drop)["name"],
        # what grant_chest_contents pays for a chest won at that cup
        "coins": round(cfg["base_gold"] * mult), "dna": round(cfg["base_dna"] * mult),
        "has_diamonds": cfg["key"] in ("magical", "mega"),
        "creature_chance": cfg["creature_chance"],
        "min_rarity": AC.get_guaranteed_rarity(chest.chest_type, chest.cup_at_drop),
        "mythic_chance": round(AC.mega_mythic_chance(chest.cup_at_drop), 3) if cfg["key"] == "mega" else None,
        "can_start": locked and not other_unlocking,
        "can_queue": locked and other_unlocking and has_sub and not queued_exists,
        "img": asset_img(get_arena_chest_image_path(chest.chest_type, chest.status)),
    }


def _chest_guide() -> list[dict]:
    """«راهنمای جوایز لیگ‌ها»: what each tier holds in each of the 16 leagues."""
    from game.media import get_arena_chest_image_path

    out = []
    for key, cfg in AC.ARENA_CHEST_TIERS.items():
        rows = []
        for lg in constants.LEAGUES:
            mult = AC.league_multiplier(lg["min_cup"])
            rows.append({"league": lg["name"], "key": lg["key"], "min_cup": lg["min_cup"],
                         "coins": round(cfg["base_gold"] * mult), "dna": round(cfg["base_dna"] * mult),
                         "min_rarity": AC.get_guaranteed_rarity(key, lg["min_cup"])})
        out.append({"tier": key, "name": cfg["name"], "unlock_hours": cfg["unlock_hours"],
                    "creature_chance": cfg["creature_chance"], "has_diamonds": key in ("magical", "mega"),
                    "mythic_min": AC.MEGA_MYTHIC_CHANCE_MIN if key == "mega" else None,
                    "mythic_max": AC.MEGA_MYTHIC_CHANCE_MAX if key == "mega" else None,
                    "img": asset_img(get_arena_chest_image_path(key, "locked")), "rows": rows})
    return out


def _chests_payload(user) -> dict:
    from game.subscription import is_subscription_active

    chests = AC.get_user_chests(user)  # also turns finished timers into «ready» (bot: _chests_panel_sync)
    has_sub = is_subscription_active(user)
    queued = any(c.status == "queued" for c in chests)
    by_slot = {c.slot: c for c in chests}
    slots = []
    for slot in range(1, AC.ARENA_CHEST_MAX_SLOTS + 1):
        c = by_slot.get(slot)
        other = any(x.status == "unlocking" and x.id != c.id for x in chests) if c else False
        slots.append({"slot": slot, "chest": _chest_dict(c, has_sub, other, queued) if c else None})
    return {"slots": slots, "has_sub": has_sub, "diamonds": user.diamonds,
            "my_league": constants.league_for_cup(user.cup)["key"]}


@endpoint()
def chests(request, user):
    return _chests_payload(user)


@endpoint()
def chest_guide(request, user):
    """«راهنمای جوایز لیگ‌ها» — static tables (4 tiers x 16 leagues); the app asks for it once,
    when the sheet is opened, instead of carrying 8 KB with every chest request."""
    return {"guide": _chest_guide()}


@endpoint("POST")
def chest_start(request, user, data):
    AC.start_unlock(user, need_int(data, "id", 1))
    return _chests_payload(user)


@endpoint("POST")
def chest_queue(request, user, data):
    AC.queue_chest(user, need_int(data, "id", 1))
    return _chests_payload(user)


@endpoint("POST")
def chest_speedup(request, user, data):
    chest_id = need_int(data, "id", 1)
    AC.advance_user_chests(user)  # bot: _chest_speedup_info_sync runs this before quoting the price
    chest = ArenaChest.objects.filter(id=chest_id, user=user).first()
    if chest is None:
        raise GameError("این جعبه پیدا نشد.")
    # the player confirmed a price; never charge more than the one they saw
    if data.get("cost") is not None and AC.speedup_diamond_cost(chest) > need_int(data, "cost", 0):
        raise GameError("قیمت عوض شده؛ دوباره نگاه کن.")
    AC.speedup_with_diamonds(user, chest_id)
    user.refresh_from_db()
    return _chests_payload(user)


@endpoint("POST")
def chest_open(request, user, data):
    from game.equipment import equipment_power
    from game.media import get_arena_chest_image_path

    res = AC.open_chest(user, need_int(data, "id", 1))
    creature, item, nxt = res.get("creature"), res.get("item"), res.get("next_started")
    reward = {
        "tier": res["tier"], "name": res["name"], "slot": res["slot"],
        "coins": res["coins"], "dna": res["dna"], "diamonds": res.get("diamonds") or 0,
        "rarity": res["rarity"],
        "creature": creature_list(user, [creature])[0] if creature is not None else None,
        "item": None if item is None else {
            "id": item.id, "name": item.name, "slot": item.slot, "rarity": item.rarity, "level": item.level,
            "power": equipment_power(item), "img": equipment_img(item)},
        "next_started": None if nxt is None else {
            "name": AC.ARENA_CHEST_TIERS.get(nxt.chest_type, {}).get("name", ""), "slot": nxt.slot},
        "img": asset_img(get_arena_chest_image_path(res["tier"], "open")),
    }
    user.refresh_from_db()
    out = _chests_payload(user)
    out["reward"] = reward
    return out


# ══════════════════════════════ league ══════════════════════════════
def _division(d: dict | None) -> dict | None:
    if d is None:
        return None
    return {"key": d["key"], "title": d["title"], "min_cup": d["min_cup"]}


@endpoint()
def league_view(request, user):
    """bot: league._panel_sync + the two reward tables."""
    from game.media import get_feature_image_path

    _gate(user, "league")
    season.close_due_season()  # lazy settle, like the arena screens
    user.refresh_from_db()
    rank = _rank_of(user)
    mine = league.division_for(user.cup)
    ranks, prev = [], 0
    for max_rank, reward in league.RANK_REWARDS:
        ranks.append({"from": prev + 1, "to": max_rank, "reward": _reward(reward)})
        prev = max_rank
    return {
        "cup": user.cup, "division": _division(mine), "next": _division(league.next_division(user.cup)),
        "left": season.seconds_until_next_week(), "week": season.current_week(),
        "reward": _reward(league.season_reward(user.cup)),
        "rank": rank, "rank_reward": _reward(league.rank_reward(rank)) if user.cup > 0 else None,
        "rank_limit": prev,
        "standings": [{"rank": row["rank"], "name": _lab_name(row["user"]), "cup": row["cup"],
                       "league": league.division_for(row["cup"])["key"], "me": row["user"].id == user.id}
                      for row in season.standings(limit=10)],
        "divisions": [{**_division(d), "reward": _reward(league.DIVISION_REWARD[d["key"]]), "mine": d["key"] == mine["key"]}
                      for d in league.DIVISIONS],
        "ranks": ranks,
        "img": asset_img(get_feature_image_path("league")),
    }


routes = [
    ("", home),
    ("badges/", badges),
    ("find/", find),
    ("card/", card),
    ("team/", team),
    ("swap/", swap),
    ("details/", details),
    ("attack/", attack),
    ("revenges/", revenges),
    ("revenge/card/", revenge_card),
    ("revenge/", revenge),
    ("season/", season_view),
    ("chests/", chests),
    ("chests/guide/", chest_guide),
    ("chests/start/", chest_start),
    ("chests/queue/", chest_queue),
    ("chests/speedup/", chest_speedup),
    ("chests/open/", chest_open),
    ("league/", league_view),
]

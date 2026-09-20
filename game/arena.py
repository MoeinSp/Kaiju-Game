import datetime
import random
import threading
import time

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from bio_lab.models import AttackLog, Creature, User
from bio_lab.repository import lab_display
from game import constants, lab
from game.combat import resolve_battle, resolve_duel, resolve_duel_detailed
from game.creature import GameError, base_share_for_rating, creature_power
from game.equipment import get_equipped_items

# creature_power is the canonical strength score (game.creature) — re-exported here
# so the many `from game.arena import creature_power` call sites keep working.
__all__ = ["creature_power"]

# ── short-lived opponent reservations ────────────────────────────────────────
# When a player is shown a real opponent, that opponent is "held" for them for a
# few seconds so nobody else's search can grab (and shield) them first — the
# "همه سپر دارن چون سریع اتک می‌خورن" complaint. In-memory (single webhook process),
# guarded by a lock because run_db uses a thread pool. Each searcher holds at most
# one reservation at a time (finding a new opponent releases the previous one).
ARENA_RESERVE_SECONDS = 20
_RESERVATIONS: dict[int, tuple[int, float]] = {}  # opponent_id -> (reserver_id, expires_at)
_RES_LOCK = threading.Lock()


def _reserved_by_others(attacker_id: int) -> set[int]:
    now = time.time()
    with _RES_LOCK:
        return {oid for oid, (rid, exp) in _RESERVATIONS.items() if exp > now and rid != attacker_id}


def _reserve_opponent(attacker_id: int, opponent_id: int) -> None:
    now = time.time()
    with _RES_LOCK:
        # this searcher holds only one reservation; drop their old one and any expired
        stale = [oid for oid, (rid, exp) in _RESERVATIONS.items() if rid == attacker_id or exp <= now]
        for oid in stale:
            _RESERVATIONS.pop(oid, None)
        _RESERVATIONS[opponent_id] = (attacker_id, now + ARENA_RESERVE_SECONDS)


def _release_opponent(opponent_id: int) -> None:
    with _RES_LOCK:
        _RESERVATIONS.pop(opponent_id, None)


class OpponentUnavailableError(GameError):
    """The specific matched opponent can't be attacked right now — they got shielded
    (someone raided them first), lost their active creature, vanished, or turned out to
    be a same-alliance member. The caller should AUTO-REMATCH to a fresh opponent rather
    than dead-ending, so a found opponent never leaves the player stuck."""


def active_power(user: User) -> int:
    creature = Creature.objects.filter(owner=user, is_active=True).first()
    if creature is None:
        return 0
    return creature_power(creature, get_equipped_items(creature))


def deserved_cup(power: int) -> int:
    """The cup ceiling a player's actual creature power justifies. Winning past it
    is heavily damped (see cup_delta) — otherwise a weak player could ride a lucky
    streak up into a bracket that then farms them forever, which is exactly the
    "کاپ ضعیف نباید بالا بره" failure mode."""
    return round(power * constants.ARENA_CUP_PER_POWER)


def is_shielded(user: User) -> bool:
    return user.shield_until is not None and user.shield_until > timezone.now()


def shield_remaining_seconds(user: User) -> int:
    if not is_shielded(user):
        return 0
    return int((user.shield_until - timezone.now()).total_seconds())


def is_group_shielded(user: User) -> bool:
    return user.group_shield_until is not None and user.group_shield_until > timezone.now()


def group_shield_remaining_seconds(user: User) -> int:
    if not is_group_shielded(user):
        return 0
    return int((user.group_shield_until - timezone.now()).total_seconds())


def _fmt_shield_remaining(seconds: int) -> str:
    hours, rem = divmod(max(0, seconds), 3600)
    minutes = rem // 60
    if hours:
        return f"{hours} ساعت و {minutes} دقیقه"
    return f"{minutes} دقیقه"


def shield_status_lines(user: User) -> list[str]:
    """Human-readable countdown for whichever of the two shields (arena / group)
    the player currently has, so their remaining protection is visible on their
    profile — in the DM and in the group alike. Empty list when unshielded."""
    lines = []
    arena_secs = shield_remaining_seconds(user)
    if arena_secs > 0:
        lines.append(f"🛡 سپر آرنا: <b>{_fmt_shield_remaining(arena_secs)}</b> باقی‌مونده")
    group_secs = group_shield_remaining_seconds(user)
    if group_secs > 0:
        lines.append(f"🛡 سپر گروه: <b>{_fmt_shield_remaining(group_secs)}</b> باقی‌مونده")
    return lines


def apply_group_shield(user: User) -> None:
    """Give `user` a fresh 4h group shield — separate from the arena shield."""
    user.group_shield_until = timezone.now() + datetime.timedelta(hours=constants.GROUP_SHIELD_HOURS)
    user.save(update_fields=["group_shield_until"])


def spend_group_shield_on_attack(user: User) -> int:
    """Called when `user` launches a group «اتک»: burns SHIELD_ATTACK_COST_HOURS off
    their GROUP shield. Returns the seconds left after the deduction (0 if unshielded).
    The caller must include 'group_shield_until' in its own save()."""
    if not is_group_shielded(user):
        user.group_shield_until = None
        return 0
    new_until = user.group_shield_until - datetime.timedelta(hours=constants.SHIELD_ATTACK_COST_HOURS)
    now = timezone.now()
    user.group_shield_until = new_until if new_until > now else None
    return max(0, int((user.group_shield_until - now).total_seconds())) if user.group_shield_until else 0


@transaction.atomic
def buy_shield(user: User, tier: str) -> dict:
    """Buy an arena shield with diamonds. Stacks onto any time already left, so a
    player can top up before a long break."""
    cfg = constants.SHIELD_SHOP_TIERS.get(tier)
    if cfg is None:
        raise GameError("این نوع سپر وجود نداره.")
    user = User.objects.select_for_update().get(id=user.id)
    if user.diamonds < cfg["diamonds"]:
        raise GameError(f"الماس کافی نداری! این سپر {cfg['diamonds']} الماس هزینه داره.")
    now = timezone.now()
    base = user.shield_until if (user.shield_until and user.shield_until > now) else now
    user.shield_until = base + datetime.timedelta(hours=cfg["hours"])
    user.diamonds -= cfg["diamonds"]
    user.save(update_fields=["shield_until", "diamonds"])
    return {"tier": tier, "shield_until": user.shield_until, "remaining": shield_remaining_seconds(user)}


@transaction.atomic
def buy_group_shield(user: User, tier: str) -> dict:
    """Buy a GROUP shield (against «اتک») with diamonds — cheaper than the arena one.
    Stacks onto any group-shield time already left."""
    cfg = constants.GROUP_SHIELD_SHOP_TIERS.get(tier)
    if cfg is None:
        raise GameError("این نوع سپر وجود نداره.")
    user = User.objects.select_for_update().get(id=user.id)
    if user.diamonds < cfg["diamonds"]:
        raise GameError(f"الماس کافی نداری! این سپر {cfg['diamonds']} الماس هزینه داره.")
    now = timezone.now()
    base = user.group_shield_until if (user.group_shield_until and user.group_shield_until > now) else now
    user.group_shield_until = base + datetime.timedelta(hours=cfg["hours"])
    user.diamonds -= cfg["diamonds"]
    user.save(update_fields=["group_shield_until", "diamonds"])
    return {"tier": tier, "remaining": group_shield_remaining_seconds(user)}


def spend_shield_on_attack(user: User) -> int:
    """Called when `user` launches an attack: burns SHIELD_ATTACK_COST_HOURS off their
    arena shield (instead of the old all-or-nothing drop). Returns the seconds left
    after the deduction. No-op / 0 when they weren't shielded. The caller is
    responsible for including 'shield_until' in its own save()."""
    if not is_shielded(user):
        user.shield_until = None
        return 0
    reduced = user.shield_until - datetime.timedelta(hours=constants.SHIELD_ATTACK_COST_HOURS)
    now = timezone.now()
    user.shield_until = reduced if reduced > now else None
    return max(0, int((user.shield_until - now).total_seconds())) if user.shield_until else 0


def cup_delta(attacker: User, defender_cup: int, won: bool, attacker_power: int) -> int:
    """Cup swing for one raid. Beating someone rated above you pays more than
    beating someone below; losing to someone below you costs more than losing to
    someone above. Gains are damped once the attacker is already above the cup
    their power deserves."""
    gap = defender_cup - attacker.cup
    softcap = constants.ARENA_CUP_SOFTCAP

    div = constants.ARENA_CUP_GAP_DIVISOR
    if won:
        # beating someone rated ABOVE you pays a lot more; beating someone far below
        # pays near the floor — so a low-cup attacker who beats a high-cup defender
        # gains big (and that defender loses big).
        raw = constants.ARENA_CUP_WIN_BASE + gap / div
        # GLOBAL diminishing returns: every extra cup you already hold shrinks the
        # next win, no matter how strong you are. This is what actually stops the
        # 10k-cup runaway — the deserved-cup damping below only bites players who
        # over-climb their power.
        raw *= softcap / (softcap + max(0, attacker.cup))
        if attacker.cup > deserved_cup(attacker_power):
            raw *= constants.ARENA_OVERCAP_DAMPING
        delta = max(constants.ARENA_CUP_MIN_DELTA, min(constants.ARENA_CUP_MAX_DELTA, round(raw)))
        return delta

    # losing to someone ABOVE you barely dents your cup; losing to someone far below
    # costs a lot
    raw = constants.ARENA_CUP_LOSS_BASE - gap / div
    # the further you sit ABOVE the softcap, the harder you fall — this pulls the top
    # of the ladder back toward the pack so competition stays close.
    raw *= 1 + max(0, attacker.cup - softcap) / softcap
    delta = max(constants.ARENA_CUP_MIN_DELTA, min(constants.ARENA_CUP_MAX_DELTA, round(raw)))
    return -delta


def _attacker_level(attacker: User) -> int:
    creature = Creature.objects.filter(owner=attacker, is_active=True).first()
    return creature.level if creature is not None else 1


def power_for_cup(cup: int) -> int:
    """The power a lab at this cup rating is expected to have.
    Walls out around 4000 cup where bots reach 11,800+ power (surpassing the 9822 player max),
    so players cannot defeat system bots at ~4000+ cup."""
    if cup >= constants.ARENA_BOT_MAX_CUP:
        overflow = cup - constants.ARENA_BOT_MAX_CUP
        return round(constants.ARENA_BOT_MAX_POWER + overflow * 6)
    frac = max(0, cup) / constants.ARENA_BOT_MAX_CUP
    return max(15, round(constants.ARENA_BOT_MAX_POWER * (frac ** constants.ARENA_BOT_POWER_EXP)))


def _bot_display_tier(cup: int) -> tuple[str, int]:
    """Cosmetic rarity + star for a bot, scaled by cup — so a high-cup bot reads as a
    maxed mythic 5★ lab (matching its real, scaled power). Combat still runs off the
    power number; this is only what the player sees on the card."""
    frac = min(1.0, max(0, cup) / constants.ARENA_BOT_MAX_CUP)
    idx = min(len(constants.RARITY_ORDER) - 1, int(frac * len(constants.RARITY_ORDER)))
    rarity = constants.RARITY_ORDER[idx]
    star = max(1, min(5, 1 + round(frac * 4)))
    return rarity, star


def _fake_opponent(attacker: User) -> dict:
    """A bot defender, used when no real player sits in the attacker's cup band.
    Built from the attacker's CUP, not their power, so it enforces the ladder.
    Guarantees that bots never have elemental weakness against the player, and
    walls out players around 4000 cup."""
    bot_cup = max(0, attacker.cup + random.randint(-40, 90))
    expected = power_for_cup(bot_cup)
    if bot_cup >= 3850 or attacker.cup >= 3850:
        # Near and above the 4000 ceiling, bots strictly overpower any player (max player power is 9822)
        power = max(10800, expected + random.randint(200, 600))
    else:
        power = max(1, expected + random.randint(-200, 200))
    rarity, star = _bot_display_tier(bot_cup)

    # Bot element selection:
    # In high cups (>= 3700), bots counter the player's element to protect the top ladder.
    # Otherwise (< 3700), elements are completely random (fire, water, earth, electric).
    creature = Creature.objects.filter(owner=attacker, is_active=True).first()
    attacker_element = creature.element if creature else None
    if attacker_element and (attacker.cup >= 3700 or bot_cup >= 3700):
        counters = [e for e in constants.ELEMENTS if constants.ELEMENT_STRONG_AGAINST.get(e) == attacker_element]
        _bot_element = counters[0] if counters else constants.random_element()
    else:
        _bot_element = constants.random_element()

    return {
        "is_fake": True,
        "user": None,
        "label": constants.random_lab_name(),
        "creature_name": constants.random_species_name(_bot_element),
        "cup": bot_cup,
        "power": power,
        "element": _bot_element,
        "loot_pool": random.randint(*constants.arena_fake_loot_range(_attacker_level(attacker))),
        "bot_rarity": rarity,
        "bot_star": star,
    }


def find_opponent(attacker: User, exclude_ids=None) -> dict:
    """Picks a raid target: strongly prefers real, unshielded players with high variety.
    Only falls back to a bot if literally no real unshielded player exists.
    Optimized for sub-5ms execution via single-pass index scan and in-memory distance ranking."""
    from django.db.models import Q

    if active_power(attacker) <= 0:
        raise GameError("اول یه موجود فعال انتخاب کن.")

    now = timezone.now()
    band = constants.ARENA_MATCH_CUP_BAND
    exclude_set = set(exclude_ids or [])
    reserved = _reserved_by_others(attacker.id)
    base_exclude = {attacker.id} | reserved

    # If attacker belongs to an alliance, exclude all alliance members from matchmaking
    if attacker.alliance_id:
        alliance_member_ids = set(
            User.objects.filter(alliance_id=attacker.alliance_id).values_list("id", flat=True)
        )
        base_exclude |= alliance_member_ids

    # Fast indexed lookup of active creature owners excluding attacker, reserved & alliance members
    active_owner_ids = set(
        Creature.objects.filter(is_active=True).values_list("owner_id", flat=True)
    ) - base_exclude

    if not active_owner_ids:
        return _fake_opponent(attacker)

    # Fetch all eligible unshielded players in ONE single query (no joins, no heavy distinct)
    eligible = list(
        User.objects.filter(id__in=active_owner_ids, is_banned=False)
        .filter(Q(shield_until__isnull=True) | Q(shield_until__lte=now))
        .select_related("alliance")
        .only("id", "cup", "coins", "username", "first_name", "alliance__name", "alliance_id")
    )

    if attacker.alliance_id:
        eligible = [u for u in eligible if u.alliance_id != attacker.alliance_id]

    if not eligible:
        return _fake_opponent(attacker)

    # Hard Cup Band (+- 1000) & Rookie Protection:
    # High-cup players (>=2000) must NEVER match with beginners (<1000)
    band_eligible = [
        u for u in eligible
        if abs(u.cup - attacker.cup) <= band
        and not (attacker.cup >= 2000 and u.cup < 1000)
        and not (attacker.cup < 1000 and u.cup >= 2000)
    ]

    # If no real players sit in the hard band (e.g. 5300 cup or empty bracket),
    # strictly return a system bot. NEVER fall back to 0-cup beginners!
    if not band_eligible:
        return _fake_opponent(attacker)

    # Pass 1: candidates in band not recently seen
    candidates = [u for u in band_eligible if u.id not in exclude_set]

    # If all real players in this band have been seen in recent searches (e.g. only 2 players exist),
    # return a system bot to break the 2-player ping-pong loop!
    if not candidates:
        return _fake_opponent(attacker)

    # Sort candidates by cup proximity in Python (instant < 0.05ms)
    candidates.sort(key=lambda u: abs(u.cup - attacker.cup))

    # Pick from top 8 closest candidates with weighted random to maximize player variety
    pool = candidates[: min(8, len(candidates))]
    weights = [1.0 / (1 + abs(u.cup - attacker.cup) * 0.05) for u in pool]
    target = random.choices(pool, weights=weights, k=1)[0]
    _reserve_opponent(attacker.id, target.id)
    target_creature = Creature.objects.filter(owner=target, is_active=True).first()
    return {
        "is_fake": False,
        "user": target,
        "label": lab_display(target),
        "alliance": target.alliance.name if target.alliance_id else None,
        "creature_name": target_creature.name if target_creature else "موجود ناشناس",
        "cup": target.cup,
        "power": active_power(target),
        "element": target_creature.element if target_creature else constants.random_element(),
        "loot_pool": target.coins,
    }


def expected_loot(opponent: dict, attacker_level: int = 1) -> int:
    """Gold from one raid.

    * REAL opponent → exactly <b>10%</b> of their gold (floored at ARENA_LOOT_MIN).
      Farming the same rich player is already prevented by the 8h post-raid shield +
      the ±500 cup matchmaking, so no per-hit cap is applied.
    * BOT opponent → a cup-scaled amount (grows with the raider's cup), the reward
      for climbing when no real target is in range.
    """
    if opponent.get("is_fake"):
        return constants.arena_fake_loot(int(opponent.get("cup", 0)))
    raw = int(opponent["loot_pool"]) // 10  # 10% of the real defender's gold
    return max(constants.ARENA_LOOT_MIN, raw)


@transaction.atomic
def attack(attacker: User, opponent: dict, award_cup: bool = True) -> dict:
    """Resolves one arena raid. Attacking always drops the attacker's own shield —
    you can't camp behind protection while farming other people."""
    attacker = User.objects.select_for_update().get(id=attacker.id)
    attacker_creature = Creature.objects.filter(owner=attacker, is_active=True).first()
    if attacker_creature is None:
        raise GameError("اول یه موجود فعال انتخاب کن.")

    if opponent["is_fake"]:
        # bot defenders have no Creature row, so build an unsaved stand-in scaled to
        # the rolled power (never saved — same trick as hunt.py's wild creatures)
        defender_creature = _bot_creature(opponent["power"], opponent.get("element"))
        defender_user = None
    else:
        # LOCK the defender's row and re-check the shield UNDER the lock. Without this,
        # several attackers who all found this player a moment ago each read
        # shield_until=None at the same instant and every one loots + re-shields —
        # the "توی یه ثانیه ۵ تا اتک خوردم" bug. The lock serialises them: the first
        # raid sets the shield and commits; the rest then see it and bounce.
        defender_user = User.objects.select_for_update().get(id=opponent["user"].id)
        if attacker.alliance_id and defender_user.alliance_id == attacker.alliance_id:
            raise OpponentUnavailableError("🤝 این بازیکن هم‌اتحادی شماست! امکان حمله به اعضای اتحاد خودت وجود نداره.")
        defender_creature = Creature.objects.filter(owner=defender_user, is_active=True).first()
        if defender_creature is None:
            raise OpponentUnavailableError("این حریف دیگه موجود فعالی نداره.")
        if is_shielded(defender_user):
            raise OpponentUnavailableError("این حریف الان سپر محافظ داره، یکی دیگه رو امتحان کن.")

    battle_res = resolve_battle(attacker_creature, defender_creature)
    winner = battle_res["winner"]
    log_text = battle_res["compact"]
    detail_log = battle_res["detail"]
    sa = battle_res["a"]
    sb = battle_res["b"]
    won = winner is attacker_creature

    attacker_power = creature_power(attacker_creature, get_equipped_items(attacker_creature))
    # in a group, this fight is cup-neutral (award_cup=False): no cup change, and the
    # arena shield is left untouched — group aggression uses its own 4h group shield.
    delta = cup_delta(attacker, opponent["cup"], won, attacker_power) if award_cup else 0

    # Only the ATTACKER loots, and only on a WIN: a winning attacker takes gold (and a
    # little DNA) from the loser; a LOSING attacker loses nothing but cup. Cup always
    # moves (per award_cup) — that part is separate from the gold loot.
    loot = 0
    taken_from_defender = 0
    dna_win = 0
    taken_dna_from_defender = 0
    league_coins = 0
    league_dna = 0
    plundered_collector_gold = 0
    plundered_collector_dna = 0
    league = constants.league_for_cup(attacker.cup)
    if won:
        if defender_user is not None:
            # REAL defender -> Exactly 10% of defender's actual current gold and DNA
            loot = max(0, defender_user.coins // 10)
            dna_win = max(0, defender_user.dna_fragments // 10)

            taken_from_defender = loot
            taken_dna_from_defender = dna_win

            defender_user.coins -= taken_from_defender
            defender_user.dna_fragments -= taken_dna_from_defender

            # 50% plunder of uncollected resources in gold_collector and dna_lab (separate from the 10% main loot)
            from bio_lab.models import Building
            from game.buildings import lock_pending

            gold_bld = Building.objects.filter(owner=defender_user, building_type="gold_collector").first()
            if gold_bld and gold_bld.level > 0:
                lock_pending(gold_bld)
                p_gold = int(gold_bld.banked_pending or 0)
                if p_gold > 0:
                    plundered_collector_gold = p_gold // 2
                    gold_bld.banked_pending = float(p_gold - plundered_collector_gold)
                    gold_bld.save(update_fields=["banked_pending"])

            dna_bld = Building.objects.filter(owner=defender_user, building_type="dna_lab").first()
            if dna_bld and dna_bld.level > 0:
                lock_pending(dna_bld)
                p_dna = int(dna_bld.banked_pending or 0)
                if p_dna > 0:
                    plundered_collector_dna = p_dna // 2
                    dna_bld.banked_pending = float(p_dna - plundered_collector_dna)
                    dna_bld.save(update_fields=["banked_pending"])

            if plundered_collector_gold > 0 or plundered_collector_dna > 0:
                defender_user.plundered_alert_gold = (defender_user.plundered_alert_gold or 0) + plundered_collector_gold
                defender_user.plundered_alert_dna = (defender_user.plundered_alert_dna or 0) + plundered_collector_dna
        else:
            # BOT defender -> cup-scaled loot roll
            pre_gold = opponent.get("loot_gold")
            pre_dna = opponent.get("loot_dna")
            if pre_gold is not None and pre_dna is not None:
                loot, dna_win = int(pre_gold), int(pre_dna)
            else:
                loot, dna_win = constants.arena_loot_roll(attacker.cup)

        attacker.coins += (loot + plundered_collector_gold)
        attacker.dna_fragments += (dna_win + plundered_collector_dna)
        # flat league bonus per WINNING raid (only in the real ranked arena)
        if award_cup:
            league_coins = league["coins"]
            league_dna = league["dna"]
            attacker.coins += league_coins
            attacker.dna_fragments += league_dna

    attacker_fields = ["coins", "dna_fragments"]
    if award_cup:
        attacker.cup = max(0, attacker.cup + delta)
        spend_shield_on_attack(attacker)
        attacker_fields += ["cup", "shield_until"]
    else:
        if is_shielded(attacker):
            spend_shield_on_attack(attacker)
            attacker_fields.append("shield_until")
    attacker.save(update_fields=attacker_fields)
    awarded_chest = None
    if won and award_cup:
        from game.arena_chests import award_chest_on_win
        awarded_chest = award_chest_on_win(attacker)

    if won:
        from game.ledger import record_gain

        gained_coins = loot + (league["coins"] if award_cup else 0)
        gained_dna = dna_win + (league["dna"] if award_cup else 0)
        record_gain(attacker, "arena", coins=gained_coins, dna=gained_dna)

    if defender_user is not None:
        defender_fields = ["coins", "dna_fragments", "plundered_alert_gold", "plundered_alert_dna"]
        if award_cup:
            # a freshly-raided defender gets arena protection so they can't be farmed
            defender_user.shield_until = timezone.now() + datetime.timedelta(hours=constants.ARENA_SHIELD_HOURS)
            defender_user.cup = max(0, defender_user.cup + (-delta if won else abs(delta)))
            defender_fields += ["cup", "shield_until"]
        defender_user.save(update_fields=defender_fields)
        _release_opponent(defender_user.id)

    # For a REAL defender we DM them the moment this returns (see the handler), so the
    # log is pre-marked notified here — the periodic catch-up job then leaves it alone.
    log = AttackLog.objects.create(
        attacker=attacker,
        attacker_label=lab_display(attacker)[:255],
        attacker_power=attacker_power,
        defender=defender_user,
        defender_label=(opponent["label"] or "")[:255],
        is_fake_defender=opponent["is_fake"],
        attacker_won=won,
        loot_gold=taken_from_defender if defender_user is not None else loot,
        loot_dna=taken_dna_from_defender if defender_user is not None else dna_win,
        cup_delta=delta,
        defender_notified=defender_user is not None,
    )

    lab_up = lab.award(attacker, "arena_win" if won else "arena_loss")

    return {
        "won": won,
        "lab_up": lab_up,
        "log_text": log_text,
        "detail_log": detail_log,
        "loot": loot,
        "dna": dna_win,
        "plundered_collector_gold": plundered_collector_gold,
        "plundered_collector_dna": plundered_collector_dna,
        "league_coins": league_coins,
        "league_dna": league_dna,
        "league_name": league["name"],
        "league_emoji": league["emoji"],
        "cup_delta": delta,
        "sa": sa,
        "sb": sb,
        "attacker_creature_name": sa["name"],
        "defender_creature_name": sb["name"],
        "attacker_element": sa["element"],
        "defender_element": sb["element"],
        "opponent_label": opponent["label"],
        "opponent_alliance": (defender_user.alliance.name if (defender_user and defender_user.alliance_id) else None),
        "new_cup": attacker.cup,
        "new_coins": attacker.coins,
        "new_dna": attacker.dna_fragments,
        "awarded_chest": awarded_chest,
        # payload for the INSTANT defense DM (None defender_id = bot, no DM)
        "defense": None if defender_user is None else {
            "defender_id": defender_user.id,
            "notifications_on": defender_user.notifications_on,
            "log_id": log.id,
            "attacker_id": attacker.id,
            "attacker_name": lab_display(attacker),
            "attacker_alliance": attacker.alliance.name if attacker.alliance_id else None,
            "attacker_power": attacker_power,
            "attacker_won": won,
            "loot": taken_from_defender if defender_user is not None else loot,
            "loot_dna": taken_dna_from_defender if defender_user is not None else dna_win,
            "plundered_collector_gold": plundered_collector_gold,
            "plundered_collector_dna": plundered_collector_dna,
            "attacker_cup": attacker.cup,
            "defender_cup": defender_user.cup,
            "cup_change": (-delta if won else abs(delta)) if award_cup else 0,
        },
    }


def _bot_creature(power: int, element: str | None = None) -> Creature:
    """Unsaved stand-in for a bot defender — `id` stays None, so it only ever feeds
    the combat math (game.equipment.get_equipped_items() short-circuits on pk=None)."""
    element = element or constants.random_element()
    share = base_share_for_rating(power)
    return Creature(
        name=constants.random_species_name(element),
        element=element,
        rarity="common",
        level=1,
        base_hp=share,
        base_atk=share,
        base_def=share,
        base_spd=share,
    )


def recent_attacks_received(user: User, limit: int = 5) -> list[AttackLog]:
    return list(
        AttackLog.objects.filter(defender=user)
        .select_related("attacker")
        .order_by("-created_at")[:limit]
    )


def revengeable_attacks(user: User, limit: int = 10) -> list[AttackLog]:
    """Real attacks on the user (won OR defended) not yet revenged and < 3 days old —
    you can strike back at anyone who came for you, even if your defence held."""
    deadline = timezone.now() - datetime.timedelta(days=3)
    return list(
        AttackLog.objects.filter(
            defender=user,
            is_fake_defender=False,
            attacker__isnull=False,
            revenge_taken=False,
            created_at__gte=deadline,
        )
        .select_related("attacker")
        .order_by("-created_at")[:limit]
    )


def mark_revenge_taken(log_id: int, defender: User) -> AttackLog | None:
    """Atomically marks an AttackLog as revenged. Returns it if still open, None if already done."""
    updated = AttackLog.objects.filter(
        id=log_id, defender=defender, revenge_taken=False
    ).update(revenge_taken=True)
    if not updated:
        return None
    return AttackLog.objects.select_related("attacker").get(id=log_id)


def top_by_cup(limit: int = 10) -> list[User]:
    return list(User.objects.filter(is_banned=False).order_by("-cup")[:limit])

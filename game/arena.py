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
from game.combat import resolve_duel, resolve_duel_detailed
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
        return 0
    new_until = user.group_shield_until - datetime.timedelta(hours=constants.SHIELD_ATTACK_COST_HOURS)
    now = timezone.now()
    user.group_shield_until = new_until if new_until > now else now
    return max(0, int((user.group_shield_until - now).total_seconds()))


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

    # Bot element selection: MUST NOT have elemental weakness against player!
    creature = Creature.objects.filter(owner=attacker, is_active=True).first()
    attacker_element = creature.element if creature else None
    if attacker_element:
        # The element that attacker beats
        weak_to_attacker = constants.ELEMENT_STRONG_AGAINST.get(attacker_element)
        safe_elements = [e for e in constants.ELEMENTS if e != weak_to_attacker]
        if attacker.cup >= 3700:
            # At high cups, the bot specifically chooses the counter element that beats the player
            counters = [e for e in constants.ELEMENTS if constants.ELEMENT_STRONG_AGAINST.get(e) == attacker_element]
            _bot_element = counters[0] if counters else random.choice(safe_elements)
        else:
            _bot_element = random.choice(safe_elements)
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

    # Fast indexed lookup of active creature owners excluding attacker & reserved
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
        defender_creature = Creature.objects.filter(owner=defender_user, is_active=True).first()
        if defender_creature is None:
            raise GameError("این حریف دیگه موجود فعالی نداره.")
        if is_shielded(defender_user):
            raise GameError("این حریف الان سپر محافظ داره، یکی دیگه رو امتحان کن.")

    winner, log_text, detail_log = resolve_duel_detailed(attacker_creature, defender_creature)
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
    league_coins = 0
    league_dna = 0
    league = constants.league_for_cup(attacker.cup)
    if won:
        loot = expected_loot(opponent, attacker_creature.level)
        if defender_user is not None:
            taken_from_defender = min(loot, max(0, defender_user.coins))
            defender_user.coins -= taken_from_defender
            loot = max(taken_from_defender, constants.ARENA_LOOT_MIN)
        attacker.coins += loot
        dna_win = round(constants.ARENA_WIN_DNA_BASE + attacker_creature.level * constants.ARENA_WIN_DNA_PER_LEVEL)
        attacker.dna_fragments += dna_win
        # flat league bonus per WINNING raid (only in the real ranked arena)
        if award_cup:
            league_coins = league["coins"]
            league_dna = league["dna"]
            attacker.coins += league_coins
            attacker.dna_fragments += league_dna

    attacker_fields = ["coins", "dna_fragments"]
    if award_cup:
        attacker.cup = max(0, attacker.cup + delta)
        # raiding spends 8h off your shield (not the whole thing anymore), so a
        # bought shield lets you attack a handful of times before it's gone
        spend_shield_on_attack(attacker)
        attacker_fields += ["cup", "shield_until"]
    attacker.save(update_fields=attacker_fields)
    if won:
        from game.ledger import record_gain

        gained_coins = loot + (league["coins"] if award_cup else 0)
        gained_dna = dna_win + (league["dna"] if award_cup else 0)
        record_gain(attacker, "arena", coins=gained_coins, dna=gained_dna)

    if defender_user is not None:
        defender_fields = ["coins"]
        if award_cup:
            # a freshly-raided defender gets arena protection so they can't be farmed
            defender_user.shield_until = timezone.now() + datetime.timedelta(hours=constants.ARENA_SHIELD_HOURS)
            defender_user.cup = max(0, defender_user.cup + (-delta if won else abs(delta)))
            defender_fields += ["cup", "shield_until"]
        defender_user.save(update_fields=defender_fields)
        _release_opponent(defender_user.id)  # raid done + shielded → free the reservation

    # For a REAL defender we DM them the moment this returns (see the handler), so the
    # log is pre-marked notified here — the periodic catch-up job then leaves it alone.
    log = AttackLog.objects.create(
        attacker=attacker,
        attacker_label=lab_display(attacker),
        attacker_power=attacker_power,
        defender=defender_user,
        defender_label=opponent["label"],
        is_fake_defender=opponent["is_fake"],
        attacker_won=won,
        loot_gold=taken_from_defender if defender_user is not None else loot,
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
        "league_coins": league_coins,
        "league_dna": league_dna,
        "league_name": league["name"],
        "league_emoji": league["emoji"],
        "cup_delta": delta,
        "opponent_label": opponent["label"],
        "opponent_alliance": (defender_user.alliance.name if (defender_user and defender_user.alliance_id) else None),
        "new_cup": attacker.cup,
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

import datetime
import random

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from bio_lab.models import AttackLog, Creature, User
from game import constants
from game.combat import resolve_duel
from game.creature import GameError, effective_stats
from game.equipment import get_equipped_items


def creature_power(creature: Creature, equipped_items: list | None = None) -> int:
    stats = effective_stats(creature, equipped_items)
    return round(stats["hp"] + stats["atk"] + stats["def"] + stats["spd"])


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


def cup_delta(attacker: User, defender_cup: int, won: bool, attacker_power: int) -> int:
    """Cup swing for one raid. Beating someone rated above you pays more than
    beating someone below; losing to someone below you costs more than losing to
    someone above. Gains are damped once the attacker is already above the cup
    their power deserves."""
    gap = defender_cup - attacker.cup

    if won:
        # +1 cup per 8 points of rating gap, so punching up is worth it
        raw = constants.ARENA_CUP_WIN_BASE + gap / 8
        if attacker.cup > deserved_cup(attacker_power):
            raw *= constants.ARENA_OVERCAP_DAMPING
        delta = max(constants.ARENA_CUP_MIN_DELTA, min(constants.ARENA_CUP_MAX_DELTA, round(raw)))
        return delta

    raw = constants.ARENA_CUP_LOSS_BASE - gap / 8
    delta = max(constants.ARENA_CUP_MIN_DELTA, min(constants.ARENA_CUP_MAX_DELTA, round(raw)))
    return -delta


def _fake_opponent(attacker: User, attacker_power: int) -> dict:
    """A bot defender, used when no real player sits in the attacker's cup band.
    Scaled around the attacker's own power so the fight is a real coin-flip, with
    deliberately swingy loot so bot raids still feel like a gamble."""
    swing = random.uniform(0.85, 1.2)
    power = max(1, round(attacker_power * swing))
    return {
        "is_fake": True,
        "user": None,
        "label": random.choice(constants.ARENA_FAKE_LAB_NAMES),
        "cup": max(0, attacker.cup + random.randint(-60, 90)),
        "power": power,
        "loot_pool": random.randint(*constants.ARENA_FAKE_LOOT_RANGE),
    }


def find_opponent(attacker: User) -> dict:
    """Picks a raid target: a real, unshielded player inside the cup band if one
    exists, otherwise a bot. Returns a uniform dict either way so callers don't
    branch on opponent kind."""
    attacker_power = active_power(attacker)
    if attacker_power <= 0:
        raise GameError("اول یه موجود فعال انتخاب کن.")

    now = timezone.now()
    band = constants.ARENA_MATCH_CUP_BAND
    candidates = list(
        User.objects.filter(
            cup__gte=attacker.cup - band,
            cup__lte=attacker.cup + band,
            is_banned=False,
        )
        .filter(Q(shield_until__isnull=True) | Q(shield_until__lte=now))
        .exclude(id=attacker.id)
        .filter(creatures__is_active=True)
        .distinct()[:25]
    )

    if not candidates:
        return _fake_opponent(attacker, attacker_power)

    target = random.choice(candidates)
    return {
        "is_fake": False,
        "user": target,
        "label": target.lab_name or f"آزمایشگاه {target.id}",
        "cup": target.cup,
        "power": active_power(target),
        "loot_pool": target.coins,
    }


def expected_loot(opponent: dict) -> int:
    return max(constants.ARENA_LOOT_MIN, round(opponent["loot_pool"] * constants.ARENA_LOOT_PERCENT))


@transaction.atomic
def attack(attacker: User, opponent: dict) -> dict:
    """Resolves one arena raid. Attacking always drops the attacker's own shield —
    you can't camp behind protection while farming other people."""
    attacker_creature = Creature.objects.filter(owner=attacker, is_active=True).first()
    if attacker_creature is None:
        raise GameError("اول یه موجود فعال انتخاب کن.")

    if opponent["is_fake"]:
        # bot defenders have no Creature row, so build an unsaved stand-in scaled to
        # the rolled power (never saved — same trick as hunt.py's wild creatures)
        defender_creature = _bot_creature(opponent["power"])
        defender_user = None
    else:
        defender_user = opponent["user"]
        defender_creature = Creature.objects.filter(owner=defender_user, is_active=True).first()
        if defender_creature is None:
            raise GameError("این حریف دیگه موجود فعالی نداره.")
        if is_shielded(defender_user):
            raise GameError("این حریف الان سپر محافظ داره، یکی دیگه رو امتحان کن.")

    winner, log_text = resolve_duel(attacker_creature, defender_creature)
    won = winner is attacker_creature

    attacker_power = creature_power(attacker_creature, get_equipped_items(attacker_creature))
    delta = cup_delta(attacker, opponent["cup"], won, attacker_power)

    loot = 0
    if won:
        loot = expected_loot(opponent)
        if defender_user is not None:
            loot = min(loot, defender_user.coins)  # never push a real defender negative
            defender_user.coins -= loot
        attacker.coins += loot

    attacker.cup = max(0, attacker.cup + delta)
    # raiding always burns your own shield, win or lose
    attacker.shield_until = None
    attacker.save(update_fields=["coins", "cup", "shield_until"])

    if defender_user is not None:
        # a freshly-raided defender gets protection so they can't be farmed
        defender_user.shield_until = timezone.now() + datetime.timedelta(hours=constants.ARENA_SHIELD_HOURS)
        defender_user.cup = max(0, defender_user.cup + (-delta if won else abs(delta)))
        defender_user.save(update_fields=["coins", "cup", "shield_until"])

    AttackLog.objects.create(
        attacker=attacker,
        defender=defender_user,
        defender_label=opponent["label"],
        is_fake_defender=opponent["is_fake"],
        attacker_won=won,
        loot_gold=loot,
        cup_delta=delta,
    )

    return {
        "won": won,
        "log_text": log_text,
        "loot": loot,
        "cup_delta": delta,
        "opponent_label": opponent["label"],
        "new_cup": attacker.cup,
    }


def _bot_creature(power: int) -> Creature:
    """Unsaved stand-in for a bot defender — `id` stays None, so it only ever feeds
    the combat math (game.equipment.get_equipped_items() short-circuits on pk=None)."""
    element = constants.random_element()
    share = max(1, power // 4)
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
    return list(AttackLog.objects.filter(defender=user).order_by("-created_at")[:limit])


def top_by_cup(limit: int = 10) -> list[User]:
    return list(User.objects.filter(is_banned=False).order_by("-cup")[:limit])

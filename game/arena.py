import datetime
import random

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


def _attacker_level(attacker: User) -> int:
    creature = Creature.objects.filter(owner=attacker, is_active=True).first()
    return creature.level if creature is not None else 1


def power_for_cup(cup: int) -> int:
    """The power a lab at this cup rating is *expected* to have.

    Inverse of deserved_cup(). Bots are built from this rather than from the
    attacker's own power, and that difference is the whole point: a bot mirroring
    the attacker was always a coin flip, so a weak player could keep winning and
    climb forever. Sized to the cup instead, the ladder pushes back — reach a cup
    your creature can't back up and the bots there simply beat you.
    """
    return max(1, round(cup / constants.ARENA_CUP_PER_POWER))


def _fake_opponent(attacker: User) -> dict:
    """A bot defender, used when no real player sits in the attacker's cup band.

    Built from the attacker's CUP, not their power, so it enforces the ladder.
    The swing keeps individual fights uncertain without softening the trend."""
    bot_cup = max(0, attacker.cup + random.randint(-40, 90))
    swing = random.uniform(0.9, 1.15)
    power = max(1, round(power_for_cup(bot_cup) * swing))
    return {
        "is_fake": True,
        "user": None,
        "label": random.choice(constants.ARENA_FAKE_LAB_NAMES),
        "cup": bot_cup,
        "power": power,
        "element": constants.random_element(),  # fixed here so the preview matches the fight
        "loot_pool": random.randint(*constants.arena_fake_loot_range(_attacker_level(attacker))),
    }


def find_opponent(attacker: User) -> dict:
    """Picks a raid target: a real, unshielded player inside the cup band if one
    exists, otherwise a bot. Returns a uniform dict either way so callers don't
    branch on opponent kind.

    **Matchmaking is by cup, never by power** — on both branches. Real players are
    filtered to the cup band; bots are sized from the attacker's cup. Pairing on
    power would quietly undo the ladder: it would hand a weak player weak
    opponents at every rating, so they'd keep winning and keep climbing.
    """
    if active_power(attacker) <= 0:
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
        return _fake_opponent(attacker)

    target = random.choice(candidates)
    target_creature = Creature.objects.filter(owner=target, is_active=True).first()
    return {
        "is_fake": False,
        "user": target,
        "label": lab_display(target),
        "cup": target.cup,
        "power": active_power(target),
        "element": target_creature.element if target_creature else constants.random_element(),
        "loot_pool": target.coins,
    }


def expected_loot(opponent: dict, attacker_level: int = 1) -> int:
    """Real opponents pay exactly 10% of their gold (integer, small floor). Bot/fake
    opponents instead pay a cup-scaled amount that climbs super-linearly, so a
    high-cup raider who only ever matches bots still earns a meaningful reward."""
    if opponent.get("is_fake"):
        return constants.arena_fake_loot(int(opponent.get("cup", 0)))
    raw = int(opponent["loot_pool"]) // 10  # exactly 10%, integer
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
        defender_user = opponent["user"]
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

    loot = 0
    if won:
        loot = expected_loot(opponent, attacker_creature.level)
        if defender_user is not None:
            loot = min(loot, defender_user.coins)  # never push a real defender negative
            defender_user.coins -= loot
        attacker.coins += loot

    attacker_fields = ["coins"]
    if award_cup:
        attacker.cup = max(0, attacker.cup + delta)
        # raiding spends 8h off your shield (not the whole thing anymore), so a
        # bought shield lets you attack a handful of times before it's gone
        spend_shield_on_attack(attacker)
        attacker_fields += ["cup", "shield_until"]
    attacker.save(update_fields=attacker_fields)

    if defender_user is not None:
        defender_fields = ["coins"]
        if award_cup:
            # a freshly-raided defender gets arena protection so they can't be farmed
            defender_user.shield_until = timezone.now() + datetime.timedelta(hours=constants.ARENA_SHIELD_HOURS)
            defender_user.cup = max(0, defender_user.cup + (-delta if won else abs(delta)))
            defender_fields += ["cup", "shield_until"]
        defender_user.save(update_fields=defender_fields)

    AttackLog.objects.create(
        attacker=attacker,
        attacker_label=lab_display(attacker),
        attacker_power=attacker_power,
        defender=defender_user,
        defender_label=opponent["label"],
        is_fake_defender=opponent["is_fake"],
        attacker_won=won,
        loot_gold=loot,
        cup_delta=delta,
    )

    lab_up = lab.award(attacker, "arena_win" if won else "arena_loss")

    return {
        "won": won,
        "lab_up": lab_up,
        "log_text": log_text,
        "detail_log": detail_log,
        "loot": loot,
        "cup_delta": delta,
        "opponent_label": opponent["label"],
        "new_cup": attacker.cup,
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

from django.db import transaction

from bio_lab.models import Creature, User
from bio_lab.repository import resolve_user
from game.creature import GameError

GRANT_RESOURCE_FIELDS = {"coins": "coins", "dna": "dna_fragments", "diamonds": "diamonds"}


def find_user_or_raise(identifier: str) -> User:
    user = resolve_user(identifier)
    if user is None:
        raise GameError(f"کاربری با شناسه/یوزرنیم «{identifier}» پیدا نشد.")
    return user


def _adjust_resource(identifier: str, resource: str, amount: int, sign: int) -> tuple[User, int]:
    if resource not in GRANT_RESOURCE_FIELDS:
        raise GameError("نوع منبع نامعتبره. باید coins، dna یا diamonds باشه.")
    if amount <= 0:
        raise GameError("مقدار باید یه عدد صحیح مثبت باشه.")
    user = find_user_or_raise(identifier)
    field = GRANT_RESOURCE_FIELDS[resource]
    new_value = max(0, getattr(user, field) + sign * amount)
    setattr(user, field, new_value)
    user.save(update_fields=[field])
    return user, new_value


def grant_resource(identifier: str, resource: str, amount: int) -> tuple[User, int]:
    return _adjust_resource(identifier, resource, amount, sign=1)


def charge_user(identifier: str, coins: int = 0, dna: int = 0, diamonds: int = 0) -> tuple[User, dict]:
    """Tops up several resources in one shot (the admin panel's «شارژ کامل»).
    Amounts may be negative to deduct; every resource still floors at zero. Returns
    (user, {resource: new_value}) covering only the resources actually touched."""
    if coins == 0 and dna == 0 and diamonds == 0:
        raise GameError("حداقل یکی از مقدارها باید غیرصفر باشه.")
    user = find_user_or_raise(identifier)

    changes = {"coins": coins, "dna": dna, "diamonds": diamonds}
    updated_fields = []
    new_values = {}
    for resource, amount in changes.items():
        if amount == 0:
            continue
        field = GRANT_RESOURCE_FIELDS[resource]
        new_value = max(0, getattr(user, field) + amount)
        setattr(user, field, new_value)
        updated_fields.append(field)
        new_values[resource] = new_value

    user.save(update_fields=updated_fields)
    return user, new_values


def deduct_resource(identifier: str, resource: str, amount: int) -> tuple[User, int]:
    return _adjust_resource(identifier, resource, amount, sign=-1)


def set_banned(identifier: str, banned: bool) -> User:
    user = find_user_or_raise(identifier)
    user.is_banned = banned
    user.save(update_fields=["is_banned"])
    return user


def get_creature_or_raise(creature_id: int) -> Creature:
    creature = Creature.objects.filter(id=creature_id).first()
    if creature is None:
        raise GameError("موجودی با این شماره پیدا نشد.")
    return creature


def delete_creature(creature_id: int) -> str:
    creature = get_creature_or_raise(creature_id)
    name = creature.name
    creature.delete()
    return name


def reset_user(identifier: str) -> User:
    """Wipe a player's entire game progress and re-bootstrap them as a brand-new
    player, keeping only their identity (telegram id, username, name, lab name,
    and ban status). Owner-only and irreversible.

    Implemented as delete-and-recreate on the same primary key rather than a
    field-by-field reset: every game row (creatures, equipment, buildings, jobs,
    cards, logs, season results, attack history, alliance membership) hangs off
    the User by a cascading FK, so dropping the row is the one operation that is
    guaranteed to leave nothing dangling. The row is then recreated with the same
    id, which falls back to the model's starting-value defaults, and the standard
    new-player bootstrap runs so the account is immediately in a valid fresh
    state instead of an empty half-state."""
    # imported here, not at module top, to keep the bootstrap dependencies
    # (game.buildings -> game.constants ...) out of moderation's import path
    from game import constants
    from game.buildings import get_or_create_buildings, grant_speedup_card
    from game.creature import create_starter_creature

    user = find_user_or_raise(identifier)
    identity = {
        "id": user.id,
        "username": user.username,
        "first_name": user.first_name,
        "lab_name": user.lab_name,
        "is_banned": user.is_banned,
    }
    with transaction.atomic():
        user.delete()  # cascades every owned game row
        fresh = User.objects.create(**identity)  # resources/progress -> model defaults
        create_starter_creature(fresh)
        for minutes, count in constants.STARTING_SPEEDUP_CARDS.items():
            grant_speedup_card(fresh, minutes, count=count)
        get_or_create_buildings(fresh)
    return fresh


def user_info(identifier: str) -> dict:
    user = find_user_or_raise(identifier)
    creatures = list(Creature.objects.filter(owner=user).order_by("id"))
    return {"user": user, "creatures": creatures}


def player_progress(identifier: str) -> dict:
    """A read-only progress snapshot + recent activity log for one player, for the
    owner's «لاگ پیشرفت» view. Everything here is derived from current state, so it
    stays correct without any extra tracking."""
    from bio_lab.models import AttackLog, Building, DailyActionLog
    from game import lab

    user = find_user_or_raise(identifier)
    creatures = list(Creature.objects.filter(owner=user))
    rarity_counts: dict[str, int] = {}
    for c in creatures:
        rarity_counts[c.rarity] = rarity_counts.get(c.rarity, 0) + 1
    buildings = {b.building_type: b.level for b in Building.objects.filter(owner=user)}
    attacks = AttackLog.objects.filter(attacker=user)
    recent = list(DailyActionLog.objects.filter(user=user).order_by("-day", "-count")[:15])
    return {
        "user": user,
        "lab_level": lab.level_for_xp(user.lab_xp),
        "lab_xp": user.lab_xp,
        "creatures_total": len(creatures),
        "rarity_counts": rarity_counts,
        "max_creature_level": max((c.level for c in creatures), default=0),
        "max_star": max((c.star_level for c in creatures), default=0),
        "buildings": buildings,
        "cup": user.cup,
        "streak": user.login_streak,
        "arena_wins": attacks.filter(attacker_won=True).count(),
        "arena_total": attacks.count(),
        "recent_activity": [(r.day, r.action, r.count) for r in recent],
        "created_at": user.created_at,
    }

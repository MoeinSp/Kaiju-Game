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


def set_lab_level(identifier: str, level: int) -> tuple[User, int]:
    """Admin tool: set a player's lab level directly by writing the exact XP floor
    for that level. Returns (user, new_level). Clamped to [1, LAB_MAX_LEVEL]."""
    from game import lab

    level = max(1, min(int(level), lab.LAB_MAX_LEVEL))
    user = find_user_or_raise(identifier)
    user.lab_xp = lab.xp_for_level(level)
    user.save(update_fields=["lab_xp"])
    return user, lab.lab_level(user)


def set_banned(identifier: str, banned: bool) -> User:
    user = find_user_or_raise(identifier)
    user.is_banned = banned
    user.save(update_fields=["is_banned"])
    return user


# ── admin: user browse / search / gifting / global stats ──────────────────────

USER_PAGE_SIZE = 10


def _user_row(user: User) -> dict:
    return {
        "id": user.id,
        "name": user.lab_name or user.first_name or (f"@{user.username}" if user.username else str(user.id)),
        "coins": user.coins,
        "cup": user.cup,
        "banned": user.is_banned,
        "last_login_day": user.last_login_day,
    }


def list_users_page(page: int = 0) -> dict:
    """One page of users, newest first, for the admin browse UI."""
    offset = max(0, page) * USER_PAGE_SIZE
    total = User.objects.count()
    rows = list(User.objects.order_by("-created_at")[offset : offset + USER_PAGE_SIZE])
    return {
        "users": [_user_row(u) for u in rows],
        "total": total,
        "page": page,
        "has_next": offset + USER_PAGE_SIZE < total,
        "has_prev": page > 0,
    }


def search_users(query: str, limit: int = 10) -> list[dict]:
    """Partial match on lab name / first name / username (and exact id)."""
    from django.db.models import Q

    q = (query or "").strip().lstrip("@")
    if not q:
        return []
    filt = Q(lab_name__icontains=q) | Q(first_name__icontains=q) | Q(username__icontains=q)
    if q.isdigit():
        filt = filt | Q(id=int(q))
    return [_user_row(u) for u in User.objects.filter(filt).order_by("-created_at")[:limit]]


@transaction.atomic
def gift_all(coins: int = 0, dna: int = 0, diamonds: int = 0) -> int:
    """Add resources to EVERY user (events / compensation). Returns affected count."""
    from django.db.models import F

    if coins <= 0 and dna <= 0 and diamonds <= 0:
        raise GameError("حداقل یکی از مقدارها باید مثبت باشه.")
    updates = {}
    if coins > 0:
        updates["coins"] = F("coins") + coins
    if dna > 0:
        updates["dna_fragments"] = F("dna_fragments") + dna
    if diamonds > 0:
        updates["diamonds"] = F("diamonds") + diamonds
    return User.objects.update(**updates)


def global_stats() -> dict:
    """Richer economy + activity snapshot for the admin dashboard."""
    from django.db.models import Sum
    from django.utils import timezone

    from game.daily import today_str

    agg = User.objects.aggregate(
        coins=Sum("coins"), dna=Sum("dna_fragments"), diamonds=Sum("diamonds")
    )
    return {
        "users": User.objects.count(),
        "banned": User.objects.filter(is_banned=True).count(),
        "active_today": User.objects.filter(last_login_day=today_str()).count(),
        "new_today": User.objects.filter(created_at__date=timezone.localtime(timezone.now()).date()).count(),
        "creatures": Creature.objects.count(),
        "total_coins": agg["coins"] or 0,
        "total_dna": agg["dna"] or 0,
        "total_diamonds": agg["diamonds"] or 0,
    }


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


def set_creature_star(creature_id: int, new_star: int) -> Creature:
    """Operator tool: force a creature's star level, cascading its power DOWN when
    the star drops. Star acts as a stat multiplier, so lowering it already weakens
    the creature — but level and body-part upgrades are pulled down too so nothing
    is left inconsistent above the new tier:

    * body parts are clamped to the new star's cap (star × 20);
    * level is scaled proportionally to the star reduction (and xp reset).

    Raising the star only bumps the star (no free levels/parts). Clamped to 1..5."""
    from game import constants

    creature = get_creature_or_raise(creature_id)
    new_star = max(1, min(5, int(new_star)))
    old_star = creature.star_level or 1
    creature.star_level = new_star

    cap = constants.part_upgrade_cap(new_star)
    for part in constants.BODY_PARTS:
        attr = f"{part}_lvl"
        if getattr(creature, attr) > cap:
            setattr(creature, attr, cap)

    if new_star < old_star:
        creature.level = max(1, round(creature.level * new_star / old_star))
        creature.xp = 0

    creature.save()
    return creature


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
    uid = user.id
    identity = {
        "id": uid,
        "username": user.username,
        "first_name": user.first_name,
        "lab_name": user.lab_name,
        "is_banned": user.is_banned,
    }
    with transaction.atomic():
        # scrub this numeric id's INVITE footprint too — `referred_by` is a plain
        # telegram-id field, not a cascading FK, so anyone this id invited would keep
        # pointing at it after the wipe (and it would still count as their referrer).
        # Clearing it makes the reset truly complete: the id is a brand-new player who
        # has invited nobody and was invited by nobody.
        User.objects.filter(referred_by=uid).update(referred_by=None)
        user.delete()  # cascades every owned game row (creatures, buildings, claims, logs…)
        fresh = User.objects.create(**identity)  # resources/progress + own referral state -> defaults
        create_starter_creature(fresh)
        for minutes, count in constants.STARTING_SPEEDUP_CARDS.items():
            grant_speedup_card(fresh, minutes, count=count)
        get_or_create_buildings(fresh)
    return fresh


def user_info(identifier: str) -> dict:
    user = find_user_or_raise(identifier)
    creatures = list(Creature.objects.filter(owner=user).order_by("id"))
    # resolve the alliance name HERE (sync/ORM context) — the card is rendered on
    # the event loop, where a lazy user.alliance FK load would raise
    # SynchronousOnlyOperation (this crashed opening any user who's in an alliance)
    alliance_name = user.alliance.name if user.alliance_id else None
    return {"user": user, "creatures": creatures, "alliance_name": alliance_name}


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

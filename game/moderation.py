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
    if sign > 0:
        from game.ledger import record_gain

        record_gain(user, "admin", **{resource: amount})
    return user, new_value


def grant_resource(identifier: str, resource: str, amount: int) -> tuple[User, int]:
    return _adjust_resource(identifier, resource, amount, sign=1)


# owner can type the rarity in English or Persian
_RARITY_ALIASES = {
    "common": "common", "معمولی": "common",
    "rare": "rare", "نایاب": "rare",
    "epic": "epic", "حماسی": "epic",
    "legendary": "legendary", "افسانه": "legendary", "افسانه‌ای": "legendary", "افسانه ای": "legendary",
    "mythic": "mythic", "اساطیری": "mythic",
}


def _build_creature_base(rarity_key: str, level: int) -> dict:
    """The base stat block for a granted creature at `level` — matches how add_xp()
    builds a leveled creature (rarity-scaled starter base + flat per-level growth)."""
    from game import constants

    mult = constants.RARITY_STAT_MULTIPLIER[rarity_key]
    grow = level - 1
    return {
        "base_hp": round(constants.STARTER_BASE_HP * mult) + constants.LEVEL_UP_HP * grow,
        "base_atk": round(constants.STARTER_BASE_ATK * mult) + constants.LEVEL_UP_ATK * grow,
        "base_def": round(constants.STARTER_BASE_DEF * mult) + constants.LEVEL_UP_DEF * grow,
        "base_spd": round(constants.STARTER_BASE_SPD * mult) + constants.LEVEL_UP_SPD * grow,
    }


def _grant_creatures(user, species, element, rarity_key, level, star, count, *, maxed_parts=False):
    from django.db import transaction as _tx

    from bio_lab.models import Creature
    from game import constants

    base = _build_creature_base(rarity_key, level)
    if maxed_parts:
        cap = constants.part_upgrade_cap(star)  # 20 per star, 100 at 5★
        base.update(wings_lvl=cap, armor_lvl=cap, fangs_lvl=cap, poison_lvl=cap)
    with _tx.atomic():
        for _ in range(count):
            Creature.objects.create(
                owner=user, name=species, element=element, rarity=rarity_key,
                star_level=star, level=level, xp=0, is_active=False, **base,
            )


def _resolve_species(species: str):
    from game import constants

    element = constants.species_element(species)
    if element is None:
        names = "، ".join(sorted(constants.SPECIES.keys()))
        raise GameError(f"گونه‌ی «{species}» ناشناخته‌ست.\nگونه‌های مجاز:\n{names}")
    return element


def _resolve_rarity(token: str) -> str:
    from game.keywords import normalize

    rarity_key = _RARITY_ALIASES.get(token.lower()) or _RARITY_ALIASES.get(normalize(token))
    if rarity_key is None:
        raise GameError("نایابی نامعتبره. یکی از: common / rare / epic / legendary / mythic.")
    return rarity_key


def admin_give_kaiju(identifier: str, raw: str) -> dict:
    """Owner tool: grant a custom creature to a player. `raw` is
    «<نایابی> <سطح> <ستاره> <تعداد> <نام‌گونه…>», e.g. «mythic 100 5 3 کرکس دریا».
    The species name may contain spaces, so it's everything after the four numbers."""
    from game import constants

    parts = (raw or "").strip().split()
    if len(parts) < 5:
        raise GameError(
            "فرمت درست: <code>&lt;نایابی&gt; &lt;سطح&gt; &lt;ستاره&gt; &lt;تعداد&gt; &lt;نام&gt;</code>\n"
            "مثال: <code>mythic 100 5 3 کرکس دریا</code>\n"
            "نایابی: common / rare / epic / legendary / mythic (یا معادل فارسی)."
        )
    rarity_key = _resolve_rarity(parts[0])
    try:
        level, star, count = int(parts[1]), int(parts[2]), int(parts[3])
    except ValueError:
        raise GameError("سطح، ستاره و تعداد باید عدد باشن.")
    species = " ".join(parts[4:]).strip()
    element = _resolve_species(species)

    star = max(1, min(constants.STAR_MAX, star))
    max_level = constants.creature_max_level(rarity_key, star)
    level = max(1, min(max_level, level))
    count = max(1, min(50, count))

    user = find_user_or_raise(identifier)
    _grant_creatures(user, species, element, rarity_key, level, star, count)
    return {
        "user": user, "species": species, "element": element, "rarity": rarity_key,
        "level": level, "star": star, "count": count, "maxed": False,
    }


def admin_give_maxed_kaiju(identifier: str, raw: str) -> dict:
    """Owner tool: grant a FULLY MAXED creature — top star, max level for that
    star, and every body part (نیش/بال/زره/غده) at its 5★ cap. `raw` is
    «<نایابی> <تعداد> <نام‌گونه…>», e.g. «mythic 3 کرکس دریا»."""
    from game import constants

    parts = (raw or "").strip().split()
    if len(parts) < 3:
        raise GameError(
            "فرمت درست: <code>&lt;نایابی&gt; &lt;تعداد&gt; &lt;نام‌گونه&gt;</code>\n"
            "مثال: <code>mythic 3 کرکس دریا</code>\n"
            "<i>سطح/ستاره/ارتقای اعضا همه خودکار مکس می‌شن.</i>"
        )
    rarity_key = _resolve_rarity(parts[0])
    try:
        count = int(parts[1])
    except ValueError:
        raise GameError("تعداد باید عدد باشه.")
    species = " ".join(parts[2:]).strip()
    element = _resolve_species(species)

    star = constants.STAR_MAX
    level = constants.creature_max_level(rarity_key, star)
    count = max(1, min(50, count))

    user = find_user_or_raise(identifier)
    _grant_creatures(user, species, element, rarity_key, level, star, count, maxed_parts=True)
    return {
        "user": user, "species": species, "element": element, "rarity": rarity_key,
        "level": level, "star": star, "count": count, "maxed": True,
    }


# owner can name the equipment slot in English or Persian
_SLOT_ALIASES = {
    "weapon": "weapon", "سلاح": "weapon", "اسلحه": "weapon",
    "armor": "armor", "زره": "armor",
    "rune": "rune", "طلسم": "rune", "حلقه": "rune",
    "offhand": "offhand", "غلاف": "offhand", "غده": "offhand",
}


def admin_give_equipment(identifier: str, raw: str) -> dict:
    """Owner tool: grant a custom equipment piece at a chosen level. `raw` is
    «<جایگاه> <نایابی> <سطح> <تعداد>», e.g. «weapon mythic 25 2». The slot picks a
    random template of that kind; level is clamped to the absolute equipment cap."""
    from django.db import transaction as _tx

    from bio_lab.models import Equipment
    from game import constants
    from game.keywords import normalize

    parts = (raw or "").strip().split()
    if len(parts) < 4:
        raise GameError(
            "فرمت درست: <code>&lt;جایگاه&gt; &lt;نایابی&gt; &lt;سطح&gt; &lt;تعداد&gt;</code>\n"
            "مثال: <code>weapon mythic 25 2</code>\n"
            "<i>جایگاه: weapon/سلاح · armor/زره · rune/طلسم · offhand/غلاف</i>"
        )
    slot = _SLOT_ALIASES.get(parts[0].lower()) or _SLOT_ALIASES.get(normalize(parts[0]))
    if slot is None:
        raise GameError("جایگاه نامعتبره. یکی از: weapon/سلاح، armor/زره، rune/طلسم، offhand/غلاف.")
    rarity_key = _resolve_rarity(parts[1])
    try:
        level, count = int(parts[2]), int(parts[3])
    except ValueError:
        raise GameError("سطح و تعداد باید عدد باشن.")
    level = max(1, min(constants.EQUIPMENT_MAX_LEVEL, level))
    count = max(1, min(50, count))

    user = find_user_or_raise(identifier)
    import random as _random

    templates = constants.EQUIPMENT_TEMPLATES[slot]
    with _tx.atomic():
        last = None
        for _ in range(count):
            template = _random.choice(templates)
            last = Equipment.objects.create(
                owner=user, slot=slot, template_key=template, name=template,
                rarity=rarity_key, level=level,
            )
    return {
        "user": user, "slot": slot, "slot_label": constants.EQUIPMENT_SLOT_LABELS[slot],
        "rarity": rarity_key, "level": level, "count": count, "name": last.name,
    }


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
    if updated_fields:
        from game.ledger import record_gain

        record_gain(user, "admin", coins=max(0, coins), dna=max(0, dna), diamonds=max(0, diamonds))
    return user, new_values


def deduct_resource(identifier: str, resource: str, amount: int) -> tuple[User, int]:
    return _adjust_resource(identifier, resource, amount, sign=-1)


def admin_max_buildings(identifier: str) -> dict:
    """Owner tool: set every one of a player's buildings to the absolute max level.
    Seeds any missing building rows first, then raises them all to BUILDING_MAX_LEVEL
    (main hall included, so the per-building cap is satisfied)."""
    from django.db import transaction as _tx

    from bio_lab.models import Building
    from game import constants
    from game.buildings import get_or_create_buildings

    user = find_user_or_raise(identifier)
    with _tx.atomic():
        get_or_create_buildings(user)  # make sure all types exist
        count = Building.objects.filter(
            owner=user, building_type__in=constants.BUILDING_TYPES
        ).update(level=constants.BUILDING_MAX_LEVEL)
    return {"user": user, "count": count, "max_level": constants.BUILDING_MAX_LEVEL}


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
    return {
        "user": user, "creatures": creatures, "alliance_name": alliance_name,
        "gains": _recent_gains_safe(user),
    }


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
        "gains": _recent_gains_safe(user),
    }


def _recent_gains_safe(user) -> dict:
    try:
        from game.ledger import recent_gains

        return recent_gains(user, days=3)
    except Exception:  # noqa: BLE001 — diagnostics must never break the lookup
        return {"days": [], "per_day": {}, "totals": {"coins": 0, "dna": 0, "diamonds": 0}}

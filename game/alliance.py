import datetime

from django.db import transaction
from django.utils import timezone

from bio_lab.models import Alliance, Creature, User
from game import constants
from game.combat import resolve_duel
from game.creature import GameError

ALLIANCE_NAME_MAX_LEN = 32


def create_alliance(user: User, name: str) -> Alliance:
    name = name.strip()
    if not name or len(name) > ALLIANCE_NAME_MAX_LEN:
        raise GameError(f"اسم اتحاد باید بین ۱ تا {ALLIANCE_NAME_MAX_LEN} کاراکتر باشه.")
    if user.alliance_id is not None:
        raise GameError("اول باید از اتحاد فعلیت با /alliance_leave خارج بشی.")
    if Alliance.objects.filter(name__iexact=name).exists():
        raise GameError("این اسم قبلاً گرفته شده، یه اسم دیگه امتحان کن.")

    alliance = Alliance.objects.create(name=name, leader=user)
    user.alliance = alliance
    user.save(update_fields=["alliance"])
    return alliance


def join_alliance(user: User, name: str) -> Alliance:
    if user.alliance_id is not None:
        raise GameError("اول باید از اتحاد فعلیت با /alliance_leave خارج بشی.")
    alliance = Alliance.objects.filter(name__iexact=name.strip()).first()
    if alliance is None:
        raise GameError("همچین اتحادی پیدا نشد. اسم رو دقیق بنویس یا با /alliance_create یکی بساز.")
    _assert_has_room(alliance)

    user.alliance = alliance
    user.save(update_fields=["alliance"])
    return alliance


def _active_power(user: User) -> int:
    from game.creature import creature_power
    from game.equipment import get_equipped_items

    c = Creature.objects.filter(owner=user, is_active=True).first()
    return creature_power(c, get_equipped_items(c)) if c is not None else 0


def _is_manager(user: User, alliance: Alliance) -> bool:
    return alliance is not None and user.id in (alliance.leader_id, alliance.deputy_id)


def _assert_manager(user: User, alliance: Alliance, what: str = "این کار") -> None:
    """Leader OR deputy may do it. The deputy has the leader's full operational powers
    (building/perk upgrades, treasury, starting a war, approvals, kicks…) — everything
    except disbanding the alliance and (re)assigning the deputy role itself."""
    if not _is_manager(user, alliance):
        raise GameError(f"فقط رهبر یا قائم‌مقام می‌تونه {what} رو انجام بده.")


@transaction.atomic
def request_or_join(user: User, name: str) -> dict:
    alliance = Alliance.objects.filter(name__iexact=name.strip()).first()
    if alliance is None:
        raise GameError("همچین اتحادی پیدا نشد. اسم رو دقیق بنویس یا با /alliance_create یکی بساز.")
    return _request_or_join(user, alliance)


@transaction.atomic
def request_or_join_by_id(user: User, alliance_id: int) -> dict:
    alliance = Alliance.objects.filter(id=alliance_id).first()
    if alliance is None:
        raise GameError("این اتحاد دیگه وجود نداره.")
    return _request_or_join(user, alliance)


def _request_or_join(user: User, alliance: Alliance) -> dict:
    """The «پیوستن» core. Joins instantly when the alliance is on auto-accept and the
    min-power gate passes; otherwise files a join request the leader/deputy approve.
    A player already in an alliance must leave first — enforced here, so no one can
    ever end up in two at once."""
    if user.alliance_id is not None:
        raise GameError("تو الان توی یه اتحادی — اول باید ازش خارج شی، بعد به یکی دیگه درخواست بدی.")
    _assert_has_room(alliance)
    power = _active_power(user)
    if power < alliance.min_join_power:
        raise GameError(
            f"حداقل قدرت لازم برای پیوستن به این اتحاد <b>{alliance.min_join_power}</b> است "
            f"(قدرت تو {power}). اول هیولات رو قوی‌تر کن."
        )
    if alliance.auto_accept:
        user.alliance = alliance
        user.save(update_fields=["alliance"])
        return {"joined": True, "alliance": alliance}

    from bio_lab.models import AllianceJoinRequest

    _, created = AllianceJoinRequest.objects.get_or_create(user=user, alliance=alliance)
    return {
        "joined": False, "alliance": alliance, "already": not created,
        "requester_power": power,
        "notify_ids": [i for i in (alliance.leader_id, alliance.deputy_id) if i],
    }


def pending_requests(alliance: Alliance) -> list[dict]:
    from bio_lab.models import AllianceJoinRequest

    out = []
    for r in AllianceJoinRequest.objects.filter(alliance=alliance).select_related("user"):
        out.append({"id": r.id, "user": r.user, "power": _active_power(r.user)})
    out.sort(key=lambda x: -x["power"])
    return out


def pending_request_count(alliance: Alliance) -> int:
    from bio_lab.models import AllianceJoinRequest

    return AllianceJoinRequest.objects.filter(alliance=alliance).count()


@transaction.atomic
def approve_request(actor: User, request_id: int) -> dict:
    """Leader/deputy accepts a join request. Re-checks the applicant is still free
    (they may have been accepted elsewhere first) and, on success, deletes ALL their
    other pending requests so they can't be double-accepted."""
    from bio_lab.models import AllianceJoinRequest

    req = (
        AllianceJoinRequest.objects.select_for_update()
        .select_related("user", "alliance")
        .filter(id=request_id)
        .first()
    )
    if req is None:
        raise GameError("این درخواست دیگه معتبر نیست (شاید لغو یا قبول شده).")
    alliance = req.alliance
    if not _is_manager(actor, alliance):
        raise GameError("فقط رهبر یا قائم‌مقام می‌تونه درخواست‌ها رو تأیید کنه.")
    applicant = User.objects.select_for_update().get(id=req.user_id)
    if applicant.alliance_id is not None:
        req.delete()
        raise GameError("این کاربر قبلاً به یه اتحاد دیگه پیوسته — درخواستش پاک شد.")
    _assert_has_room(alliance)

    applicant.alliance = alliance
    applicant.save(update_fields=["alliance"])
    # invalidate every OTHER pending request of this applicant — they've joined here
    others = list(
        AllianceJoinRequest.objects.filter(user=applicant).exclude(id=req.id).select_related("alliance")
    )
    AllianceJoinRequest.objects.filter(user=applicant).delete()
    return {
        "applicant": applicant,
        "alliance": alliance,
        "invalidated": [
            {"manager_ids": [i for i in (o.alliance.leader_id, o.alliance.deputy_id) if i],
             "alliance_name": o.alliance.name, "applicant_name": applicant.first_name or str(applicant.id)}
            for o in others
        ],
    }


@transaction.atomic
def reject_request(actor: User, request_id: int) -> dict:
    from bio_lab.models import AllianceJoinRequest

    req = AllianceJoinRequest.objects.select_related("user", "alliance").filter(id=request_id).first()
    if req is None:
        raise GameError("این درخواست دیگه معتبر نیست.")
    if not _is_manager(actor, req.alliance):
        raise GameError("فقط رهبر یا قائم‌مقام می‌تونه درخواست‌ها رو رد کنه.")
    result = {"applicant": req.user, "alliance_name": req.alliance.name}
    req.delete()
    return result


def set_join_settings(actor: User, *, auto_accept: bool | None = None, min_power: int | None = None) -> Alliance:
    alliance = actor.alliance
    if not _is_manager(actor, alliance):
        raise GameError("فقط رهبر یا قائم‌مقام می‌تونه تنظیمات اتحاد رو عوض کنه.")
    fields = []
    if auto_accept is not None:
        alliance.auto_accept = bool(auto_accept)
        fields.append("auto_accept")
    if min_power is not None:
        alliance.min_join_power = max(0, int(min_power))
        fields.append("min_join_power")
    if fields:
        alliance.save(update_fields=fields)
    return alliance


@transaction.atomic
def leave_alliance(user: User) -> None:
    alliance = user.alliance
    if alliance is None:
        raise GameError("توی هیچ اتحادی نیستی.")

    user.alliance = None
    user.save(update_fields=["alliance"])

    remaining = list(User.objects.filter(alliance=alliance).exclude(id=user.id))
    if not remaining:
        alliance.delete()
        return
    fields = []
    if alliance.deputy_id == user.id:  # the deputy left
        alliance.deputy = None
        fields.append("deputy")
    if alliance.leader_id == user.id:  # the leader left → deputy inherits if there is one
        heir = alliance.deputy if (alliance.deputy_id and alliance.deputy_id != user.id) else remaining[0]
        alliance.leader = heir
        fields.append("leader")
        if alliance.deputy_id == heir.id:  # the new leader shouldn't also be deputy
            alliance.deputy = None
            if "deputy" not in fields:
                fields.append("deputy")
    if fields:
        alliance.save(update_fields=fields)


def _alliance_power(alliance: Alliance) -> int:
    total = 0
    for member in alliance.members.all():
        creature = Creature.objects.filter(owner=member, is_active=True).first()
        if creature is not None:
            total += creature.base_hp + creature.base_atk + creature.base_def + creature.base_spd
    return total


def alliance_info(alliance: Alliance) -> dict:
    members = list(alliance.members.all())
    return {
        "name": alliance.name,
        "leader": alliance.leader,
        "deputy": alliance.deputy,
        "member_count": len(members),
        "capacity": max_members(alliance),
        "members": members,
        "power": _alliance_power(alliance),
        "treasury_gold": alliance.treasury_gold,
    }


def member_roster(alliance: Alliance) -> list[dict]:
    """Every member with their id and role, leader → deputy → the rest by power."""
    from bio_lab.repository import display_name

    rows = []
    for m in alliance.members.all():
        rows.append({
            "id": m.id,
            "name": display_name(m),
            "is_leader": m.id == alliance.leader_id,
            "is_deputy": m.id == alliance.deputy_id,
            "power": _member_power(m),
        })
    rows.sort(key=lambda r: (not r["is_leader"], not r["is_deputy"], -r["power"]))
    return rows


def _role_of(alliance: Alliance, user_id: int) -> str:
    if user_id == alliance.leader_id:
        return "leader"
    if user_id == alliance.deputy_id:
        return "deputy"
    return "member"


@transaction.atomic
def kick_member(actor: User, target_id: int) -> dict:
    """Remove a member. The leader can kick anyone but themselves; a deputy can kick
    ordinary members only (not the leader or another deputy)."""
    if actor.alliance_id is None:
        raise GameError("عضو هیچ اتحادی نیستی.")
    alliance = Alliance.objects.select_for_update().get(id=actor.alliance_id)
    actor_role = _role_of(alliance, actor.id)
    if actor_role == "member":
        raise GameError("فقط رهبر یا قائم‌مقام می‌تونه عضو حذف کنه.")
    if target_id == actor.id:
        raise GameError("نمی‌تونی خودت رو حذف کنی — برای خروج «/alliance_leave» بزن.")
    target = User.objects.filter(id=target_id, alliance_id=alliance.id).first()
    if target is None:
        raise GameError("این بازیکن عضو اتحاد تو نیست.")
    target_role = _role_of(alliance, target_id)
    if target_role == "leader":
        raise GameError("رهبر رو نمی‌شه حذف کرد.")
    if actor_role == "deputy" and target_role == "deputy":
        raise GameError("قائم‌مقام نمی‌تونه قائم‌مقام دیگه رو حذف کنه.")
    if target_role == "deputy":
        alliance.deputy = None
        alliance.save(update_fields=["deputy"])
    target.alliance = None
    target.save(update_fields=["alliance"])
    return {"name": _display(target)}


@transaction.atomic
def set_deputy(leader: User, target_id: int) -> dict:
    """Leader-only: make a member the alliance's single قائم‌مقام (deputy)."""
    if leader.alliance_id is None:
        raise GameError("عضو هیچ اتحادی نیستی.")
    alliance = Alliance.objects.select_for_update().get(id=leader.alliance_id)
    if alliance.leader_id != leader.id:
        raise GameError("فقط رهبر می‌تونه قائم‌مقام تعیین کنه.")
    if target_id == leader.id:
        raise GameError("خودت رهبری، نمی‌تونی قائم‌مقام خودت بشی.")
    target = User.objects.filter(id=target_id, alliance_id=alliance.id).first()
    if target is None:
        raise GameError("این بازیکن عضو اتحاد تو نیست.")
    alliance.deputy = target
    alliance.save(update_fields=["deputy"])
    return {"name": _display(target)}


@transaction.atomic
def remove_deputy(leader: User) -> None:
    if leader.alliance_id is None:
        raise GameError("عضو هیچ اتحادی نیستی.")
    alliance = Alliance.objects.select_for_update().get(id=leader.alliance_id)
    if alliance.leader_id != leader.id:
        raise GameError("فقط رهبر می‌تونه قائم‌مقام رو برداره.")
    alliance.deputy = None
    alliance.save(update_fields=["deputy"])


def _display(user: User) -> str:
    from bio_lab.repository import display_name

    return display_name(user)


ALLIANCE_BROWSE_PAGE_SIZE = 8


def list_alliances_page(page: int = 0) -> dict:
    """Returns one page of alliances sorted by power for the browse UI."""
    from django.db.models import Count

    offset = page * ALLIANCE_BROWSE_PAGE_SIZE
    total = Alliance.objects.count()
    raw = list(
        Alliance.objects.annotate(member_count=Count("members"))
        .order_by("-treasury_gold", "id")[offset : offset + ALLIANCE_BROWSE_PAGE_SIZE]
    )
    return {
        "alliances": [
            {
                "alliance": a,
                "power": _alliance_power(a),
                "member_count": a.member_count,
            }
            for a in raw
        ],
        "total": total,
        "page": page,
        "page_size": ALLIANCE_BROWSE_PAGE_SIZE,
        "has_next": offset + ALLIANCE_BROWSE_PAGE_SIZE < total,
        "has_prev": page > 0,
    }


def search_alliances(query: str, limit: int = 8) -> list[dict]:
    """Partial-match alliance search (case-insensitive contains)."""
    matches = list(Alliance.objects.filter(name__icontains=query.strip())[:limit])
    return [
        {
            "alliance": a,
            "power": _alliance_power(a),
            "member_count": a.members.count(),
        }
        for a in matches
    ]


def join_alliance_by_id(user: User, alliance_id: int) -> Alliance:
    if user.alliance_id is not None:
        raise GameError("اول باید از اتحاد فعلیت با /alliance_leave خارج بشی.")
    alliance = Alliance.objects.filter(id=alliance_id).first()
    if alliance is None:
        raise GameError("این اتحاد دیگه وجود نداره.")
    _assert_has_room(alliance)
    user.alliance = alliance
    user.save(update_fields=["alliance"])
    return alliance


def top_alliances(limit: int = 10) -> list[dict]:
    ranked = sorted(
        (
            {"alliance": a, "power": _alliance_power(a), "member_count": a.members.count()}
            for a in Alliance.objects.all()
        ),
        key=lambda r: r["power"],
        reverse=True,
    )
    return ranked[:limit]


# End-of-week league reward per MEMBER, by the alliance's power rank (top 10 only).
ALLIANCE_LEAGUE_REWARD_BY_RANK = {
    1: {"diamonds": 60, "coins": 4000},
    2: {"diamonds": 45, "coins": 3000},
    3: {"diamonds": 35, "coins": 2200},
    4: {"diamonds": 22, "coins": 1500},
    5: {"diamonds": 22, "coins": 1500},
    6: {"diamonds": 15, "coins": 1000},
    7: {"diamonds": 10, "coins": 700},
    8: {"diamonds": 10, "coins": 700},
    9: {"diamonds": 8, "coins": 500},
    10: {"diamonds": 8, "coins": 500},
}


def award_alliance_league() -> list[tuple[int, str]]:
    """Grant the weekly alliance-league reward to every member of the top-10 alliances
    (by power), scaled by the alliance's rank. Returns (user_id, text) DMs to send.
    Called once per week from the season close, which is already idempotent."""
    from game.battlepass import _grant

    out: list[tuple[int, str]] = []
    for rank, entry in enumerate(top_alliances(limit=10), start=1):
        reward = ALLIANCE_LEAGUE_REWARD_BY_RANK.get(rank)
        if not reward:
            continue
        alliance = entry["alliance"]
        for member in alliance.members.all():
            _grant(member, reward)
            if member.notifications_on:
                out.append((
                    member.id,
                    f"🏰 <b>جایزه‌ی لیگ اتحادها!</b>\nاتحاد <b>{alliance.name}</b> این هفته رتبه‌ی "
                    f"<b>{rank}</b> شد.\n🎁 گرفتی: {reward['diamonds']} 💎 + {reward['coins']} 🪙",
                ))
    return out


def deposit_treasury(user: User, amount: int) -> Alliance:
    if user.alliance_id is None:
        raise GameError("اول باید عضو یه اتحاد باشی.")
    if amount <= 0:
        raise GameError("مقدار باید بیشتر از صفر باشه.")
    if user.coins < amount:
        raise GameError("طلا کافی نداری.")

    alliance = user.alliance
    user.coins -= amount
    alliance.treasury_gold += amount
    user.save(update_fields=["coins"])
    alliance.save(update_fields=["treasury_gold"])
    return alliance


@transaction.atomic
def heist(attacker: User, attacker_creature: Creature, defender_alliance: Alliance) -> dict:
    """Attacks another alliance's treasury with `attacker_creature`. If the defending
    alliance has an active guardian creature among its (other) members, resolves a duel
    against its strongest one; the attacker only steals gold on a win. If nobody's
    defending, the heist auto-succeeds. Daily-attempt capping is enforced by the caller
    via game.daily's ENERGY_CAPS["heist"], mirroring how raid/guardian actions are capped."""
    if attacker.alliance_id is None:
        raise GameError("اول باید عضو یه اتحاد باشی.")
    if attacker.alliance_id == defender_alliance.id:
        raise GameError("نمی‌تونی به خزانه‌ی اتحاد خودت شبیخون بزنی!")
    if defender_alliance.treasury_gold <= 0:
        raise GameError(f"خزانه‌ی اتحاد {defender_alliance.name} خالیه، غارتی درکار نیست.")

    if defender_alliance.last_heisted_at is not None:
        elapsed = timezone.now() - defender_alliance.last_heisted_at
        cooldown = datetime.timedelta(hours=constants.HEIST_COOLDOWN_HOURS)
        if elapsed < cooldown:
            remaining = cooldown - elapsed
            hours, remainder = divmod(int(remaining.total_seconds()), 3600)
            minutes = remainder // 60
            raise GameError(f"این اتحاد به‌تازگی غارت شده، {hours} ساعت و {minutes} دقیقه دیگه صبر کن.")

    defender_creatures = list(
        Creature.objects.filter(owner__alliance=defender_alliance, is_active=True)
    )

    defender_alliance.last_heisted_at = timezone.now()
    # the دژ building softens every successful heist against this alliance
    defense = heist_defense_multiplier(defender_alliance)
    if not defender_creatures:
        stolen = round(defender_alliance.treasury_gold * constants.HEIST_STEAL_PERCENT * defense)
        defender_alliance.treasury_gold -= stolen
        defender_alliance.save(update_fields=["treasury_gold", "last_heisted_at"])
        attacker.coins += stolen
        attacker.save(update_fields=["coins"])
        return {
            "success": True,
            "stolen": stolen,
            "defender_creature": None,
            "log_text": f"هیچ نگهبانی از خزانه‌ی {defender_alliance.name} دفاع نکرد!",
        }

    defender_creature = max(
        defender_creatures, key=lambda c: c.base_hp + c.base_atk + c.base_def + c.base_spd
    )
    winner_creature, log_text = resolve_duel(attacker_creature, defender_creature)
    success = winner_creature.id == attacker_creature.id

    stolen = 0
    if success:
        stolen = round(defender_alliance.treasury_gold * constants.HEIST_STEAL_PERCENT * defense)
        defender_alliance.treasury_gold -= stolen
        attacker.coins += stolen
        attacker.save(update_fields=["coins"])
    defender_alliance.save(update_fields=["treasury_gold", "last_heisted_at"])

    return {
        "success": success,
        "stolen": stolen,
        "defender_creature": defender_creature,
        "log_text": log_text,
    }


# ── Alliance buildings + weekly war (game/alliance depth) ─────────────────────
XP_PERK_PER_LEVEL = 0.03      # آکادمی: +3% lab XP per level, all members
PASS_PERK_PER_LEVEL = 0.05    # معبد: +5% Battle Pass points per level, all members
FORTRESS_PER_LEVEL = 0.06     # دژ: -6% gold stolen per heist, per level (cap enforced by max level)
BARRACKS_PER_LEVEL = 0.08     # پادگان: +8% war power per level, all members
VAULT_INCOME_PER_LEVEL = 200  # خزانه: +200 treasury gold/day per level
CAPACITY_PER_LEVEL = 10       # تالار: +10 member slots per level
PERK_MAX_LEVEL = 5
WAR_WINNER_TREASURY_BONUS = 5000  # gold added to the top alliance's treasury each week

# Member capacity: every alliance starts at 50 and the تالار building adds
# CAPACITY_PER_LEVEL per level up to PERK_MAX_LEVEL → a hard ceiling of 100.
ALLIANCE_BASE_CAPACITY = 50
# The تالار is deliberately far more expensive than the effect-perks — its treasury
# cost climbs steeply so growing past 50 members is a serious, long-term investment.
HALL_COST = {1: 30000, 2: 70000, 3: 150000, 4: 300000, 5: 600000}


def max_members(alliance: Alliance) -> int:
    """Current member ceiling: 50 base + 10 per تالار level, capped at 100."""
    level = min(alliance.hall_level, PERK_MAX_LEVEL)
    return ALLIANCE_BASE_CAPACITY + level * CAPACITY_PER_LEVEL


def _assert_has_room(alliance: Alliance) -> None:
    if alliance.members.count() >= max_members(alliance):
        raise GameError(
            f"این اتحاد پره ({max_members(alliance)} عضو). رهبرش باید «تالار اتحاد» رو ارتقا بده تا ظرفیت بیشتر شه."
        )

# Treasury-funded, alliance-wide upgradeable buildings. All share the buy_perk()
# machinery; each maps to one integer level field on Alliance. `unit` describes
# how the effect reads in the panel.
PERKS = {
    "xp": {"emoji": "🎓", "title": "آکادمی", "field": "xp_perk_level", "per_level": XP_PERK_PER_LEVEL,
           "desc": "بونوس XP آزمایشگاه برای همه‌ی اعضا", "unit": "pct"},
    "pass": {"emoji": "⛩", "title": "معبد", "field": "pass_perk_level", "per_level": PASS_PERK_PER_LEVEL,
             "desc": "بونوس امتیاز پاس فصلی برای همه", "unit": "pct"},
    "fortress": {"emoji": "🏯", "title": "دژ", "field": "fortress_level", "per_level": FORTRESS_PER_LEVEL,
                 "desc": "کاهش طلای دزدیده‌شده در شبیخون", "unit": "pct"},
    "barracks": {"emoji": "🪖", "title": "پادگان", "field": "barracks_level", "per_level": BARRACKS_PER_LEVEL,
                 "desc": "افزایش قدرت اتحاد در جنگ یک‌روزه", "unit": "pct"},
    "vault": {"emoji": "🏦", "title": "خزانه", "field": "vault_level", "per_level": VAULT_INCOME_PER_LEVEL,
              "desc": "درآمد روزانه‌ی طلا به خزانه", "unit": "gold"},
    "hall": {"emoji": "🏰", "title": "تالار اتحاد", "field": "hall_level", "per_level": CAPACITY_PER_LEVEL,
             "desc": "افزایش ظرفیت اعضای اتحاد (پایه ۵۰، سقف ۱۰۰)", "unit": "capacity"},
}
# order shown in the panel — تالار (capacity) first, it's the headline upgrade
BUILDING_ORDER = ["hall", "xp", "pass", "fortress", "barracks", "vault"]


def perk_cost(perk_key: str, level: int) -> int:
    """Treasury gold to buy the NEXT level (level = current level, 0-based). The
    تالار has its own steep curve; every other building shares the flat 3000×tier."""
    if perk_key == "hall":
        return HALL_COST.get(level + 1, HALL_COST[max(HALL_COST)])
    return 3000 * (level + 1)


def heist_defense_multiplier(alliance: Alliance) -> float:
    """Fraction of a heist's stolen gold the defending alliance actually loses.
    Each دژ level cuts the loss by FORTRESS_PER_LEVEL (floored at a small amount)."""
    reduction = min(0.9, alliance.fortress_level * FORTRESS_PER_LEVEL)
    return 1.0 - reduction


def barracks_multiplier(alliance: Alliance) -> float:
    """War-power multiplier from the پادگان building."""
    return 1.0 + alliance.barracks_level * BARRACKS_PER_LEVEL


def vault_daily_income(alliance: Alliance) -> int:
    return alliance.vault_level * VAULT_INCOME_PER_LEVEL


def _war_week() -> str:
    return timezone.localtime(timezone.now()).strftime("%G-W%V")


def xp_perk_multiplier(user: User) -> float:
    if not user.alliance_id:
        return 1.0
    lvl = Alliance.objects.filter(id=user.alliance_id).values_list("xp_perk_level", flat=True).first() or 0
    return 1.0 + lvl * XP_PERK_PER_LEVEL


def pass_perk_multiplier(user: User) -> float:
    if not user.alliance_id:
        return 1.0
    lvl = Alliance.objects.filter(id=user.alliance_id).values_list("pass_perk_level", flat=True).first() or 0
    return 1.0 + lvl * PASS_PERK_PER_LEVEL


@transaction.atomic
def buy_perk(user: User, perk_key: str) -> dict:
    """Leader-only: spend treasury gold to raise an alliance perk by one level."""
    if perk_key not in PERKS:
        raise GameError("این پرک وجود نداره.")
    if user.alliance_id is None:
        raise GameError("عضو هیچ اتحادی نیستی.")
    alliance = Alliance.objects.select_for_update().get(id=user.alliance_id)
    _assert_manager(user, alliance, "ارتقای ساختمون/پرک اتحاد")
    field = PERKS[perk_key]["field"]
    level = getattr(alliance, field)
    if level >= PERK_MAX_LEVEL:
        raise GameError("این پرک به بالاترین سطح رسیده.")
    cost = perk_cost(perk_key, level)
    if alliance.treasury_gold < cost:
        raise GameError(f"خزانه‌ی اتحاد کافی نیست! این ارتقا {cost} طلا از خزانه می‌خواد.")
    alliance.treasury_gold -= cost
    setattr(alliance, field, level + 1)
    alliance.save(update_fields=["treasury_gold", field])
    return {"perk": perk_key, "level": level + 1, "cost": cost, "treasury": alliance.treasury_gold}


def add_war_points(user: User, points: int) -> None:
    """Credit a member's activity as war points for their alliance's current week."""
    if not user.alliance_id or points <= 0:
        return
    week = _war_week()
    alliance = Alliance.objects.filter(id=user.alliance_id).first()
    if alliance is None:
        return
    if alliance.war_week != week:
        alliance.war_points = 0
        alliance.war_week = week
    alliance.war_points += int(points)
    alliance.save(update_fields=["war_points", "war_week"])


def war_leaderboard(limit: int = 10) -> list[dict]:
    week = _war_week()
    rows = Alliance.objects.filter(war_week=week, war_points__gt=0).order_by("-war_points")[:limit]
    return [{"name": a.name, "war_points": a.war_points, "id": a.id} for a in rows]


def _building_effect_text(key: str, level: int) -> str:
    """Human-readable current effect of a building at `level`."""
    spec = PERKS[key]
    if spec["unit"] == "pct":
        return f"+{round(level * spec['per_level'] * 100)}٪"
    if spec["unit"] == "capacity":
        return f"ظرفیت {ALLIANCE_BASE_CAPACITY + level * spec['per_level']} نفر"
    return f"{level * spec['per_level']} طلا/روز"


def buildings_info(alliance: Alliance) -> dict:
    """Per-building levels, costs and current effects for the buildings panel."""
    buildings = []
    for key in BUILDING_ORDER:
        spec = PERKS[key]
        level = getattr(alliance, spec["field"])
        buildings.append({
            "key": key,
            "emoji": spec["emoji"],
            "title": spec["title"],
            "desc": spec["desc"],
            "level": level,
            "maxed": level >= PERK_MAX_LEVEL,
            "cost": perk_cost(key, level),
            "effect": _building_effect_text(key, level),
            "next_effect": _building_effect_text(key, level + 1),
        })
    return {
        "buildings": buildings,
        "max_level": PERK_MAX_LEVEL,
        "treasury": alliance.treasury_gold,
        "vault_income": vault_daily_income(alliance),
    }


def perks_info(alliance: Alliance) -> dict:
    return {
        "xp_level": alliance.xp_perk_level,
        "pass_level": alliance.pass_perk_level,
        "xp_cost": perk_cost("xp", alliance.xp_perk_level),
        "pass_cost": perk_cost("pass", alliance.pass_perk_level),
        "max_level": PERK_MAX_LEVEL,
        "treasury": alliance.treasury_gold,
        "war_points": alliance.war_points if alliance.war_week == _war_week() else 0,
    }


VAULT_COLLECT_COOLDOWN_HOURS = 24


@transaction.atomic
def collect_vault(user: User) -> dict:
    """Collect the خزانه building's accrued passive income into the treasury.
    Any member may collect; capped to one payout per 24h across the alliance."""
    if user.alliance_id is None:
        raise GameError("عضو هیچ اتحادی نیستی.")
    alliance = Alliance.objects.select_for_update().get(id=user.alliance_id)
    income = vault_daily_income(alliance)
    if income <= 0:
        raise GameError("اول باید ساختمون «خزانه» رو بسازی/ارتقا بدی تا درآمد روزانه بده.")
    now = timezone.now()
    if alliance.vault_collected_at is not None:
        elapsed = now - alliance.vault_collected_at
        if elapsed < datetime.timedelta(hours=VAULT_COLLECT_COOLDOWN_HOURS):
            remaining = datetime.timedelta(hours=VAULT_COLLECT_COOLDOWN_HOURS) - elapsed
            hours = int(remaining.total_seconds() // 3600)
            minutes = int((remaining.total_seconds() % 3600) // 60)
            raise GameError(f"درآمد خزانه قبلاً امروز جمع شده. {hours} ساعت و {minutes} دقیقه دیگه صبر کن.")
    alliance.treasury_gold += income
    alliance.vault_collected_at = now
    alliance.save(update_fields=["treasury_gold", "vault_collected_at"])
    return {"income": income, "treasury": alliance.treasury_gold}


def settle_war_if_needed() -> list[tuple[int, str]]:
    """Weekly: once the ISO week rolls over, award the top alliance's treasury a
    bonus and DM its members, then leave points to reset on next award. Returns
    (user_id, text) DMs. Safe to call often — settles each week exactly once."""
    from bio_lab.models import AllianceWarState

    current = _war_week()
    state, _ = AllianceWarState.objects.get_or_create(id=1)
    if state.last_settled_week == current:
        return []

    out: list[tuple[int, str]] = []
    # the winner is the top alliance whose points belong to a *past* week
    winner = (
        Alliance.objects.exclude(war_week=current)
        .filter(war_points__gt=0)
        .order_by("-war_points")
        .first()
    )
    if winner is not None:
        winner.treasury_gold += WAR_WINNER_TREASURY_BONUS
        winner.war_points = 0
        winner.war_week = current
        winner.save(update_fields=["treasury_gold", "war_points", "war_week"])
        for member_id in User.objects.filter(alliance_id=winner.id, notifications_on=True).values_list(
            "id", flat=True
        ):
            out.append(
                (member_id, f"🏰 <b>اتحادت «{winner.name}» جنگ هفتگی رو برد!</b> خزانه {WAR_WINNER_TREASURY_BONUS} طلا جایزه گرفت.")
            )
    state.last_settled_week = current
    state.save(update_fields=["last_settled_week", "updated_at"])
    return out


# ── One-day alliance war (matchmade, 24h) ─────────────────────────────────────
WAR_DURATION_HOURS = 24
WAR_MATCH_POWER_BAND = 0.5        # opponents within ±50% of our war power are "fair"
WAR_WIN_TREASURY_REWARD = 3000    # winner's treasury bonus
WAR_WIN_WAR_POINTS = 200          # winner also climbs the weekly leaderboard

# Presence is what wins a war. Only a small fraction of an alliance's raw power seeds
# the starting score — the rest MUST be earned by members «rallying». So a strong but
# inactive alliance loses to a weaker, fully-mobilised one, and every member showing
# up genuinely matters. (Before, the full power was auto-seeded and members barely
# needed to participate.)
WAR_SEED_FRACTION = 0.20

# Personal war payouts, handed out when the war settles. Everyone who rallied earns
# the participation reward win-or-lose; the winning side earns a big bonus on top; and
# the single top contributor on the winning side is crowned MVP for premium diamonds.
WAR_RALLY_REWARD_COINS = 400
WAR_RALLY_REWARD_DNA = 15
WAR_WIN_RALLY_BONUS_COINS = 1200
WAR_WIN_RALLY_BONUS_DNA = 40
WAR_MVP_BONUS_DIAMONDS = 25


def _member_power(user: User) -> int:
    """Base combat power of a member's active creature (same metric as _alliance_power)."""
    c = Creature.objects.filter(owner=user, is_active=True).first()
    if c is None:
        return 0
    return c.base_hp + c.base_atk + c.base_def + c.base_spd


def alliance_war_power(alliance: Alliance) -> int:
    """An alliance's fighting strength for war matchmaking and seeding: total member
    power scaled by the پادگان (barracks) building."""
    return round(_alliance_power(alliance) * barracks_multiplier(alliance))


def _either_side_q(alliance_id: int):
    from django.db.models import Q

    return Q(alliance_a_id=alliance_id) | Q(alliance_b_id=alliance_id)


def active_war_for(alliance_id: int):
    """The alliance's current unsettled war (either side), or None."""
    from bio_lab.models import AllianceWar

    return (
        AllianceWar.objects.filter(status=AllianceWar.ACTIVE)
        .filter(_either_side_q(alliance_id))
        .select_related("alliance_a", "alliance_b")
        .first()
    )


def find_war_opponent(alliance: Alliance):
    """Pick a fair opponent alliance: not us, not already at war, closest war power
    within the band (falls back to the overall closest if the band is empty)."""
    from bio_lab.models import AllianceWar

    busy_ids = set(
        AllianceWar.objects.filter(status=AllianceWar.ACTIVE).values_list("alliance_a_id", flat=True)
    ) | set(
        AllianceWar.objects.filter(status=AllianceWar.ACTIVE).values_list("alliance_b_id", flat=True)
    )
    busy_ids.add(alliance.id)

    my_power = alliance_war_power(alliance)
    candidates = list(Alliance.objects.exclude(id__in=busy_ids))
    if not candidates:
        return None
    scored = sorted(candidates, key=lambda a: abs(alliance_war_power(a) - my_power))
    band = max(50, int(my_power * WAR_MATCH_POWER_BAND))
    within = [a for a in scored if abs(alliance_war_power(a) - my_power) <= band]
    return (within or scored)[0]


@transaction.atomic
def start_war(user: User):
    """Leader-only: matchmake and start a 24h war. Returns the AllianceWar."""
    from bio_lab.models import AllianceWar

    if user.alliance_id is None:
        raise GameError("عضو هیچ اتحادی نیستی.")
    alliance = Alliance.objects.select_for_update().get(id=user.alliance_id)
    _assert_manager(user, alliance, "شروع جنگ")
    if active_war_for(alliance.id) is not None:
        raise GameError("اتحادت همین الان توی یه جنگه — اول اون تموم بشه.")
    opponent = find_war_opponent(alliance)
    if opponent is None:
        raise GameError("الان هیچ اتحاد آزادی برای جنگ پیدا نشد. بعداً امتحان کن.")

    return AllianceWar.objects.create(
        alliance_a=alliance,
        alliance_b=opponent,
        # only a small head-start is seeded from raw power; members must rally the rest
        score_a=round(alliance_war_power(alliance) * WAR_SEED_FRACTION),
        score_b=round(alliance_war_power(opponent) * WAR_SEED_FRACTION),
        ends_at=timezone.now() + datetime.timedelta(hours=WAR_DURATION_HOURS),
    )


@transaction.atomic
def rally_war(user: User) -> dict:
    """A member rallies once per war, adding their creature power (× barracks) to
    their alliance's war score."""
    from bio_lab.models import AllianceWarHit

    if user.alliance_id is None:
        raise GameError("عضو هیچ اتحادی نیستی.")
    war = active_war_for(user.alliance_id)
    if war is None:
        raise GameError("اتحادت الان توی هیچ جنگی نیست.")
    if war.ends_at <= timezone.now():
        raise GameError("این جنگ تموم شده، منتظر اعلام نتیجه باش.")
    if AllianceWarHit.objects.filter(war=war, user=user).exists():
        raise GameError("تو قبلاً توی این جنگ شرکت کردی.")

    base = _member_power(user)
    if base <= 0:
        raise GameError("اول یه موجود فعال انتخاب کن.")
    my_alliance = Alliance.objects.get(id=user.alliance_id)
    contribution = round(base * barracks_multiplier(my_alliance))

    AllianceWarHit.objects.create(war=war, user=user, power=contribution)
    if war.alliance_a_id == user.alliance_id:
        war.score_a += contribution
        war.save(update_fields=["score_a"])
        my_score, foe_score = war.score_a, war.score_b
    else:
        war.score_b += contribution
        war.save(update_fields=["score_b"])
        my_score, foe_score = war.score_b, war.score_a
    return {"contribution": contribution, "my_score": my_score, "foe_score": foe_score}


def war_contributors(war, alliance_id: int, limit: int = 5) -> list[dict]:
    """Top members of `alliance_id` who rallied in this war, strongest first — the
    'who's actually fighting' roster shown on the war screen."""
    from bio_lab.models import AllianceWarHit

    hits = (
        AllianceWarHit.objects.filter(war=war, user__alliance_id=alliance_id)
        .select_related("user")
        .order_by("-power")[:limit]
    )
    return [{"name": _display(h.user), "power": h.power, "user_id": h.user_id} for h in hits]


def war_view(user: User) -> dict | None:
    """Current war state from `user`'s perspective, or None if not in a war."""
    from bio_lab.models import AllianceWarHit

    if user.alliance_id is None:
        return None
    war = active_war_for(user.alliance_id)
    if war is None:
        return None
    if war.alliance_a_id == user.alliance_id:
        me, foe = war.alliance_a, war.alliance_b
        my_score, foe_score = war.score_a, war.score_b
    else:
        me, foe = war.alliance_b, war.alliance_a
        my_score, foe_score = war.score_b, war.score_a
    remaining = max(0, int((war.ends_at - timezone.now()).total_seconds()))
    my_hit = AllianceWarHit.objects.filter(war=war, user=user).first()
    return {
        "war_id": war.id,
        "my_name": me.name,
        "foe_name": foe.name,
        "my_score": my_score,
        "foe_score": foe_score,
        "remaining_seconds": remaining,
        "ended": war.ends_at <= timezone.now(),
        "already_rallied": my_hit is not None,
        "my_contribution": my_hit.power if my_hit else 0,
        "my_participants": AllianceWarHit.objects.filter(war=war, user__alliance_id=me.id).count(),
        "foe_participants": AllianceWarHit.objects.filter(war=war, user__alliance_id=foe.id).count(),
        "contributors": war_contributors(war, me.id),
        "is_leader": me.leader_id == user.id,
    }


def _grant_war_rewards(war, winner) -> dict[int, dict]:
    """Pay out the personal war rewards and return {user_id: {coins, dna, diamonds,
    mvp, participated}} so the DMs can be personalised. Every member who rallied earns
    the participation reward; the winning side earns a bonus on top; and the single
    top contributor on the winning side is the MVP (premium diamonds)."""
    from django.db.models import F

    from bio_lab.models import AllianceWarHit

    personal: dict[int, dict] = {}
    hits = list(
        AllianceWarHit.objects.filter(war=war).select_related("user").order_by("-power")
    )
    mvp_uid = None
    if winner is not None:
        for h in hits:  # hits are power-desc, so the first on the winning side is MVP
            if h.user.alliance_id == winner.id:
                mvp_uid = h.user_id
                break

    for h in hits:
        won = winner is not None and h.user.alliance_id == winner.id
        coins = WAR_RALLY_REWARD_COINS + (WAR_WIN_RALLY_BONUS_COINS if won else 0)
        dna = WAR_RALLY_REWARD_DNA + (WAR_WIN_RALLY_BONUS_DNA if won else 0)
        diamonds = WAR_MVP_BONUS_DIAMONDS if (won and h.user_id == mvp_uid) else 0
        User.objects.filter(id=h.user_id).update(
            coins=F("coins") + coins,
            dna_fragments=F("dna_fragments") + dna,
            diamonds=F("diamonds") + diamonds,
        )
        personal[h.user_id] = {
            "coins": coins, "dna": dna, "diamonds": diamonds,
            "mvp": h.user_id == mvp_uid and won, "participated": True,
        }
    return personal


def settle_due_wars() -> list[tuple[int, str]]:
    """Settle every active war whose 24h is up: higher score wins the treasury bonus
    and weekly war points; members who rallied earn personal rewards; both sides' get
    a personalised result DM. Returns (uid, text)."""
    from django.db.models import F

    from bio_lab.models import AllianceWar

    out: list[tuple[int, str]] = []
    due = AllianceWar.objects.filter(
        status=AllianceWar.ACTIVE, ends_at__lte=timezone.now()
    ).select_related("alliance_a", "alliance_b")
    for war in due:
        with transaction.atomic():
            war = AllianceWar.objects.select_for_update().get(id=war.id)
            if war.status != AllianceWar.ACTIVE:
                continue
            a, b = war.alliance_a, war.alliance_b
            if war.score_a == war.score_b:
                winner, win_score, lose_score = None, war.score_a, war.score_b
            elif war.score_a > war.score_b:
                winner, win_score, lose_score = a, war.score_a, war.score_b
            else:
                winner, win_score, lose_score = b, war.score_b, war.score_a

            war.status = AllianceWar.SETTLED
            war.winner = winner
            war.save(update_fields=["status", "winner"])

            if winner is not None:
                Alliance.objects.filter(id=winner.id).update(
                    treasury_gold=F("treasury_gold") + WAR_WIN_TREASURY_REWARD
                )
                _bump_war_points(winner.id, WAR_WIN_WAR_POINTS)

            personal = _grant_war_rewards(war, winner)

        for al in (a, b):
            if winner is None:
                head = f"⚔️ <b>جنگ اتحادها مساوی شد!</b> ({a.name} {war.score_a} - {war.score_b} {b.name})"
            elif al.id == winner.id:
                head = (
                    f"🏆 <b>اتحادت «{al.name}» جنگ رو برد!</b>\n"
                    f"امتیاز {win_score} در برابر {lose_score} — "
                    f"خزانه {WAR_WIN_TREASURY_REWARD} طلا و {WAR_WIN_WAR_POINTS} امتیاز جنگ گرفت."
                )
            else:
                head = (
                    f"💀 <b>اتحادت «{al.name}» جنگ رو باخت.</b>\n"
                    f"امتیاز {lose_score} در برابر {win_score}. دفعه‌ی بعد قوی‌تر بیاید!"
                )
            for uid in User.objects.filter(alliance_id=al.id, notifications_on=True).values_list("id", flat=True):
                pers = personal.get(uid)
                if pers:
                    reward_bits = [f"{pers['coins']:,} طلا", f"{pers['dna']} DNA"]
                    if pers["diamonds"]:
                        reward_bits.append(f"{pers['diamonds']} 💎")
                    line = "\n\n🎁 <b>پاداش مشارکتت:</b> " + " + ".join(reward_bits)
                    if pers["mvp"]:
                        line += "\n👑 <b>تو بهترین جنگجوی این نبرد بودی (MVP)!</b>"
                    out.append((uid, head + line))
                else:
                    out.append((
                        uid,
                        head + "\n\n<i>تو توی این جنگ شرکت نکردی و پاداشی نگرفتی — "
                        "دفعه‌ی بعد حتماً «شرکت» کن!</i>",
                    ))
    return out


def _bump_war_points(alliance_id: int, points: int) -> None:
    """Add weekly war points to an alliance directly (used by the 1-day war winner)."""
    week = _war_week()
    alliance = Alliance.objects.filter(id=alliance_id).first()
    if alliance is None:
        return
    if alliance.war_week != week:
        alliance.war_points = 0
        alliance.war_week = week
    alliance.war_points += int(points)
    alliance.save(update_fields=["war_points", "war_week"])

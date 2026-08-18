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

    user.alliance = alliance
    user.save(update_fields=["alliance"])
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
    if alliance.leader_id == user.id:
        alliance.leader = remaining[0]
        alliance.save(update_fields=["leader"])


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
        "member_count": len(members),
        "members": members,
        "power": _alliance_power(alliance),
        "treasury_gold": alliance.treasury_gold,
    }


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
    if not defender_creatures:
        stolen = round(defender_alliance.treasury_gold * constants.HEIST_STEAL_PERCENT)
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
        stolen = round(defender_alliance.treasury_gold * constants.HEIST_STEAL_PERCENT)
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


# ── Alliance perks + weekly war (game/alliance depth) ─────────────────────────
XP_PERK_PER_LEVEL = 0.03     # +3% lab XP per level, all members
PASS_PERK_PER_LEVEL = 0.05   # +5% Battle Pass points per level, all members
PERK_MAX_LEVEL = 5
WAR_WINNER_TREASURY_BONUS = 5000  # gold added to the top alliance's treasury each week

PERKS = {
    "xp": {"emoji": "⭐", "title": "بونوس XP", "field": "xp_perk_level", "per_level": XP_PERK_PER_LEVEL},
    "pass": {"emoji": "🎟", "title": "بونوس پاس", "field": "pass_perk_level", "per_level": PASS_PERK_PER_LEVEL},
}


def perk_cost(level: int) -> int:
    """Treasury gold to buy the NEXT level (level = current level, 0-based)."""
    return 3000 * (level + 1)


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
    if alliance.leader_id != user.id:
        raise GameError("فقط رهبر اتحاد می‌تونه پرک بخره.")
    field = PERKS[perk_key]["field"]
    level = getattr(alliance, field)
    if level >= PERK_MAX_LEVEL:
        raise GameError("این پرک به بالاترین سطح رسیده.")
    cost = perk_cost(level)
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


def perks_info(alliance: Alliance) -> dict:
    return {
        "xp_level": alliance.xp_perk_level,
        "pass_level": alliance.pass_perk_level,
        "xp_cost": perk_cost(alliance.xp_perk_level),
        "pass_cost": perk_cost(alliance.pass_perk_level),
        "max_level": PERK_MAX_LEVEL,
        "treasury": alliance.treasury_gold,
        "war_points": alliance.war_points if alliance.war_week == _war_week() else 0,
    }


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

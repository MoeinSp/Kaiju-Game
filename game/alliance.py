from django.db import transaction

from bio_lab.models import Alliance, Creature, User
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

from bio_lab.models import Creature, Group, User
from game.combat import resolve_duel_report
from game.creature import GameError


def _power(c: Creature) -> int:
    from game.creature import creature_power

    return creature_power(c)


def get_guardian(group: Group) -> Creature | None:
    return group.guardian_creature


def ensure_guardian(group: Group, members: list[Creature]) -> Creature | None:
    """Returns the group's guardian, auto-crowning the strongest active member if none is set yet."""
    current = get_guardian(group)
    if current is not None:
        return current
    if not members:
        return None
    top = max(members, key=_power)
    group.guardian_creature = top
    group.save(update_fields=["guardian_creature"])
    return top


def challenge_guardian(group: Group, challenger_user: User, challenger_creature: Creature) -> tuple[bool, str]:
    guardian = get_guardian(group)
    if guardian is None:
        raise GameError("این گروه هنوز محافظی نداره. اول /guardian رو بزن.")
    if guardian.owner_id == challenger_user.id:
        raise GameError("تو خودت همین الان محافظ گروهی!")

    report = resolve_duel_report(challenger_creature, guardian)
    won = report["winner"].id == challenger_creature.id
    if won:
        group.guardian_creature = challenger_creature
        group.save(update_fields=["guardian_creature"])
    return won, report

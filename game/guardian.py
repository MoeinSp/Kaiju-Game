from bio_lab.models import Creature, Group, User
from game import constants
from game.combat import resolve_duel_report
from game.creature import GameError


def _power(c: Creature) -> int:
    from game.creature import creature_power

    return creature_power(c)


def salary_for(creature: Creature) -> tuple[int, int]:
    """The guardian's daily salary (coins, DNA) for a creature of this power.

    Scales linearly with the creature's combat power against the game's ceiling
    (ARENA_BOT_MAX_POWER, a maxed mythic 5★): a maxed guardian earns the full
    50,000🪙 / 2,000🧬, a weaker one earns proportionally less down to a floor so
    the claim is always worth making."""
    power = _power(creature)
    frac = min(1.0, max(0.0, power / constants.ARENA_BOT_MAX_POWER))
    coins = max(constants.GUARDIAN_SALARY_MIN_COINS, round(constants.GUARDIAN_SALARY_MAX_COINS * frac))
    dna = max(constants.GUARDIAN_SALARY_MIN_DNA, round(constants.GUARDIAN_SALARY_MAX_DNA * frac))
    return coins, dna


def resign_guardian(group: Group, user: User, members: list[Creature]) -> bool:
    """The current guardian steps down. The seat passes to the strongest member
    creature NOT owned by the resigning player (so they actually leave rather than
    instantly re-crowning themselves); if nobody else qualifies, the seat empties.
    Returns True if the user was the guardian and has now stepped down."""
    current = get_guardian(group)
    if current is None or current.owner_id != user.id:
        return False
    successor = max(
        (c for c in members if c.owner_id != user.id), key=_power, default=None
    )
    group.guardian_creature = successor
    group.save(update_fields=["guardian_creature"])
    return True


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

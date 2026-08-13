from bio_lab.models import Creature, User
from bio_lab.repository import resolve_user
from game.creature import GameError

GRANT_RESOURCE_FIELDS = {"coins": "coins", "dna": "dna_fragments"}


def find_user_or_raise(identifier: str) -> User:
    user = resolve_user(identifier)
    if user is None:
        raise GameError(f"کاربری با شناسه/یوزرنیم «{identifier}» پیدا نشد.")
    return user


def _adjust_resource(identifier: str, resource: str, amount: int, sign: int) -> tuple[User, int]:
    if resource not in GRANT_RESOURCE_FIELDS:
        raise GameError("نوع منبع نامعتبره. باید coins یا dna باشه.")
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


def user_info(identifier: str) -> dict:
    user = find_user_or_raise(identifier)
    creatures = list(Creature.objects.filter(owner=user).order_by("id"))
    return {"user": user, "creatures": creatures}

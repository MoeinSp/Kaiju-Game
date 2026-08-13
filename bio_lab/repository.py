import html

from bio_lab.models import Creature, Group, GroupMembership, User


def get_or_create_user(tg_user) -> tuple[User, bool]:
    user, created = User.objects.get_or_create(
        id=tg_user.id, defaults={"username": tg_user.username, "first_name": tg_user.first_name}
    )
    if not created and (user.username != tg_user.username or user.first_name != tg_user.first_name):
        user.username = tg_user.username
        user.first_name = tg_user.first_name
        user.save(update_fields=["username", "first_name"])
    return user, created


def display_name(user: User) -> str:
    """Escaped for direct interpolation into parse_mode="HTML" messages — usernames
    and first names are user-controlled and may contain '<', '&', etc."""
    if user.username:
        return f"@{html.escape(user.username)}"
    if user.first_name:
        return html.escape(user.first_name)
    return f"بازیکن {user.id}"


def get_or_create_group(chat) -> Group:
    group, _ = Group.objects.get_or_create(id=chat.id, defaults={"title": chat.title})
    return group


def get_active_creature(user: User) -> Creature | None:
    return Creature.objects.filter(owner=user, is_active=True).first()


def touch_membership(group: Group, user: User) -> None:
    """Records that `user` has been active in `group`, so group-scoped features
    (leaderboard, guardian) know who to consider."""
    GroupMembership.objects.get_or_create(group=group, user=user)


def group_member_creatures(group: Group) -> list[Creature]:
    member_ids = GroupMembership.objects.filter(group=group).values_list("user_id", flat=True)
    return list(Creature.objects.filter(owner_id__in=member_ids, is_active=True))


def resolve_user(identifier: str) -> User | None:
    """Looks a player up by telegram id or @username (for owner-only moderation commands)."""
    identifier = identifier.strip().lstrip("@")
    if identifier.isdigit():
        return User.objects.filter(id=int(identifier)).first()
    return User.objects.filter(username__iexact=identifier).first()

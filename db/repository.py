from sqlalchemy import select
from sqlalchemy.orm import Session
from telegram import Chat, User as TgUser

from db.models import Creature, Group, GroupMembership, User


def get_or_create_user(session: Session, tg_user: TgUser) -> tuple[User, bool]:
    user = session.get(User, tg_user.id)
    if user is not None:
        if user.username != tg_user.username or user.first_name != tg_user.first_name:
            user.username = tg_user.username
            user.first_name = tg_user.first_name
            session.commit()
        return user, False
    user = User(id=tg_user.id, username=tg_user.username, first_name=tg_user.first_name)
    session.add(user)
    session.commit()
    return user, True


def display_name(user: User) -> str:
    if user.username:
        return f"@{user.username}"
    if user.first_name:
        return user.first_name
    return f"بازیکن {user.id}"


def get_or_create_group(session: Session, chat: Chat) -> Group:
    group = session.get(Group, chat.id)
    if group is not None:
        return group
    group = Group(id=chat.id, title=chat.title)
    session.add(group)
    session.commit()
    return group


def get_active_creature(session: Session, user: User) -> Creature | None:
    stmt = select(Creature).where(Creature.owner_id == user.id, Creature.is_active.is_(True))
    return session.execute(stmt).scalars().first()


def touch_membership(session: Session, group: Group, user: User) -> None:
    """Records that `user` has been active in `group`, so group-scoped features
    (leaderboard, guardian) know who to consider."""
    stmt = select(GroupMembership).where(
        GroupMembership.group_id == group.id, GroupMembership.user_id == user.id
    )
    if session.execute(stmt).scalar_one_or_none() is None:
        session.add(GroupMembership(group_id=group.id, user_id=user.id))
        session.commit()


def group_member_creatures(session: Session, group: Group) -> list[Creature]:
    stmt = (
        select(Creature)
        .join(GroupMembership, GroupMembership.user_id == Creature.owner_id)
        .where(GroupMembership.group_id == group.id, Creature.is_active.is_(True))
    )
    return list(session.execute(stmt).scalars().all())

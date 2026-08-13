from sqlalchemy import select
from sqlalchemy.orm import Session
from telegram import Chat, User as TgUser

from db.models import Creature, Group, User


def get_or_create_user(session: Session, tg_user: TgUser) -> tuple[User, bool]:
    user = session.get(User, tg_user.id)
    if user is not None:
        if user.username != tg_user.username:
            user.username = tg_user.username
            session.commit()
        return user, False
    user = User(id=tg_user.id, username=tg_user.username)
    session.add(user)
    session.commit()
    return user, True


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

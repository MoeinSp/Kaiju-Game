from sqlalchemy.orm import Session

from db.models import Creature, User
from game.creature import GameError, list_creatures


def gift_creature(session: Session, sender: User, receiver: User, creature: Creature) -> None:
    if creature.owner_id != sender.id:
        raise GameError("این موجود مال تو نیست.")

    was_active = creature.is_active
    creature.owner_id = receiver.id
    creature.is_active = False

    if was_active:
        remaining = [c for c in list_creatures(session, sender) if c.id != creature.id]
        if remaining:
            newest = max(remaining, key=lambda c: c.id)
            newest.is_active = True

    session.commit()

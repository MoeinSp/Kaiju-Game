from bio_lab.models import Creature, User
from game.creature import GameError, list_creatures


def gift_creature(sender: User, receiver: User, creature: Creature) -> None:
    if creature.owner_id != sender.id:
        raise GameError("این موجود مال تو نیست.")

    was_active = creature.is_active
    creature.owner = receiver
    creature.is_active = False
    creature.save(update_fields=["owner", "is_active"])

    if was_active:
        remaining = [c for c in list_creatures(sender) if c.id != creature.id]
        if remaining:
            newest = max(remaining, key=lambda c: c.id)
            newest.is_active = True
            newest.save(update_fields=["is_active"])

"""Player-chosen creatures from paid-offer tickets («اساطیری دلخواه»)."""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from bio_lab.models import CreaturePickTicket, User
from game import constants
from game.creature import GameError

# Stable order for callback indices (name → element from SPECIES).
SPECIES_LIST: list[str] = sorted(constants.SPECIES.keys())
PAGE_SIZE = 8


def pending_count(user: User, rarity: str | None = None) -> int:
    qs = CreaturePickTicket.objects.filter(user=user, used_at__isnull=True)
    if rarity:
        qs = qs.filter(rarity=rarity)
    return qs.count()


def _rarity_word(rarity: str) -> str:
    label = constants.RARITY_LABELS.get(rarity, rarity)
    parts = label.split(maxsplit=1)
    return parts[-1] if parts else rarity


def pick_button_text(user: User, count: int | None = None) -> str | None:
    """Label for the pick CTA. Uses the ticket rarity when every pending ticket matches."""
    qs = CreaturePickTicket.objects.filter(user=user, used_at__isnull=True)
    rarities = list(qs.values_list("rarity", flat=True))
    n = len(rarities) if count is None else int(count)
    if n <= 0:
        return None
    if rarities and len(set(rarities)) == 1:
        return f"انتخاب {_rarity_word(rarities[0])} دلخواه ({n})"
    if count is not None and not rarities:
        return f"انتخاب هیولای دلخواه ({n})"
    return f"انتخاب هیولای دلخواه ({n})"


def next_rarity(user: User) -> str | None:
    row = (
        CreaturePickTicket.objects.filter(user=user, used_at__isnull=True)
        .order_by("id")
        .values_list("rarity", flat=True)
        .first()
    )
    return row


def grant_tickets(user: User, rarity: str = "mythic", count: int = 1) -> int:
    rarity = rarity if rarity in constants.RARITY_ORDER else "mythic"
    n = max(1, int(count or 1))
    CreaturePickTicket.objects.bulk_create(
        [CreaturePickTicket(user=user, rarity=rarity) for _ in range(n)]
    )
    return n


def options(element: str | None = None, page: int = 0) -> dict:
    """Paginated species picker. ``element`` None = all species."""
    names = (
        list(constants.SPECIES_NAMES.get(element, []))
        if element
        else list(SPECIES_LIST)
    )
    names = sorted(names)
    total = len(names)
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(int(page), pages - 1))
    chunk = names[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
    return {
        "element": element,
        "page": page,
        "pages": pages,
        "total": total,
        "items": [
            {
                "name": n,
                "element": constants.SPECIES[n],
                "idx": SPECIES_LIST.index(n),
            }
            for n in chunk
        ],
    }


@transaction.atomic
def redeem(user: User, species_idx: int) -> dict:
    """Spend one unused ticket and create the chosen species at the ticket's rarity."""
    if species_idx < 0 or species_idx >= len(SPECIES_LIST):
        raise GameError("این گونه پیدا نشد.")
    name = SPECIES_LIST[species_idx]
    element = constants.SPECIES[name]
    ticket = (
        CreaturePickTicket.objects.select_for_update()
        .filter(user=user, used_at__isnull=True)
        .order_by("id")
        .first()
    )
    if ticket is None:
        raise GameError("بلیت انتخاب هیولا نداری.")
    from game import itemshop

    # rarity comes ONLY from the ticket — a legendary pick must never mint a mythic
    rarity = ticket.rarity if ticket.rarity in constants.RARITY_ORDER else "mythic"
    creature = itemshop._make_creature(user, rarity, element, name)
    if creature.rarity != rarity:
        raise GameError("خطا در ساخت هیولا — دوباره تلاش کن.")
    ticket.used_at = timezone.now()
    ticket.result_creature = creature
    ticket.save(update_fields=["used_at", "result_creature"])
    left = pending_count(user)
    return {
        "creature": creature,
        "name": creature.name,
        "element": creature.element,
        "rarity": creature.rarity,
        "left": left,
    }

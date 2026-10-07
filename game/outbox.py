"""Messages the WEB process wants the BOT process to send.

The Mini App runs in the Django web container, which has no Telegram connection. When
something there needs a DM right away (today: «someone outbid you» in the black market)
it calls push(); the bot's outbox job (bot/handlers/notify.py) drains the table every few
seconds with pop_all() and sends each one. Rows are deleted as they are handed over, so a
message goes out at most once; anything older than MAX_AGE is dropped unsent — a late
«you were outbid» is worse than none.
"""

from __future__ import annotations

import datetime

from django.db import transaction
from django.utils import timezone

from bio_lab.models import BotOutbox

MAX_AGE = datetime.timedelta(minutes=30)
BATCH = 50


def push(kind: str, payload: dict) -> None:
    BotOutbox.objects.create(kind=kind, payload=payload)


@transaction.atomic
def pop_all() -> list[tuple[str, dict]]:
    """Take (and delete) the waiting messages, oldest first."""
    rows = list(BotOutbox.objects.select_for_update().order_by("id")[:BATCH])
    if not rows:
        return []
    BotOutbox.objects.filter(id__in=[r.id for r in rows]).delete()
    fresh_after = timezone.now() - MAX_AGE
    return [(r.kind, r.payload) for r in rows if r.created_at >= fresh_after]

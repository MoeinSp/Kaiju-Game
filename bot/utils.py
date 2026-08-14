from asgiref.sync import sync_to_async
from telegram.error import BadRequest

from game import constants
from game.emoji import get_emoji


def mission_reward_text(m: dict) -> str:
    """One mission's payout, formatted. Lives here rather than in a handler so
    every screen that shows missions (private/group/battle) renders the same
    thing — a mission can pay coins, DNA and a speed-up card, and it's easy to
    forget one of them when each screen formats its own."""
    parts = [f"+{m['coins']} {get_emoji('coin')}"]
    if m.get("dna"):
        parts.append(f"+{m['dna']} {get_emoji('dna')}")
    if m.get("speedup"):
        parts.append(f"+{constants.SPEEDUP_LABELS[m['speedup']]}")
    return " ".join(parts)


async def run_db(func, *args, **kwargs):
    """Runs a synchronous Django-ORM function off the event loop and awaits its result.
    thread_sensitive keeps all DB access on one worker thread, matching SQLite's
    single-writer model and avoiding cross-thread connection issues."""
    return await sync_to_async(func, thread_sensitive=True)(*args, **kwargs)


async def safe_edit_message_text(query, text, **kwargs) -> None:
    """query.edit_message_text(), but swallows Telegram's "Message is not modified"
    BadRequest — every callback in this bot re-renders its view unconditionally
    after an action, and Telegram rejects the edit outright if a repeat tap (or a
    no-op action like "make unlimited" on an already-unlimited channel) would
    produce byte-identical text+markup. Any other BadRequest still propagates."""
    try:
        await query.edit_message_text(text, **kwargs)
    except BadRequest as exc:
        if "Message is not modified" not in str(exc):
            raise

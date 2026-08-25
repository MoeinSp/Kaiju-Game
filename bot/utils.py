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
        # "+⏱ ۳۰ دقیقه" read as "this mission takes 30 minutes" or "you have 30
        # minutes left". Naming the item and what it does removes both readings.
        parts.append(f"+۱ کارت سرعت {constants.SPEEDUP_PLAIN_LABELS[m['speedup']]} ⏱")
    return " ".join(parts)


def _run_db_sync(func, args, kwargs):
    # Each pool thread keeps its own Django DB connection; drop any that Postgres
    # has since closed (idle-timeout) so a reused thread never hits a stale socket.
    from django.db import close_old_connections

    close_old_connections()
    try:
        return func(*args, **kwargs)
    finally:
        close_old_connections()


async def run_db(func, *args, **kwargs):
    """Runs a synchronous Django-ORM function off the event loop and awaits its result.

    thread_sensitive=False deliberately: it runs on a real thread POOL so many
    players' DB work executes in parallel. The old thread_sensitive=True funnelled
    EVERY query bot-wide onto one worker thread (a leftover from the SQLite dev DB) —
    on the Postgres production DB that just serialised everyone and made the bot feel
    slow under load. Postgres handles concurrent connections fine; per-transaction
    locking (select_for_update in the money paths) keeps writes correct."""
    return await sync_to_async(_run_db_sync, thread_sensitive=False)(func, args, kwargs)


async def send_screen(update, text, *, reply_markup=None, parse_mode="HTML", **kwargs) -> None:
    """Render a screen the right way for however the player got here.

    Pressing a button used to post a *new* message, because the same handler
    serves both `/collection` and the «کلکسیون» button and it only knew how to
    reply. Walking the menu therefore buried the chat in near-identical cards.
    Now the update itself decides: arriving from a button edits the message in
    place, so the menu behaves like one screen the player navigates; arriving
    from a command posts a new one, which is what a typed command should do.

    Every menu screen goes through here, so the two paths can't drift.
    """
    query = getattr(update, "callback_query", None)
    if query is not None:
        await safe_edit_message_text(
            query, text, reply_markup=reply_markup, parse_mode=parse_mode, **kwargs
        )
        return
    await update.effective_message.reply_text(
        text, reply_markup=reply_markup, parse_mode=parse_mode, **kwargs
    )


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

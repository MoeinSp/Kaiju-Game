"""The periodic job that delivers re-engagement DMs.

game/notifications.collect_due() decides *what* to send (and marks it sent); this
job is the async half that actually sends. Scheduled on PTB's JobQueue so there's
no external cron — one repeating job, same event loop as the webhook listener.
"""

import asyncio

from telegram.error import Forbidden, TelegramError
from telegram.ext import ContextTypes

from bot.utils import run_db
from game.notifications import collect_due

NOTIFY_INTERVAL_SECONDS = 300  # scan every 5 minutes — finer than any timer needs
SEND_DELAY_SECONDS = 0.05  # ~20 msg/s, well under Telegram's flood limit


def _opt_out(user_id: int) -> None:
    """A player who blocked the bot shouldn't be retried — turn their master
    switch off so the collector stops queueing DMs for them."""
    from bio_lab.models import User

    User.objects.filter(id=user_id).update(notifications_on=False)


async def notify_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    pending = await run_db(collect_due)
    for user_id, text in pending:
        try:
            await context.bot.send_message(chat_id=user_id, text=text, parse_mode="HTML")
        except Forbidden:
            await run_db(_opt_out, user_id)  # blocked the bot / never opened DMs
        except TelegramError:
            pass  # transient — the row is already marked, so it just won't retry
        await asyncio.sleep(SEND_DELAY_SECONDS)


def register(application) -> None:
    job_queue = application.job_queue
    if job_queue is None:
        # the python-telegram-bot[job-queue] extra isn't installed; run without
        # push notifications rather than crashing the bot.
        import logging

        logging.getLogger(__name__).warning("JobQueue unavailable — push notifications disabled.")
        return
    job_queue.run_repeating(notify_job, interval=NOTIFY_INTERVAL_SECONDS, first=30)

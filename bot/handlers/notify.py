"""The periodic job that delivers re-engagement DMs.

game/notifications.collect_due() decides *what* to send (and marks it sent); this
job is the async half that actually sends. Scheduled on PTB's JobQueue so there's
no external cron — one repeating job, same event loop as the webhook listener.
"""

import asyncio

from telegram import InlineKeyboardMarkup
from telegram.error import Forbidden, TelegramError
from telegram.ext import ContextTypes

from bot.buttons import DANGER, btn
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
    for item in pending:
        # items are (user_id, text) or (user_id, text, revenge_callback_data). The
        # optional 3rd element attaches a red inline "revenge" button that routes
        # straight into the arena revenge pre-fight (arena_revenge:<log_id>).
        user_id, text = item[0], item[1]
        revenge_cb = item[2] if len(item) > 2 else None
        reply_markup = None
        if revenge_cb:
            reply_markup = InlineKeyboardMarkup(
                [[btn("انتقام", emoji_key="btn_revenge", style=DANGER, callback_data=revenge_cb)]]
            )
        try:
            await context.bot.send_message(
                chat_id=user_id, text=text, parse_mode="HTML", reply_markup=reply_markup
            )
        except Forbidden:
            await run_db(_opt_out, user_id)  # blocked the bot / never opened DMs
        except TelegramError:
            pass  # transient — the row is already marked, so it just won't retry
        await asyncio.sleep(SEND_DELAY_SECONDS)


AUTOBACKUP_CHECK_SECONDS = 1800  # check every 30 min; the interval itself gates the actual run


async def autobackup_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """If the owner turned on auto-backup and its interval has elapsed, build a DB
    backup and DM it to the owner."""
    from config import OWNER_TELEGRAM_ID
    from game import botconfig
    from game.backup import create_backup

    if not await run_db(botconfig.due_backup):
        return
    try:
        meta = await run_db(create_backup, "auto")
        with open(meta["path"], "rb") as fh:
            await context.bot.send_document(
                chat_id=OWNER_TELEGRAM_ID, document=fh, filename=meta["name"],
                caption="💾 بکاپ خودکار دیتابیس",
            )
        await run_db(botconfig.mark_backup_done)
    except (Forbidden, TelegramError, OSError):
        pass


def register(application) -> None:
    job_queue = application.job_queue
    if job_queue is None:
        # the python-telegram-bot[job-queue] extra isn't installed; run without
        # push notifications rather than crashing the bot.
        import logging

        logging.getLogger(__name__).warning("JobQueue unavailable — push notifications disabled.")
        return
    job_queue.run_repeating(notify_job, interval=NOTIFY_INTERVAL_SECONDS, first=30)
    job_queue.run_repeating(autobackup_job, interval=AUTOBACKUP_CHECK_SECONDS, first=120)

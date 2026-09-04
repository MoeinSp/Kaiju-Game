"""The periodic job that delivers re-engagement DMs.

game/notifications.collect_due() decides *what* to send (and marks it sent); this
job is the async half that actually sends. Scheduled on PTB's JobQueue so there's
no external cron — one repeating job, same event loop as the webhook listener.
"""

import asyncio

from telegram import InlineKeyboardMarkup
from telegram.error import Forbidden, TelegramError
from telegram.ext import ContextTypes

from bot.buttons import DANGER, NAV, btn
from bot.utils import run_db
from game.notifications import collect_due

NOTIFY_INTERVAL_SECONDS = 300  # scan every 5 minutes — finer than any timer needs
SEND_DELAY_SECONDS = 0.05  # ~20 msg/s, well under Telegram's flood limit


def _defense_details_button(attacker_id):
    if not attacker_id:
        return None
    return InlineKeyboardMarkup([[btn("🔍 جزییات حریف", style=NAV, callback_data=f"defrep_opp:{attacker_id}")]])


def _arena_button():
    from bot.buttons import BATTLE

    return InlineKeyboardMarkup([[btn("آرنا", emoji_key="btn_arena", style=BATTLE, callback_data="menu:arena")]])


def _defense_report_keyboard(defense: dict, *, group: bool):
    """Buttons under a defense report. The labels carry a compact summary (power, the
    attacker's cup, coins looted). Revenge is ARENA-ONLY; group «اتک» has no revenge."""
    power = defense.get("attacker_power", 0)
    cup = defense.get("attacker_cup")
    loot = defense.get("loot", 0)
    rows = []
    if not group and defense.get("log_id"):
        cup_bit = f" · 🏆{cup}" if cup is not None else ""
        rows.append([btn(f"⚔️ انتقام · 💪{power}{cup_bit}", emoji_key="btn_revenge", style=DANGER,
                         callback_data=f"arena_revenge:{defense['log_id']}")])
    details_label = f"🔍 جزییات حریف · 💪{power}" + (f" · 💰{loot}" if loot else "")
    rows.append([btn(details_label, style=NAV, callback_data=f"defrep_opp:{defense['attacker_id']}")])
    return InlineKeyboardMarkup(rows)


async def send_defense_report_now(context, defense: dict, *, group: bool = False) -> None:
    """Send the 'you were attacked' DM the INSTANT a raid resolves (no waiting for the
    5-minute catch-up job). Shows the attacker's full creature card (HP/ATK/…) inline,
    plus inline buttons: «انتقام» (ARENA only) and «جزییات حریف», whose captions carry a
    quick summary (power / cup / loot). No-ops if the defender has DMs off / blocked."""
    if defense is None or not defense.get("notifications_on", True):
        return
    from bot.handlers.arena import _user_details_sync, opponent_details_text
    from game.notifications import defense_report_text

    head = defense_report_text(
        defense["attacker_name"], defense["attacker_power"], defense["attacker_won"],
        defense.get("loot", 0), defense.get("cup_change", 0),
        attacker_alliance=defense.get("attacker_alliance"),
    )
    try:
        d = await run_db(_user_details_sync, defense["attacker_id"])
        text = head + "\n\n━━━━━━━━━━\n" + opponent_details_text(d)
    except Exception:  # noqa: BLE001 — a details hiccup must not drop the report
        text = head
    try:
        await context.bot.send_message(
            chat_id=defense["defender_id"], text=text, parse_mode="HTML",
            reply_markup=_defense_report_keyboard(defense, group=group),
        )
    except Forbidden:
        await run_db(_opt_out, defense["defender_id"])
    except TelegramError:
        pass


def _opt_out(user_id: int) -> None:
    """A player who blocked the bot shouldn't be retried — turn their master
    switch off so the collector stops queueing DMs for them."""
    from bio_lab.models import User

    User.objects.filter(id=user_id).update(notifications_on=False)


async def notify_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    pending = await run_db(collect_due)
    for item in pending:
        # items are (user_id, text[, _unused[, attacker_id]]). The 4th element, when
        # present, attaches a «🔍 جزییات حریف» button (defense reports). No revenge
        # button — the defense report never offers revenge (that lives in «انتقام‌ها»).
        user_id, text = item[0], item[1]
        marker = item[2] if len(item) > 2 else None
        attacker_id = item[3] if len(item) > 3 else None
        if marker == "arena":
            reply_markup = _arena_button()
        else:
            reply_markup = _defense_details_button(attacker_id)
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
    dest = await run_db(botconfig.get_backup_chat_id) or OWNER_TELEGRAM_ID
    try:
        meta = await run_db(create_backup, "auto")
        with open(meta["path"], "rb") as fh:
            await context.bot.send_document(
                chat_id=dest, document=fh, filename=meta["name"],
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

"""Group flash-drop job + claim button.

game/groupdrops decides *what/where/when* (sync); this posts the drops, edits
lapsed ones, and settles the first-tap-wins claim.
"""

import asyncio

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import CallbackQueryHandler, ContextTypes

from bio_lab.models import GroupDrop
from bot.utils import run_db, safe_edit_message_text
from game import groupdrops

DROPS_INTERVAL_SECONDS = 300  # a spawn check every 5 minutes
SEND_DELAY = 0.05


def _delete_drop(drop_id: int) -> None:
    GroupDrop.objects.filter(id=drop_id).delete()


async def drops_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    # spawn new drops
    for d in await run_db(groupdrops.due_spawns):
        text = (
            f"{d['emoji']} <b>{d['title']}</b>\n{d['flavor']}\n\n"
            "<i>اولین نفری که بزنه می‌بره! 👇</i>"
        )
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(d["btn"], callback_data=f"gdrop:{d['id']}")]])
        try:
            msg = await context.bot.send_message(chat_id=d["group_id"], text=text, parse_mode="HTML", reply_markup=keyboard)
            await run_db(groupdrops.set_message_id, d["id"], msg.message_id)
        except TelegramError:
            await run_db(_delete_drop, d["id"])  # bot not in the group anymore, etc.
        await asyncio.sleep(SEND_DELAY)

    # edit any that lapsed unclaimed
    for e in await run_db(groupdrops.expire_due):
        try:
            await context.bot.edit_message_text(
                chat_id=e["group_id"], message_id=e["message_id"],
                text="⌛ <b>زمان این جایزه تموم شد</b> — کسی به‌موقع نزد.", parse_mode="HTML",
            )
        except TelegramError:
            pass
        await asyncio.sleep(SEND_DELAY)


async def drop_claim_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    drop_id = int(query.data.split(":")[1])
    result = await run_db(groupdrops.claim, drop_id, update.effective_user)

    status = result["status"]
    if status == "won":
        await query.answer(f"🎉 بردی! {groupdrops.reward_text(result['reward'])}", show_alert=True)
        cfg = groupdrops.DROP_KINDS[result["kind"]]
        await safe_edit_message_text(
            query,
            f"{cfg['emoji']} <b>{cfg['title']}</b>\n"
            f"🎉 <b>{result['winner']}</b> اولین نفر بود و <b>{groupdrops.reward_text(result['reward'])}</b> برد!",
            parse_mode="HTML",
        )
    elif status == "taken":
        await query.answer(f"⛔ دیر رسیدی! «{result['winner']}» زودتر زد.", show_alert=True)
    elif status == "expired":
        await query.answer("⌛ زمان این جایزه تموم شده.", show_alert=True)
    else:
        await query.answer("این جایزه دیگه در دسترس نیست.", show_alert=True)


def register(application) -> None:
    application.add_handler(CallbackQueryHandler(drop_claim_callback, pattern=r"^gdrop:"))
    job_queue = application.job_queue
    if job_queue is not None:
        job_queue.run_repeating(drops_job, interval=DROPS_INTERVAL_SECONDS, first=90)

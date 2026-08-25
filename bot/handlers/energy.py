"""The «شارژ انرژی با الماس» button + its confirm→charge flow.

Offered wherever the bot says "not enough energy" (game.energy.EnergyError). The
button and both callbacks work in a group or the DM, and a confirm step always runs
before any diamonds are spent.
"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes

from bio_lab.repository import get_or_create_user
from bot.utils import run_db, safe_edit_message_text
from game import constants
from game.creature import GameError


def energy_refill_button() -> InlineKeyboardButton:
    """A single button to hang under any 'out of energy' message."""
    return InlineKeyboardButton(
        f"⚡ شارژ کامل انرژی ({constants.ENERGY_REFILL_DIAMOND_COST} 💎)",
        callback_data="enr:ask",
    )


def energy_refill_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[energy_refill_button()]])


async def show_energy_error(query, exc) -> bool:
    """If `exc` is an out-of-energy error, replace the message with it + the refill
    button and return True; otherwise return False so the caller shows it normally."""
    from game.energy import EnergyError

    if isinstance(exc, EnergyError):
        await query.answer()
        await safe_edit_message_text(query, str(exc), reply_markup=energy_refill_markup())
        return True
    return False


async def energy_ask_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(f"✅ بله ({constants.ENERGY_REFILL_DIAMOND_COST} 💎)",
                             callback_data="enr:do"),
        InlineKeyboardButton("❌ بی‌خیال", callback_data="enr:no"),
    ]])
    await safe_edit_message_text(
        query,
        f"⚡ <b>شارژ کامل انرژی</b>\n\nانرژیت به <b>{constants.MAX_ENERGY}</b> پر می‌شه و "
        f"<b>{constants.ENERGY_REFILL_DIAMOND_COST}</b> الماس ازت کم می‌شه. تأیید می‌کنی؟",
        parse_mode="HTML", reply_markup=keyboard,
    )


async def energy_no_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await safe_edit_message_text(query, "باشه، فعلاً شارژ نشد.")


def _refill_sync(tg_user):
    from game.energy import refill_energy

    user, _ = get_or_create_user(tg_user)
    return refill_energy(user)


async def energy_do_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        result = await run_db(_refill_sync, update.effective_user)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer("⚡ پر شد!")
    await safe_edit_message_text(
        query,
        f"⚡ <b>انرژی پر شد!</b> الان {result['energy']}/{constants.MAX_ENERGY} داری "
        f"(<b>{result['cost']}</b> الماس کم شد).",
        parse_mode="HTML",
    )


def register(application) -> None:
    application.add_handler(CallbackQueryHandler(energy_ask_callback, pattern=r"^enr:ask$"))
    application.add_handler(CallbackQueryHandler(energy_do_callback, pattern=r"^enr:do$"))
    application.add_handler(CallbackQueryHandler(energy_no_callback, pattern=r"^enr:no$"))

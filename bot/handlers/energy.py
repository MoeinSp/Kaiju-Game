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


def energy_refill_button(owner_id: int) -> InlineKeyboardButton:
    """A single button to hang under any 'out of energy' message.

    `owner_id` is the telegram id of the player the message is for, embedded in the
    callback so that in a GROUP nobody else can tap it and spend *their own* diamonds
    on a prompt that was never shown to them (that was a real cross-player bug)."""
    from game import botconfig

    return InlineKeyboardButton(
        f"⚡ شارژ کامل انرژی ({botconfig.get_energy_refill_cost()} 💎)",
        callback_data=f"enr:ask:{owner_id}",
    )


def energy_refill_markup(owner_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[energy_refill_button(owner_id)]])


def _owner_ok(query, parts) -> bool:
    """True if the tapping user owns this scoped energy button (or it's an old,
    unscoped button with no owner encoded — those stay tap-by-anyone as before)."""
    if len(parts) < 3:
        return True
    return query.from_user is not None and query.from_user.id == int(parts[2])


async def show_energy_error(query, exc, owner_id: int | None = None) -> bool:
    """If `exc` is an out-of-energy error, replace the message with it + the refill
    button and return True; otherwise return False so the caller shows it normally."""
    from game.energy import EnergyError

    if isinstance(exc, EnergyError):
        await query.answer()
        oid = owner_id if owner_id is not None else query.from_user.id
        await safe_edit_message_text(query, str(exc), reply_markup=energy_refill_markup(oid))
        return True
    return False


async def energy_ask_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    parts = query.data.split(":")
    if not _owner_ok(query, parts):
        await query.answer("این دکمه مال تو نیست 🙂", show_alert=True)
        return
    owner_id = parts[2] if len(parts) > 2 else query.from_user.id
    from game import botconfig

    cost = botconfig.get_energy_refill_cost()
    await query.answer()
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(f"✅ بله ({cost} 💎)",
                             callback_data=f"enr:do:{owner_id}"),
        InlineKeyboardButton("❌ بی‌خیال", callback_data=f"enr:no:{owner_id}"),
    ]])
    await safe_edit_message_text(
        query,
        f"⚡ <b>شارژ کامل انرژی</b>\n\nانرژیت به <b>{constants.MAX_ENERGY}</b> پر می‌شه و "
        f"<b>{cost}</b> الماس ازت کم می‌شه. تأیید می‌کنی؟",
        parse_mode="HTML", reply_markup=keyboard,
    )


async def energy_no_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _owner_ok(query, query.data.split(":")):
        await query.answer("این دکمه مال تو نیست 🙂", show_alert=True)
        return
    await query.answer()
    await safe_edit_message_text(query, "باشه، فعلاً شارژ نشد.")


def _refill_sync(tg_user):
    from game.energy import refill_energy

    user, _ = get_or_create_user(tg_user)
    return refill_energy(user)


async def energy_do_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _owner_ok(query, query.data.split(":")):
        await query.answer("این دکمه مال تو نیست 🙂", show_alert=True)
        return
    try:
        result = await run_db(_refill_sync, update.effective_user)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer("⚡ پر شد!")
    # a way back so the player returns to what they were doing (arena/hunt/…) instead
    # of a dead-end message. In the DM that's the main menu; in a group, the bot's PV.
    is_private = update.effective_chat is not None and update.effective_chat.type == "private"
    if is_private:
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 بازگشت به بازی", callback_data="menu:me"),
        ]])
    else:
        from config import BOT_USERNAME

        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 ادامه توی پیوی ربات", url=f"https://t.me/{BOT_USERNAME}?start=play"),
        ]])
    await safe_edit_message_text(
        query,
        f"⚡ <b>انرژی پر شد!</b> الان {result['energy']}/{constants.MAX_ENERGY} داری "
        f"(<b>{result['cost']}</b> الماس کم شد).\n<i>برگرد و کارتو ادامه بده 👇</i>",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


def register(application) -> None:
    application.add_handler(CallbackQueryHandler(energy_ask_callback, pattern=r"^enr:ask(:|$)"))
    application.add_handler(CallbackQueryHandler(energy_do_callback, pattern=r"^enr:do(:|$)"))
    application.add_handler(CallbackQueryHandler(energy_no_callback, pattern=r"^enr:no(:|$)"))

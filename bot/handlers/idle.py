"""«💤 پاداش آفلاین» — غیرفعال شد."""

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

from bot.buttons import back_btn
from bot.utils import send_screen


async def idle_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = "💤 <b>پاداش آفلاین</b>\n\nاین قابلیت از بازی حذف شده است."
    keyboard = InlineKeyboardMarkup([[back_btn("menu:hub_base", "بازگشت به پایگاه")]])
    await send_screen(update, text, parse_mode="HTML", reply_markup=keyboard)


async def idle_collect_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query:
        await query.answer("این قابلیت از بازی حذف شده است.", show_alert=True)


async def idle_dungeon_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query:
        await query.answer("این قابلیت از بازی حذف شده است.", show_alert=True)


def register(application) -> None:
    application.add_handler(CommandHandler("idle", idle_panel, filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(idle_collect_callback, pattern=r"^idle_collect$"))
    application.add_handler(CallbackQueryHandler(idle_dungeon_callback, pattern=r"^idle_dungeon$"))


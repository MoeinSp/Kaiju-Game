"""«🏅 لقب‌ها» — pick a prestige title (unlocked by your progress) to show off."""

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

from bio_lab.repository import get_or_create_user
from bot.buttons import LIST, PRIMARY, back_btn, btn
from bot.utils import run_db, safe_edit_message_text, send_screen
from game import titles
from game.creature import GameError


def _panel_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    return user.title, titles.available(user), len(titles.TITLES)


def _render(equipped, avail, total) -> tuple[str, InlineKeyboardMarkup]:
    lines = [
        f"🏅 <b>لقب‌ها</b>  ({len(avail)}/{total} باز شده)",
        "<blockquote>لقب‌ها با پیشرفتت باز می‌شن و کنار اسم آزمایشگاهت نشون داده می‌شن. "
        "یکی رو انتخاب کن تا پز بدی!</blockquote>",
    ]
    rows = []
    for t in avail:
        mark = "✅ " if t["equipped"] else ""
        rows.append([btn(f"{mark}{t['emoji']} {t['title']}", style=PRIMARY if t["equipped"] else LIST, callback_data=f"title_set:{t['key']}")])
    if equipped:
        rows.append([btn("❌ برداشتن لقب", style=LIST, callback_data="title_set:none")])
    rows.append([back_btn("menu:profile")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def titles_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    equipped, avail, total = await run_db(_panel_sync, update.effective_user)
    text, keyboard = _render(equipped, avail, total)
    await send_screen(update, text, parse_mode="HTML", reply_markup=keyboard)


def _set_sync(tg_user, key):
    user, _ = get_or_create_user(tg_user)
    titles.equip(user, key)
    return user.title, titles.available(user), len(titles.TITLES)


async def title_set_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    key = query.data.split(":")[1]
    try:
        equipped, avail, total = await run_db(_set_sync, update.effective_user, key)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer("✅ لقب تنظیم شد" if key != "none" else "لقب برداشته شد")
    text, keyboard = _render(equipped, avail, total)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


def register(application) -> None:
    application.add_handler(CommandHandler("titles", titles_panel, filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(title_set_callback, pattern=r"^title_set:"))

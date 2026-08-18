"""«🎰 بنر ویژه» — the featured-creature gacha with a pity counter."""

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

from bio_lab.repository import get_or_create_user
from bot.buttons import CONFIRM, SHOP, back_btn, btn
from bot.utils import run_db, safe_edit_message_text, send_screen
from game import banner, constants
from game.creature import GameError
from game.emoji import get_emoji


def _panel_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    return banner.status(user)


def _render(st: dict) -> tuple[str, InlineKeyboardMarkup]:
    to_pity = max(0, st["pity_threshold"] - st["pity"])
    bar = constants.render_bar(st["pity"], st["pity_threshold"], width=10)
    lines = [
        "🎰 <b>بنر ویژه‌ی این هفته</b>",
        f"<blockquote>⭐ هیولای ویژه: <b>{st['featured_label']}</b>\n"
        "با کشیدن بنر، اگه نتیجه حماسی یا بالاتر باشه، به‌احتمال زیاد همین هیولای ویژه‌ست.</blockquote>",
        f"\n🎯 <b>تضمین افسانه‌ای:</b> {bar} {st['pity']}/{st['pity_threshold']}",
        f"<i>تا {to_pity} کشش دیگه، یه افسانه‌ای تضمینی می‌گیری.</i>",
        f"\n{get_emoji('diamond')} هزینه‌ی هر کشش: <b>{st['cost']}</b>  (موجودی: {st['diamonds']})",
    ]
    rows = [
        [btn(f"🎰 کشیدن بنر ({st['cost']} 💎)", emoji_key="btn_confirm", style=CONFIRM, callback_data="banner_pull")],
        [back_btn("menu:me")],
    ]
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def banner_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    st = await run_db(_panel_sync, update.effective_user)
    text, keyboard = _render(st)
    await send_screen(update, text, parse_mode="HTML", reply_markup=keyboard)


def _pull_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    result = banner.pull(user)
    return result, banner.status(user)


async def banner_pull_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        result, st = await run_db(_pull_sync, update.effective_user)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer("🎰 کشیده شد!")
    cr = result["creature"]
    tags = []
    if result["is_featured"]:
        tags.append("⭐ <b>هیولای ویژه!</b>")
    if result["guaranteed"]:
        tags.append("🎯 <b>تضمین افسانه‌ای!</b>")
    tag_line = ("\n" + " · ".join(tags)) if tags else ""
    text, keyboard = _render(st)
    await safe_edit_message_text(
        query,
        f"🎰 <b>نتیجه‌ی بنر:</b>\n\n"
        f"<tg-spoiler>{get_emoji('egg')} <b>{cr.name}</b>\n"
        f"{constants.element_label(cr.element)} · {constants.RARITY_LABELS[cr.rarity]}</tg-spoiler>"
        f"{tag_line}\n\n<i>از «🗂 کلکسیون» می‌تونی فعالش کنی.</i>\n\n━━━━━━━━━━\n" + text,
        parse_mode="HTML",
        reply_markup=keyboard,
    )


def register(application) -> None:
    application.add_handler(CommandHandler("banner", banner_panel, filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(banner_pull_callback, pattern=r"^banner_pull$"))

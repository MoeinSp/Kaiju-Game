"""«🎪 جشنواره» — the private-chat screens for game/festival.py (home / shop / how-to).

Screens are kept short on purpose: they may be shown as a photo caption (~1,000 chars)."""

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

from bio_lab.repository import get_or_create_user
from bot.buttons import CONFIRM, LIST, NAV, SHOP, back_btn, btn
from bot.utils import alert_text, run_db, safe_edit_message_text, send_screen
from game import constants, festival
from game.creature import GameError

_DIV = "━━━━━━━━━━━━━━━━━━━━"
COIN = "🎟"


def _fmt(seconds: int) -> str:
    d, rem = divmod(max(0, int(seconds)), 86400)
    h = rem // 3600
    return f"{d} روز و {h} ساعت" if d else f"{max(1, h)} ساعت"


def _home_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    return festival.status(user)


def _home_render(st: dict, note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    th = st["theme"]
    rows = []
    if not st["active"]:
        lines = [
            "🎪 <b>جشنواره‌ی ماهانه</b>", _DIV,
            "<blockquote>هر ماه یه هفته جشنواره‌ست: از همه‌ی کارهات «سکه‌ی جشنواره» می‌افته و می‌تونی "
            "از فروشگاه مخصوصش خرید کنی. سکه‌ها آخر جشنواره باطل می‌شن.</blockquote>",
            f"⏳ جشنواره‌ی بعدی: <b>{_fmt(st['seconds_until_next'])}</b> دیگه "
            f"(روز {festival.FESTIVAL_START_DAY} تا {festival.FESTIVAL_END_DAY} هر ماه)",
        ]
    else:
        lines = [
            f"{th['emoji']} <b>جشنواره‌ی «{th['title']}»</b>", _DIV,
            f"⏳ تا پایان: <b>{_fmt(st['seconds_left'])}</b>",
            f"{COIN} سکه‌ی تو: <code>{st['coins']:,}</code> · جمع‌شده: <code>{st['earned']:,}</code>"
            + (f" · رتبه: <b>{st['rank']}</b>" if st["rank"] else ""),
            f"👑 جایزه‌ی بزرگ: هیولای افسانه‌ایِ {constants.element_label(th['element'])} "
            f"(<code>{festival.SHOP['grand'][2]}</code> سکه)",
        ]
        if st["top"]:
            lines += ["", "🏅 <b>بیشترین سکه‌ی جمع‌شده:</b>"]
            for r in st["top"]:
                prize = festival.rank_prize(r["rank"])
                lines.append(f"{r['rank']}. {r['name']} — <code>{r['earned']:,}</code>" + (f" (💎{prize})" if prize else ""))
        lines.append("<i>سکه‌ها آخر جشنواره باطل می‌شن؛ قبلش خرجشون کن.</i>")
        rows.append([btn("فروشگاه جشنواره", emoji_key="btn_shop", style=SHOP, callback_data="fest:shop"),
                     btn("چطور سکه بگیرم؟", emoji_key="btn_report", style=NAV, callback_data="fest:how")])
    if note:
        lines = [note, ""] + lines
    rows.append([back_btn("menu:hub_city", "بازگشت به شهر")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def festival_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    st = await run_db(_home_sync, update.effective_user)
    text, keyboard = _home_render(st)
    await send_screen(update, text, parse_mode="HTML", reply_markup=keyboard)


async def festival_home_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    st = await run_db(_home_sync, update.effective_user)
    await query.answer()
    text, keyboard = _home_render(st)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


def _how_text() -> str:
    lines = ["🎟 <b>چطور سکه‌ی جشنواره بگیرم؟</b>", _DIV]
    for action, (per, cap) in festival.EARN.items():
        lines.append(f"• {festival.EARN_LABELS[action]}: <code>{per}</code> سکه (تا <code>{per * cap}</code> در روز)")
    lines.append(f"• هر مأموریت روزانه: <code>{festival.MISSION_DAILY_COINS}</code> · هفتگی: <code>{festival.MISSION_WEEKLY_COINS}</code>")
    lines.append("")
    lines.append("<i>هر کار سقف روزانه داره؛ برای سکه‌ی بیشتر باید به همه‌ی بخش‌های بازی سر بزنی.</i>")
    return "\n".join(lines)


async def festival_how_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await safe_edit_message_text(
        query, _how_text(), parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[back_btn("fest:home", "بازگشت")]]),
    )


def _shop_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    return festival.shop_state(user)


def _shop_render(shop: dict, note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    th = shop["theme"]
    lines = [f"{th['emoji']} <b>فروشگاه جشنواره</b>", _DIV, f"{COIN} سکه‌ی تو: <code>{shop['coins']:,}</code>", ""]
    if note:
        lines = [note, ""] + lines
    rows = []
    for it in shop["items"]:
        left = f"{it['left']} مونده" if it["left"] else "تموم شد"
        lines.append(f"{it['emoji']} {it['title']} — <code>{it['cost']}</code> {COIN} ({left})")
        if it["left"] and shop["coins"] >= it["cost"]:
            rows.append([btn(f"{it['title']} ({it['cost']})", style=(CONFIRM if it["key"] == "grand" else LIST),
                             callback_data=f"fest:buy:{it['key']}")])
    if not rows:
        lines.append("\n<i>فعلاً سکه‌ات به هیچ‌کدوم نمی‌رسه.</i>")
    rows.append([back_btn("fest:home", "بازگشت")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def festival_shop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        shop = await run_db(_shop_sync, update.effective_user)
    except GameError as exc:
        await query.answer(alert_text(exc), show_alert=True)
        return
    await query.answer()
    text, keyboard = _shop_render(shop)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


def _buy_sync(tg_user, item_key):
    user, _ = get_or_create_user(tg_user)
    res = festival.buy(user, item_key)
    return f"✅ <b>خریدی:</b> {res['got']}", festival.shop_state(user)


async def festival_buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    item_key = query.data.split(":")[2]
    try:
        note, shop = await run_db(_buy_sync, update.effective_user, item_key)
    except GameError as exc:
        await query.answer(alert_text(exc), show_alert=True)
        return
    await query.answer("✅ خریدی!")
    text, keyboard = _shop_render(shop, note)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


def register(application) -> None:
    application.add_handler(CommandHandler("festival", festival_panel, filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(festival_home_callback, pattern=r"^fest:home$"))
    application.add_handler(CallbackQueryHandler(festival_how_callback, pattern=r"^fest:how$"))
    application.add_handler(CallbackQueryHandler(festival_shop_callback, pattern=r"^fest:shop$"))
    application.add_handler(CallbackQueryHandler(festival_buy_callback, pattern=r"^fest:buy:[a-z_]+$"))

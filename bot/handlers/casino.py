"""«🎰 کازینو» — a paid gamble with three tables plus one free daily spin."""

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

from bio_lab.repository import get_or_create_user
from bot.buttons import CONFIRM, SHOP, back_btn, btn
from bot.utils import run_db, safe_edit_message_text, send_screen
from game import casino, constants
from game.creature import GameError
from game.daily import get_daily_count
from game.emoji import get_emoji

_KIND_EMOJI_KEY = {"coins": "coin", "dna": "dna", "diamonds": "diamond", "speedup": "speedup"}


def _panel_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    free_used = get_daily_count(user, "wheel_spin") >= constants.WHEEL_DAILY_LIMIT
    return casino.tier_list(), user.coins, user.diamonds, free_used


def _render(tiers, coins, diamonds, free_used) -> tuple[str, InlineKeyboardMarkup]:
    lines = [
        "🎰 <b>کازینو</b>",
        f"<blockquote>{get_emoji('coin')} {coins} طلا · {get_emoji('diamond')} {diamonds} الماس\n"
        "یه میز رو انتخاب کن. هر میز یه جدول جایزه‌ی خودش رو داره — شانسیه، ممکنه ببری یا ببازی.</blockquote>",
    ]
    rows = []
    for t in tiers:
        if t["daily"]:
            cost_txt = " (امروز استفاده شده)" if free_used else " (رایگان امروز)"
        else:
            # plain text in button labels — get_emoji() returns <tg-emoji> HTML that
            # can't render on a button
            cur = "💎" if t["currency"] == "diamonds" else "طلا"
            cost_txt = f" — {t['cost']} {cur}"
        rows.append([btn(f"{t['label']}{cost_txt}", style=SHOP, callback_data=f"casino_pick:{t['key']}")])
    rows.append([back_btn("menu:cat_shop", "بازگشت به فروشگاه")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def casino_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tiers, coins, diamonds, free_used = await run_db(_panel_sync, update.effective_user)
    text, keyboard = _render(tiers, coins, diamonds, free_used)
    await send_screen(update, text, parse_mode="HTML", reply_markup=keyboard)


async def casino_pick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    tier = query.data.split(":")[1]
    cfg = constants.CASINO_TIERS.get(tier)
    if cfg is None:
        await query.answer("این میز پیدا نشد.", show_alert=True)
        return
    await query.answer()
    if cfg["daily"]:
        cost_line = "رایگان (روزی یک‌بار)"
    else:
        cur = get_emoji("diamond") if cfg["currency"] == "diamonds" else get_emoji("coin")
        cost_line = f"شرط: <b>{cfg['cost']}</b> {cur}"
    keyboard = InlineKeyboardMarkup([
        [btn("🎲 بچرخون!", style=CONFIRM, callback_data=f"casino_play:{tier}")],
        [back_btn("menu:casino", "بازگشت به کازینو")],
    ])
    await safe_edit_message_text(
        query,
        f"{cfg['label']}\n<blockquote>{cfg['desc']}\n{cost_line}\n\n"
        "شانسیه — ممکنه جایزه‌ی بزرگ ببری یا هیچی گیرت نیاد. مطمئنی؟</blockquote>",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


def _play_sync(tg_user, tier):
    user, _ = get_or_create_user(tg_user)
    prize = casino.play(user, tier)
    user.refresh_from_db()  # play() may have charged via a locked re-fetch
    free_used = get_daily_count(user, "wheel_spin") >= constants.WHEEL_DAILY_LIMIT
    return prize, user.coins, user.diamonds, free_used


async def casino_play_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    tier = query.data.split(":")[1]
    try:
        prize, coins, diamonds, free_used = await run_db(_play_sync, update.effective_user, tier)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return

    if prize["kind"] == "nothing":
        await query.answer("😔 این دور نبردی.")
        reveal = "😔 <b>باختی!</b> این دور چیزی نصیبت نشد."
    else:
        emoji = get_emoji(_KIND_EMOJI_KEY[prize["kind"]])
        await query.answer("🎉 بردی!")
        reveal = f"🎉 <b>بردی!</b>\n<tg-spoiler>{emoji} {prize['label']}</tg-spoiler>"

    keyboard = InlineKeyboardMarkup([
        [btn("🎲 دوباره", style=SHOP, callback_data=f"casino_pick:{tier}")],
        [back_btn("menu:casino", "بازگشت به کازینو")],
    ])
    await safe_edit_message_text(
        query,
        f"{constants.CASINO_TIERS[tier]['label']}\n\n{reveal}\n\n"
        f"<i>موجودی: {coins} طلا · {diamonds} الماس</i>",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


def register(application) -> None:
    application.add_handler(CommandHandler("casino", casino_panel, filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(casino_pick_callback, pattern=r"^casino_pick:"))
    application.add_handler(CallbackQueryHandler(casino_play_callback, pattern=r"^casino_play:"))

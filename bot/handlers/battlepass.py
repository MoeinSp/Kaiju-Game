"""«🎟 پاس فصلی» — the Battle Pass screen.

One panel: current tier + a progress bar to the next, a peek at the next few
tiers' rewards on both tracks, a «دریافت جوایز» button when anything is claimable,
and a «خرید پاس ویژه» button for players still on the free track.
"""

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

from bio_lab.repository import get_or_create_user
from bot.buttons import CONFIRM, SHOP, back_btn, btn
from bot.utils import run_db, safe_edit_message_text, send_screen
from game import battlepass, constants
from game.creature import GameError
from game.emoji import get_emoji

_PREVIEW_TIERS = 4  # how many upcoming tiers to show


def _panel_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    return user, battlepass.status(user)


def _fmt_left(seconds: int) -> str:
    days, rem = divmod(max(0, seconds), 86400)
    hours = rem // 3600
    if days:
        return f"{days} روز و {hours} ساعت"
    if hours:
        return f"{hours} ساعت"
    return "کمتر از یک ساعت"


def _render(user, st: dict) -> tuple[str, InlineKeyboardMarkup]:
    bar = constants.render_bar(st["into"], st["span"], width=10)
    track = "✦ ویژه" if st["premium"] else "رایگان"
    left = _fmt_left(battlepass.seconds_until_period_end())
    lines = [
        f"🎟 <b>پاس دوهفته‌ای</b>",
        f"<blockquote>تراک: <b>{track}</b>\n"
        f"مرحله <b>{st['tier']}</b>/{st['max_tier']}  {bar}  {st['into']}/{st['span']} امتیاز\n"
        f"⏳ <b>{left}</b> تا پایان پاس (شنبه ریست می‌شه)</blockquote>",
        "<i>هر فعالیتی (شکار، آرنا، ساختمون، ورود روزانه) امتیاز پاس می‌ده.</i>\n",
    ]
    # preview the next few tiers
    start = max(1, st["tier"] + 1 - 0)
    upcoming = [t for t in range(st["tier"] + 1, min(st["max_tier"], st["tier"] + _PREVIEW_TIERS) + 1)]
    if upcoming:
        lines.append("🎁 <b>مرحله‌های بعدی:</b>")
        for t in upcoming:
            free = battlepass.reward_text(battlepass.free_reward(t))
            prem = battlepass.reward_text(battlepass.premium_reward(t))
            lines.append(f"  <b>{t}</b> · رایگان: {free}  |  ✦ ویژه: {prem}")
    else:
        lines.append("🏆 <b>به آخرین مرحله‌ی پاس رسیدی!</b>")

    rows = []
    if st["has_claimable"]:
        rows.append([btn("🎁 دریافت جوایز", emoji_key="btn_confirm", style=CONFIRM, callback_data="pass_claim")])
    if not st["premium"]:
        rows.append(
            [btn(f"✦ خرید پاس ویژه ({st['premium_cost']} 💎)", style=SHOP, callback_data="pass_buy")]
        )
    rows.append([back_btn("menu:cat_rewards", "بازگشت به جایزه‌ها")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def battlepass_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user, st = await run_db(_panel_sync, update.effective_user)
    text, keyboard = _render(user, st)
    await send_screen(update, text, parse_mode="HTML", reply_markup=keyboard)


def _claim_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    result = battlepass.claim(user)
    return user, result, battlepass.status(user)


async def pass_claim_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user, result, st = await run_db(_claim_sync, update.effective_user)
    if not result["tiers"]:
        await query.answer("چیزی برای دریافت نیست.", show_alert=True)
        return
    await query.answer(f"🎉 جوایز {result['tiers']} مرحله گرفته شد!")
    got = battlepass.reward_text(result["reward"])
    text, keyboard = _render(user, st)
    await safe_edit_message_text(
        query,
        f"🎉 <b>جوایز پاس دریافت شد!</b>\n🎁 <b>{got}</b>\n\n━━━━━━━━━━\n" + text,
        parse_mode="HTML",
        reply_markup=keyboard,
    )


def _buy_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    battlepass.buy_premium(user)
    return user, battlepass.status(user)


async def pass_buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        user, st = await run_db(_buy_sync, update.effective_user)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer("✦ پاس ویژه فعال شد!")
    text, keyboard = _render(user, st)
    await safe_edit_message_text(
        query,
        "✦ <b>پاس ویژه فعال شد!</b> حالا جوایز ویژه‌ی همه‌ی مرحله‌هایی که رسیدی رو می‌تونی بگیری.\n\n"
        "━━━━━━━━━━\n" + text,
        parse_mode="HTML",
        reply_markup=keyboard,
    )


def register(application) -> None:
    application.add_handler(CommandHandler("pass", battlepass_panel, filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(pass_claim_callback, pattern=r"^pass_claim$"))
    application.add_handler(CallbackQueryHandler(pass_buy_callback, pattern=r"^pass_buy$"))

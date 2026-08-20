"""«🏅 دستاوردها» — the achievements screen.

One screen: a scrollable list of milestones with progress bars, plus a single
"claim everything ready" button. Progress is derived live (game/achievements),
so the panel is always current without any background bookkeeping.
"""

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

from bio_lab.repository import get_or_create_user
from bot.buttons import CONFIRM, back_btn, btn
from bot.utils import run_db, safe_edit_message_text, send_screen
from game import achievements, constants
from game.emoji import get_emoji


def _panel_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    return user, achievements.evaluate(user)


def _render(user, view: dict) -> tuple[str, InlineKeyboardMarkup]:
    lines = [
        f"🏅 <b>دستاوردها</b>  ({view['done']}/{view['total']})",
        "<blockquote>هدف‌های بلندمدت. هر کدوم رو که کامل کنی، جایزه‌ش رو یه بار می‌گیری.</blockquote>",
        "",
    ]
    for item in view["items"]:
        ach = item["ach"]
        reward = achievements._reward_text(ach.reward)
        if item["claimed"]:
            status = "✅ گرفته شد"
        elif item["earned"]:
            status = "🎁 <b>آماده‌ی دریافت!</b>"
        else:
            bar = constants.render_bar(item["current"], item["target"], width=8)
            status = f"{bar} {item['current']}/{item['target']}"
        lines.append(f"{ach.emoji} <b>{ach.title}</b> — {ach.desc}")
        lines.append(f"    {status}   🎁 <i>{reward}</i>")
    rows = []
    if view["claimable"]:
        rows.append(
            [btn(f"🎁 دریافت همه ({view['claimable']})", emoji_key="btn_confirm", style=CONFIRM, callback_data="ach_claim")]
        )
    rows.append([back_btn("menu:cat_rewards", "بازگشت به جایزه‌ها")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def achievements_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user, view = await run_db(_panel_sync, update.effective_user)
    text, keyboard = _render(user, view)
    await send_screen(update, text, reply_markup=keyboard)


def _claim_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    result = achievements.claim_all(user)
    return user, result, achievements.evaluate(user)


async def achievements_claim_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user, result, view = await run_db(_claim_sync, update.effective_user)
    if not result["claimed"]:
        await query.answer("چیزی برای دریافت نیست.", show_alert=True)
        return
    await query.answer(f"🎉 {len(result['claimed'])} دستاورد دریافت شد!")
    got = achievements._reward_text(result["reward"]) or "—"
    names = "، ".join(a.title for a in result["claimed"])
    text, keyboard = _render(user, view)
    await safe_edit_message_text(
        query,
        f"🎉 <b>دستاورد دریافت شد!</b>\n{names}\n🎁 <b>{got}</b>\n\n━━━━━━━━━━\n" + text,
        parse_mode="HTML",
        reply_markup=keyboard,
    )


def register(application) -> None:
    application.add_handler(CommandHandler("achievements", achievements_panel, filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(achievements_claim_callback, pattern=r"^ach_claim$"))

"""«🎁 دعوت دوستان» — the referral screen: your invite link, how it's doing, and
a button to collect any rewards that are ready.

Rewards also pay out automatically via the notification job once an invited friend
crosses the lab-level milestone, but the button lets a referrer claim on demand and
see exactly where each invite stands.
"""

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

from bio_lab.repository import get_or_create_user
from bot.buttons import CONFIRM, back_btn, btn
from bot.utils import run_db, safe_edit_message_text, send_screen
from game import referral


def _panel_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    return referral.stats(user)


def _render(st: dict) -> tuple[str, InlineKeyboardMarkup]:
    lines = [
        "🎁 <b>دعوت دوستان</b>",
        "<blockquote>لینکت رو برای دوستات بفرست. وقتی یکی با لینک تو بیاد و به "
        f"<b>سطح آزمایشگاه {st['milestone_level']}</b> برسه، <b>هردوتون</b> جایزه می‌گیرین:\n"
        f"• تو: <b>{st['referrer_reward']}</b> 💎   • دوستت: <b>{st['friend_reward']}</b> 💎</blockquote>",
        "",
        f"👥 کل دعوت‌ها: <b>{st['total']}</b>   ✅ موفق: <b>{st['successful']}</b>   "
        f"⏳ در انتظار: <b>{st['pending']}</b>",
    ]
    if st["friends"]:
        lines.append("\n<b>وضعیت دعوت‌هات:</b>")
        for f in st["friends"][:12]:
            if f["paid"]:
                status = "✅ جایزه گرفته شد"
            elif f["reached"]:
                status = "🎉 آماده‌ی دریافت جایزه!"
            else:
                status = f"⏳ سطح {f['level']}/{st['milestone_level']}"
            lines.append(f"• {f['name']} — {status}")
        if len(st["friends"]) > 12:
            lines.append(f"<i>… و {len(st['friends']) - 12} نفر دیگه</i>")
    else:
        lines.append("\n<i>هنوز کسی رو دعوت نکردی. لینکت رو بفرست!</i>")

    lines.append("\n🔗 <b>لینک دعوت تو:</b>")
    lines.append(f"<code>{st['link']}</code>")
    lines.append("<i>روی لینک بزن تا کپی شه، بعد برای دوستات بفرست.</i>")

    rows = []
    if st["claimable"] > 0:
        rows.append([btn(
            f"🎉 دریافت جایزه ({st['claimable']} دعوت آماده)",
            emoji_key="btn_confirm", style=CONFIRM, callback_data="ref_claim",
        )])
    rows.append([back_btn("menu:cat_rewards", "بازگشت به جایزه‌ها")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def referral_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    st = await run_db(_panel_sync, update.effective_user)
    text, keyboard = _render(st)
    await send_screen(update, text, parse_mode="HTML", reply_markup=keyboard)


def _claim_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    result = referral.claim_ready(user)
    return result, referral.stats(user)


async def referral_claim_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    result, st = await run_db(_claim_sync, update.effective_user)
    if result["claimed"] <= 0:
        await query.answer("جایزه‌ی آماده‌ای نداری.", show_alert=True)
        return
    await query.answer(f"🎉 {result['diamonds']} 💎 گرفتی!")
    text, keyboard = _render(st)
    await safe_edit_message_text(
        query,
        f"🎉 <b>{result['diamonds']} 💎 از {result['claimed']} دعوت موفق گرفتی!</b>\n━━━━━━━━━━\n" + text,
        parse_mode="HTML",
        reply_markup=keyboard,
    )


def register(application) -> None:
    application.add_handler(CommandHandler("invite", referral_panel, filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(referral_claim_callback, pattern=r"^ref_claim$"))

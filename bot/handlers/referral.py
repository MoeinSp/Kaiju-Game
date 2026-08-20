"""«🎁 دعوت دوستان» — the referral screen: your invite link and how it's doing.

Rewards are paid automatically by the notification job once an invited friend
crosses the lab-level milestone, so this screen is just the link + a tally; there
is nothing to claim here.
"""

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import CommandHandler, ContextTypes, filters

from bio_lab.repository import get_or_create_user
from bot.buttons import back_btn
from bot.utils import run_db, send_screen
from game import referral


def _panel_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    return referral.stats(user)


async def referral_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    st = await run_db(_panel_sync, update.effective_user)
    text = (
        "🎁 <b>دعوت دوستان</b>\n"
        "<blockquote>لینکت رو برای دوستات بفرست. وقتی یکی با لینک تو بیاد و به "
        f"<b>سطح آزمایشگاه {st['milestone_level']}</b> برسه، <b>هردوتون</b> جایزه می‌گیرین:\n"
        f"• تو: <b>{st['referrer_reward']}</b> 💎\n"
        f"• دوستت: <b>{st['friend_reward']}</b> 💎</blockquote>\n\n"
        f"✅ دعوت موفق: <b>{st['successful']}</b>   ⏳ در انتظار: <b>{st['pending']}</b>\n\n"
        "🔗 <b>لینک دعوت تو:</b>\n"
        f"<code>{st['link']}</code>\n"
        "<i>روی لینک بزن تا کپی شه، بعد برای دوستات بفرست.</i>"
    )
    await send_screen(update, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[back_btn("menu:cat_rewards", "بازگشت به جایزه‌ها")]]))


def register(application) -> None:
    application.add_handler(CommandHandler("invite", referral_panel, filters.ChatType.PRIVATE))

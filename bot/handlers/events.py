"""«⏳ رویداد» — the current limited-time event: its weekly bonus, a countdown,
and today's claimable event reward."""

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

from bio_lab.repository import get_or_create_user
from bot.buttons import CONFIRM, back_btn, btn
from bot.utils import run_db, safe_edit_message_text, send_screen
from game import events


def _fmt_left(seconds: int) -> str:
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return f"{d} روز و {h} ساعت"
    if h:
        return f"{h} ساعت و {m} دقیقه"
    return f"{m} دقیقه"


def _panel_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    return events.status(user)


def _render(st: dict) -> tuple[str, InlineKeyboardMarkup]:
    ev = st["event"]
    lines = [
        f"{ev['emoji']} <b>رویداد این هفته: {ev['title']}</b>",
        f"<blockquote>{ev['desc']}\n⏳ تا پایان رویداد: <b>{_fmt_left(st['seconds_left'])}</b></blockquote>",
        f"\n🎁 <b>جایزه‌ی امروزِ رویداد</b> (روز {st['day']}/۷): {events.reward_text(st['today_reward'])}",
        "<i>هر روزِ رویداد یه جایزه‌ی بزرگ‌تر — روز آخر جک‌پات الماس!</i>",
    ]
    rows = []
    if st["can_claim"]:
        rows.append([btn("🎁 دریافت جایزه‌ی امروز", emoji_key="btn_confirm", style=CONFIRM, callback_data="event_claim")])
    else:
        lines.append("\n✅ جایزه‌ی امروزو گرفتی. فردا دوباره بیا.")
    rows.append([back_btn("menu:me")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def events_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    st = await run_db(_panel_sync, update.effective_user)
    text, keyboard = _render(st)
    await send_screen(update, text, parse_mode="HTML", reply_markup=keyboard)


def _claim_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    reward = events.claim_daily(user)
    return reward, events.status(user)


async def event_claim_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    reward, st = await run_db(_claim_sync, update.effective_user)
    if reward is None:
        await query.answer("جایزه‌ی امروزو قبلاً گرفتی.", show_alert=True)
        return
    await query.answer("🎉 گرفتی!")
    text, keyboard = _render(st)
    await safe_edit_message_text(
        query,
        f"🎉 <b>جایزه‌ی رویداد دریافت شد!</b>\n🎁 <b>{events.reward_text(reward)}</b>\n\n━━━━━━━━━━\n" + text,
        parse_mode="HTML",
        reply_markup=keyboard,
    )


def register(application) -> None:
    application.add_handler(CommandHandler("event", events_panel, filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(event_claim_callback, pattern=r"^event_claim$"))

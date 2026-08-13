from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import ApplicationHandlerStop, ContextTypes, TypeHandler

from bio_lab.models import User
from bio_lab.repository import get_or_create_user
from bot.utils import run_db, safe_edit_message_text
from config import OWNER_TELEGRAM_ID
from game.force_join import NOT_JOINED_STATUSES, active_channels, grant_reward_if_unclaimed

FORCE_JOIN_CHECK_CALLBACK = "forcejoin_check"


def _is_banned_sync(user_id: int) -> bool:
    return User.objects.filter(id=user_id, is_banned=True).exists()


async def enforce_ban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Runs before every other handler (registered in group=-2, highest priority).
    Blocked players never reach game logic — this is the single place a ban is
    enforced."""
    user = update.effective_user
    if user is None or user.id == OWNER_TELEGRAM_ID:
        return
    if await run_db(_is_banned_sync, user.id):
        if update.effective_message is not None:
            await update.effective_message.reply_text("🚫 دسترسیت به این بات مسدود شده.")
        raise ApplicationHandlerStop


def _join_gate_keyboard(missing_channels) -> InlineKeyboardMarkup:
    rows = []
    for ch in missing_channels:
        url = ch.invite_link or (f"https://t.me/{ch.username}" if ch.username else None)
        if url is None:
            continue
        label = f"🔵 عضویت در {ch.title or ('@' + ch.username if ch.username else 'کانال')}"
        rows.append([InlineKeyboardButton(label, url=url)])
    rows.append([InlineKeyboardButton("✅ بررسی مجدد عضویت", callback_data=FORCE_JOIN_CHECK_CALLBACK)])
    return InlineKeyboardMarkup(rows)


async def enforce_force_join(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Runs after enforce_ban (registered in group=-1). Blocks every interaction
    for a player who hasn't joined all currently-active RequiredChannel rows, and
    grants each channel's one-time join reward the first time a player clears it.

    Caches which channel ids a player has already cleared in `context.user_data`
    so normal traffic doesn't re-call the Telegram API (get_chat_member) or hit the
    database on every single update — only re-checked when a new required channel
    appears, or when the player explicitly taps "بررسی مجدد عضویت"."""
    user = update.effective_user
    if user is None or user.id == OWNER_TELEGRAM_ID:
        return

    channels = await run_db(active_channels)
    if not channels:
        return

    current_ids = frozenset(ch.id for ch in channels)
    passed_ids = context.user_data.get("force_join_passed_ids", frozenset())
    is_check_callback = (
        update.callback_query is not None and update.callback_query.data == FORCE_JOIN_CHECK_CALLBACK
    )

    if current_ids <= passed_ids and not is_check_callback:
        return  # already verified for every channel that's currently required

    missing = []
    for ch in channels:
        try:
            member = await context.bot.get_chat_member(ch.chat_id, user.id)
        except TelegramError:
            continue  # bot likely isn't admin in that channel yet -- don't lock everyone out over it
        if member.status in NOT_JOINED_STATUSES:
            missing.append(ch)

    if missing:
        context.user_data["force_join_passed_ids"] = frozenset()
        if is_check_callback:
            await update.callback_query.answer("هنوز عضو همه‌ی کانال‌ها نشدی!", show_alert=True)
        elif update.effective_message is not None:
            await update.effective_message.reply_text(
                "📡 <b>قبل از استفاده از بات باید عضو کانال(های) زیر بشی:</b>\n\n"
                "<blockquote>بعد از عضویت، روی «بررسی مجدد عضویت» بزن.</blockquote>",
                parse_mode="HTML",
                reply_markup=_join_gate_keyboard(missing),
            )
        raise ApplicationHandlerStop

    just_passed = not (current_ids <= passed_ids)
    context.user_data["force_join_passed_ids"] = current_ids

    if just_passed:
        db_user, _ = await run_db(get_or_create_user, user)
        for ch in channels:
            await run_db(grant_reward_if_unclaimed, db_user, ch)

    if is_check_callback:
        await update.callback_query.answer("✅ عضویت تأیید شد!")
        await safe_edit_message_text(
            update.callback_query,
            "✅ <b>عضویتت تأیید شد!</b> حالا دوباره از /start یا منو استفاده کن.",
            parse_mode="HTML",
        )
        raise ApplicationHandlerStop


def register(application) -> None:
    application.add_handler(TypeHandler(Update, enforce_ban), group=-2)
    application.add_handler(TypeHandler(Update, enforce_force_join), group=-1)

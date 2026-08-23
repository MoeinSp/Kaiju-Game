from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import ApplicationHandlerStop, ContextTypes, TypeHandler

from bio_lab.models import User
from bio_lab.repository import get_or_create_user
from bot.buttons import CONFIRM, DANGER, PRIMARY, back_btn, btn
from bot.utils import run_db, safe_edit_message_text
from config import OWNER_TELEGRAM_ID
from game.emoji import get_emoji
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
        rows.append([btn(label, emoji_key="btn_join", style=PRIMARY, url=url)])
    rows.append([btn("بررسی مجدد عضویت", emoji_key="btn_recheck", style=CONFIRM, callback_data=FORCE_JOIN_CHECK_CALLBACK)])
    return InlineKeyboardMarkup(rows)


def _reward_summary(channel) -> str:
    """Human-readable one-time join reward for a channel, or '' if it has none."""
    parts = []
    if channel.reward_coins:
        parts.append(f"{channel.reward_coins} {get_emoji('coin')}")
    if channel.reward_dna:
        parts.append(f"{channel.reward_dna} {get_emoji('dna')}")
    if channel.reward_diamonds:
        parts.append(f"{channel.reward_diamonds} {get_emoji('diamond')}")
    return " + ".join(parts)


def _join_gate_text(missing_channels) -> str:
    lines = ["📡 <b>قبل از استفاده از بات باید عضو کانال(های) زیر بشی:</b>\n"]
    for ch in missing_channels:
        name = ch.title or (f"@{ch.username}" if ch.username else "کانال")
        reward = _reward_summary(ch)
        lines.append(f"• {name}" + (f" — 🎁 جایزه‌ی عضویت: {reward}" if reward else ""))
    lines.append("\n<blockquote>بعد از عضویت، روی «بررسی مجدد عضویت» بزن تا جایزه‌ها رو بگیری.</blockquote>")
    return "\n".join(lines)


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
        # Record that this person started the bot even though the join gate still
        # blocks their gameplay. Without this the User row is only created once
        # they clear the gate (below), so anything keyed on "has started the bot"
        # — the advertiser API included — would wrongly report them as absent
        # until they join. Creating the row here is free of side effects: join
        # rewards are still only granted on `just_passed`.
        await run_db(get_or_create_user, user)
        context.user_data["force_join_passed_ids"] = frozenset()
        if is_check_callback:
            await update.callback_query.answer("هنوز عضو همه‌ی کانال‌ها نشدی!", show_alert=True)
        elif update.effective_message is not None:
            await update.effective_message.reply_text(
                _join_gate_text(missing), parse_mode="HTML", reply_markup=_join_gate_keyboard(missing)
            )
        raise ApplicationHandlerStop

    just_passed = not (current_ids <= passed_ids)
    context.user_data["force_join_passed_ids"] = current_ids

    granted = []
    if just_passed:
        db_user, _ = await run_db(get_or_create_user, user)
        for ch in channels:
            if await run_db(grant_reward_if_unclaimed, db_user, ch):
                granted.append(ch)

    if is_check_callback:
        await update.callback_query.answer("✅ عضویت تأیید شد!")
        text = "✅ <b>عضویتت تأیید شد!</b> حالا دوباره از /start یا منو استفاده کن."
        if granted:
            reward_lines = "\n".join(f"🎁 {_reward_summary(ch)}" for ch in granted)
            text += f"\n\n<b>جایزه‌ی عضویت گرفتی:</b>\n{reward_lines}"
        await safe_edit_message_text(update.callback_query, text, parse_mode="HTML")
        raise ApplicationHandlerStop


def register(application) -> None:
    application.add_handler(TypeHandler(Update, enforce_ban), group=-2)
    application.add_handler(TypeHandler(Update, enforce_force_join), group=-1)

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import ApplicationHandlerStop, ContextTypes, TypeHandler

from bio_lab.models import User
from bio_lab.repository import get_or_create_user
from bot.buttons import CONFIRM, DANGER, PRIMARY, back_btn, btn
from bot.utils import run_db, safe_edit_message_text
from config import OWNER_TELEGRAM_ID
from game import keywords
from game.emoji import get_emoji
from game.force_join import NOT_JOINED_STATUSES, active_channels, grant_reward_if_unclaimed

FORCE_JOIN_CHECK_CALLBACK = "forcejoin_check"

_GROUP_CHAT_TYPES = ("group", "supergroup")


def _is_group_bot_invocation(update: Update) -> bool:
    """In a group, decide whether this update is actually aimed at the bot.

    The ban / force-join gates run on *every* update. In a group where the bot
    can read all messages that would mean replying "join the channel" (or "you're
    banned") to ordinary chatter — so in groups we only gate real invocations:
    a callback on one of our buttons, a slash command, or a recognised game word.
    Everything else is left alone (handle_group_text stays quiet on it too).
    """
    if update.callback_query is not None:
        return True
    message = update.effective_message
    if message is None or not message.text:
        return False
    text = message.text
    if text.startswith("/"):
        return True
    return keywords.match(text) is not None


def _gate_applies(update: Update) -> bool:
    """Whether the ban / force-join gate should run for this update at all.

    Always in private chats; in groups only for actual bot invocations."""
    chat = update.effective_chat
    if chat is not None and chat.type in _GROUP_CHAT_TYPES:
        return _is_group_bot_invocation(update)
    return True


def _mark_started_sync(tg_user) -> None:
    """Flip the public /api/started/ flag the instant someone presses /start — done HERE
    (before the gates) so a user who started the bot counts as 'started' even while
    they're still stuck behind the bot's OWN force-join gate. It's a plain «did they
    press /start», nothing about join requirements."""
    user, _ = get_or_create_user(tg_user)
    if not user.started_gate:
        user.started_gate = True
        user.save(update_fields=["started_gate"])


async def capture_referral(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Runs before every gate (group=-3). Marks the user 'started' on the raw /start, and
    stashes a `/start ref_<id>` payload in user_data so it survives the force-join gate —
    which raises ApplicationHandlerStop on a brand-new invitee's first message and would
    otherwise drop both. Never stops propagation; the ban/join gates and /start still run."""
    message = update.effective_message
    if message is None or not message.text or not message.text.startswith("/start"):
        return
    chat = update.effective_chat
    if chat is not None and chat.type == "private":
        await run_db(_mark_started_sync, update.effective_user)
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        return
    from game.referral import parse_payload

    ref = parse_payload(parts[1].strip())
    if ref is not None:
        context.user_data["pending_referrer"] = ref


def _create_user_and_bind_referral(tg_user, referrer_id):
    """Get-or-create the User and, if a referral payload came with this /start, bind it
    immediately (persisted to the DB), so an invite counts even if the invitee is still
    stuck behind the force-join gate when the bot restarts."""
    user, was_created = get_or_create_user(tg_user)
    if referrer_id is not None:
        from game.referral import register_referral

        register_referral(user, was_created, referrer_id)
    return user


def _is_banned_sync(user_id: int) -> bool:
    return User.objects.filter(id=user_id, is_banned=True).exists()


async def enforce_ban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Runs before every other handler (registered in group=-2, highest priority).
    Blocked players never reach game logic — this is the single place a ban is
    enforced."""
    user = update.effective_user
    if user is None or user.id == OWNER_TELEGRAM_ID:
        return
    if not _gate_applies(update):
        return  # ordinary group chatter — never gate it
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
    has_group = any(getattr(ch, "kind", "channel") == "group" for ch in missing_channels)
    where = "کانال‌ها و گروه‌ها" if has_group else "کانال‌ها"
    lines = [
        f"📡 <b>نیازمند عضویت در {where}</b>",
        f"برای استفاده از ربات و دریافت پاداش، ابتدا باید در تمام {where}ی زیر عضو شوید:",
        "",
    ]
    for ch in missing_channels:
        name = ch.title or (f"@{ch.username}" if ch.username else "کانال")
        icon = "👥" if getattr(ch, "kind", "channel") == "group" else "📢"
        lines.append(f"• {icon} <b>{name}</b>")
        for amount, emo in ((ch.reward_coins, "coin"), (ch.reward_dna, "dna"), (ch.reward_diamonds, "diamond")):
            if amount:
                lines.append(f"   ◦ {get_emoji(emo)} <code>{amount:,}</code>")
    lines += ["", "📌 پس از عضویت در تمامی موارد، روی دکمه «بررسی مجدد عضویت» کلیک کنید."]
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
    if not _gate_applies(update):
        return  # ordinary group chatter — never gate it

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
        # Create the row AND bind the referral NOW (using the ref_<id> payload that
        # capture_referral stashed in this same update) — so the referral survives a bot
        # restart before the invitee finishes the join gate. In-memory user_data would
        # otherwise be lost across a deploy and the invite would never count.
        await run_db(_create_user_and_bind_referral, user, context.user_data.get("pending_referrer"))
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
        confirm_line = "✅ <b>عضویتت تأیید شد!</b> حالا دوباره از /start یا منو استفاده کن."
        if granted:
            tot = {
                "coin": sum(ch.reward_coins for ch in granted),
                "dna": sum(ch.reward_dna for ch in granted),
                "diamond": sum(ch.reward_diamonds for ch in granted),
            }
            # reward first (the headline), the confirm/instruction line goes last
            lines = ["🎁 <b>جایزه‌ی عضویتت رو گرفتی:</b>"]
            for emo in ("coin", "dna", "diamond"):
                if tot[emo]:
                    lines.append(f"   ◦ {get_emoji(emo)} <code>+{tot[emo]:,}</code>")
            lines += ["", confirm_line]
            text = "\n".join(lines)
        else:
            text = confirm_line
        await safe_edit_message_text(update.callback_query, text, parse_mode="HTML")
        raise ApplicationHandlerStop


def register(application) -> None:
    application.add_handler(TypeHandler(Update, capture_referral), group=-3)
    application.add_handler(TypeHandler(Update, enforce_ban), group=-2)
    application.add_handler(TypeHandler(Update, enforce_force_join), group=-1)

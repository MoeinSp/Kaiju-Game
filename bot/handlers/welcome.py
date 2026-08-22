from telegram import ChatMemberUpdated, Update
from telegram.ext import ChatMemberHandler, ContextTypes

from game.emoji import get_emoji

IN_CHAT_STATUSES = ("member", "administrator", "creator")


def _build_welcome_text() -> str:
    """Built fresh per-send (not a module constant) so it reflects the owner's
    current Premium emoji choices.

    Deliberately advertises **one** thing — the word «راهنما» — instead of a list
    of slash commands. The group is played with plain words now, and a welcome
    that opens with six /commands teaches the wrong interface on first contact.
    """
    from game import keywords

    return (
        f"{get_emoji('creature')} <b>سلام! من Kaiju Legends‌ام</b> {get_emoji('raid_boss')}\n"
        "بازیِ رشد و ترکیب ژنتیکی هیولا — همینجا توی گروه می‌شه دوئل کرد، هیولای وحشی احضار کرد، "
        "دسته‌جمعی شکارش کرد و محافظ گروه شد.\n\n"
        f"{get_emoji('book')} <b>بازی با کلمه‌ست، نه دستور.</b>\n"
        f"<blockquote>کافیه کلمه‌ی <b>«{keywords.word_for('help')}»</b> رو بفرستی تا همه‌چیز "
        "دسته‌بندی‌شده برات بیاد.\n"
        f"مثلاً «{keywords.word_for('creature')}» کارت هیولات رو می‌آره و "
        f"«{keywords.word_for('reward')}» بهت جایزه می‌ده.</blockquote>\n\n"
        "برای شروع، هرکی باید اول بره پیوی من و /start رو بزنه تا هیولای اولیه‌ش رو بگیره.\n\n"
        "🙏 <b>یه خواهش مهم:</b> لطفاً من رو <b>ادمین</b> گروه کن — بدون ادمین بودن، تلگرام "
        "پیام‌های معمولی گروه رو اصلاً به من نمی‌رسونه و هیچ‌کدوم از کلمه‌ها کار نمی‌کنه."
    )


def _bot_just_added(chat_member_update: ChatMemberUpdated) -> bool:
    was_in = chat_member_update.old_chat_member.status in IN_CHAT_STATUSES
    is_in = chat_member_update.new_chat_member.status in IN_CHAT_STATUSES
    return (not was_in) and is_in


async def on_my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    result = update.my_chat_member
    if result is None or result.chat.type not in ("group", "supergroup"):
        return
    if not _bot_just_added(result):
        return

    await context.bot.send_message(chat_id=result.chat.id, text=_build_welcome_text(), parse_mode="HTML")
    # The welcome text tells the group to type words at the bot. If privacy mode
    # is on those words never reach it, so say so immediately instead of letting
    # them find out by being ignored.
    from bot.handlers.group_words import announce_setup

    await announce_setup(context.bot, result.chat.id)


def register(application) -> None:
    application.add_handler(ChatMemberHandler(on_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))

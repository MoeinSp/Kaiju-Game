from telegram import ChatMemberUpdated, Update
from telegram.ext import ChatMemberHandler, ContextTypes

from game.emoji import get_emoji

IN_CHAT_STATUSES = ("member", "administrator", "creator")


def _build_welcome_text() -> str:
    # built fresh per-send (not a module constant) so it reflects the owner's
    # current Premium emoji choices
    return (
        f"{get_emoji('creature')} <b>سلام! من Kaiju Bio-Lab‌ام</b> {get_emoji('raid_boss')}\n"
        "بازیِ رشد و ترکیب ژنتیکی هیولا — همینجا تو گروه میشه دوئل کرد، هیولای وحشی احضار کرد "
        "و دسته‌جمعی شکارش کرد، و یه محافظ برای گروه داشت.\n\n"
        "برای شروع، هرکی باید اول بره پیوی من و /start رو بزنه تا موجود اولیه‌ش رو از آزمایشگاه بگیره.\n\n"
        f"{get_emoji('battle')} <code>/duel</code> — دوئل خودکار (ریپلای روی پیام حریف)\n"
        "🎮 <code>/battle</code> — نبرد زنده با اسکیل نوبت‌به‌نوبت\n"
        f"{get_emoji('raid_boss')} <code>/raid_spawn</code> — احضار هیولای وحشی برای شکار دسته‌جمعی\n"
        f"{get_emoji('trophy')} <code>/leaderboard</code> — برترین موجودای گروه\n"
        f"{get_emoji('guardian')} <code>/guardian</code> — محافظ فعلی گروه\n"
        "📖 <code>/help</code> — لیست کامل دستورات\n\n"
        "🙏 <b>یه خواهش:</b> لطفاً من رو <b>ادمین کامل</b> گروه کن — برای پین کردن اعلان‌های رید و "
        "مدیریت بهتر پیام‌های بازی بهش نیاز دارم."
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

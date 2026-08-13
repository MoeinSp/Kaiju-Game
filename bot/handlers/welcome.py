from telegram import ChatMemberUpdated, Update
from telegram.ext import ChatMemberHandler, ContextTypes

IN_CHAT_STATUSES = ("member", "administrator", "creator")

WELCOME_TEXT = (
    "🧬 <b>سلام! من Kaiju Bio-Lab‌ام</b> 🐲\n"
    "بازیِ رشد و ترکیب ژنتیکی هیولا — همینجا تو گروه میشه دوئل کرد، هیولای وحشی احضار کرد "
    "و دسته‌جمعی شکارش کرد، و یه محافظ برای گروه داشت.\n\n"
    "برای شروع، هرکی باید اول بره پیوی من و /start رو بزنه تا موجود اولیه‌ش رو از آزمایشگاه بگیره.\n\n"
    "⚔️ <code>/duel</code> — دوئل خودکار (ریپلای روی پیام حریف)\n"
    "🎮 <code>/battle</code> — نبرد زنده با اسکیل نوبت‌به‌نوبت\n"
    "🐲 <code>/raid_spawn</code> — احضار هیولای وحشی برای شکار دسته‌جمعی\n"
    "🏆 <code>/leaderboard</code> — برترین موجودای گروه\n"
    "🛡 <code>/guardian</code> — محافظ فعلی گروه\n"
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

    await context.bot.send_message(chat_id=result.chat.id, text=WELCOME_TEXT, parse_mode="HTML")


def register(application) -> None:
    application.add_handler(ChatMemberHandler(on_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))

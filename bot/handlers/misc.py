from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

HELP_TEXT = (
    "🧬 <b>Kaiju Bio-Lab</b> — راهنما\n\n"
    "<b>🥼 آزمایشگاه (توی پیوی بات):</b>\n"
    "/start — شروع یا مشاهده‌ی آزمایشگاه\n"
    "/me — کارت موجود فعال + دکمه‌های تغذیه/تمرین/ارتقا\n"
    "/collection — لیست همه‌ی موجوداتت\n"
    "/select — تعویض موجود فعال (/select 3)\n"
    "/splice — ترکیب دو موجود برای ساخت موجود جدید (/splice 3 5)\n"
    "/missions — ماموریت‌های امروز و پیشرفتشون\n\n"
    "<b>⚔️ گروه:</b>\n"
    "/duel — دوئل خودکار (ریپلای روی پیام حریف)\n"
    "/battle — نبرد تعاملی با اسکیل (ریپلای روی پیام حریف)\n"
    "/raid_spawn — احضار یک هیولای وحشی\n"
    "/attack — حمله به هیولای فعال گروه\n"
    "/mutation_event — یه رویداد جهش رایگان برای کل گروه (یک‌بار در روز)\n"
    "/leaderboard — برترین موجودات این گروه\n"
    "/guardian — دیدن محافظ فعلی گروه\n"
    "/guardian_challenge — چالش برای گرفتن عنوان محافظ\n"
    "/guardian_claim — جایزه‌ی روزانه‌ی محافظ (اگه محافظی)\n"
    "/give — هدیه دادن سکه/DNA/موجود (ریپلای روی پیام گیرنده)\n\n"
    "برای شروع، برو پیوی بات و /start رو بزن!"
)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_TEXT, parse_mode="HTML")


def register(application) -> None:
    application.add_handler(CommandHandler("help", help_command))

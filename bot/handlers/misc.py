from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from game import constants

HELP_TEXT = (
    "🧬 <b>Kaiju Bio-Lab</b>\n"
    "بازیِ رشد، ترکیب ژنتیکی و نبرد هیولا — توی پیوی پرورش می‌دی، توی گروه می‌جنگی.\n\n"
    "━━━━━━━━━━━━━━\n"
    "🥼 <b>آزمایشگاه</b>  <i>(پیوی بات)</i>\n"
    "/start — شروع یا مشاهده‌ی آزمایشگاه + پاداش ورود روزانه\n"
    "/menu — منوی اصلی با دکمه (سریع‌ترین راه ناوبری)\n"
    "/me — کارت موجود فعال + دکمه‌های تغذیه/تمرین/ارتقا\n"
    "/collection — لیست همه‌ی موجوداتت\n"
    "/select — تعویض موجود فعال (<code>/select 3</code>)\n"
    "/splice — ترکیب دو موجود برای ساخت موجود جدید (<code>/splice 3 5</code>)\n"
    "/missions — ماموریت‌های امروز و پیشرفتشون\n"
    "/hunt — شکار انفرادی یه هیولای وحشی (بدون نیاز به گروه!)\n"
    "/rank — رتبه‌بندی سراسری همه‌ی موجودات\n\n"
    "━━━━━━━━━━━━━━\n"
    "🤝 <b>اتحاد</b>  <i>(پیوی بات)</i>\n"
    "/alliance_create — ساختن یه اتحاد جدید (<code>/alliance_create اسم</code>)\n"
    "/alliance_join — پیوستن به یه اتحاد (<code>/alliance_join اسم</code>)\n"
    "/alliance_leave — خروج از اتحاد فعلی\n"
    "/alliance_info — اطلاعات اتحاد فعلیت\n"
    "/alliance_top — برترین اتحادها\n\n"
    "━━━━━━━━━━━━━━\n"
    "⚔️ <b>میدان نبرد</b>  <i>(گروه)</i>\n"
    "/duel — دوئل خودکار (ریپلای روی پیام حریف)\n"
    "/battle — نبرد زنده با اسکیل نوبت‌به‌نوبت (ریپلای روی پیام حریف)\n"
    "/raid_spawn — احضار یک هیولای وحشی\n"
    "/attack — حمله به هیولای فعال گروه\n"
    "/mutation_event — یه رویداد جهش رایگان برای کل گروه (یک‌بار در روز)\n"
    "/leaderboard — برترین موجودات این گروه\n\n"
    "━━━━━━━━━━━━━━\n"
    "🛡 <b>محافظ گروه</b>\n"
    "/guardian — دیدن محافظ فعلی\n"
    "/guardian_challenge — چالش برای گرفتن عنوان محافظ\n"
    "/guardian_claim — جایزه‌ی روزانه‌ی محافظ فعلی\n\n"
    "━━━━━━━━━━━━━━\n"
    "🎁 <b>اجتماعی</b>\n"
    "/give — هدیه دادن سکه/DNA/موجود (ریپلای روی پیام گیرنده)\n\n"
    f"⚡ <b>انرژی:</b> تغذیه، شکار، و حمله به رید انرژی مصرف می‌کنن. هر {constants.ENERGY_REGEN_MINUTES} دقیقه "
    f"یه واحد شارژ می‌شه (سقف {constants.MAX_ENERGY} تا) — پس هرچند ساعت یه‌بار سر بزن!\n\n"
    "برای شروع، برو پیوی بات و /start رو بزن 🚀"
)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_TEXT, parse_mode="HTML")


def register(application) -> None:
    application.add_handler(CommandHandler("help", help_command))

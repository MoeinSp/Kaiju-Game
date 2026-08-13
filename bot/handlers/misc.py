from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from game import constants
from game.emoji import get_emoji


def _build_help_text() -> str:
    # built fresh on every call (not a module-level constant) so it always reflects
    # the owner's current Premium emoji choices, not just whatever was cached at
    # import time before the cache was warmed
    return (
        f"{get_emoji('creature')} <b>Kaiju Bio-Lab</b>\n"
        "بازیِ رشد، ترکیب ژنتیکی و نبرد هیولا — توی پیوی پرورش می‌دی، توی گروه می‌جنگی.\n\n"
        "━━━━━━━━━━━━━━\n"
        f"{get_emoji('lab')} <b>آزمایشگاه</b>  <i>(پیوی بات)</i>\n"
        "/start — شروع یا مشاهده‌ی آزمایشگاه + پاداش ورود روزانه\n"
        "/menu — منوی اصلی با دکمه (سریع‌ترین راه ناوبری)\n"
        "/me — کارت موجود فعال + دکمه‌های تغذیه/تمرین/ارتقا\n"
        f"/collection — لیست همه‌ی موجوداتت ({get_emoji('collection')})\n"
        "/select — تعویض موجود فعال (<code>/select 3</code>)\n"
        "/splice — ترکیب دو موجود برای ساخت موجود جدید (<code>/splice 3 5</code>)\n"
        f"/missions — ماموریت‌های امروز و پیشرفتشون ({get_emoji('mission')})\n"
        f"/hunt — شکار انفرادی یه هیولای وحشی، بدون نیاز به گروه ({get_emoji('hunt')})\n"
        f"/rank — رتبه‌بندی سراسری همه‌ی موجودات ({get_emoji('trophy')})\n"
        f"/profile — آمار کل بازیت، نه فقط امروز ({get_emoji('profile')})\n\n"
        "━━━━━━━━━━━━━━\n"
        f"{get_emoji('alliance')} <b>اتحاد</b>  <i>(پیوی بات)</i>\n"
        "/alliance_create — ساختن یه اتحاد جدید (<code>/alliance_create اسم</code>)\n"
        "/alliance_join — پیوستن به یه اتحاد (<code>/alliance_join اسم</code>)\n"
        "/alliance_leave — خروج از اتحاد فعلی\n"
        "/alliance_info — اطلاعات اتحاد فعلیت\n"
        "/alliance_top — برترین اتحادها\n\n"
        "━━━━━━━━━━━━━━\n"
        f"{get_emoji('battle')} <b>میدان نبرد</b>  <i>(گروه)</i>\n"
        "/duel — دوئل خودکار (ریپلای روی پیام حریف)\n"
        "/battle — نبرد زنده با اسکیل نوبت‌به‌نوبت (ریپلای روی پیام حریف)\n"
        f"/raid_spawn — احضار یک هیولای وحشی ({get_emoji('raid_boss')})\n"
        "/attack — حمله به هیولای فعال گروه\n"
        f"/mutation_event — یه رویداد جهش رایگان برای کل گروه، یک‌بار در روز ({get_emoji('comet')})\n"
        "/leaderboard — برترین موجودات این گروه\n\n"
        "━━━━━━━━━━━━━━\n"
        f"{get_emoji('guardian')} <b>محافظ گروه</b>\n"
        "/guardian — دیدن محافظ فعلی\n"
        "/guardian_challenge — چالش برای گرفتن عنوان محافظ\n"
        "/guardian_claim — جایزه‌ی روزانه‌ی محافظ فعلی\n\n"
        "━━━━━━━━━━━━━━\n"
        f"{get_emoji('gift')} <b>اجتماعی</b>\n"
        "/give — هدیه دادن سکه/DNA/موجود (ریپلای روی پیام گیرنده)\n\n"
        f"{get_emoji('energy')} <b>انرژی:</b> تغذیه، شکار، و حمله به رید انرژی مصرف می‌کنن. "
        f"هر {constants.ENERGY_REGEN_MINUTES} دقیقه یه واحد شارژ می‌شه (سقف {constants.MAX_ENERGY} تا) — "
        "پس هرچند ساعت یه‌بار سر بزن!\n\n"
        "برای شروع، برو پیوی بات و /start رو بزن 🚀"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(_build_help_text(), parse_mode="HTML")


def register(application) -> None:
    application.add_handler(CommandHandler("help", help_command))

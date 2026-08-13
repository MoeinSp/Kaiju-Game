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
        "/fusion — سوزاندن دو موجود + طلا برای ساخت موجود جدید (<code>/fusion 3 5</code>)\n"
        f"/missions — ماموریت‌های امروز و پیشرفتشون ({get_emoji('mission')})\n"
        f"/hunt — شکار انفرادی یه هیولای وحشی، بدون نیاز به گروه ({get_emoji('hunt')})\n"
        f"/rank — رتبه‌بندی سراسری همه‌ی موجودات ({get_emoji('trophy')})\n"
        f"/profile — آمار کل بازیت، نه فقط امروز ({get_emoji('profile')})\n\n"
        "━━━━━━━━━━━━━━\n"
        f"{get_emoji('biocrate')} <b>اقتصاد و تجهیزات</b>  <i>(پیوی بات)</i>\n"
        f"/biocrate — باز کردن یه باکس ژنتیکی با طلا ({constants.BIOCRATE_GOLD_COST} {get_emoji('coin')})\n"
        f"/inventory — کوله‌پشتی تجهیزات ({get_emoji('collection')})\n"
        "/equip — تجهیز روی موجود فعال (<code>/equip 5</code>)\n"
        "/unequip — خارج کردن تجهیزات (<code>/unequip 5</code>)\n"
        "/upgrade_item — ارتقا با یه نمونه‌ی تکراری (<code>/upgrade_item 5 9</code>)\n\n"
        "━━━━━━━━━━━━━━\n"
        f"{get_emoji('alliance')} <b>اتحاد</b>  <i>(پیوی بات)</i>\n"
        "/alliance_create — ساختن یه اتحاد جدید (<code>/alliance_create اسم</code>)\n"
        "/alliance_join — پیوستن به یه اتحاد (<code>/alliance_join اسم</code>)\n"
        "/alliance_leave — خروج از اتحاد فعلی\n"
        "/alliance_info — اطلاعات اتحاد فعلیت (شامل خزانه)\n"
        "/alliance_top — برترین اتحادها\n"
        "/alliance_deposit — واریز طلا به خزانه‌ی اتحاد (<code>/alliance_deposit 100</code>)\n"
        f"/heist — شبیخون به خزانه‌ی یه اتحاد دیگه (<code>/heist اسم اتحاد</code>) — "
        f"روزی {constants.HEIST_DAILY_ATTEMPTS} بار، {int(constants.HEIST_STEAL_PERCENT * 100)}٪ خزانه در صورت برد\n\n"
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
        "/give — هدیه دادن طلا/DNA/موجود (ریپلای روی پیام گیرنده)\n\n"
        f"{get_emoji('energy')} <b>انرژی:</b> تغذیه، شکار، و حمله به رید انرژی مصرف می‌کنن. "
        f"هر {constants.ENERGY_REGEN_MINUTES} دقیقه یه واحد شارژ می‌شه (سقف {constants.MAX_ENERGY} تا) — "
        "پس هرچند ساعت یه‌بار سر بزن!\n\n"
        "برای شروع، برو پیوی بات و /start رو بزن 🚀"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(_build_help_text(), parse_mode="HTML")


def register(application) -> None:
    application.add_handler(CommandHandler("help", help_command))

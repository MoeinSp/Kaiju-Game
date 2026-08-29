from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from game import botconfig, constants
from game.emoji import get_emoji


def _build_help_text() -> str:
    # built fresh on every call (not a module-level constant) so it always reflects
    # the owner's current Premium emoji choices, not just whatever was cached at
    # import time before the cache was warmed
    return (
        f"{get_emoji('creature')} <b>Kaiju Legends</b>\n"
        "بازیِ رشد، ترکیب ژنتیکی و نبرد هیولا — توی پیوی پرورش می‌دی، توی گروه می‌جنگی.\n\n"
        "━━━━━━━━━━━━━━\n"
        f"{get_emoji('lab')} <b>آزمایشگاه</b>  <i>(پیوی بات)</i>\n"
        "/start — شروع یا مشاهده‌ی آزمایشگاه + پاداش ورود روزانه\n"
        "/menu — منوی اصلی با دکمه (سریع‌ترین راه ناوبری)\n"
        "/me — کارت موجود فعال + دکمه‌های تغذیه/تمرین/ارتقا\n"
        f"/collection — لیست همه‌ی موجوداتت ({get_emoji('collection')})\n"
        "/select — تعویض موجود فعال (<code>/select 3</code>)\n"
        "/upgrade — انتخاب هیولا (به ترتیب قدرت) و ارتقای اعضا/تغذیه/تمرین\n"
        "/fusion — ترکیب دو هیولای <b>هم‌نوع و هم‌ستاره</b> → یه ستاره بالاتر (<code>/fusion 3 5</code>)\n"
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
        f"{get_emoji('trophy')} <b>آرنا و آهنگری</b>  <i>(پیوی بات)</i>\n"
        f"/arena — حمله به آزمایشگاه بقیه، کاپ جمع کن و طلا غارت کن "
        f"({int(constants.ARENA_LOOT_PERCENT * 100)}٪ طلای حریف در صورت برد)\n"
        f"⚒ /blacksmith — ارتقای تجهیزات با طلا (بدون نمونه‌ی تکراری، ولی با ریسک شکست)\n"
        f"🛡 بعد از اینکه بهت حمله شد {constants.ARENA_SHIELD_HOURS} ساعت سپر می‌گیری — "
        "ولی اگه خودت حمله کنی سپرت می‌پره\n\n"
        "━━━━━━━━━━━━━━\n"
        f"{get_emoji('building')} <b>ساختمون، الماس و شانس</b>  <i>(پیوی بات)</i>\n"
        f"/buildings — جمع‌کننده‌های طلا/الماس؛ فقط یه کارگر همزمان داری ({get_emoji('building')})\n"
        f"/diamondbox — جعبه‌های الماسی چندسطحی، همیشه یه موجود می‌دن ({get_emoji('diamond_box')})\n"
        f"/wheel — گردونه‌ی شانس روزانه، یه چرخش رایگان در روز ({get_emoji('wheel')})\n"
        f"{get_emoji('speedup')} کارت سرعت از گردونه/جوایز فعالیت‌ها می‌گیری، برای سریع کردن ارتقای ساختمون‌ها\n\n"
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
        f"{get_emoji('battle')} <b>میدان نبرد</b>  <i>(گروه — با کلمه کار می‌کنه، نه دستور)</i>\n"
        "«اتک» — بدون ریپلای به باس گروه، با ریپلای به یه بازیکن حمله می‌کنه\n"
        f"«احضار» — یه باس مشترک برای کل گروه می‌آره ({get_emoji('raid_boss')})\n"
        "«جدول» — برترین‌های این گروه\n\n"
        "━━━━━━━━━━━━━━\n"
        f"{get_emoji('guardian')} <b>محافظ گروه</b>\n"
        "«محافظ» — دیدن محافظ فعلی\n"
        "«تسخیر» — چالش برای گرفتن عنوان محافظ\n"
        "«حقوق» — جایزه‌ی روزانه‌ی محافظ فعلی\n\n"
        "━━━━━━━━━━━━━━\n"
        f"{get_emoji('gift')} <b>انتقال و هدیه</b>  <i>(روی پیام گیرنده ریپلای کن)</i>\n"
        "«انتقال طلا [عدد]» — دادن طلا (رایگان)\n"
        "«انتقال کایجو [کد]» یا «انتقال هیولا [کد]» — فروش/هدیه‌ی هیولا\n"
        "«انتقال تجهیزات [کد]» — فروش/هدیه‌ی تجهیزات\n"
        f"<blockquote>کدِ هیولا/تجهیزات رو توی پیوی از «کلکسیون»/«تجهیزات» می‌بینی. "
        "اول <b>تو</b> قیمت (به طلا) رو تعیین می‌کنی یا رایگان می‌ذاری؛ بعد گیرنده قیمت و "
        f"کارمزد الماس رو می‌بینه و قبول/رد می‌کنه. هر پیشنهاد ۵ دقیقه اعتبار داره و برای هر دو طرف "
        f"{constants.TRANSFER_COOLDOWN_HOURS} ساعت کول‌داون.</blockquote>\n\n"
        f"{get_emoji('energy')} <b>انرژی:</b> شکار، آرنا و حمله به رید انرژی مصرف می‌کنن. "
        f"هر {constants.ENERGY_REGEN_MINUTES} دقیقه یه واحد شارژ می‌شه (سقف {constants.MAX_ENERGY} تا)؛ "
        f"یا با {botconfig.get_energy_refill_cost()} الماس فوری پرش کن.\n\n"
        "برای شروع، برو پیوی بات و /start رو بزن 🚀"
    )


def _group_help_text() -> str:
    """Group help lists only what actually works IN A GROUP — the word triggers and
    the handful of group commands — so it never advertises PV-only slash commands
    that do nothing here. Everything else lives in the DM."""
    return (
        f"{get_emoji('creature')} <b>Kaiju Legends — راهنمای گروه</b>\n"
        "<i>توی گروه با «کلمه» بازی می‌کنی، نه دستور.</i>\n\n"
        f"{get_emoji('battle')} <b>نبرد</b>\n"
        "«اتک» (باس گروه یا ریپلای روی بازیکن) · «احضار» (باس گروه)\n"
        "«شکار» (تکی)\n<i>آرنا فقط توی پیوی رباته.</i>\n\n"
        f"{get_emoji('trophy')} <b>گروه</b>\n"
        "«جدول» (برترین‌ها) · «محافظ»/«تسخیر»/«حقوق» · «کازینو»\n"
        "«جایزه» یا «کایجو» — جایزه‌ی دوره‌ای رایگان\n\n"
        f"{get_emoji('gift')} <b>انتقال</b>  <i>(روی پیام گیرنده ریپلای کن)</i>\n"
        "«انتقال طلا [عدد]» · «انتقال کایجو [کد]» · «انتقال تجهیزات [کد]»\n"
        f"<blockquote>فروشنده قیمت (طلا) می‌ذاره یا رایگان؛ گیرنده قیمت + کارمزد الماس رو می‌بینه و "
        f"قبول/رد می‌کنه. ۵ دقیقه اعتبار · {constants.TRANSFER_COOLDOWN_HOURS} ساعت کول‌داون.</blockquote>\n\n"
        f"{get_emoji('lab')} <b>پرورش، اقتصاد، ساختمون و اتحاد</b> همه توی <b>پیوی ربات</b> با دکمه‌ست.\n"
        "برای شروع برو پیوی و /start رو بزن. برای لیست کامل کلمه‌ها، همینجا «راهنما» بفرست 🚀"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """The single /help handler for every chat type. In a group it prints the word
    cheat-sheet; in the DM it opens the interactive guide (same screen as the menu's
    «راهنما»). Kept as ONE registration so handler-ordering can never leave /help
    matching the wrong screen — or no screen — in either place."""
    chat = update.effective_chat
    if chat is not None and chat.type in ("group", "supergroup"):
        await update.message.reply_text(_group_help_text(), parse_mode="HTML")
        return
    # DM → the rich, button-driven guide. Imported lazily: private.py imports a lot
    # and pulling it at module top would risk an import cycle.
    from bot.handlers.private import guide_panel

    await guide_panel(update, context)


def register(application) -> None:
    application.add_handler(CommandHandler("help", help_command))

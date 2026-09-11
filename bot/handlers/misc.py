from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from game import botconfig, constants
from game.emoji import get_emoji


def _build_help_text() -> str:
    return (
        f"{get_emoji('creature')} <b>Kaiju Legends — راهنمای جامع بازی</b>\n"
        "<i>دنیای پرورش، جهش ژنتیکی و نبردهای هیولا!</i>\n\n"
        "━━━━━━━━━━━━━━\n"
        f"{get_emoji('lab')} <b>پرورش و آزمایشگاه</b>\n"
        "• /start یا /menu — منوی اصلی دکمه‌ای و سریع بازی\n"
        "• /me — کارت و وضعیت هیولای فعال، منابع و سپرها\n"
        "• /upgrade — ارتقای اندام‌های هیولا (بال، زره، نیش، زهر) و تغذیه\n"
        "• /collection — فهرست تمام هیولاهای شما و انتخاب هیولای فعال\n"
        "• /fusion — ترکیب دو هیولای هم‌نام و هم‌ستاره برای ارتقای ستاره\n"
        "• /breeding — غار هیولا برای تخم‌گذاری و تولید گونه‌های تازه\n\n"
        "━━━━━━━━━━━━━━\n"
        f"{get_emoji('battle')} <b>میدان‌های نبرد و ماجراجویی</b>\n"
        f"• /hunt — شکار هیولای وحشی در بیابان ({get_emoji('hunt')})\n"
        f"• /campaign — نبردهای دانجن و مراحل ۳ به ۳ ({get_emoji('dungeon', '🗺')})\n"
        f"• /team — چیدن و مدیریت تیم ۳ نفره برای دانجن\n"
        f"• /arena — حمله به آزمایشگاه سایر بازیکنان، غارت طلا و کسب کاپ ({get_emoji('trophy')})\n\n"
        "━━━━━━━━━━━━━━\n"
        f"{get_emoji('biocrate')} <b>تجهیزات، اقتصاد و آهنگری</b>\n"
        f"• /inventory — کوله‌پشتی سلاح‌ها، زره‌ها و آیتم‌ها\n"
        "• /blacksmith — آهنگری و ارتقای لول تجهیزات با طلا یا ترکیب هم‌نوع\n"
        f"• /biocrate — باکس ژنتیکی شانسی ({get_emoji('biocrate')})\n"
        f"• /diamondbox — جعبه‌های الماسی با تضمین دریافت هیولا ({get_emoji('diamond_box')})\n"
        f"• /buildings — ساخت و ارتقای کالکتورهای طلا، DNA و الماس ({get_emoji('building')})\n\n"
        "━━━━━━━━━━━━━━\n"
        f"{get_emoji('alliance')} <b>اتحاد و جامعه</b>\n"
        "• /alliance_info — مشخصات اتحاد، اعضا، ساختمون‌ها و خزانه\n"
        "• /heist — شبیخون و غارت خزانه‌ی اتحادهای رقیب\n"
        f"• /rank — رتبه‌بندی برترین آزمایشگاه‌ها و هیولاها\n\n"
        "━━━━━━━━━━━━━━\n"
        f"{get_emoji('gift')} <b>نبرد و فعالیت در گروه‌ها</b>\n"
        "توی گروه‌ها بازی با <b>کلمه‌ست</b> نه دستور:\n"
        "• «اتک» (حمله به باس گروه یا ریپلای روی بازیکن)\n"
        "• «احضار» (احضار باس رید برای همه اعضای گروه)\n"
        "• «شکار» · «جدول» · «جایزه» · «محافظ»\n"
        "• «انتقال طلا [عدد]» یا «انتقال کایجو [کد]» (با ریپلای روی کاربر)\n\n"
        "🚀 برای شروع بازی کافیه دستور /start رو لمس کنی!"
    )


def _group_help_text() -> str:
    return (
        f"{get_emoji('creature')} <b>راهنمای سریع Kaiju Legends در گروه</b>\n"
        "<i>اینجا بدون نیاز به اسلش، فقط با ارسال کلمه بازی کن:</i>\n\n"
        f"{get_emoji('battle')} <b>نبرد و شکار:</b>\n"
        "• <b>اتک</b> — حمله به باس فعال گروه (یا با ریپلای، نبرد با بازیکن دیگر)\n"
        "• <b>احضار</b> — احضار یک باس قدرتمند مشترک برای اعضای گروه\n"
        "• <b>شکار</b> — مبارزه فوری تک‌نفره با هیولای وحشی\n\n"
        f"{get_emoji('trophy')} <b>رتبه‌ها و جوایز:</b>\n"
        "• <b>جدول</b> — مشاهده قوی‌ترین بازیکن‌های این گروه\n"
        "• <b>جایزه</b> یا <b>کایجو</b> — دریافت پاداش رایگان دوره‌ای\n"
        "• <b>محافظ</b> / <b>تسخیر</b> / <b>حقوق</b> — رقابت برای عنوان محافظ گروه\n\n"
        f"{get_emoji('gift')} <b>تجارت (با ریپلای روی پیام طرف مقابل):</b>\n"
        "• <b>انتقال طلا [مقدار]</b> — واریز مستقیم طلا\n"
        "• <b>انتقال کایجو [کد]</b> — فروش یا هدیه دادن هیولا\n"
        "• <b>انتقال تجهیزات [کد]</b> — فروش یا هدیه تجهیزات\n\n"
        f"💡 تمام کارهای پرورش، ساخت‌وساز، آهنگری و خرید در <b>پیوی ربات</b> انجام می‌شه."
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

"""Mini App — step 1: the reachability probe (see telgame_site/miniapp_views.py).

`/apptest` in the private chat sends a button that opens the probe page as a Mini App,
plus a «باز نشد» button for the case we can't observe ourselves: the page never loads.
`/apptest stats` (admins) summarises what came back.
"""

from urllib.parse import urlsplit

from telegram import InlineKeyboardMarkup, Update, WebAppInfo
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

from bot.buttons import CONFIRM, DANGER, NAV, btn
from bot.utils import run_db, safe_edit_message_text
from config import ADMIN_PANEL_URL, WEBHOOK_URL


def base_url() -> str:
    """https origin the web container is served from (same host as the webhook/panel)."""
    for candidate in (WEBHOOK_URL, ADMIN_PANEL_URL):
        parts = urlsplit(candidate or "")
        if parts.scheme == "https" and parts.netloc:
            return f"https://{parts.netloc}"
    return ""


def _mark_sent_sync(user_id: int) -> str:
    from django.utils import timezone

    from bio_lab.models import MiniAppProbe
    from telgame_site.miniapp_views import probe_sig

    probe, _ = MiniAppProbe.objects.get_or_create(user_id=user_id)
    probe.sent_at = timezone.now()
    probe.failed_reported = False
    probe.save(update_fields=["sent_at", "failed_reported"])
    return probe_sig(user_id)


def _stats_sync() -> str:
    from bio_lab.models import MiniAppProbe

    rows = list(MiniAppProbe.objects.exclude(sent_at=None))
    sent = len(rows)
    if not sent:
        return "هنوز کسی تست رو باز نکرده. به بازیکن‌ها بگو توی پیوی ربات /apptest رو بفرستن."
    html = [r for r in rows if r.html_at and r.html_at >= r.sent_at]
    reported = [r for r in html if r.report_at and r.report_at >= r.sent_at]
    full = [r for r in reported if r.sdk_ok and r.auth_ok and r.img_ok]
    failed = [r for r in rows if r.failed_reported]
    silent = sent - len(html) - len([r for r in failed if r not in html])

    def med(values):
        values = sorted(v for v in values if v is not None)
        return values[len(values) // 2] if values else None

    def pct(n):
        return f"{100 * n // sent}٪"

    platforms: dict[str, int] = {}
    for r in reported:
        platforms[r.platform or "?"] = platforms.get(r.platform or "?", 0) + 1
    lines = [
        "🧪 <b>تست دسترسی مینی‌اپ</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"📨 دکمه برای <b>{sent}</b> نفر فرستاده شد",
        f"📄 صفحه به سرور رسید: <b>{len(html)}</b> ({pct(len(html))})",
        f"⚙️ اسکریپت اجرا شد و گزارش داد: <b>{len(reported)}</b> ({pct(len(reported))})",
        f"✅ همه‌چیز سالم (تلگرام + هویت + عکس): <b>{len(full)}</b> ({pct(len(full))})",
        f"❌ «باز نشد» زدن: <b>{len(failed)}</b> ({pct(len(failed))})",
        f"❔ بدون هیچ نشانه‌ای (شاید اصلاً دکمه رو نزدن): <b>{max(0, silent)}</b>",
    ]
    if reported:
        lines += [
            "",
            f"⏱ میانه‌ی زمان باز شدن صفحه: <b>{med(r.load_ms for r in reported)} ms</b>",
            f"🖼 میانه‌ی بارگذاری عکس ۲۴۰ کیلوبایتی: <b>{med(r.img_ms for r in reported if r.img_ok)} ms</b>",
            f"🔐 هویت تأییدشده: <b>{sum(1 for r in reported if r.auth_ok)}</b> از {len(reported)}"
            f" · اتصال تلگرام: <b>{sum(1 for r in reported if r.sdk_ok)}</b>",
            "📱 " + " · ".join(f"{k}: {v}" for k, v in sorted(platforms.items(), key=lambda kv: -kv[1])),
        ]
    return "\n".join(lines)


async def apptest_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if context.args and context.args[0].lower() == "stats":
        from bot.handlers.owner import _is_admin

        if _is_admin(update):
            await update.effective_message.reply_text(await run_db(_stats_sync), parse_mode="HTML")
        return
    origin = base_url()
    if not origin:
        await update.effective_message.reply_text("مینی‌اپ روی این سرور تنظیم نشده.")
        return
    sig = await run_db(_mark_sent_sync, user.id)
    url = f"{origin}/app/test/?u={user.id}&s={sig}"
    keyboard = InlineKeyboardMarkup([
        [btn("باز کردن تست", emoji_key="btn_confirm", style=CONFIRM, web_app=WebAppInfo(url=url))],
        # phase 1 of the real app (read-only: profile, collection, equipment, leaderboard)
        [btn("مینی‌اپ بازی (آزمایشی)", emoji_key="btn_collection", style=NAV, web_app=WebAppInfo(url=f"{origin}/app/"))],
        [btn("باز نشد", emoji_key="btn_cancel", style=DANGER, callback_data="apptest:fail")],
    ])
    await update.effective_message.reply_text(
        "🧪 <b>تست مینی‌اپ</b>\n"
        "می‌خوایم ببینیم مینی‌اپ بازی روی گوشی و اینترنت تو باز می‌شه یا نه.\n\n"
        "۱) «باز کردن تست» رو بزن و چند ثانیه صبر کن تا چهار تا چراغ سبز بشه.\n"
        "۲) اگه صفحه سفید موند یا باز نشد، برگرد و «باز نشد» رو بزن.\n"
        "۳) اگه تست سبز شد، «مینی‌اپ بازی» رو هم باز کن و کلکسیونت رو ببین.\n\n"
        "<i>چیزی ازت نمی‌خواد و فقط چند ثانیه طول می‌کشه. ممنون!</i>",
        parse_mode="HTML", reply_markup=keyboard,
    )


def _mark_failed_sync(user_id: int) -> None:
    from bio_lab.models import MiniAppProbe

    MiniAppProbe.objects.filter(user_id=user_id).update(failed_reported=True)


async def apptest_fail_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await run_db(_mark_failed_sync, update.effective_user.id)
    await query.answer("ثبت شد، ممنون!")
    await safe_edit_message_text(
        query,
        "🙏 <b>ثبت شد.</b> ممنون که خبر دادی — همین اطلاعات بهمون می‌گه مینی‌اپ برای همه قابل استفاده هست یا نه.",
        parse_mode="HTML",
    )


def register(application) -> None:
    application.add_handler(CommandHandler("apptest", apptest_cmd, filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(apptest_fail_callback, pattern=r"^apptest:fail$"))

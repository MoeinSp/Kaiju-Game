import asyncio

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, MessageEntity, Update
from telegram.error import TelegramError
from telegram.ext import CommandHandler, ContextTypes, filters

from bio_lab.models import User
from bot.utils import run_db
from config import ADMIN_PANEL_URL, OWNER_TELEGRAM_ID
from game.emoji import EMOJI_KEYS, clear_emoji, list_overrides, set_emoji
from game.report import dashboard_stats, progress_report

BROADCAST_DELAY_SECONDS = 0.05  # ~20 msg/s, safely under Telegram's flood limits


def _is_owner(update: Update) -> bool:
    return update.effective_user is not None and update.effective_user.id == OWNER_TELEGRAM_ID


def _keys_help() -> str:
    return "\n".join(f"<code>{k}</code> — {label}" for k, label in EMOJI_KEYS.items())


async def set_emoji_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update):
        return

    if not context.args:
        await update.message.reply_text(
            "🎨 <b>تنظیم ایموجی پرمیوم</b>\n"
            "توی یه پیام بنویس <code>/set_emoji &lt;کلید&gt;</code> و بعدش خودِ ایموجی پرمیوم رو بچسبون، مثلاً:\n"
            "<code>/set_emoji coin</code> 🪙  (اون 🪙 باید واقعاً ایموجی پرمیومی باشه که از کیبورد پرمیوم انتخاب کردی)\n\n"
            "کلیدهای قابل تنظیم:\n" + _keys_help(),
            parse_mode="HTML",
        )
        return

    key = context.args[0]
    if key not in EMOJI_KEYS:
        await update.message.reply_text(
            "کلید نامعتبره. کلیدهای قابل تنظیم:\n" + _keys_help(), parse_mode="HTML"
        )
        return

    custom_entity = next(
        (e for e in (update.message.entities or []) if e.type == MessageEntity.CUSTOM_EMOJI), None
    )
    if custom_entity is None:
        await update.message.reply_text(
            "⚠️ هیچ ایموجی پرمیومی توی پیامت پیدا نشد. باید خودِ ایموجی پرمیوم (نه یونیکد معمولی) رو بفرستی — "
            "مطمئن شو اشتراک پرمیومت فعاله و از کیبورد ایموجی «پرمیوم» تلگرام انتخابش کردی، نه ایموجی معمولی."
        )
        return

    placeholder = update.message.text[custom_entity.offset : custom_entity.offset + custom_entity.length]
    await run_db(set_emoji, key, custom_entity.custom_emoji_id, placeholder)
    await update.message.reply_text(f"✅ ایموجی «{EMOJI_KEYS[key]}» با موفقیت تنظیم شد.")


async def clear_emoji_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update):
        return
    if not context.args or context.args[0] not in EMOJI_KEYS:
        await update.message.reply_text(
            "استفاده: <code>/clear_emoji coin</code> (یکی از کلیدهای زیر)\n" + _keys_help(),
            parse_mode="HTML",
        )
        return
    removed = await run_db(clear_emoji, context.args[0])
    await update.message.reply_text("✅ به حالت پیش‌فرض برگشت." if removed else "این کلید سفارشی تنظیم نشده بود.")


async def list_emoji_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update):
        return
    overrides = await run_db(list_overrides)

    lines = ["🎨 <b>ایموجی‌های سفارشی فعلی</b>\n"]
    if not overrides:
        lines.append("<i>هنوز چیزی تنظیم نشده.</i>\n")
    else:
        for o in overrides:
            label = EMOJI_KEYS.get(o.key, o.key)
            lines.append(
                f'<tg-emoji emoji-id="{o.custom_emoji_id}">{o.placeholder}</tg-emoji> — {label} (<code>{o.key}</code>)'
            )
        lines.append("")

    lines.append("همه‌ی کلیدهای قابل تنظیم:")
    lines.append(_keys_help())
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update):
        return
    stats = await run_db(dashboard_stats)
    text = (
        "🛠 <b>پنل مدیریت</b>\n\n"
        f"👥 کاربران: {stats['users']}\n"
        f"🧬 موجودات: {stats['creatures']}\n"
        f"🤝 اتحادها: {stats['alliances']}\n"
        f"🐲 رید فعال: {stats['active_raids']}\n\n"
        "برای گزارش کامل و تشخیص فعالیت مشکوک: /report\n"
        "<i>دکمه‌ی زیر فقط وقتی کار می‌کنه که از همون سیستمی بازش کنی که بات روش اجرا می‌شه "
        "(مگه اینکه ADMIN_PANEL_URL رو روی یه آدرس عمومی/تانل تنظیم کرده باشی).</i>"
    )
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🌐 باز کردن پنل ادمین کامل", url=ADMIN_PANEL_URL)]])
    await update.effective_message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def report_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update):
        return
    data = await run_db(progress_report)

    lines = [
        "📊 <b>گزارش پیشرفت</b>\n",
        f"👥 کاربران: {data['users']}   🧬 موجودات: {data['creatures']}   🤝 اتحادها: {data['alliances']}\n",
        "💰 <b>برترین بازیکن‌ها (سکه):</b>",
    ]
    for p in data["top_players"]:
        lines.append(f"• {p['name']} — {p['coins']} سکه")

    lines.append("\n🧬 <b>قوی‌ترین موجودات (سطح):</b>")
    for c in data["top_creatures"]:
        lines.append(f"• {c['name']} (Lv{c['level']}) — {c['owner']}")

    lines.append("")
    if data["suspicious"]:
        lines.append("🚨 <b>فعالیت مشکوک امروز</b> (بیشتر از سقف نظری ممکن با انرژی عادی — یعنی یا باگه یا اکسپلویت):")
        for s in data["suspicious"]:
            lines.append(f"• {s['name']} — {s['action']}: {s['count']} بار (سقف نظری: {s['limit']})")
    else:
        lines.append("✅ هیچ فعالیت مشکوکی امروز پیدا نشد.")

    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")


def _all_user_ids_sync() -> list[int]:
    return list(User.objects.values_list("id", flat=True))


async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update):
        return
    if not context.args:
        await update.effective_message.reply_text(
            "استفاده: <code>/broadcast متن پیام</code> — برای همه‌ی بازیکن‌های ثبت‌شده فرستاده می‌شه.",
            parse_mode="HTML",
        )
        return

    text = " ".join(context.args)
    user_ids = await run_db(_all_user_ids_sync)
    sent = 0
    failed = 0
    for user_id in user_ids:
        try:
            await context.bot.send_message(chat_id=user_id, text=f"📢 {text}")
            sent += 1
        except TelegramError:
            failed += 1
        await asyncio.sleep(BROADCAST_DELAY_SECONDS)

    summary = f"✅ به {sent} نفر ارسال شد."
    if failed:
        summary += f" ({failed} نفر ناموفق — احتمالاً بات رو بلاک کردن)"
    await update.effective_message.reply_text(summary)


def register(application) -> None:
    private_only = filters.ChatType.PRIVATE
    application.add_handler(CommandHandler("set_emoji", set_emoji_cmd, private_only))
    application.add_handler(CommandHandler("clear_emoji", clear_emoji_cmd, private_only))
    application.add_handler(CommandHandler("list_emoji", list_emoji_cmd, private_only))
    application.add_handler(CommandHandler("admin", admin_cmd, private_only))
    application.add_handler(CommandHandler("report", report_cmd, private_only))
    application.add_handler(CommandHandler("broadcast", broadcast_cmd, private_only))

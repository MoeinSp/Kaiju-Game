from telegram import MessageEntity, Update
from telegram.ext import CommandHandler, ContextTypes, filters

from bot.utils import run_db
from config import OWNER_TELEGRAM_ID
from game.emoji import EMOJI_KEYS, clear_emoji, list_overrides, set_emoji


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


def register(application) -> None:
    private_only = filters.ChatType.PRIVATE
    application.add_handler(CommandHandler("set_emoji", set_emoji_cmd, private_only))
    application.add_handler(CommandHandler("clear_emoji", clear_emoji_cmd, private_only))
    application.add_handler(CommandHandler("list_emoji", list_emoji_cmd, private_only))

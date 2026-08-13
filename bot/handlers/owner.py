import asyncio

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, MessageEntity, Update
from telegram.error import TelegramError
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from bio_lab.models import User
from bio_lab.repository import display_name
from bot.utils import run_db
from config import ADMIN_PANEL_URL, OWNER_TELEGRAM_ID
from game import constants
from game.creature import GameError
from game.emoji import EMOJI_DEFS, EMOJI_KEYS, CATEGORY_LABELS, CATEGORY_OF, clear_emoji, get_emoji, list_overrides, set_emoji
from game.moderation import (
    deduct_resource,
    delete_creature,
    get_creature_or_raise,
    grant_resource,
    set_banned,
    user_info,
)
from game.report import dashboard_stats, progress_report

BROADCAST_DELAY_SECONDS = 0.05  # ~20 msg/s, safely under Telegram's flood limits


def _is_owner(update: Update) -> bool:
    return update.effective_user is not None and update.effective_user.id == OWNER_TELEGRAM_ID


def _keys_help() -> str:
    return "\n".join(f"<code>{k}</code> — {label}" for k, label in EMOJI_KEYS.items())


EMOJI_KEY_CALLBACK_PREFIX = "set_emoji_key:"
EMOJI_CAT_CALLBACK_PREFIX = "set_emoji_cat:"
EMOJI_BACK_CALLBACK = "set_emoji_back"


def _category_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(label, callback_data=f"{EMOJI_CAT_CALLBACK_PREFIX}{cat}")
        for cat, label in CATEGORY_LABELS.items()
    ]
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    return InlineKeyboardMarkup(rows)


def _key_keyboard(category: str) -> InlineKeyboardMarkup:
    keys_in_cat = [k for k, c in CATEGORY_OF.items() if c == category]
    buttons = [
        InlineKeyboardButton(EMOJI_KEYS[k], callback_data=f"{EMOJI_KEY_CALLBACK_PREFIX}{k}")
        for k in keys_in_cat
    ]
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton("◀️ بازگشت به دسته‌ها", callback_data=EMOJI_BACK_CALLBACK)])
    return InlineKeyboardMarkup(rows)


def _extract_custom_emoji(message) -> tuple[str, str] | None:
    """Returns (custom_emoji_id, placeholder_text) if `message` contains a Premium
    custom emoji entity, else None."""
    entity = next((e for e in (message.entities or []) if e.type == MessageEntity.CUSTOM_EMOJI), None)
    if entity is None:
        return None
    placeholder = message.text[entity.offset : entity.offset + entity.length]
    return entity.custom_emoji_id, placeholder


async def set_emoji_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update):
        return

    if not context.args:
        await update.effective_message.reply_text(
            f"{get_emoji('settings')} <b>تنظیم ایموجی پرمیوم</b>\n"
            "اول یه دسته انتخاب کن، بعد کلید موردنظر رو، بعدش فقط همون <b>ایموجی پرمیوم</b> رو تک و تنها بفرست "
            "(از کیبورد ایموجی «پرمیوم» تلگرام، نه یونیکد معمولی).\n\n"
            "میان‌بر برای حرفه‌ای‌ها: <code>/set_emoji coin</code> 🪙 (کلید + ایموجی تو یه پیام)\n"
            "برای دیدن نتیجه‌ی فعلی همه‌چیز: /preview_emoji",
            parse_mode="HTML",
            reply_markup=_category_keyboard(),
        )
        return

    key = context.args[0]
    if key not in EMOJI_KEYS:
        await update.effective_message.reply_text(
            "کلید نامعتبره. کلیدهای قابل تنظیم:\n" + _keys_help(), parse_mode="HTML"
        )
        return

    extracted = _extract_custom_emoji(update.message)
    if extracted is None:
        await update.effective_message.reply_text(
            "⚠️ هیچ ایموجی پرمیومی توی پیامت پیدا نشد. باید خودِ ایموجی پرمیوم (نه یونیکد معمولی) رو بفرستی — "
            "مطمئن شو اشتراک پرمیومت فعاله و از کیبورد ایموجی «پرمیوم» تلگرام انتخابش کردی، نه ایموجی معمولی."
        )
        return

    custom_emoji_id, placeholder = extracted
    await run_db(set_emoji, key, custom_emoji_id, placeholder)
    await update.effective_message.reply_text(
        f"{get_emoji('confirm')} ایموجی «{EMOJI_KEYS[key]}» با موفقیت تنظیم شد.", parse_mode="HTML"
    )


async def set_emoji_category_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_owner(update):
        await query.answer()
        return
    category = query.data[len(EMOJI_CAT_CALLBACK_PREFIX) :]
    if category not in CATEGORY_LABELS:
        await query.answer("دسته نامعتبر شد، دوباره /set_emoji رو بزن.", show_alert=True)
        return
    await query.answer()
    await query.edit_message_text(
        f"{CATEGORY_LABELS[category]}\nکدوم کلید؟", reply_markup=_key_keyboard(category)
    )


async def set_emoji_back_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_owner(update):
        await query.answer()
        return
    await query.answer()
    await query.edit_message_text(
        f"{get_emoji('settings')} یه دسته انتخاب کن:", parse_mode="HTML", reply_markup=_category_keyboard()
    )


async def set_emoji_key_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_owner(update):
        await query.answer()
        return
    key = query.data[len(EMOJI_KEY_CALLBACK_PREFIX) :]
    if key not in EMOJI_KEYS:
        await query.answer("کلید نامعتبر شد، دوباره /set_emoji رو بزن.", show_alert=True)
        return

    context.user_data["awaiting_emoji_key"] = key
    await query.answer()
    await query.edit_message_text(
        f"👌 باشه! حالا فقط <b>ایموجی پرمیوم</b> مربوط به «{EMOJI_KEYS[key]}» رو بفرست "
        f"(تک و تنها، از کیبورد ایموجی «پرمیوم» تلگرام).\n\nبرای انصراف کافیه هر دستور دیگه‌ای بزنی.",
        parse_mode="HTML",
    )


async def capture_emoji_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Completes the /set_emoji button flow: fires on the owner's very next plain
    message after picking a key, since that message is expected to be just the
    Premium emoji itself. No-ops if no key selection is currently pending."""
    key = context.user_data.pop("awaiting_emoji_key", None)
    if key is None:
        return

    message = update.effective_message
    extracted = _extract_custom_emoji(message)
    if extracted is None:
        await message.reply_text(
            "⚠️ توی این پیام ایموجی پرمیومی پیدا نکردم. دوباره /set_emoji رو بزن و امتحان کن."
        )
        return

    custom_emoji_id, placeholder = extracted
    await run_db(set_emoji, key, custom_emoji_id, placeholder)
    await message.reply_text(
        f"{get_emoji('confirm')} ایموجی «{EMOJI_KEYS[key]}» با موفقیت تنظیم شد.", parse_mode="HTML"
    )


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
    await update.effective_message.reply_text(
        f"{get_emoji('confirm')} به حالت پیش‌فرض برگشت." if removed else "این کلید سفارشی تنظیم نشده بود.",
        parse_mode="HTML",
    )


async def list_emoji_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update):
        return
    overrides = await run_db(list_overrides)

    lines = [f"{get_emoji('settings')} <b>ایموجی‌های سفارشی فعلی</b>\n"]
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
    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")


async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update):
        return
    stats = await run_db(dashboard_stats)
    text = (
        "🛠 <b>پنل مدیریت</b>\n\n"
        f"{get_emoji('users')} کاربران: {stats['users']}   {get_emoji('creature')} موجودات: {stats['creatures']}\n"
        f"{get_emoji('alliance')} اتحادها: {stats['alliances']}   "
        f"{get_emoji('raid_boss')} رید فعال: {stats['active_raids']}\n\n"
        "━━━━━━━━━━━━━━\n"
        f"{get_emoji('profile')} <b>مدیریت کاربر</b> (آیدی یا @یوزرنیم)\n"
        "<code>/user_info آیدی</code> — پروفایل کامل\n"
        "<code>/grant آیدی coins/dna مقدار</code> — اعطای منبع\n"
        "<code>/deduct آیدی coins/dna مقدار</code> — کسر منبع\n"
        "<code>/ban آیدی</code> · <code>/unban آیدی</code> — مسدودسازی\n\n"
        f"{get_emoji('creature')} <b>مدیریت موجود</b>\n"
        "<code>/delete_creature شماره</code> — حذف واقعی (با تأیید)\n\n"
        f"{get_emoji('broadcast')} <b>ارتباط</b>\n"
        "<code>/broadcast متن</code> — پیام همگانی\n\n"
        f"{get_emoji('settings')} <b>ایموجی پرمیوم</b>\n"
        "<code>/set_emoji</code> — دکمه‌ای، دسته و کلید رو انتخاب کن بعد فقط ایموجی رو بفرست\n"
        "<code>/clear_emoji کلید</code> — برگردوندن به حالت پیش‌فرض\n"
        "<code>/preview_emoji</code> — دیدن نتیجه‌ی فعلی همه‌چیز، یه‌جا\n\n"
        "<i>دکمه‌ی «پنل کامل» فقط از همون سیستمی که بات روش اجرا می‌شه باز می‌شه "
        "(مگه ADMIN_PANEL_URL رو روی آدرس عمومی/تانل تنظیم کرده باشی).</i>"
    )
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📊 گزارش پیشرفت", callback_data="admin_menu:report"),
                InlineKeyboardButton("🔍 پیش‌نمایش ایموجی‌ها", callback_data="admin_menu:preview_emoji"),
            ],
            [InlineKeyboardButton("🌐 باز کردن پنل کامل", url=ADMIN_PANEL_URL)],
        ]
    )
    await update.effective_message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


_ADMIN_MENU_ACTIONS = {}  # populated at the bottom of the module, after every command is defined


async def admin_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_owner(update):
        await query.answer()
        return
    await query.answer()
    action = query.data.split(":", 1)[1]
    handler = _ADMIN_MENU_ACTIONS.get(action)
    if handler is not None:
        await handler(update, context)


async def report_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update):
        return
    data = await run_db(progress_report)

    lines = [
        f"{get_emoji('stats')} <b>گزارش پیشرفت</b>\n",
        f"{get_emoji('users')} کاربران: {data['users']}   {get_emoji('creature')} موجودات: {data['creatures']}"
        f"   {get_emoji('alliance')} اتحادها: {data['alliances']}\n",
        f"{get_emoji('coin')} <b>برترین بازیکن‌ها (سکه):</b>",
    ]
    for p in data["top_players"]:
        lines.append(f"• {p['name']} — {p['coins']} سکه")

    lines.append(f"\n{get_emoji('creature')} <b>قوی‌ترین موجودات (سطح):</b>")
    for c in data["top_creatures"]:
        lines.append(f"• {c['name']} (Lv{c['level']}) — {c['owner']}")

    lines.append("")
    if data["suspicious"]:
        lines.append(
            f"{get_emoji('warning')} <b>فعالیت مشکوک امروز</b> "
            "(بیشتر از سقف نظری ممکن با انرژی عادی — یعنی یا باگه یا اکسپلویت):"
        )
        for s in data["suspicious"]:
            lines.append(f"• {s['name']} — {s['action']}: {s['count']} بار (سقف نظری: {s['limit']})")
    else:
        lines.append(f"{get_emoji('confirm')} هیچ فعالیت مشکوکی امروز پیدا نشد.")

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


async def user_info_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update):
        return
    if not context.args:
        await update.effective_message.reply_text(
            "استفاده: <code>/user_info آیدی_یا_یوزرنیم</code>", parse_mode="HTML"
        )
        return
    try:
        data = await run_db(user_info, context.args[0])
    except GameError as exc:
        await update.effective_message.reply_text(str(exc))
        return

    user = data["user"]
    lines = [
        f"{get_emoji('profile')} <b>{display_name(user)}</b>  (<code>{user.id}</code>)",
        f"{get_emoji('coin')} {user.coins}   {get_emoji('dna')} {user.dna_fragments}   "
        f"{get_emoji('energy')} {user.energy}/{constants.MAX_ENERGY}",
        f"🔥 streak: {user.login_streak}   {get_emoji('alliance')} اتحاد: "
        f"{user.alliance.name if user.alliance_id else '—'}",
        f"{get_emoji('banned')} مسدود: {'بله' if user.is_banned else 'نه'}",
        f"📅 عضو از: {user.created_at.strftime('%Y-%m-%d')}\n",
        f"{get_emoji('creature')} <b>موجودات ({len(data['creatures'])}):</b>",
    ]
    for c in data["creatures"]:
        active_tag = " ✅فعال" if c.is_active else ""
        lines.append(f"  • <code>#{c.id}</code> {c.name} Lv{c.level} ({c.rarity}){active_tag}")
    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")


async def grant_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update):
        return
    if len(context.args) != 3 or not context.args[2].isdigit():
        await update.effective_message.reply_text(
            "استفاده: <code>/grant آیدی_یا_یوزرنیم coins/dna مقدار</code>", parse_mode="HTML"
        )
        return
    identifier, resource, amount_str = context.args
    try:
        user, new_value = await run_db(grant_resource, identifier, resource, int(amount_str))
    except GameError as exc:
        await update.effective_message.reply_text(str(exc))
        return
    await update.effective_message.reply_text(
        f"{get_emoji('confirm')} به {display_name(user)} داده شد. مقدار جدید {resource}: {new_value}",
        parse_mode="HTML",
    )


async def deduct_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update):
        return
    if len(context.args) != 3 or not context.args[2].isdigit():
        await update.effective_message.reply_text(
            "استفاده: <code>/deduct آیدی_یا_یوزرنیم coins/dna مقدار</code>", parse_mode="HTML"
        )
        return
    identifier, resource, amount_str = context.args
    try:
        user, new_value = await run_db(deduct_resource, identifier, resource, int(amount_str))
    except GameError as exc:
        await update.effective_message.reply_text(str(exc))
        return
    await update.effective_message.reply_text(
        f"{get_emoji('confirm')} از {display_name(user)} کم شد. مقدار جدید {resource}: {new_value}",
        parse_mode="HTML",
    )


async def ban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update):
        return
    if not context.args:
        await update.effective_message.reply_text(
            "استفاده: <code>/ban آیدی_یا_یوزرنیم</code>", parse_mode="HTML"
        )
        return
    try:
        user = await run_db(set_banned, context.args[0], True)
    except GameError as exc:
        await update.effective_message.reply_text(str(exc))
        return
    await update.effective_message.reply_text(
        f"{get_emoji('banned')} {display_name(user)} مسدود شد.", parse_mode="HTML"
    )


async def unban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update):
        return
    if not context.args:
        await update.effective_message.reply_text(
            "استفاده: <code>/unban آیدی_یا_یوزرنیم</code>", parse_mode="HTML"
        )
        return
    try:
        user = await run_db(set_banned, context.args[0], False)
    except GameError as exc:
        await update.effective_message.reply_text(str(exc))
        return
    await update.effective_message.reply_text(
        f"{get_emoji('confirm')} {display_name(user)} دیگه مسدود نیست.", parse_mode="HTML"
    )


def _delete_creature_preview_sync(creature_id: int):
    creature = get_creature_or_raise(creature_id)
    owner = User.objects.filter(id=creature.owner_id).first()
    return creature, display_name(owner) if owner is not None else str(creature.owner_id)


async def delete_creature_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update):
        return
    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text(
            "استفاده: <code>/delete_creature شماره</code>", parse_mode="HTML"
        )
        return
    creature_id = int(context.args[0])
    try:
        creature, owner_name = await run_db(_delete_creature_preview_sync, creature_id)
    except GameError as exc:
        await update.effective_message.reply_text(str(exc))
        return

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ حذف کن", callback_data=f"admin_del:{creature_id}"),
                InlineKeyboardButton("❌ بی‌خیال", callback_data="admin_del_cancel"),
            ]
        ]
    )
    await update.effective_message.reply_text(
        f"{get_emoji('warning')} مطمئنی می‌خوای <b>{creature.name}</b> (<code>#{creature.id}</code>, "
        f"مال {owner_name}) رو برای همیشه حذف کنی؟ این کار غیرقابل‌برگشته و لاگ‌های رید/نبرد مرتبط باهاش هم پاک می‌شن.",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


async def delete_creature_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_owner(update):
        await query.answer()
        return

    if query.data == "admin_del_cancel":
        await query.answer()
        await query.edit_message_text(f"{get_emoji('cancel')} لغو شد، چیزی حذف نشد.", parse_mode="HTML")
        return

    creature_id = int(query.data.split(":")[1])
    try:
        name = await run_db(delete_creature, creature_id)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer()
    await query.edit_message_text(f"🗑 موجود «{name}» برای همیشه حذف شد.", parse_mode="HTML")


async def preview_emoji_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """'گزینه تست' — shows a realistic sample card rendered with the current live
    emoji set (custom or default), plus every key grouped by category so it's easy
    to spot which ones are still default and which are already customized."""
    if not _is_owner(update):
        return

    lines = [
        "🔍 <b>پیش‌نمایش نمونه</b> (شبیه کارت موجود واقعی):\n",
        f"{get_emoji('creature')} <b>Pyrofang</b>  <code>#1</code>",
        f"{constants.element_label('fire')} · سطح ۵",
        "",
        f"{get_emoji('hp')} 90   {get_emoji('atk')} 24   {get_emoji('def')} 18   "
        f"{get_emoji('spd')} 15   {get_emoji('poison')} 3",
        f"{get_emoji('wings')} بال ۲ · {get_emoji('def')} زره ۱ · {get_emoji('fangs')} نیش ۳",
        "",
        f"{get_emoji('energy')} 14/20",
        f"{get_emoji('coin')} 850   {get_emoji('dna')} 40",
        "",
        "━━━━━━━━━━━━━━",
        "📋 <b>همه‌ی کلیدها به تفکیک دسته</b> (اگه پرمیوم تنظیم کرده باشی همینجا می‌بینیش):",
    ]
    for cat, cat_label in CATEGORY_LABELS.items():
        lines.append(f"\n<b>{cat_label}</b>")
        for key, (label, _default, key_cat) in EMOJI_DEFS.items():
            if key_cat != cat:
                continue
            lines.append(f"{get_emoji(key)} {label} (<code>{key}</code>)")

    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")


_ADMIN_MENU_ACTIONS.update(
    {"report": report_cmd, "list_emoji": list_emoji_cmd, "preview_emoji": preview_emoji_cmd}
)


def register(application) -> None:
    private_only = filters.ChatType.PRIVATE
    application.add_handler(CommandHandler("set_emoji", set_emoji_cmd, private_only))
    application.add_handler(CommandHandler("clear_emoji", clear_emoji_cmd, private_only))
    application.add_handler(CommandHandler("list_emoji", list_emoji_cmd, private_only))
    application.add_handler(CommandHandler("admin", admin_cmd, private_only))
    application.add_handler(CommandHandler("report", report_cmd, private_only))
    application.add_handler(CommandHandler("broadcast", broadcast_cmd, private_only))
    application.add_handler(CommandHandler("user_info", user_info_cmd, private_only))
    application.add_handler(CommandHandler("grant", grant_cmd, private_only))
    application.add_handler(CommandHandler("deduct", deduct_cmd, private_only))
    application.add_handler(CommandHandler("ban", ban_cmd, private_only))
    application.add_handler(CommandHandler("unban", unban_cmd, private_only))
    application.add_handler(CommandHandler("delete_creature", delete_creature_cmd, private_only))
    application.add_handler(CommandHandler("preview_emoji", preview_emoji_cmd, private_only))
    application.add_handler(CallbackQueryHandler(delete_creature_confirm_callback, pattern=r"^admin_del"))
    application.add_handler(CallbackQueryHandler(admin_menu_callback, pattern=r"^admin_menu:"))
    application.add_handler(
        CallbackQueryHandler(set_emoji_category_callback, pattern=f"^{EMOJI_CAT_CALLBACK_PREFIX}")
    )
    application.add_handler(
        CallbackQueryHandler(set_emoji_back_callback, pattern=f"^{EMOJI_BACK_CALLBACK}$")
    )
    application.add_handler(
        CallbackQueryHandler(set_emoji_key_callback, pattern=f"^{EMOJI_KEY_CALLBACK_PREFIX}")
    )
    application.add_handler(
        MessageHandler(
            filters.User(user_id=OWNER_TELEGRAM_ID) & filters.ChatType.PRIVATE & ~filters.COMMAND,
            capture_emoji_reply,
        )
    )

import asyncio

from django.utils import timezone
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, MessageEntity, MessageOriginChannel, Update
from telegram.error import TelegramError
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

from bio_lab.models import User
from bio_lab.repository import display_name
from bot.buttons import ADMIN, CONFIRM, DANGER, LIST, PRIMARY, back_btn, btn
from bot.utils import run_db, safe_edit_message_text
from game.button_emoji import (
    BUTTON_CATEGORY_LABELS,
    BUTTON_CATEGORY_OF,
    BUTTON_EMOJI_KEYS,
    clear_button_emoji,
    list_button_overrides,
    set_button_emoji,
)
from config import ADMIN_PANEL_URL, OWNER_TELEGRAM_ID
from game import constants
from game.creature import GameError
from game.emoji import EMOJI_DEFS, EMOJI_KEYS, CATEGORY_LABELS, CATEGORY_OF, clear_emoji, get_emoji, list_overrides, set_emoji
from game.force_join import (
    add_channel,
    list_channels,
    remove_channel,
    set_duration,
    set_reward,
)
from game.moderation import (
    charge_user,
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
        btn(label, style=ADMIN, callback_data=f"{EMOJI_CAT_CALLBACK_PREFIX}{cat}")
        for cat, label in CATEGORY_LABELS.items()
    ]
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    return InlineKeyboardMarkup(rows)


def _key_keyboard(category: str) -> InlineKeyboardMarkup:
    keys_in_cat = [k for k, c in CATEGORY_OF.items() if c == category]
    buttons = [
        btn(EMOJI_KEYS[k], style=LIST, callback_data=f"{EMOJI_KEY_CALLBACK_PREFIX}{k}")
        for k in keys_in_cat
    ]
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    rows.append([back_btn(EMOJI_BACK_CALLBACK, "بازگشت به دسته‌ها")])
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
    await safe_edit_message_text(query,
        f"{CATEGORY_LABELS[category]}\nکدوم کلید؟", reply_markup=_key_keyboard(category)
    )


async def set_emoji_back_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_owner(update):
        await query.answer()
        return
    await query.answer()
    await safe_edit_message_text(query,
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
    await safe_edit_message_text(query,
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
        "یکی رو از پایین انتخاب کن:"
    )
    keyboard = InlineKeyboardMarkup(
        [
            [
                btn("گزارش پیشرفت", emoji_key="btn_report", style=ADMIN, callback_data="admin_menu:report"),
                btn("مدیریت کاربر", emoji_key="btn_profile", style=ADMIN, callback_data="admin_menu:user_manage"),
            ],
            [
                btn("حذف موجود", emoji_key="btn_delete", style=DANGER, callback_data="admin_menu:del_creature_start"),
                btn("ارسال همگانی", emoji_key="btn_broadcast", style=ADMIN, callback_data="admin_menu:broadcast_start"),
            ],
            [
                btn("🎨 ایموجی متن‌ها", style=ADMIN, callback_data="admin_menu:set_emoji_start"),
                btn("🎛 ایموجی دکمه‌ها", style=ADMIN, callback_data="admin_menu:button_emoji"),
            ],
            [btn("🔍 پیش‌نمایش ایموجی‌ها", style=ADMIN, callback_data="admin_menu:preview_emoji")],
            [btn("📡 جوین اجباری", style=ADMIN, callback_data="admin_menu:force_join")],
            [btn("🌐 پنل تحت وب (رنگ دکمه‌ها، لودآوت، پشتیبان‌گیری)", style=PRIMARY, url=ADMIN_PANEL_URL)],
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
        f"{get_emoji('coin')} <b>برترین بازیکن‌ها (طلا):</b>",
    ]
    for p in data["top_players"]:
        lines.append(f"• {p['name']} — {p['coins']} طلا")

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


def _user_info_text(data: dict) -> str:
    user = data["user"]
    lines = [
        f"{get_emoji('profile')} <b>{display_name(user)}</b>  (<code>{user.id}</code>)",
        f"{get_emoji('coin')} {user.coins}   {get_emoji('dna')} {user.dna_fragments}   "
        f"{get_emoji('diamond')} {user.diamonds}   {get_emoji('energy')} {user.energy}/{constants.MAX_ENERGY}",
        f"🔥 streak: {user.login_streak}   {get_emoji('alliance')} اتحاد: "
        f"{user.alliance.name if user.alliance_id else '—'}",
        f"{get_emoji('banned')} مسدود: {'بله' if user.is_banned else 'نه'}",
        f"📅 عضو از: {timezone.localtime(user.created_at).strftime('%Y-%m-%d')}\n",
        f"{get_emoji('creature')} <b>موجودات ({len(data['creatures'])}):</b>",
    ]
    for c in data["creatures"]:
        active_tag = " ✅فعال" if c.is_active else ""
        lines.append(f"  • <code>#{c.id}</code> {c.name} Lv{c.level} ({c.rarity}){active_tag}")
    return "\n".join(lines)


def _user_manage_keyboard(target_id: int, is_banned: bool) -> InlineKeyboardMarkup:
    ban_button = (
        btn("رفع مسدودی", emoji_key="btn_confirm", style=CONFIRM, callback_data=f"admin_unban:{target_id}")
        if is_banned
        else btn("مسدود کردن", emoji_key="btn_cancel", style=DANGER, callback_data=f"admin_ban:{target_id}")
    )
    return InlineKeyboardMarkup(
        [
            [
                btn("اعطای طلا", style=CONFIRM, callback_data=f"admin_grant:{target_id}:coins"),
                btn("اعطای DNA", style=CONFIRM, callback_data=f"admin_grant:{target_id}:dna"),
                btn("اعطای الماس", style=CONFIRM, callback_data=f"admin_grant:{target_id}:diamonds"),
            ],
            [
                btn("کسر طلا", style=DANGER, callback_data=f"admin_deduct:{target_id}:coins"),
                btn("کسر DNA", style=DANGER, callback_data=f"admin_deduct:{target_id}:dna"),
                btn("کسر الماس", style=DANGER, callback_data=f"admin_deduct:{target_id}:diamonds"),
            ],
            [btn("شارژ کامل (طلا+DNA+الماس)", emoji_key="btn_charge", style=CONFIRM, callback_data=f"admin_charge:{target_id}")],
            [ban_button],
            [back_btn("admin_menu:admin_home", "بازگشت به پنل ادمین")],
        ]
    )


async def user_info_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Power-user shortcut — the advertised path is the admin panel's «👤 مدیریت
    کاربر» button, which also attaches quick grant/deduct/ban action buttons."""
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
    await update.effective_message.reply_text(
        _user_info_text(data), parse_mode="HTML", reply_markup=_user_manage_keyboard(user.id, user.is_banned)
    )


async def charge_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """One-shot multi-resource top-up: /charge <user> <gold> <dna> <diamonds>.
    The advertised path is the admin panel's «⚡ شارژ کامل» button."""
    if not _is_owner(update):
        return
    if len(context.args) != 4 or not all(_is_signed_int(a) for a in context.args[1:]):
        await update.effective_message.reply_text(
            "استفاده: <code>/charge آیدی_یا_یوزرنیم طلا DNA الماس</code>\n"
            "مثلاً: <code>/charge @someone 1000 50 20</code> (عدد منفی هم برای کسر قبوله)",
            parse_mode="HTML",
        )
        return
    identifier, coins, dna, diamonds = context.args
    try:
        user, new_values = await run_db(charge_user, identifier, int(coins), int(dna), int(diamonds))
    except GameError as exc:
        await update.effective_message.reply_text(str(exc))
        return
    await update.effective_message.reply_text(
        f"{get_emoji('confirm')} <b>{display_name(user)}</b> شارژ شد!\n\n" + _charge_summary(new_values),
        parse_mode="HTML",
        reply_markup=_user_manage_keyboard(user.id, user.is_banned),
    )


async def grant_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update):
        return
    if len(context.args) != 3 or not context.args[2].isdigit():
        await update.effective_message.reply_text(
            "استفاده: <code>/grant آیدی_یا_یوزرنیم coins/dna/diamonds مقدار</code>", parse_mode="HTML"
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


def _delete_creature_confirm_text(creature, owner_name: str) -> str:
    return (
        f"{get_emoji('warning')} مطمئنی می‌خوای <b>{creature.name}</b> (<code>#{creature.id}</code>, "
        f"مال {owner_name}) رو حذف کنی؟\n\n"
        "<blockquote>این کار غیرقابل‌برگشته و لاگ‌های رید/نبرد مرتبط باهاش هم پاک می‌شن.</blockquote>"
    )


def _delete_creature_confirm_keyboard(creature_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                btn("حذف کن", emoji_key="btn_delete", style=DANGER, callback_data=f"admin_del:{creature_id}"),
                btn("بی‌خیال", emoji_key="btn_cancel", style=DANGER, callback_data="admin_del_cancel"),
            ]
        ]
    )


async def delete_creature_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Power-user shortcut — the advertised path is the admin panel's «🗑 حذف موجود» button."""
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

    await update.effective_message.reply_text(
        _delete_creature_confirm_text(creature, owner_name),
        parse_mode="HTML",
        reply_markup=_delete_creature_confirm_keyboard(creature_id),
    )


async def delete_creature_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_owner(update):
        await query.answer()
        return

    if query.data == "admin_del_cancel":
        await query.answer()
        await safe_edit_message_text(query, f"{get_emoji('cancel')} لغو شد، چیزی حذف نشد.", parse_mode="HTML")
        return

    creature_id = int(query.data.split(":")[1])
    try:
        name = await run_db(delete_creature, creature_id)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer()
    await safe_edit_message_text(query, f"🗑 موجود «{name}» برای همیشه حذف شد.", parse_mode="HTML")


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


AWAITING_FORCE_JOIN_KEY = "awaiting_force_join"


def _channel_card(channel) -> str:
    limit = "♾ نامحدود" if channel.expires_at is None else f"⏳ تا {channel.expires_at.strftime('%Y-%m-%d %H:%M')}"
    reward_parts = []
    if channel.reward_coins:
        reward_parts.append(f"{channel.reward_coins} {get_emoji('coin')}")
    if channel.reward_dna:
        reward_parts.append(f"{channel.reward_dna} {get_emoji('dna')}")
    if channel.reward_diamonds:
        reward_parts.append(f"{channel.reward_diamonds} {get_emoji('diamond')}")
    reward = f"🎁 {' + '.join(reward_parts)}" if reward_parts else "🎁 بدون جایزه"
    handle = f"@{channel.username}" if channel.username else str(channel.chat_id)
    return (
        f"📡 <b>{channel.title or handle}</b> ({handle})\n{limit}\n{reward}\n\n"
        "<i>یادت نباشه بات رو ادمین همین کانال کنی، وگرنه نمی‌تونه عضویت رو چک کنه.</i>"
    )


def _channel_manage_keyboard(channel_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                btn("⏳ تنظیم مدت", style=ADMIN, callback_data=f"fj_dur:{channel_id}"),
                btn("♾ نامحدود کن", style=ADMIN, callback_data=f"fj_unlim:{channel_id}"),
            ],
            [btn("🎁 تنظیم جایزه", style=CONFIRM, callback_data=f"fj_reward:{channel_id}")],
            [btn("حذف کانال", emoji_key="btn_delete", style=DANGER, callback_data=f"fj_rm:{channel_id}")],
            [back_btn("admin_menu:force_join", "بازگشت به لیست")],
        ]
    )


async def force_join_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    channels = await run_db(list_channels)
    text = f"{get_emoji('settings')} <b>کانال‌های جوین اجباری</b>\n\n"
    rows = []
    if not channels:
        text += "<i>هنوز هیچ کانالی اضافه نشده.</i>"
    else:
        text += "برای مدیریت هرکدوم روش بزن:"
        for ch in channels:
            handle = f"@{ch.username}" if ch.username else str(ch.chat_id)
            rows.append(
                [btn(f"📡 {ch.title or handle}", style=LIST, callback_data=f"fj_manage:{ch.id}")]
            )
    rows.append([btn("افزودن کانال جدید", emoji_key="btn_confirm", style=CONFIRM, callback_data="fj_add")])
    rows.append([back_btn("admin_menu:admin_home", "بازگشت به پنل ادمین")])
    await update.effective_message.reply_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))


async def force_join_add_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_owner(update):
        await query.answer()
        return
    context.user_data[AWAITING_FORCE_JOIN_KEY] = {"action": "add_channel"}
    await query.answer()
    await safe_edit_message_text(query,
        "🟢 یه پیام از خودِ کانال موردنظر رو همینجا فوروارد کن (نه لینکش رو بفرستی، خودِ پیام رو فوروارد کن).\n\n"
        "<i>برای انصراف، از منوی پنل ادمین دوباره شروع کن.</i>",
        parse_mode="HTML",
    )


async def force_join_manage_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_owner(update):
        await query.answer()
        return
    channel_id = int(query.data.split(":")[1])
    channels = {c.id: c for c in await run_db(list_channels)}
    channel = channels.get(channel_id)
    if channel is None:
        await query.answer("این کانال دیگه پیدا نشد.", show_alert=True)
        return
    await query.answer()
    await safe_edit_message_text(query,
        _channel_card(channel), parse_mode="HTML", reply_markup=_channel_manage_keyboard(channel_id)
    )


async def force_join_duration_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_owner(update):
        await query.answer()
        return
    channel_id = int(query.data.split(":")[1])
    context.user_data[AWAITING_FORCE_JOIN_KEY] = {"action": "set_duration", "channel_id": channel_id}
    await query.answer()
    await safe_edit_message_text(query,
        "⏳ چند ساعت معتبر باشه؟ یه عدد بفرست (مثلاً <code>24</code>).", parse_mode="HTML"
    )


async def force_join_unlimited_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_owner(update):
        await query.answer()
        return
    channel_id = int(query.data.split(":")[1])
    try:
        channel = await run_db(set_duration, channel_id, None)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer("♾ نامحدود شد.")
    await safe_edit_message_text(query,
        _channel_card(channel), parse_mode="HTML", reply_markup=_channel_manage_keyboard(channel_id)
    )


async def force_join_reward_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_owner(update):
        await query.answer()
        return
    channel_id = int(query.data.split(":")[1])
    context.user_data[AWAITING_FORCE_JOIN_KEY] = {"action": "set_reward", "channel_id": channel_id}
    await query.answer()
    await safe_edit_message_text(query,
        f"🎁 سه عدد بفرست با فاصله: {get_emoji('coin')} طلا، {get_emoji('dna')} DNA، {get_emoji('diamond')} الماس\n"
        "(مثلاً <code>50 5 2</code> — برای بدون جایزه بنویس <code>0 0 0</code>).",
        parse_mode="HTML",
    )


async def force_join_remove_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_owner(update):
        await query.answer()
        return
    channel_id = int(query.data.split(":")[1])
    keyboard = InlineKeyboardMarkup(
        [
            [
                btn("بله، حذف کن", emoji_key="btn_delete", style=DANGER, callback_data=f"fj_rm_confirm:{channel_id}"),
                btn("بی‌خیال", emoji_key="btn_cancel", style=DANGER, callback_data=f"fj_manage:{channel_id}"),
            ]
        ]
    )
    await query.answer()
    await safe_edit_message_text(query,
        "مطمئنی این کانال از لیست جوین اجباری حذف بشه؟ دیگه اجباری نخواهد بود.", reply_markup=keyboard
    )


async def force_join_remove_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_owner(update):
        await query.answer()
        return
    channel_id = int(query.data.split(":")[1])
    await run_db(remove_channel, channel_id)
    await query.answer("🔴 حذف شد.")
    await force_join_panel(update, context)


async def capture_force_join_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    awaiting = context.user_data.pop(AWAITING_FORCE_JOIN_KEY, None)
    if awaiting is None:
        return
    message = update.effective_message
    action = awaiting["action"]

    if action == "add_channel":
        origin = message.forward_origin
        if not isinstance(origin, MessageOriginChannel):
            context.user_data[AWAITING_FORCE_JOIN_KEY] = awaiting  # keep waiting
            await message.reply_text(
                "⚠️ این یه پیام فورواردشده از یه کانال نبود. یه پیام رو مستقیم از خودِ کانال فوروارد کن."
            )
            return
        chat = origin.chat
        channel = await run_db(add_channel, chat.id, chat.username, chat.title)
        await message.reply_text(
            f"{get_emoji('confirm')} کانال اضافه شد!\n\n" + _channel_card(channel),
            parse_mode="HTML",
            reply_markup=_channel_manage_keyboard(channel.id),
        )
        return

    if action == "set_duration":
        channel_id = awaiting["channel_id"]
        text = (message.text or "").strip()
        if text in ("نامحدود", "∞", "0"):
            hours = None
        elif text.isdigit() and int(text) > 0:
            hours = int(text)
        else:
            context.user_data[AWAITING_FORCE_JOIN_KEY] = awaiting
            await message.reply_text("⚠️ یه عدد مثبت بفرست (ساعت)، یا بنویس «نامحدود».")
            return
        try:
            channel = await run_db(set_duration, channel_id, hours)
        except GameError as exc:
            await message.reply_text(str(exc))
            return
        await message.reply_text(
            _channel_card(channel), parse_mode="HTML", reply_markup=_channel_manage_keyboard(channel_id)
        )
        return

    if action == "set_reward":
        channel_id = awaiting["channel_id"]
        parts = (message.text or "").split()
        # accept 2 numbers too, so the old "gold DNA" muscle memory still works
        if len(parts) not in (2, 3) or not all(p.isdigit() for p in parts):
            context.user_data[AWAITING_FORCE_JOIN_KEY] = awaiting
            await message.reply_text(
                "⚠️ سه عدد با فاصله بفرست، مثلاً: <code>50 5 2</code>", parse_mode="HTML"
            )
            return
        coins, dna = int(parts[0]), int(parts[1])
        diamonds = int(parts[2]) if len(parts) == 3 else 0
        try:
            channel = await run_db(set_reward, channel_id, coins, dna, diamonds)
        except GameError as exc:
            await message.reply_text(str(exc))
            return
        await message.reply_text(
            _channel_card(channel), parse_mode="HTML", reply_markup=_channel_manage_keyboard(channel_id)
        )
        return


AWAITING_ADMIN_KEY = "awaiting_admin_input"

# ── button icons (separate registry from the message-body emoji above) ─────────
BTN_EMOJI_CAT_PREFIX = "btnemoji_cat:"
BTN_EMOJI_KEY_PREFIX = "btnemoji_key:"
BTN_EMOJI_BACK = "btnemoji_back"
BTN_EMOJI_CLEAR_PREFIX = "btnemoji_clear:"
AWAITING_BUTTON_EMOJI_KEY = "awaiting_button_emoji_key"


def _btn_emoji_category_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        btn(label, style=ADMIN, callback_data=f"{BTN_EMOJI_CAT_PREFIX}{cat}")
        for cat, label in BUTTON_CATEGORY_LABELS.items()
    ]
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    rows.append([back_btn("admin_menu:admin_home", "بازگشت به پنل ادمین")])
    return InlineKeyboardMarkup(rows)


def _btn_emoji_key_keyboard(category: str) -> InlineKeyboardMarkup:
    keys = [k for k, c in BUTTON_CATEGORY_OF.items() if c == category]
    buttons = [btn(BUTTON_EMOJI_KEYS[k], style=LIST, callback_data=f"{BTN_EMOJI_KEY_PREFIX}{k}") for k in keys]
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    rows.append([back_btn(BTN_EMOJI_BACK, "بازگشت به دسته‌ها")])
    return InlineKeyboardMarkup(rows)


async def button_emoji_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    overrides = await run_db(list_button_overrides)
    lines = [
        "🎛 <b>ایموجی دکمه‌ها</b>",
        "اینجا ایموجی پرمیومی که <b>روی خودِ دکمه‌ها</b> نشون داده می‌شه رو تنظیم می‌کنی — "
        "جدا از «🎨 ایموجی متن‌ها» که برای متن پیام‌هاست.\n",
        f"<b>الان {len(overrides)} دکمه ایموجی سفارشی داره.</b>",
        "\n<blockquote>هر دکمه فقط <b>یک</b> ایموجی می‌گیره که قبل از متنش می‌شینه. "
        "روی کلاینت‌های خیلی قدیمی ممکنه نمایش داده نشه، برای همین ایموجی معمولی هم توی متن دکمه می‌مونه.</blockquote>",
    ]
    await update.effective_message.reply_text(
        "\n".join(lines), parse_mode="HTML", reply_markup=_btn_emoji_category_keyboard()
    )


async def btn_emoji_category_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_owner(update):
        await query.answer()
        return
    category = query.data[len(BTN_EMOJI_CAT_PREFIX) :]
    if category not in BUTTON_CATEGORY_LABELS:
        await query.answer("دسته نامعتبره.", show_alert=True)
        return
    await query.answer()
    await safe_edit_message_text(
        query,
        f"{BUTTON_CATEGORY_LABELS[category]}\nکدوم دکمه؟",
        reply_markup=_btn_emoji_key_keyboard(category),
    )


async def btn_emoji_back_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_owner(update):
        await query.answer()
        return
    await query.answer()
    await safe_edit_message_text(
        query, "🎛 یه دسته انتخاب کن:", parse_mode="HTML", reply_markup=_btn_emoji_category_keyboard()
    )


async def btn_emoji_key_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_owner(update):
        await query.answer()
        return
    key = query.data[len(BTN_EMOJI_KEY_PREFIX) :]
    if key not in BUTTON_EMOJI_KEYS:
        await query.answer("این دکمه دیگه وجود نداره.", show_alert=True)
        return

    context.user_data[AWAITING_BUTTON_EMOJI_KEY] = key
    await query.answer()
    keyboard = InlineKeyboardMarkup(
        [
            [btn("پاک کردن (برگشت به پیش‌فرض)", emoji_key="btn_delete", style=DANGER,
                 callback_data=f"{BTN_EMOJI_CLEAR_PREFIX}{key}")],
            [back_btn(BTN_EMOJI_BACK, "بازگشت به دسته‌ها")],
        ]
    )
    await safe_edit_message_text(
        query,
        f"👌 حالا فقط <b>ایموجی پرمیوم</b> دکمه‌ی «{BUTTON_EMOJI_KEYS[key]}» رو بفرست "
        "(تک و تنها، از کیبورد ایموجی پرمیوم تلگرام).",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


async def btn_emoji_clear_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_owner(update):
        await query.answer()
        return
    key = query.data[len(BTN_EMOJI_CLEAR_PREFIX) :]
    removed = await run_db(clear_button_emoji, key)
    context.user_data.pop(AWAITING_BUTTON_EMOJI_KEY, None)
    await query.answer("↩️ به پیش‌فرض برگشت." if removed else "چیزی تنظیم نشده بود.")
    await safe_edit_message_text(
        query, "🎛 یه دسته انتخاب کن:", parse_mode="HTML", reply_markup=_btn_emoji_category_keyboard()
    )


async def capture_button_emoji_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    key = context.user_data.pop(AWAITING_BUTTON_EMOJI_KEY, None)
    if key is None:
        return
    message = update.effective_message
    extracted = _extract_custom_emoji(message)
    if extracted is None:
        context.user_data[AWAITING_BUTTON_EMOJI_KEY] = key  # keep waiting
        await message.reply_text(
            "⚠️ توی این پیام ایموجی پرمیومی پیدا نکردم. یه ایموجی پرمیوم تک و تنها بفرست."
        )
        return

    custom_emoji_id, placeholder = extracted
    await run_db(set_button_emoji, key, custom_emoji_id, placeholder)
    await message.reply_text(
        f"{get_emoji('confirm')} ایموجی دکمه‌ی «{BUTTON_EMOJI_KEYS[key]}» تنظیم شد.\n"
        "یه نمونه از همون دکمه 👇",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[btn("نمونه", emoji_key=key, style=PRIMARY, callback_data="btnemoji_noop")]]),
    )


async def btn_emoji_noop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer("این فقط یه نمونه‌ست 🙂")


async def user_manage_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data[AWAITING_ADMIN_KEY] = {"action": "user_info"}
    await update.effective_message.reply_text(
        f"{get_emoji('profile')} آیدی عددی یا @یوزرنیم بازیکن رو بفرست:", parse_mode="HTML"
    )


async def delete_creature_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data[AWAITING_ADMIN_KEY] = {"action": "delete_creature"}
    await update.effective_message.reply_text(
        f"{get_emoji('creature')} شماره‌ی موجودی که می‌خوای برای همیشه حذفش کنی رو بفرست:", parse_mode="HTML"
    )


async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data[AWAITING_ADMIN_KEY] = {"action": "broadcast"}
    await update.effective_message.reply_text(
        f"{get_emoji('broadcast')} متن پیام همگانی رو بفرست:", parse_mode="HTML"
    )


_RESOURCE_LABELS = {"coins": "طلا", "dna": "DNA", "diamonds": "الماس"}
_RESOURCE_EMOJI_KEYS = {"coins": "coin", "dna": "dna", "diamonds": "diamond"}


def _is_signed_int(value: str) -> bool:
    return value.lstrip("-").isdigit()


def _charge_summary(new_values: dict) -> str:
    return "\n".join(
        f"{get_emoji(_RESOURCE_EMOJI_KEYS[res])} {_RESOURCE_LABELS[res]}: <b>{value}</b>"
        for res, value in new_values.items()
    )


async def admin_grant_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_owner(update):
        await query.answer()
        return
    _, target_id, resource = query.data.split(":")
    context.user_data[AWAITING_ADMIN_KEY] = {"action": "grant", "target_id": target_id, "resource": resource}
    await query.answer()
    label = _RESOURCE_LABELS[resource]
    await safe_edit_message_text(query, f"🟢 چقدر {label} اعطا کنم؟ یه عدد مثبت بفرست:", parse_mode="HTML")


async def admin_deduct_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_owner(update):
        await query.answer()
        return
    _, target_id, resource = query.data.split(":")
    context.user_data[AWAITING_ADMIN_KEY] = {"action": "deduct", "target_id": target_id, "resource": resource}
    await query.answer()
    label = _RESOURCE_LABELS[resource]
    await safe_edit_message_text(query, f"🟠 چقدر {label} کسر کنم؟ یه عدد مثبت بفرست:", parse_mode="HTML")


async def admin_charge_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_owner(update):
        await query.answer()
        return
    target_id = query.data.split(":")[1]
    context.user_data[AWAITING_ADMIN_KEY] = {"action": "charge", "target_id": target_id}
    await query.answer()
    await safe_edit_message_text(
        query,
        f"⚡ سه عدد با فاصله بفرست: {get_emoji('coin')} طلا، {get_emoji('dna')} DNA، {get_emoji('diamond')} الماس\n"
        "(مثلاً <code>1000 50 20</code> — عدد منفی هم قبوله برای کسر کردن، مثل <code>-500 0 0</code>)",
        parse_mode="HTML",
    )


async def admin_ban_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_owner(update):
        await query.answer()
        return
    target_id = query.data.split(":")[1]
    try:
        user = await run_db(set_banned, target_id, True)
        data = await run_db(user_info, str(user.id))
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer("🔴 مسدود شد.")
    await safe_edit_message_text(query,
        _user_info_text(data), parse_mode="HTML", reply_markup=_user_manage_keyboard(user.id, True)
    )


async def admin_unban_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_owner(update):
        await query.answer()
        return
    target_id = query.data.split(":")[1]
    try:
        user = await run_db(set_banned, target_id, False)
        data = await run_db(user_info, str(user.id))
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer("✅ رفع شد.")
    await safe_edit_message_text(query,
        _user_info_text(data), parse_mode="HTML", reply_markup=_user_manage_keyboard(user.id, False)
    )


async def capture_admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    awaiting = context.user_data.pop(AWAITING_ADMIN_KEY, None)
    if awaiting is None:
        return
    message = update.effective_message
    action = awaiting["action"]
    text = (message.text or "").strip()

    if action == "user_info":
        try:
            data = await run_db(user_info, text)
        except GameError as exc:
            context.user_data[AWAITING_ADMIN_KEY] = awaiting
            await message.reply_text(str(exc))
            return
        user = data["user"]
        await message.reply_text(
            _user_info_text(data), parse_mode="HTML", reply_markup=_user_manage_keyboard(user.id, user.is_banned)
        )
        return

    if action in ("grant", "deduct"):
        if not text.isdigit() or int(text) <= 0:
            context.user_data[AWAITING_ADMIN_KEY] = awaiting
            await message.reply_text("⚠️ یه عدد مثبت بفرست.")
            return
        amount = int(text)
        target_id = awaiting["target_id"]
        resource = awaiting["resource"]
        func = grant_resource if action == "grant" else deduct_resource
        try:
            user, new_value = await run_db(func, target_id, resource, amount)
        except GameError as exc:
            await message.reply_text(str(exc))
            return
        verb = "اعطا شد" if action == "grant" else "کسر شد"
        await message.reply_text(
            f"{get_emoji('confirm')} به/از {display_name(user)} {verb}. مقدار جدید {resource}: {new_value}",
            parse_mode="HTML",
        )
        return

    if action == "charge":
        parts = text.split()
        if len(parts) != 3 or not all(_is_signed_int(p) for p in parts):
            context.user_data[AWAITING_ADMIN_KEY] = awaiting
            await message.reply_text("⚠️ سه عدد با فاصله بفرست، مثلاً: <code>1000 50 20</code>", parse_mode="HTML")
            return
        coins, dna, diamonds = (int(p) for p in parts)
        try:
            user, new_values = await run_db(charge_user, awaiting["target_id"], coins, dna, diamonds)
        except GameError as exc:
            context.user_data[AWAITING_ADMIN_KEY] = awaiting
            await message.reply_text(str(exc))
            return
        await message.reply_text(
            f"{get_emoji('confirm')} <b>{display_name(user)}</b> شارژ شد!\n\n" + _charge_summary(new_values),
            parse_mode="HTML",
            reply_markup=_user_manage_keyboard(user.id, user.is_banned),
        )
        return

    if action == "delete_creature":
        if not text.isdigit():
            context.user_data[AWAITING_ADMIN_KEY] = awaiting
            await message.reply_text("⚠️ یه شماره‌ی معتبر بفرست.")
            return
        creature_id = int(text)
        try:
            creature, owner_name = await run_db(_delete_creature_preview_sync, creature_id)
        except GameError as exc:
            await message.reply_text(str(exc))
            return
        await message.reply_text(
            _delete_creature_confirm_text(creature, owner_name),
            parse_mode="HTML",
            reply_markup=_delete_creature_confirm_keyboard(creature_id),
        )
        return

    if action == "broadcast":
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
        await message.reply_text(summary)
        return


async def capture_owner_text_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Single dispatcher for every 'awaiting a plain-text/forwarded reply' flow in
    the owner panel — PTB only ever runs the first handler that matches an update
    within a group, so every such flow has to live behind one registration."""
    if context.user_data.get("awaiting_emoji_key") is not None:
        await capture_emoji_reply(update, context)
        return
    if context.user_data.get(AWAITING_FORCE_JOIN_KEY) is not None:
        await capture_force_join_reply(update, context)
        return
    if context.user_data.get(AWAITING_ADMIN_KEY) is not None:
        await capture_admin_reply(update, context)
        return
    if context.user_data.get(AWAITING_BUTTON_EMOJI_KEY) is not None:
        await capture_button_emoji_reply(update, context)
        return


_ADMIN_MENU_ACTIONS.update(
    {
        "report": report_cmd,
        "list_emoji": list_emoji_cmd,
        "preview_emoji": preview_emoji_cmd,
        "force_join": force_join_panel,
        "admin_home": admin_cmd,
        "user_manage": user_manage_start,
        "del_creature_start": delete_creature_start,
        "broadcast_start": broadcast_start,
        "set_emoji_start": set_emoji_cmd,
        "button_emoji": button_emoji_panel,
    }
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
    application.add_handler(CommandHandler("charge", charge_cmd, private_only))
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
    application.add_handler(CallbackQueryHandler(force_join_add_callback, pattern=r"^fj_add$"))
    application.add_handler(CallbackQueryHandler(force_join_manage_callback, pattern=r"^fj_manage:"))
    application.add_handler(CallbackQueryHandler(force_join_duration_callback, pattern=r"^fj_dur:"))
    application.add_handler(CallbackQueryHandler(force_join_unlimited_callback, pattern=r"^fj_unlim:"))
    application.add_handler(CallbackQueryHandler(force_join_reward_callback, pattern=r"^fj_reward:"))
    application.add_handler(
        CallbackQueryHandler(force_join_remove_confirm_callback, pattern=r"^fj_rm_confirm:")
    )
    application.add_handler(CallbackQueryHandler(force_join_remove_callback, pattern=r"^fj_rm:"))
    application.add_handler(CallbackQueryHandler(btn_emoji_category_callback, pattern=f"^{BTN_EMOJI_CAT_PREFIX}"))
    application.add_handler(CallbackQueryHandler(btn_emoji_back_callback, pattern=f"^{BTN_EMOJI_BACK}$"))
    application.add_handler(CallbackQueryHandler(btn_emoji_clear_callback, pattern=f"^{BTN_EMOJI_CLEAR_PREFIX}"))
    application.add_handler(CallbackQueryHandler(btn_emoji_key_callback, pattern=f"^{BTN_EMOJI_KEY_PREFIX}"))
    application.add_handler(CallbackQueryHandler(btn_emoji_noop_callback, pattern=r"^btnemoji_noop$"))
    application.add_handler(CallbackQueryHandler(admin_grant_callback, pattern=r"^admin_grant:"))
    application.add_handler(CallbackQueryHandler(admin_deduct_callback, pattern=r"^admin_deduct:"))
    application.add_handler(CallbackQueryHandler(admin_charge_callback, pattern=r"^admin_charge:"))
    application.add_handler(CallbackQueryHandler(admin_unban_callback, pattern=r"^admin_unban:"))
    application.add_handler(CallbackQueryHandler(admin_ban_callback, pattern=r"^admin_ban:"))

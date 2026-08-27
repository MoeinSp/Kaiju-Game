import asyncio
import html
import json
import logging

from django.utils import timezone
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, MessageEntity, MessageOriginChannel, Update
from telegram.error import TelegramError
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

from bio_lab.models import User
from bio_lab.repository import display_name
from bot.buttons import ADMIN, CONFIRM, DANGER, LIST, NAV, PRIMARY, back_btn, btn
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
from game import botconfig, constants
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
    gift_all,
    global_stats,
    grant_resource,
    list_users_page,
    player_progress,
    reset_user,
    search_users,
    set_banned,
    set_lab_level,
    user_info,
)
from game.report import dashboard_stats, progress_report

logger = logging.getLogger(__name__)

BROADCAST_DELAY_SECONDS = 0.05  # ~20 msg/s, safely under Telegram's flood limits


def _is_owner(update: Update) -> bool:
    """Strict: only the single bot owner. Used for admin management (add/remove admin)."""
    return update.effective_user is not None and update.effective_user.id == OWNER_TELEGRAM_ID


def _is_admin(update: Update) -> bool:
    """The owner OR any owner-granted admin. Gates the whole panel except admin
    management. In-memory check (game.admins), so it's async-safe."""
    from game import admins

    u = update.effective_user
    return u is not None and (u.id == OWNER_TELEGRAM_ID or admins.is_admin(u.id))


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
    if not _is_admin(update):
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
    if not _is_admin(update):
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
    if not _is_admin(update):
        await query.answer()
        return
    await query.answer()
    await safe_edit_message_text(query,
        f"{get_emoji('settings')} یه دسته انتخاب کن:", parse_mode="HTML", reply_markup=_category_keyboard()
    )


async def set_emoji_key_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_admin(update):
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
    if not _is_admin(update):
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
    if not _is_admin(update):
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
    if not _is_admin(update):
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
                btn("📊 آمار کلی", style=ADMIN, callback_data="admin_menu:global_stats"),
                btn("گزارش پیشرفت", emoji_key="btn_report", style=ADMIN, callback_data="admin_menu:report"),
            ],
            [
                btn("👥 لیست کاربران", style=ADMIN, callback_data="admin_menu:users"),
                btn("🔍 جستجوی کاربر", emoji_key="btn_profile", style=ADMIN, callback_data="admin_menu:user_manage"),
            ],
            [btn("🕵 چیت‌یاب (جایزه‌گیرهای مشکوک)", style=DANGER, callback_data="admin_menu:cheat")],
            [btn("🛍 مدیریت فروشگاه (آیتم/پک)", style=ADMIN, callback_data="admin_menu:itemshop")],
            [
                btn("🎁 هدیه به همه", style=ADMIN, callback_data="admin_menu:gift_all"),
                btn("ارسال همگانی", emoji_key="btn_broadcast", style=ADMIN, callback_data="admin_menu:broadcast_start"),
            ],
            [btn("حذف موجود", emoji_key="btn_delete", style=DANGER, callback_data="admin_menu:del_creature_start")],
            [
                btn("🎨 ایموجی متن‌ها", style=ADMIN, callback_data="admin_menu:set_emoji_start"),
                btn("🎛 ایموجی دکمه‌ها", style=ADMIN, callback_data="admin_menu:button_emoji"),
            ],
            [btn("🔍 پیش‌نمایش ایموجی‌ها", style=ADMIN, callback_data="admin_menu:preview_emoji")],
            [
                btn("📡 جوین اجباری", style=ADMIN, callback_data="admin_menu:force_join"),
                btn("🎮 گروه بازی", style=ADMIN, callback_data="admin_menu:group_link"),
            ],
            [btn("🌐 پنل تحت وب (رنگ دکمه‌ها، لودآوت، پشتیبان‌گیری)", style=PRIMARY, url=ADMIN_PANEL_URL)],
        ]
    )
    if _is_owner(update):
        # admin management + auto-backup are the owner's alone
        rows = list(keyboard.inline_keyboard)
        rows.insert(-1, [
            btn("👮 مدیریت ادمین‌ها", style=ADMIN, callback_data="admin_menu:admin_manage"),
            btn("💾 بکاپ خودکار", style=ADMIN, callback_data="admin_menu:autobackup"),
        ])
        keyboard = InlineKeyboardMarkup(rows)
    await update.effective_message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


# ── admin management (owner-only) ─────────────────────────────────────────────

async def admin_manage_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update):
        if update.callback_query:
            await update.callback_query.answer("فقط مالک می‌تونه ادمین‌ها رو مدیریت کنه.", show_alert=True)
        return
    from game import admins

    admin_list = await run_db(admins.list_admins)
    lines = ["👮 <b>مدیریت ادمین‌ها</b>\n", "<i>ادمین‌ها همه‌کاره‌ی پنل‌ان جز افزودن/حذف ادمین.</i>\n"]
    rows = []
    if admin_list:
        for a in admin_list:
            lines.append(f"• {display_name(a)} (<code>{a.id}</code>)")
            rows.append([btn(f"🗑 حذف {display_name(a)}", style=DANGER, callback_data=f"admin_rm:{a.id}")])
    else:
        lines.append("هنوز ادمینی اضافه نشده.")
    rows.append([btn("➕ افزودن ادمین", style=CONFIRM, callback_data="admin_menu:admin_add")])
    rows.append([back_btn("admin_menu:admin_home", "بازگشت به پنل ادمین")])
    target = update.callback_query.message if update.callback_query else update.effective_message
    await target.reply_text("\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))


async def admin_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update):
        return
    context.user_data[AWAITING_ADMIN_KEY] = {"action": "add_admin"}
    await update.effective_message.reply_text(
        "👮 آیدی عددی یا @یوزرنیم کسی که می‌خوای ادمین شه رو بفرست:", parse_mode="HTML"
    )


async def admin_remove_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_owner(update):
        await query.answer("فقط مالک می‌تونه.", show_alert=True)
        return
    from game import admins

    target_id = int(query.data.split(":")[1])
    await run_db(admins.remove_admin, target_id)
    await query.answer("حذف شد.")
    await admin_manage_panel(update, context)


_BACKUP_INTERVAL_CHOICES = [0, 6, 12, 24, 48]


def _autobackup_panel_markup(hours: int, dest_id) -> tuple[str, InlineKeyboardMarkup]:
    status = "خاموش" if hours == 0 else f"هر <b>{hours}</b> ساعت"
    dest_label = "پیوی مالک (پیش‌فرض)" if not dest_id else f"<code>{dest_id}</code>"
    text = (
        "💾 <b>بکاپ خودکار دیتابیس</b>\n\n"
        f"⏱ بازه: {status}\n"
        f"📍 مقصد: {dest_label}\n\n"
        "<blockquote>هر بازه یه نسخه‌ی فشرده از کل دیتابیس ساخته و به مقصد فرستاده می‌شه. "
        "می‌تونی بازه‌ی دلخواه بذاری، مقصد رو به یه گروه/کانال تغییر بدی، همین حالا بکاپ بگیری، "
        "یا از یه فایل بکاپ بازیابی کنی.</blockquote>"
    )
    labels = {0: "🚫 خاموش", 6: "۶ ساعت", 12: "۱۲ ساعت", 24: "۲۴ ساعت", 48: "۴۸ ساعت"}
    rows = [[btn(("✅ " if h == hours else "") + labels[h], style=(CONFIRM if h == hours else ADMIN),
                 callback_data=f"autobk_set:{h}")] for h in _BACKUP_INTERVAL_CHOICES]
    rows.append([btn("⏱ بازه‌ی دلخواه (ساعت)", style=ADMIN, callback_data="autobk_custom")])
    rows.append([
        btn("📍 مقصد: همینجا", style=ADMIN, callback_data="autobk_dest_here"),
        btn("📍 مقصد دلخواه", style=ADMIN, callback_data="autobk_dest_set"),
    ])
    rows.append([btn("📤 بکاپ همین حالا", style=CONFIRM, callback_data="autobk_now")])
    rows.append([btn("♻️ بازیابی از فایل", style=DANGER, callback_data="autobk_restore")])
    rows.append([back_btn("admin_menu:admin_home", "بازگشت به پنل ادمین")])
    return text, InlineKeyboardMarkup(rows)


def _autobackup_state_sync():
    return botconfig.get_backup_interval(), botconfig.get_backup_chat_id()


async def autobackup_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update):
        if update.callback_query:
            await update.callback_query.answer("فقط مالک.", show_alert=True)
        return
    hours, dest_id = await run_db(_autobackup_state_sync)
    text, keyboard = _autobackup_panel_markup(hours, dest_id)
    target = update.callback_query.message if update.callback_query else update.effective_message
    await target.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def autobackup_set_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_owner(update):
        await query.answer("فقط مالک.", show_alert=True)
        return
    hours = int(query.data.split(":")[1])
    await run_db(botconfig.set_backup_interval, hours)
    await query.answer("ذخیره شد." if hours else "خاموش شد.")
    await autobackup_panel(update, context)


# ── cheat-finder ──────────────────────────────────────────────────────────────
def _cheat_report_sync(limit: int = 15):
    """Today's biggest «جایزه» claimers, most-claimed first — the main signal for a
    reward-farming account. With the global cooldown a normal player tops out well
    under the theoretical cap, so an outlier near it stands out."""
    from django.utils import timezone

    from bio_lab.models import DailyActionLog, User
    from bio_lab.repository import display_name
    from game.daily import today_str

    rows = (
        DailyActionLog.objects.filter(action="word_reward", day=today_str())
        .select_related("user")
        .order_by("-count")[:limit]
    )
    now = timezone.now()
    out = []
    for r in rows:
        u = r.user
        age_days = (now - u.created_at).days if u.created_at else 0
        out.append({
            "id": u.id,
            "name": display_name(u),
            "count": r.count,
            "banned": u.is_banned,
            "age_days": age_days,
            "lifetime": u.reward_total_claims,
        })
    return out, User.objects.count()


async def cheat_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        if update.callback_query:
            await update.callback_query.answer()
        return
    rows, total_users = await run_db(_cheat_report_sync)
    lines = [
        "🕵 <b>چیت‌یاب — جایزه‌گیرهای امروز</b>",
        f"<blockquote>بیشترین دفعاتِ گرفتن «جایزه»/«کایجو» در امروز. "
        f"کول‌داون حالا سراسریه، پس عدد خیلی بالا = مشکوک.\n"
        f"👥 کل کاربرها: {total_users}</blockquote>",
    ]
    keyboard_rows = []
    if not rows:
        lines.append("\n<i>امروز هنوز کسی جایزه نگرفته.</i>")
    for i, r in enumerate(rows, start=1):
        flag = "🚫 " if r["banned"] else ""
        lines.append(
            f"{i}. {flag}<b>{r['name']}</b> — امروز <b>{r['count']}</b> بار "
            f"<i>(کل: {r['lifetime']} · سن حساب: {r['age_days']} روز)</i>"
        )
        keyboard_rows.append([btn(
            f"{flag}مدیریت {r['name']} ({r['count']})",
            style=ADMIN, callback_data=f"admin_uinfo:{r['id']}",
        )])
    keyboard_rows.append([back_btn("admin_menu:admin_home", "بازگشت به پنل ادمین")])
    target = update.callback_query.message if update.callback_query else update.effective_message
    await target.reply_text("\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard_rows))


# ── owner-authored item shop management ───────────────────────────────────────
_ITEMSHOP_HELP = (
    "🛍 <b>افزودن آیتم به فروشگاه</b>\n\n"
    "آیتم رو توی این قالب بفرست (هر خط یه چیز):\n"
    "<blockquote>خط ۱: عنوان (می‌تونه با ایموجی شروع شه)\n"
    "خط ۲: قیمت — مثل «قیمت: 19000 جم» یا «قیمت: 5000 سکه 50 جم»\n"
    "خط‌های بعد: محتوا، هرکدوم یکی:\n"
    "• <code>سکه 10000</code>\n"
    "• <code>جم 25</code>\n"
    "• <code>dna 200</code>\n"
    "• <code>کارت 60 3</code>  (کارت سرعت ۶۰دقیقه ×۳)\n"
    "• <code>هیولا mythic fire</code>  (عنصر اختیاریه)\n"
    "• <code>تجهیزات weapon legendary</code></blockquote>\n"
    "<b>نمونه پک:</b>\n"
    "<code>🐉 پک اساطیری\nقیمت: 19000 جم\nسکه 10000\ndna 200\nهیولا mythic fire\nتجهیزات weapon legendary</code>"
)


def _itemshop_list_sync():
    from game import itemshop

    items = itemshop.list_items(active_only=False)
    return [{
        "id": it.id, "emoji": it.emoji, "title": it.title, "active": it.is_active,
        "price": itemshop.price_text(it),
        "contents": itemshop.content_summary(json.loads(it.contents_json)),
    } for it in items]


async def itemshop_manage_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        if update.callback_query:
            await update.callback_query.answer()
        return
    items = await run_db(_itemshop_list_sync)
    lines = ["🛍 <b>مدیریت فروشگاه آیتم‌ها</b>"]
    rows = [
        [btn("➕ ساخت آیتم/پک (با دکمه)", style=CONFIRM, callback_data="ish:new")],
        [btn("✍️ افزودن با متن", style=NAV, callback_data="sitem_add")],
    ]
    if not items:
        lines.append("<i>هنوز آیتمی نساختی.</i>")
    for it in items:
        state = "🟢" if it["active"] else "🔴"
        lines.append(f"\n{state} {it['emoji']} <b>{it['title']}</b> — {it['price']}\n   <i>{it['contents']}</i>")
        rows.append([
            btn(("🔴 غیرفعال" if it["active"] else "🟢 فعال") + f" {it['title'][:14]}",
                style=ADMIN, callback_data=f"sitem_toggle:{it['id']}"),
            btn("🗑 حذف", style=DANGER, callback_data=f"sitem_del:{it['id']}"),
        ])
    rows.append([back_btn("admin_menu:admin_home", "بازگشت به پنل ادمین")])
    target = update.callback_query.message if update.callback_query else update.effective_message
    await target.reply_text("\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))


async def itemshop_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_admin(update):
        await query.answer()
        return
    context.user_data[AWAITING_ADMIN_KEY] = {"action": "shop_item"}
    await query.answer()
    await query.message.reply_text(_ITEMSHOP_HELP, parse_mode="HTML")


# ── inline item/pack builder ──────────────────────────────────────────────────
_ISH_DRAFT = "itemshop_draft"
_ISH_PRESETS = {
    "coins": [1000, 5000, 10000, 50000, 100000],
    "diamonds": [10, 50, 100, 500, 1000],
    "dna": [50, 100, 200, 500, 1000],
}
_ISH_CTYPE_LABELS = {
    "coins": "🪙 سکه", "diamonds": "💎 جم", "dna": "🧬 DNA",
    "speedup": "⏱ کارت سرعت", "creature": "🐉 هیولا", "equipment": "⚔️ تجهیزات",
}


def _ish_draft(context) -> dict:
    return context.user_data.setdefault(
        _ISH_DRAFT, {"title": None, "emoji": "🎁", "price_coins": 0, "price_diamonds": 0, "contents": [], "max_per_user": 0}
    )


def _ish_home_markup(draft: dict):
    from game import itemshop

    title = draft["title"] or "—"
    price_bits = []
    if draft["price_coins"]:
        price_bits.append(f"{draft['price_coins']:,} طلا")
    if draft["price_diamonds"]:
        price_bits.append(f"{draft['price_diamonds']} 💎")
    price = " + ".join(price_bits) or "— (تنظیم نشده)"
    contents = itemshop.content_summary(draft["contents"]) if draft["contents"] else "— (خالی)"
    limit = draft.get("max_per_user", 0)
    limit_txt = f"هر نفر {limit} بار" if limit else "نامحدود"
    text = (
        "🛠 <b>ساخت آیتم/پک (با دکمه)</b>\n\n"
        f"{draft['emoji']} عنوان: <b>{title}</b>\n"
        f"💰 قیمت: <b>{price}</b>\n"
        f"🔢 محدودیت خرید: <b>{limit_txt}</b>\n"
        f"🎁 محتوا ({len(draft['contents'])}): {contents}"
    )
    rows = [
        [btn("✏️ عنوان", style=ADMIN, callback_data="ish:title"),
         btn("💰 قیمت", style=ADMIN, callback_data="ish:price")],
        [btn("🔢 محدودیت خرید", style=ADMIN, callback_data="ish:limit")],
        [btn("➕ افزودن محتوا", style=CONFIRM, callback_data="ish:addc")],
    ]
    if draft["contents"]:
        rows.append([btn("↩️ حذف آخرین محتوا", style=NAV, callback_data="ish:rmlast")])
    ready = bool(draft["title"]) and (draft["price_coins"] or draft["price_diamonds"]) and draft["contents"]
    if ready:
        rows.append([btn("✅ ثبت و انتشار آیتم", style=CONFIRM, callback_data="ish:save")])
    rows.append([btn("🗑 پاک‌کردن پیش‌نویس", style=DANGER, callback_data="ish:discard"),
                 back_btn("admin_menu:itemshop", "بازگشت")])
    return text, InlineKeyboardMarkup(rows)


async def _ish_show_home(update, context, *, edit=True):
    text, kb = _ish_home_markup(_ish_draft(context))
    q = update.callback_query
    if edit and q is not None:
        await safe_edit_message_text(q, text, parse_mode="HTML", reply_markup=kb)
    else:
        target = q.message if q else update.effective_message
        await target.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def _ish_ask_name(query) -> None:
    """Offer a custom name for a creature/equipment component, or add it as-is."""
    kb = InlineKeyboardMarkup([
        [btn("✏️ اسم دلخواه بده", style=ADMIN, callback_data="ish:name")],
        [btn("بدون اسم (پیش‌فرض)", style=CONFIRM, callback_data="ish:noname")],
    ])
    await safe_edit_message_text(
        query, "می‌خوای اسم دلخواه روش بذاری؟ (مثلاً «اژدهای آتش» یا «شمشیر مرگ»)",
        parse_mode="HTML", reply_markup=kb,
    )


async def itemshop_builder_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_admin(update):
        await query.answer()
        return
    from game import constants

    parts = query.data.split(":")
    verb = parts[1]
    draft = _ish_draft(context)

    if verb in ("new", "home"):
        if verb == "new":
            context.user_data[_ISH_DRAFT] = {"title": None, "emoji": "🎁",
                                             "price_coins": 0, "price_diamonds": 0, "contents": [], "max_per_user": 0}
        await query.answer()
        await _ish_show_home(update, context)
        return
    if verb == "discard":
        context.user_data.pop(_ISH_DRAFT, None)
        await query.answer("پیش‌نویس پاک شد.")
        await itemshop_manage_panel(update, context)
        return
    if verb == "rmlast":
        if draft["contents"]:
            draft["contents"].pop()
        await query.answer("حذف شد.")
        await _ish_show_home(update, context)
        return
    if verb == "title":
        context.user_data[AWAITING_ADMIN_KEY] = {"action": "ish_title"}
        await query.answer()
        await query.message.reply_text("✏️ عنوان آیتم رو بفرست (می‌تونه با ایموجی شروع شه):")
        return
    if verb == "price":
        context.user_data[AWAITING_ADMIN_KEY] = {"action": "ish_price"}
        await query.answer()
        await query.message.reply_text(
            "💰 قیمت رو بفرست، مثل: <code>19000 جم</code> یا <code>5000 سکه</code> یا <code>5000 سکه 50 جم</code>",
            parse_mode="HTML",
        )
        return
    if verb == "addc":
        rows = [[btn(_ISH_CTYPE_LABELS[k], style=ADMIN, callback_data=f"ish:ct:{k}")]
                for k in ("coins", "diamonds", "dna", "speedup", "creature", "equipment")]
        rows.append([btn("↩️ بازگشت", style=NAV, callback_data="ish:home")])
        await query.answer()
        await safe_edit_message_text(query, "➕ <b>نوع محتوا رو انتخاب کن:</b>", parse_mode="HTML",
                                     reply_markup=InlineKeyboardMarkup(rows))
        return
    if verb == "ct":
        kind = parts[2]
        if kind in _ISH_PRESETS:
            rows = [[btn(f"{v:,}", style=ADMIN, callback_data=f"ish:amt:{kind}:{v}")]
                    for v in _ISH_PRESETS[kind]]
            rows.append([btn("🔢 مقدار دلخواه", style=NAV, callback_data=f"ish:amtx:{kind}")])
            rows.append([btn("↩️ بازگشت", style=NAV, callback_data="ish:addc")])
            await query.answer()
            await safe_edit_message_text(query, f"مقدار «{_ISH_CTYPE_LABELS[kind]}» رو انتخاب کن:",
                                         parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))
            return
        if kind == "speedup":
            rows = [[btn(f"{m} دقیقه", style=ADMIN, callback_data=f"ish:sp:{m}")]
                    for m in constants.SPEEDUP_MINUTES]
            rows.append([btn("↩️ بازگشت", style=NAV, callback_data="ish:addc")])
            await query.answer()
            await safe_edit_message_text(query, "⏱ مدت کارت سرعت رو انتخاب کن:", parse_mode="HTML",
                                         reply_markup=InlineKeyboardMarkup(rows))
            return
        if kind == "creature":
            rows = [[btn(constants.RARITY_LABELS[r], style=ADMIN, callback_data=f"ish:cr:{r}")]
                    for r in constants.RARITY_ORDER]
            rows.append([btn("↩️ بازگشت", style=NAV, callback_data="ish:addc")])
            await query.answer()
            await safe_edit_message_text(query, "🐉 نایابیِ هیولا رو انتخاب کن:", parse_mode="HTML",
                                         reply_markup=InlineKeyboardMarkup(rows))
            return
        if kind == "equipment":
            rows = [[btn(constants.EQUIPMENT_SLOT_LABELS[s], style=ADMIN, callback_data=f"ish:eq:{s}")]
                    for s in constants.EQUIPMENT_SLOTS]
            rows.append([btn("↩️ بازگشت", style=NAV, callback_data="ish:addc")])
            await query.answer()
            await safe_edit_message_text(query, "⚔️ اسلات تجهیزات رو انتخاب کن:", parse_mode="HTML",
                                         reply_markup=InlineKeyboardMarkup(rows))
            return
    if verb == "amt":
        kind, amount = parts[2], int(parts[3])
        draft["contents"].append({"type": kind, "amount": amount})
        await query.answer("اضافه شد.")
        await _ish_show_home(update, context)
        return
    if verb == "amtx":
        context.user_data[AWAITING_ADMIN_KEY] = {"action": "ish_amount", "kind": parts[2]}
        await query.answer()
        await query.message.reply_text(f"🔢 مقدار «{_ISH_CTYPE_LABELS[parts[2]]}» رو به عدد بفرست:")
        return
    if verb == "sp":
        minutes = int(parts[2])
        rows = [[btn(f"×{c}", style=ADMIN, callback_data=f"ish:spc:{minutes}:{c}")] for c in (1, 2, 3, 5, 10)]
        rows.append([btn("↩️ بازگشت", style=NAV, callback_data="ish:ct:speedup")])
        await query.answer()
        await safe_edit_message_text(query, f"تعداد کارت {minutes}دقیقه‌ای:", parse_mode="HTML",
                                     reply_markup=InlineKeyboardMarkup(rows))
        return
    if verb == "spc":
        draft["contents"].append({"type": "speedup", "minutes": int(parts[2]), "count": int(parts[3])})
        await query.answer("اضافه شد.")
        await _ish_show_home(update, context)
        return
    if verb == "cr":
        rarity = parts[2]
        rows = [[btn(constants.ELEMENT_WORDS[e], style=ADMIN, callback_data=f"ish:cre:{rarity}:{e}")]
                for e in constants.ELEMENTS]
        rows.append([btn("🎲 تصادفی", style=NAV, callback_data=f"ish:cre:{rarity}:rand")])
        rows.append([btn("↩️ بازگشت", style=NAV, callback_data="ish:ct:creature")])
        await query.answer()
        await safe_edit_message_text(query, "🐉 عنصرِ هیولا:", parse_mode="HTML",
                                     reply_markup=InlineKeyboardMarkup(rows))
        return
    if verb == "cre":
        rarity, element = parts[2], parts[3]
        context.user_data["ish_pending"] = {"type": "creature", "rarity": rarity,
                                            "element": None if element == "rand" else element}
        await query.answer()
        await _ish_ask_name(query)
        return
    if verb == "eq":
        slot = parts[2]
        rows = [[btn(constants.RARITY_LABELS[r], style=ADMIN, callback_data=f"ish:eqr:{slot}:{r}")]
                for r in constants.RARITY_ORDER]
        rows.append([btn("↩️ بازگشت", style=NAV, callback_data="ish:ct:equipment")])
        await query.answer()
        await safe_edit_message_text(query, "⚔️ نایابیِ تجهیزات:", parse_mode="HTML",
                                     reply_markup=InlineKeyboardMarkup(rows))
        return
    if verb == "eqr":
        context.user_data["ish_pending"] = {"type": "equipment", "slot": parts[2], "rarity": parts[3]}
        await query.answer()
        await _ish_ask_name(query)
        return
    if verb == "noname":  # add the pending creature/equipment without a custom name
        pending = context.user_data.pop("ish_pending", None)
        if pending:
            draft["contents"].append(pending)
        await query.answer("اضافه شد.")
        await _ish_show_home(update, context)
        return
    if verb == "name":  # ask for a custom name for the pending component
        if context.user_data.get("ish_pending"):
            context.user_data[AWAITING_ADMIN_KEY] = {"action": "ish_cname"}
            await query.answer()
            await query.message.reply_text("✏️ اسم دلخواه برای این هیولا/تجهیزات رو بفرست:")
        else:
            await query.answer()
        return
    if verb == "limit":
        context.user_data[AWAITING_ADMIN_KEY] = {"action": "ish_limit"}
        await query.answer()
        await query.message.reply_text(
            "🔢 هر نفر چند بار بتونه این آیتم رو بخره؟ عدد بفرست (<code>0</code> = نامحدود، <code>1</code> = فقط یک‌بار).",
            parse_mode="HTML",
        )
        return
    if verb == "save":
        from game import itemshop

        if not (draft.get("title") and (draft["price_coins"] or draft["price_diamonds"]) and draft["contents"]):
            await query.answer("عنوان، قیمت و حداقل یه محتوا لازمه.", show_alert=True)
            return
        item = await run_db(itemshop.create_item_from_draft, draft)
        context.user_data.pop(_ISH_DRAFT, None)
        await query.answer("✅ ثبت شد!")
        await safe_edit_message_text(
            query,
            f"✅ <b>آیتم ساخته شد:</b> {item.emoji} {item.title}\n"
            "حالا توی «🛍 آیتم‌های ویژه»ی فروشگاه دیده می‌شه.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[back_btn("admin_menu:itemshop", "بازگشت به مدیریت فروشگاه")]]),
        )
        return
    await query.answer()


async def itemshop_toggle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_admin(update):
        await query.answer()
        return
    from game import itemshop

    try:
        await run_db(itemshop.toggle_item, int(query.data.split(":")[1]))
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer("انجام شد.")
    await itemshop_manage_panel(update, context)


async def itemshop_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_admin(update):
        await query.answer()
        return
    from game import itemshop

    await run_db(itemshop.delete_item, int(query.data.split(":")[1]))
    await query.answer("حذف شد.")
    await itemshop_manage_panel(update, context)


async def autobackup_custom_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_owner(update):
        await query.answer("فقط مالک.", show_alert=True)
        return
    context.user_data[AWAITING_ADMIN_KEY] = {"action": "backup_interval"}
    await query.answer()
    await query.message.reply_text(
        "⏱ بازه‌ی بکاپ خودکار رو به <b>ساعت</b> بفرست (مثلاً <code>8</code>). "
        "<code>0</code> یعنی خاموش.",
        parse_mode="HTML",
    )


async def autobackup_dest_here_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_owner(update):
        await query.answer("فقط مالک.", show_alert=True)
        return
    await run_db(botconfig.set_backup_chat_id, None)
    await query.answer("مقصد شد پیوی مالک.")
    await autobackup_panel(update, context)


async def autobackup_dest_set_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_owner(update):
        await query.answer("فقط مالک.", show_alert=True)
        return
    context.user_data[AWAITING_ADMIN_KEY] = {"action": "backup_dest"}
    await query.answer()
    await query.message.reply_text(
        "📍 <b>chat id</b> مقصد بکاپ رو بفرست (مثلاً <code>-1001234567890</code> برای یه گروه/کانال). "
        "بات باید اونجا عضو/ادمین باشه تا بتونه فایل بفرسته.\n"
        "<i>برای برگردوندن به پیوی مالک، عدد <code>0</code> بفرست.</i>",
        parse_mode="HTML",
    )


async def autobackup_now_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_owner(update):
        await query.answer("فقط مالک.", show_alert=True)
        return
    await query.answer("در حال ساخت بکاپ…")
    from game.backup import create_backup

    try:
        meta = await run_db(create_backup, "manual")
        dest = await run_db(botconfig.get_backup_chat_id) or OWNER_TELEGRAM_ID
        with open(meta["path"], "rb") as fh:
            await context.bot.send_document(
                chat_id=dest, document=fh, filename=meta["name"], caption="💾 بکاپ دستی دیتابیس",
            )
        await query.message.reply_text("✅ بکاپ ساخته و به مقصد فرستاده شد.")
    except (TelegramError, OSError) as exc:
        await query.message.reply_text(f"⚠️ نشد بکاپ رو بفرستم: {exc}")


async def autobackup_restore_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_owner(update):
        await query.answer("فقط مالک.", show_alert=True)
        return
    context.user_data[AWAITING_RESTORE_KEY] = True
    await query.answer()
    await query.message.reply_text(
        "♻️ <b>بازیابی دیتابیس از فایل</b>\n\n"
        "فایل بکاپ (<code>.json.gz</code>) رو همینجا بفرست.\n"
        "<b>⚠️ هشدار:</b> بازیابی کل دیتای فعلی بازی رو با محتوای فایل <b>جایگزین</b> می‌کنه "
        "و برگشت‌پذیر نیست. قبلش یه «📤 بکاپ همین حالا» بگیر.\n\n"
        "<i>برای انصراف، هر پیام دیگه‌ای بفرست.</i>",
        parse_mode="HTML",
    )


_ADMIN_MENU_ACTIONS = {}  # populated at the bottom of the module, after every command is defined


async def admin_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_admin(update):
        await query.answer()
        return
    await query.answer()
    action = query.data.split(":", 1)[1]
    handler = _ADMIN_MENU_ACTIONS.get(action)
    if handler is not None:
        await handler(update, context)


async def report_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
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
    if not _is_admin(update):
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
        f"{html.escape(data['alliance_name']) if data.get('alliance_name') else '—'}",
        f"{get_emoji('banned')} مسدود: {'بله' if user.is_banned else 'نه'}",
        f"📅 عضو از: {timezone.localtime(user.created_at).strftime('%Y-%m-%d')}\n",
        f"{get_emoji('creature')} <b>موجودات ({len(data['creatures'])}):</b>",
    ]
    # cap the list so a big roster can't push the message past Telegram's 4096 limit;
    # names are escaped because a creature/lab name can contain <, > or & (HTML-unsafe)
    shown = data["creatures"][:40]
    for c in shown:
        active_tag = " ✅فعال" if c.is_active else ""
        lines.append(f"  • <code>#{c.id}</code> {html.escape(c.name)} Lv{c.level} ({c.rarity}){active_tag}")
    if len(data["creatures"]) > len(shown):
        lines.append(f"  <i>… و {len(data['creatures']) - len(shown)} تای دیگه</i>")
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
                btn("💰 اعطای طلا", style=CONFIRM, callback_data=f"admin_grant:{target_id}:coins"),
                btn("🧬 اعطای DNA", style=CONFIRM, callback_data=f"admin_grant:{target_id}:dna"),
                btn("💎 اعطای الماس", style=CONFIRM, callback_data=f"admin_grant:{target_id}:diamonds"),
            ],
            [
                btn("💰 کسر طلا", style=DANGER, callback_data=f"admin_deduct:{target_id}:coins"),
                btn("🧬 کسر DNA", style=DANGER, callback_data=f"admin_deduct:{target_id}:dna"),
                btn("💎 کسر الماس", style=DANGER, callback_data=f"admin_deduct:{target_id}:diamonds"),
            ],
            [btn("شارژ کامل (طلا+DNA+الماس)", emoji_key="btn_charge", style=CONFIRM, callback_data=f"admin_charge:{target_id}")],
            [btn("🔬 تنظیم سطح آزمایشگاه", emoji_key="btn_lab", style=CONFIRM, callback_data=f"admin_lablevel:{target_id}")],
            [
                btn("📊 لاگ پیشرفت", emoji_key="btn_report", style=ADMIN, callback_data=f"admin_plog:{target_id}"),
                btn("✉️ پیام", style=ADMIN, callback_data=f"admin_dm:{target_id}"),
            ],
            [ban_button],
            [btn("♻️ ریست کامل بازیکن", emoji_key="btn_delete", style=DANGER, callback_data=f"admin_reset:{target_id}")],
            [back_btn("admin_menu:admin_home", "بازگشت به پنل ادمین")],
        ]
    )


async def user_info_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Power-user shortcut — the advertised path is the admin panel's «👤 مدیریت
    کاربر» button, which also attaches quick grant/deduct/ban action buttons."""
    if not _is_admin(update):
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
    if not _is_admin(update):
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
    if not _is_admin(update):
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
    if not _is_admin(update):
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
    if not _is_admin(update):
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
    if not _is_admin(update):
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
    if not _is_admin(update):
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
    if not _is_admin(update):
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


def _reset_preview_sync(identifier: str):
    """Fetch the target (raising if unknown) so the confirm card can name them and
    show what they're about to lose."""
    data = user_info(identifier)
    return data["user"], len(data["creatures"])


def _reset_confirm_text(user, creature_count: int) -> str:
    return (
        f"{get_emoji('warning')} مطمئنی می‌خوای بازیِ <b>{display_name(user)}</b> "
        f"(<code>{user.id}</code>) رو <b>کامل ریست</b> کنی؟\n\n"
        f"<blockquote>همه‌ی پیشرفتش پاک می‌شه: {creature_count} موجود، ساختمون‌ها، تجهیزات، "
        "کاپ، سطح آزمایشگاه، کارت‌ها و کل تاریخچه. بعدش دقیقاً مثل یه بازیکن تازه شروع می‌کنه "
        "(اسم آزمایشگاه و هدیه‌ی شروع سرجاش). این کار غیرقابل‌برگشته.</blockquote>"
    )


def _reset_confirm_keyboard(target_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                btn("بله، ریست کن", emoji_key="btn_delete", style=DANGER, callback_data=f"admin_reset_do:{target_id}"),
                btn("بی‌خیال", emoji_key="btn_cancel", style=DANGER, callback_data=f"admin_reset_cancel:{target_id}"),
            ]
        ]
    )


async def reset_user_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Power-user shortcut — the advertised path is the «♻️ ریست کامل بازیکن»
    button under «مدیریت کاربر» in the admin panel."""
    if not _is_admin(update):
        return
    if not context.args:
        await update.effective_message.reply_text(
            "استفاده: <code>/reset_user آیدی_یا_یوزرنیم</code>", parse_mode="HTML"
        )
        return
    try:
        user, creature_count = await run_db(_reset_preview_sync, context.args[0])
    except GameError as exc:
        await update.effective_message.reply_text(str(exc))
        return
    await update.effective_message.reply_text(
        _reset_confirm_text(user, creature_count),
        parse_mode="HTML",
        reply_markup=_reset_confirm_keyboard(user.id),
    )


async def reset_user_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Panel button «♻️ ریست کامل بازیکن» → show the typed-style confirmation."""
    query = update.callback_query
    if not _is_admin(update):
        await query.answer()
        return
    target_id = int(query.data.split(":")[1])
    try:
        user, creature_count = await run_db(_reset_preview_sync, str(target_id))
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer()
    await safe_edit_message_text(
        query,
        _reset_confirm_text(user, creature_count),
        parse_mode="HTML",
        reply_markup=_reset_confirm_keyboard(target_id),
    )


async def reset_user_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_admin(update):
        await query.answer()
        return

    action, target_id = query.data.split(":")
    if action == "admin_reset_cancel":
        await query.answer()
        await safe_edit_message_text(query, f"{get_emoji('cancel')} لغو شد، چیزی ریست نشد.", parse_mode="HTML")
        return

    try:
        user = await run_db(reset_user, target_id)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer("ریست شد", show_alert=False)
    await safe_edit_message_text(
        query,
        f"♻️ بازیِ <b>{display_name(user)}</b> (<code>{user.id}</code>) کامل ریست شد؛ "
        "الان یه آزمایشگاه تازه با هدیه‌ی شروع داره.",
        parse_mode="HTML",
    )


_ACTION_LABELS = {
    "feed": "تغذیه", "train": "تمرین", "hunt": "شکار", "arena_attack": "حمله آرنا",
    "raid_attack": "حمله رید", "duel_win": "برد نبرد گروهی", "fusion": "ادغام",
    "collect": "جمع‌آوری", "wheel_spin": "گردونه", "guardian_stipend": "حقوق محافظ",
    "heist": "شبیخون", "guardian_challenge": "چالش محافظ",
}


def _player_log_text(d: dict) -> str:
    user = d["user"]
    rarities = "، ".join(
        f"{constants.RARITY_LABELS.get(r, r)}×{n}" for r, n in d["rarity_counts"].items()
    ) or "—"
    halls = "، ".join(
        f"{constants.BUILDING_LABELS.get(bt, bt)} L{lv}" for bt, lv in sorted(d["buildings"].items()) if lv
    ) or "—"
    lines = [
        f"📊 <b>لاگ پیشرفت {display_name(user)}</b>  (<code>{user.id}</code>)",
        f"🔬 سطح آزمایشگاه: <b>{d['lab_level']}</b> (XP {d['lab_xp']})",
        f"{get_emoji('coin')} {user.coins}   {get_emoji('dna')} {user.dna_fragments}   {get_emoji('diamond')} {user.diamonds}",
        f"🏆 کاپ: {d['cup']}   🔥 استریک: {d['streak']}",
        f"⚔️ آرنا: {d['arena_wins']}/{d['arena_total']} برد",
        "",
        f"{get_emoji('creature')} موجودات: <b>{d['creatures_total']}</b>  ({rarities})",
        f"⭐ بیشترین ستاره: {d['max_star']}   📈 بالاترین سطح موجود: {d['max_creature_level']}",
        f"🏗 ساختمون‌ها: {halls}",
        f"📅 عضو از: {timezone.localtime(d['created_at']).strftime('%Y-%m-%d')}",
    ]
    if d["recent_activity"]:
        lines.append("\n🗒 <b>فعالیت اخیر:</b>")
        for day, action, count in d["recent_activity"]:
            lines.append(f"  {day} · {_ACTION_LABELS.get(action, action)}: {count}")
    else:
        lines.append("\n🗒 <i>فعالیت ثبت‌شده‌ای نداره.</i>")
    return "\n".join(lines)


async def player_log_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Power-user shortcut for «📊 لاگ پیشرفت». Accepts a numeric id, @username, or
    lab name."""
    if not _is_admin(update):
        return
    if not context.args:
        await update.effective_message.reply_text(
            "استفاده: <code>/player_log آیدی_یا_یوزرنیم_یا_اسم‌آزمایشگاه</code>", parse_mode="HTML"
        )
        return
    try:
        data = await run_db(player_progress, " ".join(context.args))
    except GameError as exc:
        await update.effective_message.reply_text(str(exc))
        return
    await update.effective_message.reply_text(_player_log_text(data), parse_mode="HTML")


async def player_log_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_admin(update):
        await query.answer()
        return
    target_id = query.data.split(":")[1]
    try:
        data = await run_db(player_progress, target_id)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer()
    await safe_edit_message_text(
        query,
        _player_log_text(data),
        parse_mode="HTML",
        reply_markup=_user_manage_keyboard(data["user"].id, data["user"].is_banned),
    )


async def preview_emoji_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """'گزینه تست' — shows a realistic sample card rendered with the current live
    emoji set (custom or default), plus every key grouped by category so it's easy
    to spot which ones are still default and which are already customized."""
    if not _is_admin(update):
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
        f"{get_emoji('energy')} 38/50",
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
    if not _is_admin(update):
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
    if not _is_admin(update):
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
    if not _is_admin(update):
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
    if not _is_admin(update):
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
    if not _is_admin(update):
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
    if not _is_admin(update):
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
    if not _is_admin(update):
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
AWAITING_RESTORE_KEY = "awaiting_restore_file"  # owner is expected to upload a backup .json.gz

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
    if not _is_admin(update):
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
    if not _is_admin(update):
        await query.answer()
        return
    await query.answer()
    await safe_edit_message_text(
        query, "🎛 یه دسته انتخاب کن:", parse_mode="HTML", reply_markup=_btn_emoji_category_keyboard()
    )


async def btn_emoji_key_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_admin(update):
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
    if not _is_admin(update):
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
    context.user_data[AWAITING_ADMIN_KEY] = {"action": "user_search"}
    await update.effective_message.reply_text(
        f"{get_emoji('profile')} آیدی عددی، @یوزرنیم یا بخشی از اسم بازیکن رو بفرست:", parse_mode="HTML"
    )


def _user_row_button(row: dict):
    flag = "🚫 " if row["banned"] else ""
    return btn(
        f"{flag}{row['name']} — 🏆{row['cup']} · 💰{row['coins']}",
        style=ADMIN, callback_data=f"admin_uinfo:{row['id']}",
    )


def _users_browse_render(data: dict) -> tuple[str, InlineKeyboardMarkup]:
    lines = [f"👥 <b>کاربران</b> (صفحه {data['page'] + 1}، جمعاً {data['total']})"]
    rows = [[_user_row_button(u)] for u in data["users"]]
    nav = []
    if data["has_prev"]:
        nav.append(btn("◀️ قبلی", style=ADMIN, callback_data=f"admin_users:{data['page'] - 1}"))
    if data["has_next"]:
        nav.append(btn("بعدی ▶️", style=ADMIN, callback_data=f"admin_users:{data['page'] + 1}"))
    if nav:
        rows.append(nav)
    rows.append([back_btn("admin_menu:admin_home", "بازگشت به پنل ادمین")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def users_browse_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        return
    page = 0
    data_str = update.callback_query.data if update.callback_query else ""
    if data_str.startswith("admin_users:"):
        page = int(data_str.split(":")[1])
    data = await run_db(list_users_page, page)
    text, keyboard = _users_browse_render(data)
    if update.callback_query:
        await update.callback_query.answer()
        await safe_edit_message_text(update.callback_query, text, parse_mode="HTML", reply_markup=keyboard)
    else:
        await update.effective_message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


def _user_open_sync(target_id: int):
    return user_info(str(target_id))


async def user_open_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_admin(update):
        await query.answer()
        return
    target_id = int(query.data.split(":")[1])
    try:
        data = await run_db(_user_open_sync, target_id)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer()
    user = data["user"]
    text = _user_info_text(data)
    keyboard = _user_manage_keyboard(user.id, user.is_banned)
    try:
        await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)
    except TelegramError:
        # editing the results message can fail (too old, not modifiable, parse edge
        # case) — never leave the owner staring at a dead button; post a fresh card
        # and record why so the underlying cause is visible.
        logger.exception("user_open edit failed for target %s; falling back to new message", target_id)
        try:
            await query.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)
        except TelegramError:
            logger.exception("user_open fallback send also failed for target %s", target_id)
            await query.answer("نمایش این کاربر با خطا خورد — لاگ رو چک کن.", show_alert=True)


async def gift_all_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data[AWAITING_ADMIN_KEY] = {"action": "gift_all"}
    await update.effective_message.reply_text(
        "🎁 <b>هدیه به همه‌ی کاربران</b>\n"
        "سه عدد با فاصله بفرست: <code>طلا DNA الماس</code>\n"
        "مثلاً <code>1000 50 10</code> (هرکدوم رو نخواستی، صفر بذار).",
        parse_mode="HTML",
    )


async def global_stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        return
    s = await run_db(global_stats)
    text = (
        f"{get_emoji('stats')} <b>آمار کلی</b>\n\n"
        f"{get_emoji('users')} کاربران: <b>{s['users']}</b>  (🚫 {s['banned']} مسدود)\n"
        f"🟢 فعال امروز: <b>{s['active_today']}</b>   🆕 جدید امروز: <b>{s['new_today']}</b>\n"
        f"{get_emoji('creature')} موجودات: <b>{s['creatures']}</b>\n\n"
        f"<b>اقتصاد کل بازی:</b>\n"
        f"{get_emoji('coin')} طلا: <b>{s['total_coins']:,}</b>\n"
        f"{get_emoji('dna')} DNA: <b>{s['total_dna']:,}</b>\n"
        f"{get_emoji('diamond')} الماس: <b>{s['total_diamonds']:,}</b>"
    )
    kb = InlineKeyboardMarkup([[back_btn("admin_menu:admin_home", "بازگشت به پنل ادمین")]])
    if update.callback_query:
        await update.callback_query.answer()
        await safe_edit_message_text(update.callback_query, text, parse_mode="HTML", reply_markup=kb)
    else:
        await update.effective_message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def dm_user_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_admin(update):
        await query.answer()
        return
    target_id = int(query.data.split(":")[1])
    context.user_data[AWAITING_ADMIN_KEY] = {"action": "dm_user", "target_id": target_id}
    await query.answer()
    await query.message.reply_text(
        f"✉️ متن پیامی که می‌خوای به کاربر <code>{target_id}</code> بفرستم رو بنویس:",
        parse_mode="HTML",
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


def _group_link_sync() -> tuple[str, str]:
    link = botconfig.get_group_link()
    if link is None:
        return "", ""
    return link  # (url, title)


def _group_link_panel_keyboard(has_link: bool) -> InlineKeyboardMarkup:
    rows = [[btn("✏️ تنظیم/تغییر لینک گروه", style=PRIMARY, callback_data="admin_menu:group_link_set")]]
    if has_link:
        rows.append([btn("🗑 حذف دکمه‌ی گروه", style=DANGER, callback_data="admin_menu:group_link_clear")])
    rows.append([back_btn("admin_menu:admin_home", "بازگشت به پنل ادمین")])
    return InlineKeyboardMarkup(rows)


async def group_link_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    url, title = await run_db(_group_link_sync)
    if url:
        body = (
            f"🎮 <b>گروه بازی</b>\n\n"
            f"لینک فعلی: <code>{url}</code>\n"
            f"متن دکمه: <b>{title}</b>\n\n"
            "این دکمه ته منوی اصلیِ همه‌ی بازیکن‌ها نشون داده می‌شه."
        )
    else:
        body = (
            "🎮 <b>گروه بازی</b>\n\n"
            "هنوز گروهی تنظیم نشده. با تنظیم لینک، یه دکمه ته منوی اصلیِ همه‌ی بازیکن‌ها اضافه می‌شه "
            "که مستقیم می‌برتشون به گروه بازی."
        )
    await update.effective_message.reply_text(
        body, parse_mode="HTML", reply_markup=_group_link_panel_keyboard(bool(url))
    )


async def group_link_set_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data[AWAITING_ADMIN_KEY] = {"action": "set_group_link"}
    await update.effective_message.reply_text(
        "🎮 لینک گروه رو بفرست (مثلاً <code>https://t.me/mygroup</code>).\n\n"
        "اگه می‌خوای متن دکمه هم عوض شه، بعد از لینک یه <code>|</code> بذار و متن دلخواه رو بنویس:\n"
        "<code>https://t.me/mygroup | 🎮 گروه ما</code>",
        parse_mode="HTML",
    )


async def group_link_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await run_db(botconfig.set_group_link, "", "")
    await update.effective_message.reply_text(
        "✅ دکمه‌ی گروه حذف شد.", reply_markup=_group_link_panel_keyboard(False)
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
    if not _is_admin(update):
        await query.answer()
        return
    _, target_id, resource = query.data.split(":")
    context.user_data[AWAITING_ADMIN_KEY] = {"action": "grant", "target_id": target_id, "resource": resource}
    await query.answer()
    label = _RESOURCE_LABELS[resource]
    await safe_edit_message_text(query, f"🟢 چقدر {label} اعطا کنم؟ یه عدد مثبت بفرست:", parse_mode="HTML")


async def admin_deduct_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_admin(update):
        await query.answer()
        return
    _, target_id, resource = query.data.split(":")
    context.user_data[AWAITING_ADMIN_KEY] = {"action": "deduct", "target_id": target_id, "resource": resource}
    await query.answer()
    label = _RESOURCE_LABELS[resource]
    await safe_edit_message_text(query, f"🟠 چقدر {label} کسر کنم؟ یه عدد مثبت بفرست:", parse_mode="HTML")


async def admin_charge_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_admin(update):
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


async def admin_lablevel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_admin(update):
        await query.answer()
        return
    target_id = query.data.split(":")[1]
    context.user_data[AWAITING_ADMIN_KEY] = {"action": "set_lab_level", "target_id": target_id}
    await query.answer()
    from game.lab import LAB_MAX_LEVEL

    await safe_edit_message_text(
        query,
        f"🔬 سطح آزمایشگاه جدید رو بفرست (یه عدد بین ۱ تا {LAB_MAX_LEVEL}):",
        parse_mode="HTML",
    )


async def admin_ban_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_admin(update):
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
    if not _is_admin(update):
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

    if action in ("user_info", "user_search"):
        # exact id/username → open directly; otherwise show matching candidates
        exact = None
        try:
            exact = await run_db(user_info, text)
        except GameError:
            exact = None
        if exact is not None:
            user = exact["user"]
            await message.reply_text(
                _user_info_text(exact), parse_mode="HTML",
                reply_markup=_user_manage_keyboard(user.id, user.is_banned),
            )
            return
        matches = await run_db(search_users, text)
        if not matches:
            context.user_data[AWAITING_ADMIN_KEY] = awaiting
            await message.reply_text("کاربری پیدا نشد. یه آیدی، @یوزرنیم یا بخشی از اسم دیگه بفرست:")
            return
        rows = [[_user_row_button(m)] for m in matches]
        rows.append([back_btn("admin_menu:admin_home", "بازگشت به پنل ادمین")])
        await message.reply_text(
            f"🔍 <b>{len(matches)} کاربر پیدا شد</b> — یکی رو انتخاب کن:",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows),
        )
        return

    if action == "gift_all":
        parts = text.split()
        if len(parts) != 3 or not all(p.lstrip("-").isdigit() for p in parts):
            context.user_data[AWAITING_ADMIN_KEY] = awaiting
            await message.reply_text("⚠️ سه عدد با فاصله بفرست، مثلاً: <code>1000 50 10</code>", parse_mode="HTML")
            return
        coins, dna, diamonds = (int(p) for p in parts)
        try:
            affected = await run_db(gift_all, coins, dna, diamonds)
        except GameError as exc:
            context.user_data[AWAITING_ADMIN_KEY] = awaiting
            await message.reply_text(str(exc))
            return
        await message.reply_text(
            f"🎁 به <b>{affected}</b> کاربر هدیه داده شد: "
            f"{coins} طلا + {dna} DNA + {diamonds} الماس.",
            parse_mode="HTML",
        )
        return

    if action == "add_admin":
        from game import admins

        try:
            user = await run_db(admins.add_admin, text)
        except GameError as exc:
            context.user_data[AWAITING_ADMIN_KEY] = awaiting
            await message.reply_text(str(exc))
            return
        await message.reply_text(
            f"✅ <b>{display_name(user)}</b> حالا ادمینه (همه‌کاره جز مدیریت ادمین‌ها).",
            parse_mode="HTML",
        )
        return

    if action == "dm_user":
        target_id = awaiting["target_id"]
        try:
            await context.bot.send_message(chat_id=target_id, text=f"✉️ <b>پیام از مدیریت:</b>\n\n{text}", parse_mode="HTML")
        except TelegramError:
            await message.reply_text("⚠️ نشد بفرستم — احتمالاً کاربر بات رو بلاک کرده یا استارت نزده.")
            return
        await message.reply_text(f"✅ پیام به کاربر <code>{target_id}</code> فرستاده شد.", parse_mode="HTML")
        return

    if action == "set_lab_level":
        from game.lab import LAB_MAX_LEVEL

        if not text.isdigit() or not (1 <= int(text) <= LAB_MAX_LEVEL):
            context.user_data[AWAITING_ADMIN_KEY] = awaiting
            await message.reply_text(f"⚠️ یه عدد بین ۱ تا {LAB_MAX_LEVEL} بفرست.")
            return
        try:
            user, new_level = await run_db(set_lab_level, awaiting["target_id"], int(text))
            data = await run_db(user_info, str(user.id))
        except GameError as exc:
            await message.reply_text(str(exc))
            return
        await message.reply_text(
            f"{get_emoji('confirm')} سطح آزمایشگاه <b>{display_name(user)}</b> روی <b>{new_level}</b> تنظیم شد.",
            parse_mode="HTML",
            reply_markup=_user_manage_keyboard(user.id, user.is_banned),
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

    if action == "set_group_link":
        raw = text
        if "|" in raw:
            url_part, title_part = raw.split("|", 1)
        else:
            url_part, title_part = raw, ""
        url = url_part.strip()
        title = title_part.strip()
        # accept t.me/... without scheme; require a plausible link otherwise
        if url.startswith("t.me/") or url.startswith("@"):
            url = "https://t.me/" + url.lstrip("@").removeprefix("t.me/")
        if not (url.startswith("http://") or url.startswith("https://")):
            context.user_data[AWAITING_ADMIN_KEY] = awaiting
            await message.reply_text(
                "⚠️ لینک معتبر نیست. باید با <code>https://</code> شروع شه یا مثل "
                "<code>https://t.me/mygroup</code> باشه. دوباره بفرست:",
                parse_mode="HTML",
            )
            return
        await run_db(botconfig.set_group_link, url, title)
        shown_title = title or botconfig.DEFAULT_GROUP_TITLE
        await message.reply_text(
            f"✅ دکمه‌ی گروه تنظیم شد.\nلینک: <code>{url}</code>\nمتن دکمه: <b>{shown_title}</b>\n\n"
            "حالا ته منوی اصلیِ همه‌ی بازیکن‌ها نشون داده می‌شه.",
            parse_mode="HTML",
        )
        return

    if action == "backup_interval":
        digits = text.strip()
        if not digits.isdigit() or int(digits) > 720:
            context.user_data[AWAITING_ADMIN_KEY] = awaiting
            await message.reply_text("⚠️ یه عدد ساعت بین ۰ تا ۷۲۰ بفرست (۰ = خاموش).")
            return
        hours = int(digits)
        await run_db(botconfig.set_backup_interval, hours)
        await message.reply_text(
            "🚫 بکاپ خودکار خاموش شد." if hours == 0 else f"✅ بازه‌ی بکاپ خودکار شد هر <b>{hours}</b> ساعت.",
            parse_mode="HTML",
        )
        return

    if action == "backup_dest":
        raw = text.strip()
        if not _is_signed_int(raw):
            context.user_data[AWAITING_ADMIN_KEY] = awaiting
            await message.reply_text(
                "⚠️ یه chat id معتبر بفرست (مثلاً <code>-1001234567890</code>) یا <code>0</code> برای پیوی مالک.",
                parse_mode="HTML",
            )
            return
        chat_id = int(raw)
        await run_db(botconfig.set_backup_chat_id, None if chat_id == 0 else chat_id)
        if chat_id == 0:
            await message.reply_text("✅ مقصد بکاپ شد پیوی مالک.")
        else:
            await message.reply_text(
                f"✅ مقصد بکاپ شد <code>{chat_id}</code>. مطمئن شو بات اونجا می‌تونه فایل بفرسته.",
                parse_mode="HTML",
            )
        return

    if action == "ish_title":
        draft = _ish_draft(context)
        emoji = "🎁"
        t = text.strip()
        first = t.split()[0] if t.split() else ""
        if first and any(ord(ch) > 0x2600 for ch in first) and len(first) <= 4:
            emoji, t = first, (t[len(first):].strip() or t)
        draft["title"] = t[:64]
        draft["emoji"] = emoji
        await _ish_show_home(update, context, edit=False)
        return

    if action == "ish_cname":
        pending = context.user_data.pop("ish_pending", None)
        if pending is not None:
            pending["name"] = text.strip()[:48]
            _ish_draft(context)["contents"].append(pending)
        await _ish_show_home(update, context, edit=False)
        return

    if action == "ish_limit":
        digits = text.strip().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
        if not digits.isdigit():
            context.user_data[AWAITING_ADMIN_KEY] = awaiting
            await message.reply_text("⚠️ یه عدد بفرست (۰ = نامحدود).")
            return
        _ish_draft(context)["max_per_user"] = int(digits)
        await _ish_show_home(update, context, edit=False)
        return

    if action == "ish_price":
        from game import itemshop

        try:
            coins, diamonds = itemshop.parse_price_line(text)
        except GameError as exc:
            context.user_data[AWAITING_ADMIN_KEY] = awaiting
            await message.reply_text(f"⚠️ {exc}")
            return
        draft = _ish_draft(context)
        draft["price_coins"], draft["price_diamonds"] = coins, diamonds
        await _ish_show_home(update, context, edit=False)
        return

    if action == "ish_amount":
        digits = text.strip().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
        if not digits.isdigit() or int(digits) <= 0:
            context.user_data[AWAITING_ADMIN_KEY] = awaiting
            await message.reply_text("⚠️ یه عدد مثبت بفرست.")
            return
        _ish_draft(context)["contents"].append({"type": awaiting["kind"], "amount": int(digits)})
        await _ish_show_home(update, context, edit=False)
        return

    if action == "shop_item":
        from game import itemshop

        def _create():
            spec = itemshop.parse_spec(text)
            return itemshop.create_item(spec), spec

        try:
            item, spec = await run_db(_create)
        except GameError as exc:
            context.user_data[AWAITING_ADMIN_KEY] = awaiting
            await message.reply_text(f"⚠️ {exc}\n\nدوباره با قالب درست بفرست:")
            return
        await message.reply_text(
            f"✅ <b>آیتم ساخته شد:</b> {item.emoji} {item.title}\n"
            f"قیمت: {itemshop.price_text(item)}\n"
            f"محتوا: {itemshop.content_summary(spec['contents'])}\n\n"
            "حالا توی «🛍 آیتم‌های ویژه»ی فروشگاه برای بازیکن‌ها دیده می‌شه.",
            parse_mode="HTML",
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


async def capture_restore_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Owner sent (hopefully) a backup file after tapping «بازیابی از فایل». Validate
    it, stash it alongside the other backups, and ask for one final confirmation
    before it replaces the whole database."""
    import io

    from game import backup as backup_mod

    message = update.effective_message
    if not _is_owner(update):
        context.user_data.pop(AWAITING_RESTORE_KEY, None)
        return
    document = message.document
    if document is None:
        context.user_data.pop(AWAITING_RESTORE_KEY, None)
        await message.reply_text("❌ بازیابی لغو شد (فایلی نفرستادی).")
        return
    if document.file_size and document.file_size > 60 * 1024 * 1024:
        context.user_data.pop(AWAITING_RESTORE_KEY, None)
        await message.reply_text("⚠️ فایل خیلی بزرگه (بیشتر از ۶۰ مگابایت).")
        return

    context.user_data.pop(AWAITING_RESTORE_KEY, None)
    try:
        tg_file = await context.bot.get_file(document.file_id)
        raw = await tg_file.download_as_bytearray()
    except TelegramError:
        await message.reply_text("⚠️ نشد فایل رو دانلود کنم. دوباره امتحان کن.")
        return

    def _validate_and_store():
        meta = backup_mod.store_upload(io.BytesIO(bytes(raw)), "restore-upload")
        payload = backup_mod.read_payload(meta["path"])
        return meta, (payload.get("manifest") or {})

    try:
        meta, manifest = await run_db(_validate_and_store)
    except backup_mod.BackupError as exc:
        await message.reply_text(f"⚠️ {exc}")
        return

    created = manifest.get("created_at", "—")
    count = manifest.get("object_count", "—")
    await message.reply_text(
        "♻️ <b>تأیید بازیابی</b>\n\n"
        f"فایل: <code>{meta['name']}</code>\n"
        f"ساخته‌شده: <code>{created}</code>\n"
        f"تعداد رکورد: <b>{count}</b>\n\n"
        "<b>⚠️ این کل دیتای فعلی بازی رو جایگزین می‌کنه و برگشت‌پذیر نیست.</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [btn("♻️ بله، بازیابی کن", style=DANGER, callback_data=f"autobk_restore_do:{meta['name']}")],
            [btn("انصراف", style=NAV, callback_data="admin_menu:autobackup")],
        ]),
    )


async def autobackup_restore_do_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_owner(update):
        await query.answer("فقط مالک.", show_alert=True)
        return
    name = query.data.split(":", 1)[1]
    await query.answer("در حال بازیابی…")

    def _do_restore():
        from game import admins as admins_mod
        from game import backup as backup_mod
        from game import botconfig as botconfig_mod

        result = backup_mod.restore_from_file(name)
        # the caches the bot serves from are now stale (they were rebuilt from the
        # OLD rows); refresh the ones restore_payload doesn't already handle
        botconfig_mod.refresh_cache()
        admins_mod.refresh_cache()
        return result

    try:
        result = await run_db(_do_restore)
    except Exception as exc:  # noqa: BLE001 — surface any restore failure to the owner
        await query.message.reply_text(f"⚠️ بازیابی ناموفق بود: {exc}")
        return
    await safe_edit_message_text(
        query,
        f"✅ <b>بازیابی انجام شد.</b>\n{result.get('object_count', 0)} رکورد بازگردانده شد.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[back_btn("admin_menu:autobackup", "بازگشت")]]),
    )


async def capture_owner_text_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Single dispatcher for every 'awaiting a plain-text/forwarded reply' flow in
    the owner panel — PTB only ever runs the first handler that matches an update
    within a group, so every such flow has to live behind one registration."""
    if context.user_data.get(AWAITING_RESTORE_KEY):
        await capture_restore_upload(update, context)
        return
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
        "group_link": group_link_panel,
        "group_link_set": group_link_set_start,
        "group_link_clear": group_link_clear,
        "users": users_browse_callback,
        "gift_all": gift_all_start,
        "global_stats": global_stats_cmd,
        "admin_manage": admin_manage_panel,
        "admin_add": admin_add_start,
        "autobackup": autobackup_panel,
        "cheat": cheat_panel,
        "itemshop": itemshop_manage_panel,
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
    application.add_handler(CommandHandler("reset_user", reset_user_cmd, private_only))
    application.add_handler(CommandHandler("player_log", player_log_cmd, private_only))
    application.add_handler(CommandHandler("preview_emoji", preview_emoji_cmd, private_only))
    application.add_handler(CallbackQueryHandler(users_browse_callback, pattern=r"^admin_users:\d+$"))
    application.add_handler(CallbackQueryHandler(user_open_callback, pattern=r"^admin_uinfo:\d+$"))
    application.add_handler(CallbackQueryHandler(dm_user_start, pattern=r"^admin_dm:\d+$"))
    application.add_handler(CallbackQueryHandler(admin_remove_callback, pattern=r"^admin_rm:\d+$"))
    application.add_handler(CallbackQueryHandler(itemshop_add_start, pattern=r"^sitem_add$"))
    application.add_handler(CallbackQueryHandler(itemshop_builder_callback, pattern=r"^ish:"))
    application.add_handler(CallbackQueryHandler(itemshop_toggle_callback, pattern=r"^sitem_toggle:\d+$"))
    application.add_handler(CallbackQueryHandler(itemshop_delete_callback, pattern=r"^sitem_del:\d+$"))
    application.add_handler(CallbackQueryHandler(autobackup_set_callback, pattern=r"^autobk_set:\d+$"))
    application.add_handler(CallbackQueryHandler(autobackup_custom_start, pattern=r"^autobk_custom$"))
    application.add_handler(CallbackQueryHandler(autobackup_dest_here_callback, pattern=r"^autobk_dest_here$"))
    application.add_handler(CallbackQueryHandler(autobackup_dest_set_start, pattern=r"^autobk_dest_set$"))
    application.add_handler(CallbackQueryHandler(autobackup_now_callback, pattern=r"^autobk_now$"))
    application.add_handler(CallbackQueryHandler(autobackup_restore_start, pattern=r"^autobk_restore$"))
    application.add_handler(
        CallbackQueryHandler(autobackup_restore_do_callback, pattern=r"^autobk_restore_do:")
    )
    application.add_handler(CallbackQueryHandler(player_log_callback, pattern=r"^admin_plog:"))
    application.add_handler(CallbackQueryHandler(delete_creature_confirm_callback, pattern=r"^admin_del"))
    application.add_handler(CallbackQueryHandler(reset_user_start_callback, pattern=r"^admin_reset:"))
    application.add_handler(
        CallbackQueryHandler(reset_user_confirm_callback, pattern=r"^admin_reset_(do|cancel):")
    )
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
    application.add_handler(CallbackQueryHandler(admin_lablevel_callback, pattern=r"^admin_lablevel:"))
    application.add_handler(CallbackQueryHandler(admin_unban_callback, pattern=r"^admin_unban:"))
    application.add_handler(CallbackQueryHandler(admin_ban_callback, pattern=r"^admin_ban:"))

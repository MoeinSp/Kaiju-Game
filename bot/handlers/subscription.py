"""«⭐ اشتراک‌های ویژه» — VIP Subscriptions management and purchase.

Two tiers:
- Silver (۲۵۰,۰۰۰ تومان - ۳۰ روزه)
- Gold (۵۰۰,۰۰۰ تومان - ۳۰ روزه)
"""

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

from bio_lab.repository import get_or_create_user
from bot.buttons import CONFIRM, NAV, PRIMARY, SHOP, back_btn, btn
from bot.utils import run_db, safe_edit_message_text, send_screen
from game import botconfig, purchase
from game.creature import GameError
from game.emoji import get_emoji
from game.subscription import SUBSCRIPTION_TIERS, get_subscription_info


def _sub_panel_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    return user, get_subscription_info(user)


def _render_subscription_text(info: dict) -> str:
    lines = [
        f"{get_emoji('sub_vip')} <b>اشتراک‌های ویژه کایجو</b>",
        "━━━━━━━━━━━━━━━━━━━━",
    ]

    if info["is_active"]:
        tier_emoji = get_emoji(f"sub_{info['tier']}", info['badge'])
        lines += [
            f"✨ اشتراک فعال شما: {tier_emoji} <b>{info['tier_name']}</b>",
            f"⏳ زمان باقی‌مانده: <b>{info['days_left']} روز و {info['hours_left']} ساعت</b>",
            "",
            "<b>مزایای فعال شما:</b>",
        ]
        cfg = SUBSCRIPTION_TIERS.get(info["tier"])
        if cfg:
            for p in cfg["perks"]:
                lines.append(f"  • {p}")
        lines.append("")
        lines.append("<i>با خرید مجدد، ۳۰ روز به مدت اشتراک فعلی شما افزوده می‌شود.</i>")
    else:
        lines += [
            "وضعیت اشتراک: <b>عادی (بدون اشتراک فعال)</b>",
            "",
            "با تهیه اشتراک ویژه، قابلیت‌های استثنایی و سرعت رشد چند برابری به دست آورید!",
        ]

    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        f"{get_emoji('sub_silver')} <b>اشتراک نقره‌ای (۳۰ روزه):</b>",
        f"  • {get_emoji('sub_silver')} نشان اختصاصی پرمیوم نقره‌ای در کنار نام شما",
        f"  • {get_emoji('energy')} افزایش سقف انرژی به ۱۰۰ (به جای ۵۰)",
        f"  • 📋 امکان در صف گذاشتن یک جعبه آرنا (بازگشایی خودکار)",
        f"  • {get_emoji('hunt')} افزایش ۲۵ درصدی جوایز و درآمد شکار خودکار",
        f"  {get_emoji('coin')} قیمت: <b>۲۵۰,۰۰۰ تومان</b>",
        "",
        f"{get_emoji('sub_gold')} <b>اشتراک طلایی (۳۰ روزه):</b>",
        f"  • {get_emoji('sub_gold')} نشان اختصاصی پرمیوم طلایی در کنار نام شما",
        f"  • {get_emoji('energy')} افزایش سقف انرژی به ۱۰۰ (به جای ۵۰)",
        f"  • 🕳 افزایش ظرفیت همزمانی غار هیولا به ۲ جفت همزمان",
        f"  • 📋 امکان در صف گذاشتن یک جعبه آرنا (بازگشایی خودکار)",
        f"  • {get_emoji('hunt')} افزایش ۵۰ درصدی جوایز و درآمد شکار خودکار",
        f"  {get_emoji('coin')} قیمت: <b>۵۰۰,۰۰۰ تومان</b>",
        "━━━━━━━━━━━━━━━━━━━━",
    ]
    return "\n".join(lines)


def _render_subscription_keyboard(info: dict) -> InlineKeyboardMarkup:
    rows = [
        [btn("🥈 خرید اشتراک نقره‌ای (۲۵۰ هزار تومان)", emoji_key="btn_sub_silver", style=PRIMARY, callback_data="sub_pick:silver")],
        [btn("👑 خرید اشتراک طلایی (۵۰۰ هزار تومان)", emoji_key="btn_sub_gold", style=CONFIRM, callback_data="sub_pick:gold")],
        [back_btn("menu:cat_shop", "بازگشت به فروشگاه")],
    ]
    return InlineKeyboardMarkup(rows)


async def subscription_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user, info = await run_db(_sub_panel_sync, update.effective_user)
    text = _render_subscription_text(info)
    keyboard = _render_subscription_keyboard(info)

    from game.media import get_feature_image_path
    photo = get_feature_image_path("subscription")
    await send_screen(update, text, photo=photo, parse_mode="HTML", reply_markup=keyboard)


def _create_sub_req_sync(tg_user, tier: str):
    user, _ = get_or_create_user(tg_user)
    return purchase.create_subscription_pending(user, tier)


async def sub_pick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    tier = query.data.split(":")[1]

    if not botconfig.inbot_purchase_ready():
        await query.answer("سیستم پرداخت موقتاً در دسترس نیست.", show_alert=True)
        return

    try:
        req = await run_db(_create_sub_req_sync, update.effective_user, tier)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return

    context.user_data["buy_awaiting_receipt_req"] = req.id
    card_number, holder = botconfig.get_buy_card()
    await query.answer()

    sub_cfg = SUBSCRIPTION_TIERS.get(tier, {})
    lines = [
        f"🧾 <b>پرداخت و فعال‌سازی {get_emoji('sub_vip')} اشتراک ویژه</b>",
        "",
        f"⭐ سطح انتخابی: <b>{sub_cfg.get('name', tier)}</b> (۳۰ روزه)",
        f"{get_emoji('coin')} مبلغ قابل پرداخت: <b>{req.price_toman:,} تومان</b>",
        "",
        "💳 <b>مبلغ رو به این کارت واریز کن:</b>",
        f"<code>{card_number}</code>",
    ]
    if holder:
        lines.append(f"به نام: <b>{holder}</b>")
    lines += [
        "",
        "📸 بعد از واریز، <b>عکس رسید</b> رو همین‌جا بفرست تا بلافاصله بررسی و فعال بشه.",
        "<i>به‌محض تأیید رسید توسط پشتیبانی، اشتراک به مدت ۳۰ روز روی اکانتت اعمال می‌شه.</i>",
    ]
    kb = InlineKeyboardMarkup([[btn("انصراف", emoji_key="btn_cancel", style=NAV, callback_data="menu:subscription")]])
    await safe_edit_message_text(query, "\n".join(lines), parse_mode="HTML", reply_markup=kb)


def register(application) -> None:
    application.add_handler(CommandHandler("subscription", subscription_panel, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("vip", subscription_panel, filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(sub_pick_callback, pattern=r"^sub_pick:(silver|gold)$"))

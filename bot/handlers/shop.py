"""«🛒 شاپ» — the rotating daily shop."""

import json

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

from bio_lab.repository import get_or_create_user
from bot.buttons import BUILD, SHOP, back_btn, btn
from bot.utils import run_db, safe_edit_message_text, send_screen
from game import constants, shop
from game.creature import GameError
from game.emoji import get_emoji


def _panel_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    return shop.today_offers(), user.coins, user.diamonds


def _render(offers, coins, diamonds) -> tuple[str, InlineKeyboardMarkup]:
    lines = [
        "🛒 <b>شاپ روزانه</b>",
        f"<blockquote>هر روز آفرهای تازه. {get_emoji('coin')} {coins} طلا · {get_emoji('diamond')} {diamonds} الماس</blockquote>",
    ]
    rows = []
    for o in offers:
        cur = "💎" if o["currency"] == "diamonds" else "طلا"
        star = "⭐ " if o["featured"] else ""
        disc = "  🔻تخفیف امروز" if o["featured"] else ""
        lines.append(f"{star}{o['emoji']} <b>{o['title']}</b> — {o['price']} {cur}{disc}")
        rows.append([btn(f"{o['emoji']} خرید {o['title']} ({o['price']} {cur})", style=BUILD if o['featured'] else SHOP, callback_data=f"shop_buy:{o['key']}")])
    rows.append([back_btn("menu:cat_shop", "بازگشت به فروشگاه")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def shop_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    offers, coins, diamonds = await run_db(_panel_sync, update.effective_user)
    text, keyboard = _render(offers, coins, diamonds)
    await send_screen(update, text, parse_mode="HTML", reply_markup=keyboard)


def _buy_sync(tg_user, key):
    user, _ = get_or_create_user(tg_user)
    offer = shop.buy(user, key)
    return offer, shop.today_offers(), user.coins, user.diamonds


async def shop_buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    key = query.data.split(":")[1]
    try:
        offer, offers, coins, diamonds = await run_db(_buy_sync, update.effective_user, key)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer(f"✅ خریدی: {shop.offer_reward_text(offer)}")
    text, keyboard = _render(offers, coins, diamonds)
    await safe_edit_message_text(
        query,
        f"✅ <b>خرید موفق:</b> {offer['emoji']} {offer['title']}\n\n━━━━━━━━━━\n" + text,
        parse_mode="HTML",
        reply_markup=keyboard,
    )


# ── 🛡 shield shop (diamonds) ─────────────────────────────────────────────────
def _shield_state_sync(tg_user):
    from game.arena import shield_remaining_seconds

    user, _ = get_or_create_user(tg_user)
    return user.diamonds, shield_remaining_seconds(user)


def _fmt_hours(seconds: int) -> str:
    hours, rem = divmod(max(0, seconds), 3600)
    minutes = rem // 60
    if hours and minutes:
        return f"{hours} ساعت و {minutes} دقیقه"
    if hours:
        return f"{hours} ساعت"
    return f"{minutes} دقیقه"


def _shield_render(diamonds: int, shield_secs: int) -> tuple[str, InlineKeyboardMarkup]:
    sh = get_emoji("shield")
    status = f"{sh} سپر فعلی: <b>{_fmt_hours(shield_secs)}</b>" if shield_secs > 0 else f"{sh} الان سپر نداری"
    lines = [
        f"{sh} <b>خرید سپر محافظ</b>",
        f"<blockquote>{status}\n"
        f"موجودی: {get_emoji('diamond')} <b>{diamonds}</b> الماس\n\n"
        f"تا وقتی سپر داری کسی نمی‌تونه توی آرنا غارتت کنه. هر حمله‌ای که <b>خودت</b> بزنی "
        f"{constants.SHIELD_ATTACK_COST_HOURS} ساعت از سپرت کم می‌کنه. خریدها روی هم جمع می‌شن.</blockquote>",
    ]
    rows = []
    for tier, cfg in constants.SHIELD_SHOP_TIERS.items():
        # button labels are plain text — never get_emoji() here (it returns <tg-emoji> HTML)
        rows.append([btn(
            f"{cfg['label']} — {cfg['diamonds']} 💎",
            style=SHOP, callback_data=f"shield_buy:{tier}",
        )])
    rows.append([back_btn("menu:cat_shop", "بازگشت به فروشگاه")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def shield_shop_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    diamonds, shield_secs = await run_db(_shield_state_sync, update.effective_user)
    text, keyboard = _shield_render(diamonds, shield_secs)
    await send_screen(update, text, parse_mode="HTML", reply_markup=keyboard)


def _shield_buy_sync(tg_user, tier):
    from game.arena import buy_shield, shield_remaining_seconds

    user, _ = get_or_create_user(tg_user)
    result = buy_shield(user, tier)
    user.refresh_from_db()  # buy_shield charged via a locked re-fetch; outer instance is stale
    return result, user.diamonds, shield_remaining_seconds(user)


async def shield_buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    tier = query.data.split(":")[1]
    try:
        result, diamonds, shield_secs = await run_db(_shield_buy_sync, update.effective_user, tier)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer("🛡 سپر فعال شد!")
    text, keyboard = _shield_render(diamonds, shield_secs)
    await safe_edit_message_text(
        query,
        f"✅ <b>سپر خریداری شد!</b> الان <b>{_fmt_hours(shield_secs)}</b> محافظت داری.\n\n━━━━━━━━━━\n" + text,
        parse_mode="HTML",
        reply_markup=keyboard,
    )


# ── 🛍 owner-authored item shop / packs ───────────────────────────────────────
def _item_shop_sync(tg_user):
    from game import itemshop

    user, _ = get_or_create_user(tg_user)
    return itemshop.list_items(active_only=True), user.coins, user.diamonds


def _item_shop_render(items, coins, diamonds) -> tuple[str, InlineKeyboardMarkup]:
    from game import itemshop

    lines = [
        f"{get_emoji('shop_item')} <b>آیتم‌های ویژه</b>",
        f"<blockquote>{get_emoji('coin')} {coins:,} طلا · {get_emoji('diamond')} {diamonds} الماس</blockquote>",
    ]
    rows = []
    if not items:
        lines.append("\n<i>الان آیتم ویژه‌ای موجود نیست. بعداً سر بزن.</i>")
    for it in items:
        contents = json.loads(it.contents_json)
        lines.append(
            f"\n{it.emoji} <b>{it.title}</b> — {itemshop.price_text(it)}\n"
            f"   <i>{itemshop.content_summary(contents)}</i>"
            + (f"\n   {it.description}" if it.description else "")
        )
        rows.append([btn(f"{it.emoji} خرید {it.title} ({itemshop.price_text(it)})",
                         style=SHOP, callback_data=f"sitem_buy:{it.id}")])
    rows.append([back_btn("menu:cat_shop", "بازگشت به فروشگاه")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def item_shop_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    items, coins, diamonds = await run_db(_item_shop_sync, update.effective_user)
    text, keyboard = _item_shop_render(items, coins, diamonds)
    await send_screen(update, text, parse_mode="HTML", reply_markup=keyboard)


def _item_buy_sync(tg_user, item_id):
    from game import itemshop

    user, _ = get_or_create_user(tg_user)
    result = itemshop.buy(user, item_id)
    return result, itemshop.list_items(active_only=True)


async def item_buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    item_id = int(query.data.split(":")[1])
    try:
        result, items = await run_db(_item_buy_sync, update.effective_user, item_id)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer("✅ خریداری شد!")
    text, keyboard = _item_shop_render(items, result["coins"], result["diamonds"])
    got = "، ".join(result["notes"])
    await safe_edit_message_text(
        query,
        f"✅ <b>خرید موفق: {result['emoji']} {result['title']}</b>\n🎁 گرفتی: {got}\n\n━━━━━━━━━━\n" + text,
        parse_mode="HTML",
        reply_markup=keyboard,
    )


# ── 🛡 group-shield shop (cheaper, protects from group «اتک») ─────────────────
def _gshield_state_sync(tg_user):
    from game.arena import group_shield_remaining_seconds

    user, _ = get_or_create_user(tg_user)
    return user.diamonds, group_shield_remaining_seconds(user)


def _gshield_render(diamonds: int, shield_secs: int) -> tuple[str, InlineKeyboardMarkup]:
    status = f"🛡 سپر گروه فعلی: <b>{_fmt_hours(shield_secs)}</b>" if shield_secs > 0 else "🛡 الان سپر گروه نداری"
    lines = [
        "🛡 <b>خرید سپر گروه</b>",
        f"<blockquote>{status}\n"
        f"موجودی: {get_emoji('diamond')} <b>{diamonds}</b> الماس\n\n"
        "تا وقتی سپر گروه داری، کسی نمی‌تونه توی گروه با «اتک» بهت حمله کنه. "
        "از سپر آرنا جداست و ارزون‌تره.</blockquote>",
    ]
    rows = []
    for tier, cfg in constants.GROUP_SHIELD_SHOP_TIERS.items():
        rows.append([btn(f"{cfg['label']} — {cfg['diamonds']} 💎",
                         style=SHOP, callback_data=f"gshield_buy:{tier}")])
    rows.append([back_btn("menu:arena", "بازگشت به آرنا")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def group_shield_shop_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    diamonds, shield_secs = await run_db(_gshield_state_sync, update.effective_user)
    text, keyboard = _gshield_render(diamonds, shield_secs)
    await send_screen(update, text, parse_mode="HTML", reply_markup=keyboard)


def _gshield_buy_sync(tg_user, tier):
    from game.arena import buy_group_shield, group_shield_remaining_seconds

    user, _ = get_or_create_user(tg_user)
    result = buy_group_shield(user, tier)
    user.refresh_from_db()
    return result, user.diamonds, group_shield_remaining_seconds(user)


async def group_shield_buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    tier = query.data.split(":")[1]
    try:
        result, diamonds, shield_secs = await run_db(_gshield_buy_sync, update.effective_user, tier)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer("🛡 سپر گروه فعال شد!")
    text, keyboard = _gshield_render(diamonds, shield_secs)
    await safe_edit_message_text(
        query,
        f"✅ <b>سپر گروه خریداری شد!</b> الان <b>{_fmt_hours(shield_secs)}</b> محافظت داری.\n\n━━━━━━━━━━\n" + text,
        parse_mode="HTML", reply_markup=keyboard,
    )


def register(application) -> None:
    application.add_handler(CommandHandler("shop", shop_panel, filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(shop_buy_callback, pattern=r"^shop_buy:"))
    application.add_handler(CommandHandler("shield", shield_shop_panel, filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(shield_buy_callback, pattern=r"^shield_buy:"))
    application.add_handler(CallbackQueryHandler(group_shield_shop_panel, pattern=r"^gshield_shop$"))
    application.add_handler(CallbackQueryHandler(group_shield_buy_callback, pattern=r"^gshield_buy:"))
    application.add_handler(CommandHandler("items", item_shop_panel, filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(item_buy_callback, pattern=r"^sitem_buy:\d+$"))

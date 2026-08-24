"""«🛒 شاپ» — the rotating daily shop."""

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

from bio_lab.repository import get_or_create_user
from bot.buttons import BUILD, CONFIRM, SHOP, back_btn, btn
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
    status = f"🛡 سپر فعلی: <b>{_fmt_hours(shield_secs)}</b>" if shield_secs > 0 else "🛡 الان سپر نداری"
    lines = [
        "🛡 <b>خرید سپر محافظ</b>",
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


def register(application) -> None:
    application.add_handler(CommandHandler("shop", shop_panel, filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(shop_buy_callback, pattern=r"^shop_buy:"))
    application.add_handler(CommandHandler("shield", shield_shop_panel, filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(shield_buy_callback, pattern=r"^shield_buy:"))

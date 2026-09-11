"""«🛒 شاپ» — the rotating daily shop."""

import json

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

from bio_lab.repository import get_or_create_user
from bot.buttons import BUILD, NAV, SHOP, back_btn, btn
from bot.utils import run_db, safe_edit_message_text, send_screen
from game import constants, shop
from game.creature import GameError
from game.emoji import get_emoji


def _panel_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    return shop.offers_with_remaining(user), user.coins, user.diamonds


def _render(offers, coins, diamonds) -> tuple[str, InlineKeyboardMarkup]:
    lines = [
        "🛒 <b>فروشگاه روزانه</b> — هر روز آفرهای تازه و محدود",
        "",
        "💰 <b>موجودی شما:</b>",
        f"{get_emoji('coin')} طلا: <b>{coins:,}</b>",
        f"{get_emoji('diamond')} الماس: <b>{diamonds:,}</b>",
    ]
    rows: list = []

    def _section(header: str, group: list) -> None:
        if not group:
            return
        lines.append("")
        lines.append(header)
        for o in group:
            cur = "💎" if o["currency"] == "diamonds" else "طلا"
            star = "⭐ " if o["featured"] else ""
            disc = " 🔻تخفیف امروز" if o["featured"] else ""
            rem = o.get("remaining")
            lim_txt = f"  <i>({rem} عدد مانده امروز)</i>" if rem is not None else ""
            lines.append(f"{star}{o['emoji']} <b>{o['title']}</b>{disc}")
            # ┘ (up-and-LEFT corner) reads as a proper sub-branch in RTL, unlike └
            lines.append(f"┘ قیمت: <b>{o['price']:,}</b> {cur}{lim_txt}")
            sold_out = rem == 0
            label = (f"⛔ سقف امروز پر شد — {o['title']}" if sold_out
                     else f"{o['emoji']} خرید {o['title']} ({o['price']:,} {cur})")
            rows.append([btn(
                label, style=BUILD if o["featured"] else SHOP, callback_data=f"shop_buy:{o['key']}",
            )])

    _section("💎 <b>خرید با الماس:</b>", [o for o in offers if o["currency"] == "diamonds"])
    _section("🪙 <b>خرید با طلا:</b>", [o for o in offers if o["currency"] != "diamonds"])
    if not offers:
        lines.append("\n<i>الان آفری موجود نیست. بعداً سر بزن.</i>")
    rows.append([back_btn("menu:cat_shop", "بازگشت به فروشگاه")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


_SHOWN_OFFERS_KEY = "shop_shown_offers"


def _remember_offers(context, offers) -> None:
    """Remember exactly what prices we showed this player, so a purchase can be charged
    at the shown price and never more (see shop.buy)."""
    context.user_data[_SHOWN_OFFERS_KEY] = {
        o["key"]: {"price": o["price"], "currency": o["currency"]} for o in offers
    }


async def shop_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    offers, coins, diamonds = await run_db(_panel_sync, update.effective_user)
    _remember_offers(context, offers)
    text, keyboard = _render(offers, coins, diamonds)
    await send_screen(update, text, parse_mode="HTML", reply_markup=keyboard)


def _buy_sync(tg_user, key, shown_price, shown_currency, count=1):
    user, _ = get_or_create_user(tg_user)
    offer = shop.buy(user, key, count=count, shown_price=shown_price, shown_currency=shown_currency)
    user.refresh_from_db()  # buy() charges via a locked re-fetch; outer instance is stale
    return offer, shop.offers_with_remaining(user), user.coins, user.diamonds


def is_quantity_offer(offer_or_key) -> bool:
    key = offer_or_key if isinstance(offer_or_key, str) else offer_or_key.get("key", "")
    return key in ("cap_small", "cap_medium", "cap_large")


def _render_qty_picker(offer: dict, coins: int, diamonds: int) -> tuple[str, InlineKeyboardMarkup]:
    key = offer["key"]
    title = offer["title"]
    emoji = offer["emoji"]
    cur = "💎" if offer["currency"] == "diamonds" else "طلا"
    price = offer["price"]
    user_bal = diamonds if offer["currency"] == "diamonds" else coins
    max_afford = user_bal // max(1, price)
    rem = offer.get("remaining")
    if rem is not None:
        max_qty = min(max_afford, rem)
    else:
        max_qty = max_afford

    lines = [
        f"{emoji} <b>خرید {title}</b>\n",
        f"💰 قیمت هر عدد: <b>{price:,}</b> {cur}",
        f"👛 موجودی شما: <b>{user_bal:,}</b> {cur}",
        f"📊 حداکثر قابل خرید با موجودی فعلی: <b>{max_qty:,}</b> عدد" + (f" <i>(سقف امروز: {rem})</i>" if rem is not None else ""),
        "",
        "چند عدد می‌خوای بخری؟",
    ]

    presets = [1, 5, 10, 25, 50, 100]
    valid_presets = [p for p in presets if p <= max_qty]
    if not valid_presets:
        valid_presets = [1]

    rows = []
    btn_row = []
    for p in valid_presets:
        btn_row.append(btn(f"{p} عدد", style=SHOP, callback_data=f"shop_do_buy:{key}:{p}"))
        if len(btn_row) == 3:
            rows.append(btn_row)
            btn_row = []
    if btn_row:
        rows.append(btn_row)

    action_row = []
    if max_qty > 1 and max_qty not in valid_presets:
        action_row.append(btn(f"🛒 خرید حداکثر ({max_qty:,} عدد)", style=BUILD, callback_data=f"shop_do_buy:{key}:{max_qty}"))
    action_row.append(btn("🔢 تعداد دلخواه", emoji_key="btn_custom_amt", style=NAV, callback_data=f"shop_custom_qty:{key}"))
    rows.append(action_row)
    rows.append([back_btn("menu:shop", "بازگشت به فروشگاه")])

    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def shop_buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    key = query.data.split(":")[1]
    if is_quantity_offer(key):
        offers, coins, diamonds = await run_db(_panel_sync, update.effective_user)
        _remember_offers(context, offers)
        target_offer = next((o for o in offers if o["key"] == key), None)
        if target_offer is None:
            await query.answer("این آفر در دسترس نیست.", show_alert=True)
            return
        await query.answer()
        text, keyboard = _render_qty_picker(target_offer, coins, diamonds)
        await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)
        return

    shown = context.user_data.get(_SHOWN_OFFERS_KEY, {}).get(key, {})
    try:
        offer, offers, coins, diamonds = await run_db(
            _buy_sync, update.effective_user, key, shown.get("price"), shown.get("currency"), count=1
        )
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    _remember_offers(context, offers)
    await query.answer(f"✅ خریدی: {shop.offer_reward_text(offer)}")
    text, keyboard = _render(offers, coins, diamonds)
    await safe_edit_message_text(
        query,
        f"✅ <b>خرید موفق:</b> {offer['emoji']} {offer['title']}\n\n━━━━━━━━━━\n" + text,
        parse_mode="HTML",
        reply_markup=keyboard,
    )


async def shop_do_buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, key, raw_count = query.data.split(":")
    count = int(raw_count)
    shown = context.user_data.get(_SHOWN_OFFERS_KEY, {}).get(key, {})
    try:
        offer, offers, coins, diamonds = await run_db(
            _buy_sync, update.effective_user, key, shown.get("price"), shown.get("currency"), count=count
        )
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    _remember_offers(context, offers)
    await query.answer(f"✅ خریدی: {shop.offer_reward_text(offer)}")
    text, keyboard = _render(offers, coins, diamonds)
    tot_price = offer.get("total_price", offer["price"] * count)
    cur = "💎" if offer["currency"] == "diamonds" else "طلا"
    await safe_edit_message_text(
        query,
        f"✅ <b>خرید موفق ({count:,} عدد):</b> {offer['emoji']} {offer['title']}\n"
        f"💳 کل مبلغ پرداختی: <b>{tot_price:,}</b> {cur}\n\n━━━━━━━━━━\n" + text,
        parse_mode="HTML",
        reply_markup=keyboard,
    )


async def shop_custom_qty_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    key = query.data.split(":")[1]
    offers, coins, diamonds = await run_db(_panel_sync, update.effective_user)
    _remember_offers(context, offers)
    target_offer = next((o for o in offers if o["key"] == key), None)
    if target_offer is None:
        await query.answer("این آفر در دسترس نیست.", show_alert=True)
        return
    shown = context.user_data.get(_SHOWN_OFFERS_KEY, {}).get(key, {})
    context.user_data["awaiting_player_input"] = {
        "action": "shop_buy_qty",
        "key": key,
        "shown_price": shown.get("price"),
        "shown_currency": shown.get("currency"),
    }
    await query.answer()
    cur = "💎" if target_offer["currency"] == "diamonds" else "طلا"
    await safe_edit_message_text(
        query,
        f"🔢 <b>خرید تعداد دلخواه {target_offer['emoji']} {target_offer['title']}</b>\n\n"
        f"💰 قیمت هر عدد: <b>{target_offer['price']:,}</b> {cur}\n"
        f"لطفاً <b>تعداد</b> مورد نظرت رو به صورت عدد انگلیسی یا فارسی بفرست (مثلاً 20):",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[back_btn("menu:shop", "انصراف")]]),
    )


async def handle_custom_qty_buy(update: Update, context: ContextTypes.DEFAULT_TYPE, key: str, qty: int, shown_price, shown_currency) -> None:
    message = update.effective_message
    try:
        offer, offers, coins, diamonds = await run_db(
            _buy_sync, update.effective_user, key, shown_price, shown_currency, count=qty
        )
    except GameError as exc:
        await message.reply_text(f"⚠️ {exc}")
        return
    _remember_offers(context, offers)
    text, keyboard = _render(offers, coins, diamonds)
    tot_price = offer.get("total_price", offer["price"] * qty)
    cur = "💎" if offer["currency"] == "diamonds" else "طلا"
    await message.reply_text(
        f"✅ <b>خرید موفق ({qty:,} عدد):</b> {offer['emoji']} {offer['title']}\n"
        f"💳 کل مبلغ پرداختی: <b>{tot_price:,}</b> {cur}\n\n━━━━━━━━━━\n" + text,
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
    rows.append([back_btn("menu:shield_shop", "بازگشت")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def shield_shop_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """The «خرید سپر» entry — first choose which shield to buy (arena vs group)."""
    sh = get_emoji("shield")
    text = (
        f"{sh} <b>خرید سپر</b>\n"
        "<blockquote>🛡 <b>سپر آرنا</b>: جلوی غارت‌شدن توی آرنا رو می‌گیره.\n"
        "🛡 <b>سپر گروه</b>: جلوی «اتک»‌خوردن توی گروه رو می‌گیره (ارزون‌تر).</blockquote>\n"
        "کدوم رو می‌خوای؟"
    )
    rows = InlineKeyboardMarkup([
        [btn("🛡 سپر آرنا", style=SHOP, callback_data="shield_arena")],
        [btn("🛡 سپر گروه", style=SHOP, callback_data="gshield_shop")],
        [back_btn("menu:cat_shop", "بازگشت به فروشگاه")],
    ])
    await send_screen(update, text, parse_mode="HTML", reply_markup=rows)


async def shield_arena_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
    from game import itemshop, gemkaiju

    user, _ = get_or_create_user(tg_user)
    return itemshop.list_items(active_only=True), user.coins, user.diamonds, gemkaiju.gem_offer(user)


def _item_shop_render(items, coins, diamonds, gem=None) -> tuple[str, InlineKeyboardMarkup]:
    from game import itemshop

    lines = [
        f"{get_emoji('shop_item')} <b>آیتم‌های ویژه</b>",
        f"<blockquote>{get_emoji('coin')} {coins:,} طلا · {get_emoji('diamond')} {diamonds} الماس</blockquote>",
    ]
    rows = []
    # 💎 daily gem-kaiju — a same-species/same-rarity twin of one of the player's top kaiju
    if gem:
        label = constants.RARITY_LABELS[gem["rarity"]]
        lines.append(f"\n💎 <b>{gem['name']}</b> {label}")
        if gem["claimed"]:
            lines.append("✅ امروز خریدیش")
        else:
            lines.append(f"🏷 <b>{gem['price']}</b> {get_emoji('diamond')} · <i>روزی یک‌بار قابل خرید</i>")
            rows.append([btn(f"💎 خرید {gem['name']} ({gem['price']} الماس)", style=SHOP, callback_data="gemk_buy")])
    if not items:
        lines.append("\n<i>الان آیتم ویژه‌ی دیگه‌ای موجود نیست. بعداً سر بزن.</i>")
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
    items, coins, diamonds, gem = await run_db(_item_shop_sync, update.effective_user)
    text, keyboard = _item_shop_render(items, coins, diamonds, gem)
    await send_screen(update, text, parse_mode="HTML", reply_markup=keyboard)


def _item_buy_sync(tg_user, item_id):
    from game import itemshop, gemkaiju

    user, _ = get_or_create_user(tg_user)
    result = itemshop.buy(user, item_id)
    return result, itemshop.list_items(active_only=True), gemkaiju.gem_offer(user)


async def item_buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    item_id = int(query.data.split(":")[1])
    try:
        result, items, gem = await run_db(_item_buy_sync, update.effective_user, item_id)
    except GameError as exc:
        if await show_gold_error(query, exc):
            return
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer("✅ خریداری شد!")
    text, keyboard = _item_shop_render(items, result["coins"], result["diamonds"], gem)
    got = "، ".join(result["notes"])
    await safe_edit_message_text(
        query,
        f"✅ <b>خرید موفق: {result['emoji']} {result['title']}</b>\n🎁 گرفتی: {got}\n\n━━━━━━━━━━\n" + text,
        parse_mode="HTML",
        reply_markup=keyboard,
    )


def _gem_buy_sync(tg_user):
    from game import gemkaiju, itemshop

    user, _ = get_or_create_user(tg_user)
    result = gemkaiju.buy_gem_kaiju(user)
    return result, itemshop.list_items(active_only=True), gemkaiju.gem_offer(user)


async def gem_kaiju_buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        result, items, gem = await run_db(_gem_buy_sync, update.effective_user)
    except GameError as exc:
        if await show_gold_error(query, exc):
            return
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer("✅ کایجوی جمی خریداری شد!")
    c = result["creature"]
    text, keyboard = _item_shop_render(items, result["coins"], result["diamonds"], gem)
    await safe_edit_message_text(
        query,
        f"✅ <b>کایجوی جمی خریده شد!</b>\n"
        f"🧬 <b>{c.name}</b> {constants.RARITY_LABELS[c.rarity]} به کلکسیونت اضافه شد "
        f"(−{result['price']} {get_emoji('diamond')}).\n"
        "<i>از «🗂 کلکسیون» می‌تونی فعالش کنی یا برای فیوژن استفاده کنی.</i>\n\n━━━━━━━━━━\n" + text,
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
    rows.append([back_btn("menu:shield_shop", "بازگشت")])
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


# ── 💰 gold exchange (diamonds → gold), always available ──────────────────────
def gold_shop_button():
    """A button that jumps straight to the gold exchange — hung under any
    'not enough gold' message so the player has an immediate way out."""
    return btn("خرید طلا با الماس", emoji_key="btn_gold_shop", style=BUILD, callback_data="gold_shop")


async def show_gold_error(query, exc) -> bool:
    """If `exc` is an InsufficientGoldError, re-render the message with it + a
    «خرید طلا با الماس» button and return True; else return False. Mirrors the
    energy handler's show_energy_error so any diamond/gold spend can offer a way out."""
    from game.creature import InsufficientGoldError

    if isinstance(exc, InsufficientGoldError):
        await query.answer()
        await safe_edit_message_text(
            query, f"{get_emoji('coin')} {exc}", parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[gold_shop_button()]]),
        )
        return True
    return False


def _gold_shop_render(coins: int, diamonds: int) -> tuple[str, InlineKeyboardMarkup]:
    lines = [
        f"{get_emoji('coin')} <b>خرید طلا با الماس</b>",
        f"<blockquote>موجودی: {get_emoji('coin')} <b>{coins:,}</b> طلا · "
        f"{get_emoji('diamond')} <b>{diamonds}</b> الماس\n"
        "بسته‌های بزرگ‌تر کمی به‌صرفه‌ترن.</blockquote>",
    ]
    rows = []
    for i, pack in enumerate(shop.GOLD_PACKS):
        rows.append([btn(
            f"💰 {pack['gold']:,} طلا — {pack['diamonds']} 💎",
            style=SHOP, callback_data=f"gold_buy:{i}",
        )])
    rows.append([back_btn("menu:cat_shop", "بازگشت به فروشگاه")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def _gold_shop_state_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    return user.coins, user.diamonds


async def gold_shop_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    coins, diamonds = await run_db(_gold_shop_state_sync, update.effective_user)
    text, keyboard = _gold_shop_render(coins, diamonds)
    await send_screen(update, text, parse_mode="HTML", reply_markup=keyboard)


def _gold_buy_sync(tg_user, idx):
    user, _ = get_or_create_user(tg_user)
    pack = shop.buy_gold_pack(user, idx)
    return pack, user.coins, user.diamonds


async def gold_buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    idx = int(query.data.split(":")[1])
    try:
        pack, coins, diamonds = await run_db(_gold_buy_sync, update.effective_user, idx)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer(f"✅ {pack['gold']:,} طلا گرفتی!")
    text, keyboard = _gold_shop_render(coins, diamonds)
    await safe_edit_message_text(
        query,
        f"✅ <b>{pack['gold']:,} طلا</b> به موجودیت اضافه شد "
        f"({pack['diamonds']} 💎 کم شد).\n\n━━━━━━━━━━\n" + text,
        parse_mode="HTML", reply_markup=keyboard,
    )


def register(application) -> None:
    application.add_handler(CommandHandler("shop", shop_panel, filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(gold_shop_panel, pattern=r"^gold_shop$"))
    application.add_handler(CallbackQueryHandler(gold_buy_callback, pattern=r"^gold_buy:\d+$"))
    application.add_handler(CallbackQueryHandler(shop_buy_callback, pattern=r"^shop_buy:"))
    application.add_handler(CallbackQueryHandler(shop_do_buy_callback, pattern=r"^shop_do_buy:"))
    application.add_handler(CallbackQueryHandler(shop_custom_qty_callback, pattern=r"^shop_custom_qty:"))
    application.add_handler(CommandHandler("shield", shield_shop_panel, filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(shield_arena_panel, pattern=r"^shield_arena$"))
    application.add_handler(CallbackQueryHandler(shield_buy_callback, pattern=r"^shield_buy:"))
    application.add_handler(CallbackQueryHandler(group_shield_shop_panel, pattern=r"^gshield_shop$"))
    application.add_handler(CallbackQueryHandler(group_shield_buy_callback, pattern=r"^gshield_buy:"))
    application.add_handler(CommandHandler("items", item_shop_panel, filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(item_buy_callback, pattern=r"^sitem_buy:\d+$"))
    application.add_handler(CallbackQueryHandler(gem_kaiju_buy_callback, pattern=r"^gemk_buy$"))

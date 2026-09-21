"""«🛒 شاپ» — the rotating daily shop."""

import json

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

from bio_lab.repository import get_or_create_user
from bot.buttons import BUILD, CONFIRM, DANGER, NAV, PRIMARY, SHOP, back_btn, btn
from bot.utils import run_db, safe_edit_message_text, send_screen
from game import constants, shop
from game.creature import GameError
from game.emoji import get_emoji


def _panel_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    return shop.offers_with_remaining(user), user.coins, user.diamonds


def _render(offers, coins, diamonds, is_group: bool = False) -> tuple[str, InlineKeyboardMarkup]:
    lines = [
        "🛒 <b>فروشگاه روزانه</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "<i>هر روز آفرهای تازه و با تخفیف محدود</i>\n",
        f"💰 موجودی طلا: <code>{coins:,} طلا</code>",
        f"💎 موجودی الماس: <code>{diamonds:,} الماس</code>",
        "━━━━━━━━━━━━━━━━━━━━",
    ]
    rows: list = []

    def _section(header: str, group: list) -> None:
        if not group:
            return
        lines.append("")
        lines.append(header)
        for o in group:
            cur = "الماس" if o["currency"] == "diamonds" else "طلا"
            cur_icon = f"{get_emoji('diamond')}" if o["currency"] == "diamonds" else f"{get_emoji('coin')}"
            star = f"{get_emoji('star')} " if o["featured"] else ""
            disc = "\n🔻 <i>تخفیف ویژه امروز</i>" if o["featured"] else ""
            rem = o.get("remaining")
            lim_txt = f"\n📦 باقیمانده امروز: <code>{rem} عدد</code>" if rem is not None else ""
            lines.append(
                f"<blockquote>{star}{o['emoji']} <b>{o['title']}</b>{disc}\n"
                f"💰 قیمت: <code>{o['price']:,} {cur}</code>{lim_txt}</blockquote>"
            )
            sold_out = rem == 0
            label = (f"⛔ تکمیل سقف — {o['title']}" if sold_out
                     else f"{o['emoji']} {o['title']}")
            rows.append([btn(
                label, style=BUILD if o["featured"] else SHOP, callback_data=f"shop_buy:{o['key']}",
            )])

    _section(f"{get_emoji('diamond')} <b>خرید با الماس:</b>", [o for o in offers if o["currency"] == "diamonds"])
    _section(f"{get_emoji('coin')} <b>خرید با طلا:</b>", [o for o in offers if o["currency"] != "diamonds"])
    if not offers:
        lines.append("\n<i>الان آفری موجود نیست. بعداً سر بزن.</i>")
    if not is_group:
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
    is_group = bool(update.effective_chat and update.effective_chat.type in ("group", "supergroup"))
    offers, coins, diamonds = await run_db(_panel_sync, update.effective_user)
    _remember_offers(context, offers)
    text, keyboard = _render(offers, coins, diamonds, is_group=is_group)
    from game.media import get_feature_image_path
    photo = get_feature_image_path("shop")
    await send_screen(update, text, photo=photo, parse_mode="HTML", reply_markup=keyboard)


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
    cur = "الماس" if offer["currency"] == "diamonds" else "طلا"
    price = offer["price"]
    user_bal = diamonds if offer["currency"] == "diamonds" else coins
    max_afford = user_bal // max(1, price)
    rem = offer.get("remaining")
    if rem is not None:
        max_qty = min(max_afford, rem)
    else:
        max_qty = max_afford

    lines = [
        f"{emoji} <b>خرید {title}</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"💰 قیمت هر عدد: <code>{price:,} {cur}</code>",
        f"👛 موجودی شما: <code>{user_bal:,} {cur}</code>",
        f"📊 حداکثر قابل خرید: <code>{max_qty:,} عدد</code>" + (f" <i>(سقف: <code>{rem}</code>)</i>" if rem is not None else ""),
        "━━━━━━━━━━━━━━━━━━━━",
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
        action_row.append(btn(f"خرید حداکثر ({max_qty:,})", emoji_key="btn_buy", style=BUILD, callback_data=f"shop_do_buy:{key}:{max_qty}"))
    action_row.append(btn("تعداد دلخواه", emoji_key="btn_custom_amt", style=NAV, callback_data=f"shop_custom_qty:{key}"))
    rows.append(action_row)
    rows.append([back_btn("menu:shop", "بازگشت به فروشگاه")])

    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def _render_shop_confirm(query, title: str, emoji: str, price: int, currency: str, count: int, key: str, diamonds: int) -> None:
    cur_label = "الماس" if currency == "diamonds" else "طلا"
    lines = [
        f"{get_emoji('diamond')} <b>تأیید خرید</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"<blockquote>آیتم: {emoji} <b>{title}</b>\n"
        f"تعداد: <code>{count:,} عدد</code>\n"
        f"مبلغ پرداختی: <code>{price:,} {cur_label}</code>\n"
        f"موجودی الماس شما: <code>{diamonds:,} الماس</code></blockquote>",
        "━━━━━━━━━━━━━━━━━━━━",
        "آیا برای انجام این خرید اطمینان داری؟",
    ]
    keyboard = InlineKeyboardMarkup([
        [
            btn("تأیید و خرید", emoji_key="btn_confirm", style=CONFIRM, callback_data=f"shop_confirm_buy:{key}:{count}"),
            back_btn("menu:shop", "انصراف"),
        ],
    ])
    await safe_edit_message_text(query, "\n".join(lines), parse_mode="HTML", reply_markup=keyboard)


async def shop_buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    key = query.data.split(":")[1]
    offers, coins, diamonds = await run_db(_panel_sync, update.effective_user)
    _remember_offers(context, offers)
    target_offer = next((o for o in offers if o["key"] == key), None)
    if target_offer is None:
        await query.answer("این آفر در دسترس نیست.", show_alert=True)
        return

    if is_quantity_offer(key):
        await query.answer()
        text, keyboard = _render_qty_picker(target_offer, coins, diamonds)
        await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)
        return

    shown = context.user_data.get(_SHOWN_OFFERS_KEY, {}).get(key, {})
    currency = shown.get("currency") or target_offer["currency"]
    price = shown.get("price") or target_offer["price"]

    # Diamond purchases MUST ask for confirmation before deducting diamonds
    if currency == "diamonds":
        await query.answer()
        await _render_shop_confirm(query, target_offer["title"], target_offer["emoji"], price, currency, 1, key, diamonds)
        return

    try:
        offer, offers, coins, diamonds = await run_db(
            _buy_sync, update.effective_user, key, price, currency, count=1
        )
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    _remember_offers(context, offers)
    await query.answer(f"✅ خریدی: {shop.offer_reward_text(offer)}")
    text, keyboard = _render(offers, coins, diamonds)
    await safe_edit_message_text(
        query,
        f"✅ <b>خرید موفق:</b> {offer['emoji']} {offer['title']}\n\n━━━━━━━━━━━━━━━━━━━━\n" + text,
        parse_mode="HTML",
        reply_markup=keyboard,
    )


async def shop_do_buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, key, raw_count = query.data.split(":")
    count = int(raw_count)
    offers, coins, diamonds = await run_db(_panel_sync, update.effective_user)
    _remember_offers(context, offers)
    target_offer = next((o for o in offers if o["key"] == key), None)
    if target_offer is None:
        await query.answer("این آفر در دسترس نیست.", show_alert=True)
        return

    shown = context.user_data.get(_SHOWN_OFFERS_KEY, {}).get(key, {})
    currency = shown.get("currency") or target_offer["currency"]
    unit_price = shown.get("price") or target_offer["price"]
    tot_price = unit_price * count

    # Diamond purchases MUST ask for confirmation before deducting diamonds
    if currency == "diamonds":
        await query.answer()
        await _render_shop_confirm(query, target_offer["title"], target_offer["emoji"], tot_price, currency, count, key, diamonds)
        return

    try:
        offer, offers, coins, diamonds = await run_db(
            _buy_sync, update.effective_user, key, unit_price, currency, count=count
        )
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    _remember_offers(context, offers)
    await query.answer(f"✅ خریدی: {shop.offer_reward_text(offer)}")
    text, keyboard = _render(offers, coins, diamonds)
    cur = "الماس" if offer["currency"] == "diamonds" else "طلا"
    await safe_edit_message_text(
        query,
        f"✅ <b>خرید موفق ({count:,} عدد):</b> {offer['emoji']} {offer['title']}\n"
        f"💳 کل مبلغ پرداختی: <code>{tot_price:,} {cur}</code>\n\n━━━━━━━━━━━━━━━━━━━━\n" + text,
        parse_mode="HTML",
        reply_markup=keyboard,
    )


async def shop_confirm_buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Execute confirmed diamond/coin purchase."""
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
    cur = "الماس" if offer["currency"] == "diamonds" else "طلا"
    await safe_edit_message_text(
        query,
        f"✅ <b>خرید موفق ({count:,} عدد):</b> {offer['emoji']} {offer['title']}\n"
        f"💳 کل مبلغ پرداختی: <code>{tot_price:,} {cur}</code>\n\n━━━━━━━━━━━━━━━━━━━━\n" + text,
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
    cur = "الماس" if target_offer["currency"] == "diamonds" else "طلا"
    await safe_edit_message_text(
        query,
        f"🔢 <b>خرید تعداد دلخواه {target_offer['emoji']} {target_offer['title']}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 قیمت هر عدد: <code>{target_offer['price']:,} {cur}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "لطفاً <b>تعداد</b> مورد نظر خود را ارسال کنید:\n"
        "<i>(مثال: <code>20</code>)</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[back_btn("menu:shop", "انصراف")]]),
    )


async def handle_custom_qty_buy(update: Update, context: ContextTypes.DEFAULT_TYPE, key: str, qty: int, shown_price, shown_currency) -> None:
    message = update.effective_message
    if shown_currency == "diamonds":
        offers, coins, diamonds = await run_db(_panel_sync, update.effective_user)
        target_offer = next((o for o in offers if o["key"] == key), None)
        title = target_offer["title"] if target_offer else key
        emoji = target_offer["emoji"] if target_offer else f"{get_emoji('gift')}"
        tot_price = (shown_price or target_offer["price"]) * qty
        lines = [
            f"{get_emoji('diamond')} <b>تأیید خرید</b>",
            "━━━━━━━━━━━━━━━━━━━━",
            f"<blockquote>آیتم: {emoji} <b>{title}</b>\n"
            f"تعداد: <code>{qty:,} عدد</code>\n"
            f"مبلغ پرداختی: <code>{tot_price:,} الماس</code>\n"
            f"موجودی شما: <code>{diamonds:,} الماس</code></blockquote>",
            "━━━━━━━━━━━━━━━━━━━━",
            "آیا از انجام این خرید اطمینان داری؟",
        ]
        keyboard = InlineKeyboardMarkup([
            [
                btn("تأیید و خرید", emoji_key="btn_confirm", style=CONFIRM, callback_data=f"shop_confirm_buy:{key}:{qty}"),
                back_btn("menu:shop", "انصراف"),
            ],
        ])
        await message.reply_text("\n".join(lines), parse_mode="HTML", reply_markup=keyboard)
        return

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
    cur = "الماس" if offer["currency"] == "diamonds" else "طلا"
    await message.reply_text(
        f"✅ <b>خرید موفق ({qty:,} عدد):</b> {offer['emoji']} {offer['title']}\n"
        f"💳 کل مبلغ پرداختی: <code>{tot_price:,} {cur}</code>\n\n━━━━━━━━━━━━━━━━━━━━\n" + text,
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
    status = f"⏱ مدت باقیمانده: <code>{_fmt_hours(shield_secs)}</code>" if shield_secs > 0 else f"{get_emoji('sub_silver')}️ <i>در حال حاضر سپر فعال ندارید</i>"
    lines = [
        f"{sh} <b>خرید سپر محافظ آرنا</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"📌 وضعیت فعلی:\n{status}",
        f"💎 موجودی الماس: <code>{diamonds:,} الماس</code>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"<blockquote>تا وقتی سپر داری کسی نمی‌تونه در آرنا بهت حمله کنه.\n"
        f"هر حمله‌ای که <b>خودت</b> بزنی <code>{constants.SHIELD_ATTACK_COST_HOURS} ساعت</code> از سپرت کم می‌کنه.\n"
        "خریدها روی هم جمع می‌شوند.</blockquote>",
    ]
    rows = []
    for tier, cfg in constants.SHIELD_SHOP_TIERS.items():
        rows.append([btn(
            f"سپر {cfg['hours']} ساعته",
            emoji_key="btn_shield",
            style=SHOP, callback_data=f"shield_buy:{tier}",
        )])
    rows.append([back_btn("menu:shield_shop", "بازگشت")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def shield_shop_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """The «خرید سپر» entry — first choose which shield to buy (arena vs group)."""
    sh = get_emoji("shield")
    text = (
        f"{sh} <b>خرید سپر محافظ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"<blockquote>{get_emoji('def')} <b>سپر آرنا:</b> جلوگیری از غارت منابع در آرنا\n"
        f"{get_emoji('def')} <b>سپر گروه:</b> جلوگیری از حمله در گروه‌ها</blockquote>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "کدام سپر را می‌خواهید؟"
    )
    rows = InlineKeyboardMarkup([
        [
            btn("سپر آرنا", emoji_key="btn_shield", style=SHOP, callback_data="shield_arena"),
            btn("سپر گروه", emoji_key="btn_shield", style=SHOP, callback_data="gshield_shop"),
        ],
        [back_btn("menu:cat_shop", "بازگشت به فروشگاه")],
    ])
    from game.media import get_feature_image_path
    photo = get_feature_image_path("shield_shop")
    await send_screen(update, text, photo=photo, parse_mode="HTML", reply_markup=rows)


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
    cfg = constants.SHIELD_SHOP_TIERS.get(tier)
    if not cfg:
        await query.answer("این سپر پیدا نشد.", show_alert=True)
        return
    diamonds, _ = await run_db(_shield_state_sync, update.effective_user)
    await query.answer()
    lines = [
        f"{get_emoji('def')} <b>تأیید خرید سپر آرنا</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"<blockquote>نوع سپر: <b>{cfg['label']}</b>\n"
        f"مدت زمان: <code>{cfg['hours']} ساعت</code>\n"
        f"هزینه: <code>{cfg['diamonds']} الماس</code>\n"
        f"موجودی الماس شما: <code>{diamonds:,} الماس</code></blockquote>",
        "━━━━━━━━━━━━━━━━━━━━",
        "آیا تأیید می‌کنی؟",
    ]
    keyboard = InlineKeyboardMarkup([
        [
            btn("تأیید و خرید", emoji_key="btn_confirm", style=CONFIRM, callback_data=f"shield_do_buy:{tier}"),
            back_btn("shield_arena", "انصراف"),
        ],
    ])
    await safe_edit_message_text(query, "\n".join(lines), parse_mode="HTML", reply_markup=keyboard)


async def shield_do_buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    tier = query.data.split(":")[1]
    try:
        result, diamonds, shield_secs = await run_db(_shield_buy_sync, update.effective_user, tier)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer(f"{get_emoji('def')} سپر فعال شد!")
    text, keyboard = _shield_render(diamonds, shield_secs)
    await safe_edit_message_text(
        query,
        f"✅ <b>سپر خریداری شد!</b> الان <code>{_fmt_hours(shield_secs)}</code> محافظت داری.\n\n━━━━━━━━━━━━━━━━━━━━\n" + text,
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
        f"{get_emoji('shop_item')} <b>آیتم‌ها و بسته‌های ویژه</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"💰 موجودی طلا: <code>{coins:,} طلا</code>",
        f"💎 موجودی الماس: <code>{diamonds:,} الماس</code>",
        "━━━━━━━━━━━━━━━━━━━━",
    ]
    rows = []
    # 💎 daily gem-kaiju — a same-species/same-rarity twin of one of the player's top kaiju
    if gem:
        label = constants.RARITY_LABELS[gem["rarity"]]
        lines.append(f"💎 <b>کایجوی جمی: {gem['name']}</b> ({label})")
        if gem["claimed"]:
            lines.append(f"<blockquote>{get_emoji('confirm')} امروز خریداری شده است.</blockquote>")
        else:
            lines.append(
                f"<blockquote>💰 قیمت: <code>{gem['price']} الماس</code>\n"
                "<i>روزی یک‌بار قابل خرید</i></blockquote>"
            )
            rows.append([btn(f"خرید {gem['name']}", emoji_key="btn_creature", style=SHOP, callback_data="gemk_buy")])
        lines.append("")

    if not items:
        lines.append("<i>الان آیتم ویژه‌ی دیگری موجود نیست.</i>")
    for it in items:
        contents = json.loads(it.contents_json)
        summary = itemshop.content_summary(contents)
        desc = f"\n<i>{it.description}</i>" if it.description else ""
        lines.append(
            f"<blockquote>{it.emoji} <b>{it.title}</b>\n"
            f"💰 قیمت: <code>{itemshop.price_text(it)}</code>\n"
            f"🎁 محتویات: {summary}{desc}</blockquote>"
        )
        rows.append([btn(f"{it.emoji} {it.title}", style=SHOP, callback_data=f"sitem_buy:{it.id}")])
    rows.append([back_btn("menu:cat_shop", "بازگشت به فروشگاه")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def item_shop_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    items, coins, diamonds, gem = await run_db(_item_shop_sync, update.effective_user)
    text, keyboard = _item_shop_render(items, coins, diamonds, gem)
    from game.media import get_feature_image_path
    photo = get_feature_image_path("item_shop")
    await send_screen(update, text, photo=photo, parse_mode="HTML", reply_markup=keyboard)


def _item_buy_sync(tg_user, item_id):
    from game import itemshop, gemkaiju

    user, _ = get_or_create_user(tg_user)
    result = itemshop.buy(user, item_id)
    return result, itemshop.list_items(active_only=True), gemkaiju.gem_offer(user)


async def item_buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    item_id = int(query.data.split(":")[1])
    from game.itemshop import get_item
    item = await run_db(get_item, item_id)
    if item is None or not item.is_active:
        await query.answer("این آیتم در دسترس نیست.", show_alert=True)
        return

    # If it costs diamonds, ask for confirmation
    if item.price_diamonds > 0:
        _, _, diamonds, _ = await run_db(_item_shop_sync, update.effective_user)
        await query.answer()
        contents = json.loads(item.contents_json)
        from game import itemshop
        lines = [
            f"🛍 <b>تأیید خرید آیتم ویژه</b>",
            f"━━━━━━━━━━━━━━━━━━━━",
            f"<blockquote>📦 آیتم: {item.emoji} <b>{item.title}</b>\n"
            f"🎁 محتویات: <i>{itemshop.content_summary(contents)}</i>\n"
            f"💰 قیمت: <code>{item.price_diamonds:,} الماس</code>\n"
            f"💎 موجودی الماس: <code>{diamonds:,} الماس</code></blockquote>",
            "━━━━━━━━━━━━━━━━━━━━",
            "آیا از خرید این آیتم اطمینان داری؟",
        ]
        keyboard = InlineKeyboardMarkup([
            [
                btn("تأیید و خرید", emoji_key="btn_confirm", style=CONFIRM, callback_data=f"sitem_do_buy:{item.id}"),
                back_btn("menu:items", "انصراف"),
            ],
        ])
        await safe_edit_message_text(query, "\n".join(lines), parse_mode="HTML", reply_markup=keyboard)
        return

    # If coins only, buy directly
    await item_do_buy_callback(update, context)


async def item_do_buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    item_id = int(query.data.split(":")[1])
    try:
        result, items, gem = await run_db(_item_buy_sync, update.effective_user, item_id)
    except GameError as exc:
        if await show_gold_error(query, exc):
            return
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer(f"{get_emoji('confirm')} خریداری شد!")
    text, keyboard = _item_shop_render(items, result["coins"], result["diamonds"], gem)
    got = "، ".join(result["notes"])
    await safe_edit_message_text(
        query,
        f"✅ <b>خرید موفق: {result['emoji']} {result['title']}</b>\n"
        f"🎁 دریافتی: <i>{got}</i>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n" + text,
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
    items, coins, diamonds, gem = await run_db(_item_shop_sync, update.effective_user)
    if not gem or gem.get("claimed"):
        await query.answer("آفر کایجوی جمی امروز فعال نیست یا دریافت شده.", show_alert=True)
        return
    await query.answer()
    label = constants.RARITY_LABELS[gem["rarity"]]
    lines = [
        f"{get_emoji('diamond')} <b>تأیید خرید کایجوی جمی</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"<blockquote>🧬 هیولا: <b>{gem['name']}</b> ({label})\n"
        f"💰 قیمت: <code>{gem['price']:,} الماس</code>\n"
        f"💎 موجودی الماس: <code>{diamonds:,} الماس</code></blockquote>",
        "━━━━━━━━━━━━━━━━━━━━",
        "آیا از خرید این کایجو اطمینان داری؟",
    ]
    keyboard = InlineKeyboardMarkup([
        [
            btn("تأیید و خرید", emoji_key="btn_confirm", style=CONFIRM, callback_data="gemk_do_buy"),
            back_btn("menu:items", "انصراف"),
        ],
    ])
    await safe_edit_message_text(query, "\n".join(lines), parse_mode="HTML", reply_markup=keyboard)


async def gem_kaiju_do_buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        result, items, gem = await run_db(_gem_buy_sync, update.effective_user)
    except GameError as exc:
        if await show_gold_error(query, exc):
            return
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer(f"{get_emoji('confirm')} کایجوی جمی خریداری شد!")
    c = result["creature"]
    text, keyboard = _item_shop_render(items, result["coins"], result["diamonds"], gem)
    await safe_edit_message_text(
        query,
        f"✅ <b>کایجوی جمی خریده شد!</b>\n"
        f"🧬 <b>{c.name}</b> ({constants.RARITY_LABELS[c.rarity]}) به کلکسیونت اضافه شد.\n"
        f"💎 هزینه: <code>{result['price']:,}</code> الماس\n\n"
        f"<blockquote>از «🗂 کلکسیون» می‌تونی فعالش کنی یا برای فیوژن استفاده کنی.</blockquote>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n" + text,
        parse_mode="HTML",
        reply_markup=keyboard,
    )


# ── 🛡 group-shield shop (cheaper, protects from group «اتک») ─────────────────
def _gshield_state_sync(tg_user):
    from game.arena import group_shield_remaining_seconds

    user, _ = get_or_create_user(tg_user)
    return user.diamonds, group_shield_remaining_seconds(user)


def _gshield_render(diamonds: int, shield_secs: int) -> tuple[str, InlineKeyboardMarkup]:
    status = f"⏱ زمان باقیمانده: <code>{_fmt_hours(shield_secs)}</code>" if shield_secs > 0 else f"{get_emoji('sub_silver')}️ <i>در حال حاضر سپر گروه فعال ندارید</i>"
    lines = [
        f"{get_emoji('def')} <b>خرید سپر گروه</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"{status}",
        f"💎 موجودی الماس: <code>{diamonds:,} الماس</code>",
        "━━━━━━━━━━━━━━━━━━━━",
        "<blockquote>تا وقتی سپر گروه داری، کسی نمی‌تونه توی گروه با «اتک» بهت حمله کنه.\n"
        "این سپر از سپر آرنا جداست و ارزون‌تره.</blockquote>",
    ]
    rows = []
    for tier, cfg in constants.GROUP_SHIELD_SHOP_TIERS.items():
        rows.append([btn(f"سپر گروه {cfg['hours']} ساعته",
                         emoji_key="btn_shield", style=SHOP, callback_data=f"gshield_buy:{tier}")])
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
    cfg = constants.GROUP_SHIELD_SHOP_TIERS.get(tier)
    if not cfg:
        await query.answer("این سپر پیدا نشد.", show_alert=True)
        return
    diamonds, _ = await run_db(_gshield_state_sync, update.effective_user)
    await query.answer()
    lines = [
        f"{get_emoji('def')} <b>تأیید خرید سپر گروه</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"<blockquote>نوع سپر: <b>{cfg['label']}</b>\n"
        f"⏱ مدت زمان: <code>{cfg['hours']} ساعت</code>\n"
        f"💰 هزینه: <code>{cfg['diamonds']:,} الماس</code>\n"
        f"💎 موجودی الماس: <code>{diamonds:,} الماس</code></blockquote>",
        "━━━━━━━━━━━━━━━━━━━━",
        "آیا از خرید این سپر اطمینان داری؟",
    ]
    keyboard = InlineKeyboardMarkup([
        [
            btn("تأیید و خرید", emoji_key="btn_confirm", style=CONFIRM, callback_data=f"gshield_do_buy:{tier}"),
            back_btn("gshield_shop", "انصراف"),
        ],
    ])
    await safe_edit_message_text(query, "\n".join(lines), parse_mode="HTML", reply_markup=keyboard)


async def group_shield_do_buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    tier = query.data.split(":")[1]
    try:
        result, diamonds, shield_secs = await run_db(_gshield_buy_sync, update.effective_user, tier)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer(f"{get_emoji('def')} سپر گروه فعال شد!")
    text, keyboard = _gshield_render(diamonds, shield_secs)
    await safe_edit_message_text(
        query,
        f"✅ <b>سپر گروه خریداری شد!</b>\n"
        f"⏱ زمان محافظت فعال: <code>{_fmt_hours(shield_secs)}</code>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n" + text,
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
        "━━━━━━━━━━━━━━━━━━━━",
        f"💰 موجودی طلا: <code>{coins:,}</code>",
        f"💎 موجودی الماس: <code>{diamonds:,}</code>",
        "",
        "<blockquote>بسته‌های بزرگ‌تر تخفیف بیشتری دارند و به‌صرفه‌تر هستند.</blockquote>",
    ]
    rows = []
    for i, pack in enumerate(shop.GOLD_PACKS):
        rows.append([btn(
            f"بسته {pack['gold']:,} طلا",
            emoji_key="btn_gold_shop",
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
    from game.media import get_feature_image_path
    photo = get_feature_image_path("gold_shop")
    await send_screen(update, text, photo=photo, parse_mode="HTML", reply_markup=keyboard)


def _gold_buy_sync(tg_user, idx):
    user, _ = get_or_create_user(tg_user)
    pack = shop.buy_gold_pack(user, idx)
    return pack, user.coins, user.diamonds


async def gold_buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    idx = int(query.data.split(":")[1])
    if idx < 0 or idx >= len(shop.GOLD_PACKS):
        await query.answer("این بسته پیدا نشد.", show_alert=True)
        return
    pack = shop.GOLD_PACKS[idx]
    coins, diamonds = await run_db(_gold_shop_state_sync, update.effective_user)
    await query.answer()
    lines = [
        f"{get_emoji('coin')} <b>تأیید خرید طلا با الماس</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"<blockquote>🎁 دریافتی: <code>+{pack['gold']:,} طلا</code>\n"
        f"💎 پرداختی: <code>{pack['diamonds']:,} الماس</code>\n"
        f"💎 موجودی الماس: <code>{diamonds:,} الماس</code></blockquote>",
        "━━━━━━━━━━━━━━━━━━━━",
        "آیا از تبدیل الماس به طلا اطمینان داری؟",
    ]
    keyboard = InlineKeyboardMarkup([
        [
            btn("تأیید و خرید", emoji_key="btn_confirm", style=CONFIRM, callback_data=f"gold_do_buy:{idx}"),
            back_btn("gold_shop", "انصراف"),
        ],
    ])
    await safe_edit_message_text(query, "\n".join(lines), parse_mode="HTML", reply_markup=keyboard)


async def gold_do_buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
        f"✅ <b>خرید طلا با موفقیت انجام شد!</b>\n"
        f"💰 دریافتی: <code>+{pack['gold']:,} طلا</code>\n"
        f"💎 پرداختی: <code>{pack['diamonds']:,} الماس</code>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n" + text,
        parse_mode="HTML", reply_markup=keyboard,
    )


def register(application) -> None:
    application.add_handler(CommandHandler("shop", shop_panel, filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(gold_shop_panel, pattern=r"^gold_shop$"))
    application.add_handler(CallbackQueryHandler(gold_buy_callback, pattern=r"^gold_buy:\d+$"))
    application.add_handler(CallbackQueryHandler(gold_do_buy_callback, pattern=r"^gold_do_buy:\d+$"))
    application.add_handler(CallbackQueryHandler(shop_buy_callback, pattern=r"^shop_buy:"))
    application.add_handler(CallbackQueryHandler(shop_do_buy_callback, pattern=r"^shop_do_buy:"))
    application.add_handler(CallbackQueryHandler(shop_confirm_buy_callback, pattern=r"^shop_confirm_buy:"))
    application.add_handler(CallbackQueryHandler(shop_custom_qty_callback, pattern=r"^shop_custom_qty:"))
    application.add_handler(CommandHandler("shield", shield_shop_panel, filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(shield_arena_panel, pattern=r"^shield_arena$"))
    application.add_handler(CallbackQueryHandler(shield_buy_callback, pattern=r"^shield_buy:"))
    application.add_handler(CallbackQueryHandler(shield_do_buy_callback, pattern=r"^shield_do_buy:"))
    application.add_handler(CallbackQueryHandler(group_shield_shop_panel, pattern=r"^gshield_shop$"))
    application.add_handler(CallbackQueryHandler(group_shield_buy_callback, pattern=r"^gshield_buy:"))
    application.add_handler(CallbackQueryHandler(group_shield_do_buy_callback, pattern=r"^gshield_do_buy:"))
    application.add_handler(CommandHandler("items", item_shop_panel, filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(item_buy_callback, pattern=r"^sitem_buy:\d+$"))
    application.add_handler(CallbackQueryHandler(item_do_buy_callback, pattern=r"^sitem_do_buy:\d+$"))
    application.add_handler(CallbackQueryHandler(gem_kaiju_buy_callback, pattern=r"^gemk_buy$"))
    application.add_handler(CallbackQueryHandler(gem_kaiju_do_buy_callback, pattern=r"^gemk_do_buy$"))

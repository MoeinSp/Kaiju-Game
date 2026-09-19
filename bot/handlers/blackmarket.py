"""Midnight Black Market (بازار سیاه) UI & Bot Handlers."""

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

from bio_lab.models import BlackMarketAuction, User
from bio_lab.repository import get_or_create_user
from bot.buttons import CONFIRM, NAV, SHOP, back_btn, back_only_keyboard, btn
from bot.utils import run_db, safe_edit_message_text, send_screen
from game import blackmarket
from game.creature import GameError
from game.emoji import get_emoji


def _bm_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    auctions = blackmarket.get_active_auctions()
    return user, auctions


def _render_bm_text(user: User, auctions: list[BlackMarketAuction]) -> str:
    lines = [
        "⏳ <b>بازار سیاه و مزایده‌های نیمه‌شب</b>",
        "<i>هر شب اقلام نایاب و بسته‌های باارزش برای مزایده گذاشته می‌شوند.</i>\n",
        f"💰 موجودی شما: <b>{user.coins:,}</b> طلا · <b>{user.diamonds:,}</b> الماس\n",
        "━━━━━━━━━━━━━━━━━━━━",
    ]
    if not auctions:
        lines.append("<i>در حال حاضر مزایده فعالی در بازار موجود نیست.</i>")
    else:
        for a in auctions:
            curr = "طلا" if a.bid_currency == "coins" else "الماس"
            top_bidder = a.highest_bidder_name or "هنوز پیشنهادی ثبت نشده"
            time_left = a.ends_at.strftime("%H:%M:%S")
            lines.append(
                f"🏷 <b>{a.title}</b>\n"
                f"  💵 بالاترین پیشنهاد: <b>{a.current_bid:,}</b> {curr}\n"
                f"  👤 برنده فعلی: <b>{top_bidder}</b>\n"
                f"  ⏱ مهلت تا: <b>{time_left}</b>\n"
            )
    return "\n".join(lines)


def _render_bm_keyboard(auctions: list[BlackMarketAuction]) -> InlineKeyboardMarkup:
    rows = []
    for a in auctions:
        curr = "طلا" if a.bid_currency == "coins" else "💎"
        step = blackmarket.get_min_bid_increment(a.current_bid, a.bid_currency)
        next_bid = (a.current_bid + step) if a.highest_bidder_id is not None else a.min_bid
        short_title = a.title[:14]
        rows.append([
            btn(f"➕ پیشنهاد {next_bid:,} {curr} ({short_title}...)",
                emoji_key="btn_bm_bid",
                style=SHOP,
                callback_data=f"bm_bid:{a.id}:{next_bid}")
        ])
        rows.append([
            btn(f"🔢 ثبت پیشنهاد دلخواه ({short_title}...)",
                emoji_key="btn_custom_amt",
                style=CONFIRM,
                callback_data=f"bm_custom:{a.id}")
        ])
    rows.append([btn("🔄 بروزرسانی بازار", emoji_key="btn_bm_refresh", style=NAV, callback_data="bm:refresh")])
    rows.append([back_btn("menu:me")])
    return InlineKeyboardMarkup(rows)


def _render_confirmation_text(preview: dict) -> str:
    auc = preview["auction"]
    bid_amount = preview["bid_amount"]
    cost = preview["cost"]
    curr = "طلا" if preview["currency"] == "coins" else "الماس"
    prev_name = preview["prev_bidder_name"] or "هنوز پیشنهادی ثبت نشده"
    prev_amount = preview["prev_amount"]
    
    cost_info = f"<b>{cost:,}</b> {curr}"
    if preview.get("is_own_increase"):
        cost_info += f" <i>(افزایش روی پیشنهاد قبلی خودتان)</i>"

    lines = [
        "🏷 <b>تأیید نهایی ثبت پیشنهاد در مزایده</b>",
        f"«<b>{auc.title}</b>»\n",
        f"💵 پیشنهاد شما: <b>{bid_amount:,}</b> {curr}",
        f"👤 بالاترین پیشنهاد قبلی: <b>{prev_name}</b> ({prev_amount:,} {curr})",
        f"💰 مبلغ کسر از حساب: {cost_info}\n",
        "━━━━━━━━━━━━━━━━━━━━",
        "⚠️ <i>آیا از ثبت این پیشنهاد با مبلغ فوق اطمینان دارید؟</i>",
    ]
    return "\n".join(lines)


def _render_confirmation_keyboard(preview: dict) -> InlineKeyboardMarkup:
    auc = preview["auction"]
    bid_amount = preview["bid_amount"]
    curr = "طلا" if preview["currency"] == "coins" else "الماس"
    return InlineKeyboardMarkup([
        [
            btn(
                f"✅ بله، ثبت پیشنهاد ({bid_amount:,} {curr})",
                emoji_key="btn_confirm",
                style=CONFIRM,
                callback_data=f"bm_bid_go:{auc.id}:{bid_amount}",
            )
        ],
        [
            back_btn("menu:blackmarket", "❌ انصراف و بازگشت")
        ],
    ])


async def blackmarket_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user, auctions = await run_db(_bm_sync, update.effective_user)
    except GameError as exc:
        await send_screen(update, str(exc), parse_mode=None, reply_markup=back_only_keyboard())
        return

    from game.media import get_feature_image_path
    photo = get_feature_image_path("blackmarket")
    await send_screen(
        update,
        _render_bm_text(user, auctions),
        photo=photo,
        parse_mode="HTML",
        reply_markup=_render_bm_keyboard(auctions),
    )


async def bm_refresh_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    try:
        user, auctions = await run_db(_bm_sync, update.effective_user)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    from game.media import get_feature_image_path
    photo = get_feature_image_path("blackmarket")
    await send_screen(
        update,
        _render_bm_text(user, auctions),
        photo=photo,
        parse_mode="HTML",
        reply_markup=_render_bm_keyboard(auctions),
    )


async def bm_bid_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    parts = query.data.split(":")
    auc_id = int(parts[1])
    bid_amount = int(parts[2])

    def _validate(tg_user):
        user, _ = get_or_create_user(tg_user)
        return blackmarket.validate_bid_preview(user, auc_id, bid_amount)

    try:
        preview = await run_db(_validate, update.effective_user)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return

    await query.answer()
    text = _render_confirmation_text(preview)
    reply_markup = _render_confirmation_keyboard(preview)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=reply_markup)


async def bm_custom_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    auc_id = int(query.data.split(":")[1])

    def _get_auc():
        return BlackMarketAuction.objects.filter(id=auc_id, is_settled=False).first()

    auc = await run_db(_get_auc)
    if not auc:
        await query.answer("این مزایده پیدا نشد یا منقضی شده است.", show_alert=True)
        return

    from bot.handlers.private import AWAITING_PLAYER_KEY

    curr = "طلا" if auc.bid_currency == "coins" else "الماس"
    if auc.highest_bidder_id is None:
        min_required = auc.min_bid
        step = blackmarket.get_min_bid_increment(auc.min_bid, auc.bid_currency)
    else:
        step = blackmarket.get_min_bid_increment(auc.current_bid, auc.bid_currency)
        min_required = auc.current_bid + step

    context.user_data[AWAITING_PLAYER_KEY] = {
        "action": "blackmarket_custom_bid",
        "auction_id": auc.id,
    }
    await query.answer()
    text = (
        f"🏷 <b>ثبت پیشنهاد دلخواه در مزایده:</b>\n"
        f"«<b>{auc.title}</b>»\n\n"
        f"💵 بالاترین پیشنهاد فعلی: <b>{auc.current_bid:,}</b> {curr}\n"
        f"📈 حداقل افزایش مجاز: <b>+{step:,}</b> {curr}\n"
        f"🎯 <b>حداقل مبلغ کل پیشنهادی:</b> <b>{min_required:,}</b> {curr}\n\n"
        f"لطفاً مبلغ مورد نظر خود را همینجا ارسال کنید:\n"
        f"<i>(مثال: <code>{min_required}</code>)</i>"
    )
    await safe_edit_message_text(
        query,
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[back_btn("menu:blackmarket", "انصراف و بازگشت")]]),
    )


async def handle_custom_bid_input(update: Update, context: ContextTypes.DEFAULT_TYPE, awaiting: dict) -> None:
    from bot.handlers.private import AWAITING_PLAYER_KEY

    message = update.effective_message
    raw = (message.text or "").strip()
    norm = raw.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧۸۹", "01234567890123456789")).replace(",", "").replace("_", "").replace(" ", "")

    auc_id = awaiting.get("auction_id")
    if not norm.isdigit() or int(norm) <= 0:
        context.user_data[AWAITING_PLAYER_KEY] = awaiting
        await message.reply_text("⚠️ لطفاً فقط یک عدد معتبر ارسال کنید (مثلاً <code>100000</code>).", parse_mode="HTML")
        return

    bid_amount = int(norm)

    def _validate(tg_user):
        user, _ = get_or_create_user(tg_user)
        return blackmarket.validate_bid_preview(user, int(auc_id), bid_amount)

    try:
        preview = await run_db(_validate, update.effective_user)
    except GameError as exc:
        context.user_data[AWAITING_PLAYER_KEY] = awaiting
        await message.reply_text(f"⚠️ {exc}\n\nلطفاً مبلغ دیگری ارسال کنید یا انصراف دهید:", parse_mode="HTML")
        return

    # Valid preview - clear awaiting state and show confirmation screen
    context.user_data.pop(AWAITING_PLAYER_KEY, None)
    text = _render_confirmation_text(preview)
    reply_markup = _render_confirmation_keyboard(preview)
    await message.reply_text(text, parse_mode="HTML", reply_markup=reply_markup)


async def bm_bid_go_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    parts = query.data.split(":")
    auc_id = int(parts[1])
    bid_amount = int(parts[2])

    def _do_bid(tg_user):
        user, _ = get_or_create_user(tg_user)
        return blackmarket.place_bid(user, auc_id, bid_amount)

    try:
        res = await run_db(_do_bid, update.effective_user)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return

    curr = "طلا" if res["bid_currency"] == "coins" else "الماس"
    await query.answer(f"✅ پیشنهاد {res['bid_amount']:,} {curr} با موفقیت ثبت شد!", show_alert=True)

    outbid = res.get("outbid_info")
    if outbid:
        import asyncio
        from bot.handlers.notify import send_outbid_notification_now
        asyncio.create_task(send_outbid_notification_now(context, outbid))

    user, auctions = await run_db(_bm_sync, update.effective_user)
    from game.media import get_feature_image_path
    photo = get_feature_image_path("blackmarket")
    await send_screen(
        update,
        _render_bm_text(user, auctions),
        photo=photo,
        parse_mode="HTML",
        reply_markup=_render_bm_keyboard(auctions),
    )


def register(application) -> None:
    application.add_handler(CommandHandler(["blackmarket", "market"], blackmarket_panel))
    application.add_handler(CallbackQueryHandler(bm_refresh_callback, pattern=r"^bm:refresh$"))
    application.add_handler(CallbackQueryHandler(bm_bid_callback, pattern=r"^bm_bid:\d+:\d+$"))
    application.add_handler(CallbackQueryHandler(bm_bid_go_callback, pattern=r"^bm_bid_go:\d+:\d+$"))
    application.add_handler(CallbackQueryHandler(bm_custom_callback, pattern=r"^bm_custom:\d+$"))


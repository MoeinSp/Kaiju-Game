"""«🛒 خرید درون‌بازی» — the in-bot purchase flow.

Player side: pick amounts with steppers → see the Toman price and the owner's card →
upload a receipt photo. Owner side: gets the receipt with تایید / رد / بلاک / آنبلاک /
مدیریت کاربر buttons. Approval credits the resources (game.purchase).
"""

import html

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes, MessageHandler, filters

from bot.utils import alert_text
from bio_lab.repository import display_name, get_or_create_user
from bot.buttons import ADMIN, BACK, CONFIRM, DANGER, NAV, PRIMARY, SHOP, back_btn, btn
from bot.utils import run_db, safe_edit_message_text
from config import OWNER_TELEGRAM_ID
from game import botconfig, purchase
from game.creature import GameError
from game.emoji import get_emoji

_AMOUNTS_KEY = "buy_amounts"
_AWAIT_RECEIPT_KEY = "buy_awaiting_receipt_req"


def _is_reviewer(update: Update) -> bool:
    """Who may act on a receipt: the owner, or any granted admin."""
    u = update.effective_user
    if u is None:
        return False
    if u.id == OWNER_TELEGRAM_ID:
        return True
    from game import admins

    return admins.is_admin(u.id)


# ── player: amount selection ──────────────────────────────────────────────────
def _amounts(context) -> dict:
    return context.user_data.setdefault(_AMOUNTS_KEY, {"coins": 0, "dna": 0, "diamonds": 0})


def _sellable(prices: dict) -> list[str]:
    return [res for res in ("coins", "dna", "diamonds") if prices[res] > 0]


_RULE = "━━━━━━━━━━━━━━━━━━━━"
_RES_TITLE = {"coins": f"{get_emoji('coin')} طلا", "dna": f"{get_emoji('dna')} دی‌ان‌ای", "diamonds": f"{get_emoji('diamond')} الماس"}
_RES_UNIT_WORD = {"coins": "طلا", "dna": "عدد", "diamonds": "عدد"}


def _amount_screen(context, is_sub: bool = True) -> tuple[str, InlineKeyboardMarkup]:
    from game import globalsale

    prices = botconfig.get_buy_prices()
    amounts = _amounts(context)
    sellable = _sellable(prices)
    lines = [
        "💎 <b>خرید طلا و الماس</b>",
        _RULE,
    ]
    banner = globalsale.banner_text()
    if banner:
        lines += [banner, _RULE]
    lines += [
        "مقدار مورد نظرت رو با دکمه‌های ➖ و➕ تنظیم کن:",
        "",
    ]
    rows = []
    for res in sellable:
        amt = amounts.get(res, 0)
        step = purchase.STEP[res]
        step_price = round(prices[res] * step)
        lines.append(f"{_RES_TITLE[res]}: <b>{amt:,}</b>")
        # ┘ (RTL branch) and = read correctly right-to-left, unlike └ and ⟵
        lines.append(f"┘ نرخ: هر {step:,} {_RES_UNIT_WORD[res]} = {step_price:,} تومان")
        lines.append("")
        rows.append([
            btn(f"➖ {step:,}", style=NAV, callback_data=f"buy_adj:{res}:-"),
            btn(f"{purchase.RES_LABEL[res]}", style=NAV, callback_data="buy_noop"),
            btn(f"➕ {step:,}", style=CONFIRM, callback_data=f"buy_adj:{res}:+"),
        ])
        rows.append([btn(f"عدد دلخواه ({purchase.RES_LABEL[res]})", emoji_key="btn_custom_amt", style=NAV, callback_data=f"buy_custom:{res}")])
    list_total = purchase.price_for(
        amounts["coins"], amounts["dna"], amounts["diamonds"], apply_sale=False,
    )
    total = purchase.price_for(amounts["coins"], amounts["dna"], amounts["diamonds"])
    minimum = botconfig.get_buy_min()
    if list_total > total > 0:
        lines += [_RULE, f"💳 <b>مبلغ قابل پرداخت: <s>{list_total:,}</s> ← {total:,} تومان</b>"]
    else:
        lines += [_RULE, f"💳 <b>مبلغ قابل پرداخت: {total:,} تومان</b>"]
    if minimum > 0:
        lines.append(f"<i>حداقل خرید: {minimum:,} تومان</i>")
    if total > 0 and total >= minimum:
        rows.append([btn("کارت بانکی", emoji_key="btn_confirm", style=PRIMARY, callback_data="buy_submit")])
    elif total > 0 and minimum > 0:
        lines.append(f"⚠️ <b>برای ثبت خرید حداقل {minimum:,} تومان لازمه</b> — کمی بیشتر انتخاب کن.")
    if total > 0:
        rows.append([btn("صفر کردن", emoji_key="btn_reset", style=DANGER, callback_data="buy_reset")])
    if is_sub:
        rows.append([back_btn("menu:buy_open", "بازگشت")])
    else:
        rows.append([back_btn("menu:hub_shop", "بازگشت به فروشگاه")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def _fmt_ends(sec: int) -> str:
    h, rem = divmod(max(0, int(sec)), 3600)
    m = rem // 60
    if h >= 24:
        return f"{h // 24} روز و {h % 24}س"
    return f"{h}س و {m}د" if h else f"{m} دقیقه"


def _store_screen(packs: list[dict], custom_ok: bool, deals: dict | None = None) -> tuple[str, InlineKeyboardMarkup]:
    """Buy-store home: daily/weekly deals, owner packs, optional custom amount."""
    from game import globalsale
    from game.paid_offers import CADENCE_DAILY, CADENCE_EMOJI, CADENCE_LABEL, CADENCE_WEEKLY

    lines = ["💎 <b>خرید طلا و الماس</b>", _RULE]
    banner = globalsale.banner_text()
    if banner:
        lines += [banner, _RULE]
    rows = []
    deals = deals or {}
    for cadence in (CADENCE_WEEKLY, CADENCE_DAILY):
        bucket = deals.get(cadence) or []
        if not bucket:
            continue
        lines.append(f"{CADENCE_EMOJI[cadence]} <b>{CADENCE_LABEL[cadence]}</b>")
        lines.append(f"<i>تموم می‌شه تا {_fmt_ends(bucket[0]['ends_in'])}</i>")
        lines.append("")
        for d in bucket:
            badge = f"  🔥 {d['discount']}٪" if d["discount"] else ""
            sold = " — ❌ سقف پر" if d.get("sold_out") else ""
            lines.append(f"{d['emoji']} <b>{html.escape(d['title'])}</b>{badge}{sold}")
            lines.append(f"┘ {html.escape(d['gets'])}")
            if d["discount"]:
                lines.append(f"┘ <s>{d['original']:,}</s> ← <b>{d['price']:,} تومان</b>")
            else:
                lines.append(f"┘ <b>{d['price']:,} تومان</b>")
            lines.append("")
            if not d.get("sold_out"):
                label = f"{CADENCE_EMOJI[cadence]} {d['emoji']} {d['title']}"
                if d["discount"]:
                    label = f"🔥{d['discount']}% · {d['emoji']} {d['title']}"
                rows.append([btn(label[:64], emoji_key="btn_shop", style=SHOP, callback_data=f"buy_deal:{d['id']}")])
        lines.append(_RULE)
    if packs:
        lines.append("🎁 <b>پک‌های ویژه</b>")
        lines.append("")
        for p in packs:
            badge = f"  🔥 {p['discount']}٪ تخفیف" if p["discount"] > 0 else ""
            lines.append(f"{p['emoji']} <b>{p['title']}</b>{badge}")
            lines.append(f"┘ {p['contents']}")
            if p["discount"] > 0:
                lines.append(f"┘ <s>{p['original']:,}</s> ← <b>{p['price']:,} تومان</b>")
            else:
                lines.append(f"┘ <b>{p['price']:,} تومان</b>")
            lines.append("")
            label = f"{p['emoji']} {p['title']}"
            if p["discount"] > 0:
                label = f"🔥 {p['discount']}% · {p['emoji']} {p['title']}"
            rows.append([btn(label, emoji_key="btn_shop", style=SHOP, callback_data=f"buy_pack:{p['id']}")])
    if not packs and not any(deals.get(c) for c in (CADENCE_DAILY, CADENCE_WEEKLY)):
        lines.append("مقدار مورد نظرت رو بساز و پرداخت کن:")
    if custom_ok:
        rows.append([btn("مقدار دلخواه", emoji_key="btn_custom_amt", style=NAV, callback_data="buy_custom_home")])
    rows.append([back_btn("menu:hub_shop", "بازگشت به فروشگاه")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def _store_state_for(tg_user):
    from game import globalsale, paid_offers

    user, _ = get_or_create_user(tg_user)
    globalsale.active_sync()  # warm async-safe cache for the store banner
    return (
        purchase.list_active_packs(),
        botconfig.inbot_purchase_ready(),
        paid_offers.list_for_user(user),
    )


async def buy_open_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    buy_link = botconfig.get_buy_link()
    card_ok = bool(botconfig.get_buy_card()[0])
    # packs/custom are cache-only; daily/weekly deals need a sync check — allow through
    # whenever a card is set so we can see them even before any PurchasePack exists
    if not botconfig.store_ready() and not card_ok:
        if buy_link is not None:
            burl, btitle = buy_link
            await query.answer()
            await safe_edit_message_text(
                query,
                f"💎 <b>خرید طلا و الماس</b>\n\nجهت خرید بسته‌ها و الماس به درگاه رسمی زیر مراجعه کنید:\n\n{burl}",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [btn(btitle or "ورود به درگاه خرید", emoji_key="btn_buy", style=PRIMARY, url=burl)],
                    [back_btn("menu:hub_shop", "بازگشت به فروشگاه")],
                ]),
            )
            return
        await query.answer("💎 بخش خرید طلا و الماس در حال راه‌اندازی است.", show_alert=True)
        return
    context.user_data[_AMOUNTS_KEY] = {"coins": 0, "dna": 0, "diamonds": 0}
    context.user_data.pop(_AWAIT_RECEIPT_KEY, None)
    packs, custom_ok, deals = await run_db(_store_state_for, update.effective_user)
    has_deals = bool((deals or {}).get("daily") or (deals or {}).get("weekly"))
    if not botconfig.store_ready() and not has_deals:
        await query.answer("💎 بخش خرید طلا و الماس در حال راه‌اندازی است.", show_alert=True)
        return
    await query.answer()
    if packs or has_deals:
        text, kb = _store_screen(packs, custom_ok, deals)
    else:
        text, kb = _amount_screen(context, is_sub=False)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=kb)


async def buy_custom_home_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """«مقدار دلخواه» — open the classic stepper amount screen."""
    query = update.callback_query
    if not botconfig.inbot_purchase_ready():
        await query.answer("خرید مقدار دلخواه فعال نیست.", show_alert=True)
        return
    context.user_data[_AMOUNTS_KEY] = {"coins": 0, "dna": 0, "diamonds": 0}
    await query.answer()
    packs = await run_db(purchase.list_active_packs)
    text, kb = _amount_screen(context, is_sub=bool(packs))
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=kb)


def _deal_go_sync(tg_user, item_id: int):
    user, _ = get_or_create_user(tg_user)
    return purchase.create_item_pending(user, item_id)


async def buy_deal_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Daily/weekly paid deal → same receipt flow as a ShopItem Toman pack."""
    query = update.callback_query
    item_id = int(query.data.split(":")[1])
    try:
        req = await run_db(_deal_go_sync, update.effective_user, item_id)
    except GameError as exc:
        await query.answer(alert_text(exc), show_alert=True)
        return
    context.user_data[_AWAIT_RECEIPT_KEY] = req.id
    await query.answer()
    text, kb = _receipt_screen(req)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=kb)


def _pack_detail_screen(p: dict) -> tuple[str, InlineKeyboardMarkup]:
    lines = [
        f"{p['emoji']} <b>{p['title']}</b>",
        _RULE,
        f"🎁 محتوا: {p['contents']}",
    ]
    if p["discount"] > 0:
        lines.append(f"🔥 <b>{p['discount']}٪ تخفیف</b>")
        lines.append(f"💰 قیمت: <s>{p['original']:,}</s> ← <b>{p['price']:,} تومان</b>")
    else:
        lines.append(f"💰 قیمت: <b>{p['price']:,} تومان</b>")
    lines += ["", "بعد از تأیید، کارت پرداخت رو می‌بینی و رسیدت رو می‌فرستی."]
    kb = InlineKeyboardMarkup([
        [
            btn("خرید این پک", emoji_key="btn_confirm", style=PRIMARY, callback_data=f"buy_pack_go:{p['id']}"),
            back_btn("menu:buy_open", "انصراف"),
        ],
    ])
    return "\n".join(lines), kb


async def buy_pack_detail_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    pack_id = int(query.data.split(":")[1])
    p = await run_db(purchase.get_pack, pack_id)
    if p is None or not p["active"]:
        await query.answer("این پک دیگه در دسترس نیست.", show_alert=True)
        return
    await query.answer()
    text, kb = _pack_detail_screen(p)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=kb)


def _create_pack_pending_sync(tg_user, pack_id):
    user, _ = get_or_create_user(tg_user)
    return purchase.create_pack_pending(user, pack_id)


async def buy_pack_go_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    pack_id = int(query.data.split(":")[1])
    try:
        req = await run_db(_create_pack_pending_sync, update.effective_user, pack_id)
    except GameError as exc:
        await query.answer(alert_text(exc), show_alert=True)
        return
    context.user_data[_AWAIT_RECEIPT_KEY] = req.id
    await query.answer()
    text, kb = _receipt_screen(req)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=kb)


async def buy_noop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()


async def buy_adjust_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, res, sign = query.data.split(":")
    amounts = _amounts(context)
    step = purchase.STEP.get(res, 0)
    delta = step if sign == "+" else -step
    amounts[res] = max(0, min(purchase.MAX_UNITS.get(res, 0), amounts.get(res, 0) + delta))
    await query.answer()
    packs = await run_db(purchase.list_active_packs)
    text, kb = _amount_screen(context, is_sub=bool(packs))
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=kb)


async def buy_reset_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data[_AMOUNTS_KEY] = {"coins": 0, "dna": 0, "diamonds": 0}
    await update.callback_query.answer("صفر شد.")
    packs = await run_db(purchase.list_active_packs)
    text, kb = _amount_screen(context, is_sub=bool(packs))
    await safe_edit_message_text(update.callback_query, text, parse_mode="HTML", reply_markup=kb)


async def buy_custom_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """«🔢 عدد دلخواه» — arm a capture so the user's next typed number sets this
    resource's amount directly."""
    query = update.callback_query
    res = query.data.split(":")[1]
    from bot.handlers.private import AWAITING_PLAYER_KEY

    context.user_data[AWAITING_PLAYER_KEY] = {"action": "buy_custom", "res": res}
    await query.answer()
    await safe_edit_message_text(
        query,
        f"🔢 چه مقدار <b>{purchase.RES_LABEL[res]}</b> می‌خوای؟ عدد رو همین‌جا بفرست.\n"
        f"<i>مثلاً <code>{purchase.STEP[res] * 3:,}</code></i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[back_btn("menu:buy_open", "انصراف")]]),
    )


async def handle_custom_amount(update: Update, context: ContextTypes.DEFAULT_TYPE, awaiting: dict) -> None:
    """The user typed a number after «عدد دلخواه». Set it (clamped) and re-show the
    amount screen as a fresh message. Called from private.capture_player_text_reply."""
    from bot.handlers.private import AWAITING_PLAYER_KEY

    res = awaiting.get("res")
    message = update.effective_message
    raw = (message.text or "").strip().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
    raw = raw.replace(",", "").replace("٬", "")
    if not raw.isdigit():
        context.user_data[AWAITING_PLAYER_KEY] = awaiting  # keep waiting
        await message.reply_text("فقط یه عدد بفرست (مثلاً 50000).")
        return
    if res in purchase.STEP:
        _amounts(context)[res] = max(0, min(purchase.MAX_UNITS.get(res, 0), int(raw)))
    packs, _ = await run_db(_store_state_sync)
    text, kb = _amount_screen(context, is_sub=bool(packs))
    await message.reply_text(text, parse_mode="HTML", reply_markup=kb)


def _create_pending_sync(tg_user, coins, dna, diamonds):
    user, _ = get_or_create_user(tg_user)
    return purchase.create_pending(user, coins, dna, diamonds)


def _receipt_screen(req) -> tuple[str, InlineKeyboardMarkup]:
    """The «pay & send receipt» screen for a pending purchase — shared by the custom
    stepper flow and the one-tap pack flow."""
    text = purchase.receipt_instructions_html(req)
    kb = InlineKeyboardMarkup([[
        btn("لغو سفارش", emoji_key="btn_cancel", style=DANGER, callback_data="buy_cancel"),
    ]])
    return text, kb


def _cancel_awaiting_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    return purchase.cancel_awaiting(user)


async def buy_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Drop the awaiting-receipt draft and return to the store."""
    query = update.callback_query
    cancelled = await run_db(_cancel_awaiting_sync, update.effective_user)
    context.user_data.pop(_AWAIT_RECEIPT_KEY, None)
    await query.answer("سفارش لغو شد." if cancelled else "سفارشی برای لغو نبود.", show_alert=not cancelled)
    context.user_data[_AMOUNTS_KEY] = {"coins": 0, "dna": 0, "diamonds": 0}
    packs, custom_ok, deals = await run_db(_store_state_for, update.effective_user)
    has_deals = bool((deals or {}).get("daily") or (deals or {}).get("weekly"))
    if packs or has_deals:
        text, kb = _store_screen(packs, custom_ok, deals)
    else:
        text, kb = _amount_screen(context, is_sub=False)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=kb)


async def buy_submit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    amounts = _amounts(context)
    try:
        req = await run_db(_create_pending_sync, update.effective_user,
                           amounts["coins"], amounts["dna"], amounts["diamonds"])
    except GameError as exc:
        await query.answer(alert_text(exc), show_alert=True)
        return
    context.user_data[_AWAIT_RECEIPT_KEY] = req.id
    await query.answer()
    text, kb = _receipt_screen(req)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=kb)


# ── player: receipt photo upload ──────────────────────────────────────────────
def _attach_sync(tg_user, req_id, file_id):
    user, _ = get_or_create_user(tg_user)
    req = purchase.attach_receipt(req_id, user.id, file_id)
    return user, req


def _recent_awaiting_req_id_sync(tg_id):
    """The player's open «awaiting receipt» request from the last 2 hours — the durable
    fallback for when the bot restarted between the invoice and the receipt photo (the
    in-memory pointer is gone, but the paid player must not be ignored)."""
    import datetime

    from django.utils import timezone

    from bio_lab.models import PurchaseRequest

    since = timezone.now() - datetime.timedelta(hours=2)
    return (
        PurchaseRequest.objects.filter(user_id=tg_id, status="awaiting_receipt", created_at__gte=since)
        .order_by("-id").values_list("id", flat=True).first()
    )


def _receipt_file_id(message) -> str | None:
    """Largest photo size, or an image/PDF document (Telegram «send as file»)."""
    if message.photo:
        return message.photo[-1].file_id
    doc = message.document
    if doc is None:
        return None
    mime = (doc.mime_type or "").lower()
    name = (doc.file_name or "").lower()
    if mime.startswith("image/") or mime == "application/pdf":
        return doc.file_id
    if name.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".pdf")):
        return doc.file_id
    return None


async def receipt_photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A photo/image-doc sent in private WHILE a purchase is awaiting its receipt is
    treated as that receipt. Otherwise ignored (so ordinary photos aren't captured)."""
    message = update.effective_message
    if message is None or update.effective_user is None:
        return
    file_id = _receipt_file_id(message)
    if not file_id:
        return
    req_id = context.user_data.get(_AWAIT_RECEIPT_KEY)
    if req_id is None:
        req_id = await run_db(_recent_awaiting_req_id_sync, update.effective_user.id)
    if req_id is None:
        return
    user, req = await run_db(_attach_sync, update.effective_user, req_id, file_id)
    context.user_data.pop(_AWAIT_RECEIPT_KEY, None)
    if req is None:
        await message.reply_text("این درخواست دیگه معتبر نیست. از منو دوباره «خرید» رو بزن.")
        return
    await message.reply_text(
        "✅ رسیدت دریافت شد و برای تأیید ارسال شد. به‌محض تأیید، موجودی اضافه می‌شه. 🙏"
    )
    # forward the receipt to the owner with review actions
    base_caption = (
        f"🧾 <b>درخواست خرید جدید</b>\n"
        f"👤 {display_name(user)} (<code>{user.id}</code>)\n"
        f"🛒 {purchase.request_summary(req)}\n"
        f"💰 مبلغ: <b>{req.price_toman:,} تومان</b>"
    )
    kb = InlineKeyboardMarkup([
        [btn("تأیید", emoji_key="btn_confirm", style=CONFIRM, callback_data=f"buyok:{req.id}"),
         btn("رد", emoji_key="btn_cancel", style=DANGER, callback_data=f"buyno:{req.id}")],
        [btn("بلاک رسید", emoji_key="btn_cancel", style=DANGER, callback_data=f"buyblk:{user.id}"),
         btn("آنبلاک", emoji_key="btn_confirm", style=NAV, callback_data=f"buyunblk:{user.id}")],
        [btn("مدیریت کاربر", emoji_key="btn_profile", style=ADMIN, callback_data=f"buymgr:{user.id}")],
    ])
    as_doc = bool(message.document) and not message.photo

    async def _forward_receipt(chat_id, caption, markup=None):
        if as_doc:
            return await context.bot.send_document(
                chat_id=chat_id, document=file_id, caption=caption, parse_mode="HTML", reply_markup=markup,
            )
        return await context.bot.send_photo(
            chat_id=chat_id, photo=file_id, caption=caption, parse_mode="HTML", reply_markup=markup,
        )

    try:
        await _forward_receipt(OWNER_TELEGRAM_ID, base_caption, kb)
    except Exception:  # noqa: BLE001 — never fail the user's flow over a delivery hiccup
        import logging

        logging.exception("purchase receipt could not be delivered to the owner (request %s)", req.id)

    # also post a report to the configured purchase-report channel (no buttons — review
    # happens in the owner's DM; the channel message is edited on approve/reject)
    from game import botconfig

    channel_id = botconfig.get_buy_channel_id()
    if channel_id:
        try:
            msg = await _forward_receipt(
                channel_id, base_caption + "\n\n⏳ <b>وضعیت: در حال انتظار</b>",
            )
            await run_db(purchase.set_channel_message, req.id, channel_id, msg.message_id)
        except Exception:  # noqa: BLE001 — a channel delivery hiccup must not break the flow
            pass


# ── owner: review actions ─────────────────────────────────────────────────────
async def _notify_user(context, user_id: int, text: str, reply_markup=None) -> None:
    try:
        await context.bot.send_message(chat_id=user_id, text=text, parse_mode="HTML", reply_markup=reply_markup)
    except Exception:  # noqa: BLE001
        pass


async def _update_channel_status(context, res: dict, status_html: str) -> None:
    """Edit the purchase-report channel message so its status reflects approve/reject."""
    chat_id, msg_id = res.get("channel_chat_id"), res.get("channel_message_id")
    if not chat_id or not msg_id:
        return
    bits = []
    if res.get("summary") and res.get("item"):
        bits.append(res["summary"])
    if res.get("subscription_tier"):
        from game.subscription import SUBSCRIPTION_TIERS
        sub = SUBSCRIPTION_TIERS.get(res["subscription_tier"])
        name = sub["name"] if sub else res["subscription_tier"]
        badge = sub["badge"] if sub else f"{get_emoji('star')}"
        bits.append(f"{badge} {name} (۳۰ روزه)")
    if res.get("coins"):
        bits.append(f"{res['coins']:,} 🪙")
    if res.get("dna"):
        bits.append(f"{res['dna']:,} 🧬")
    if res.get("diamonds"):
        bits.append(f"{res['diamonds']:,} 💎")
    caption = (
        f"🧾 <b>درخواست خرید</b>\n"
        f"👤 کاربر: <code>{res['user_id']}</code>\n"
        f"🛒 {' · '.join(bits) or '—'}\n"
        f"💰 مبلغ: <b>{res['price']:,} تومان</b>\n\n{status_html}"
    )
    try:
        await context.bot.edit_message_caption(chat_id=chat_id, message_id=msg_id,
                                                caption=caption, parse_mode="HTML")
    except Exception:  # noqa: BLE001
        pass


async def buy_approve_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_reviewer(update):
        await query.answer()
        return
    req_id = int(query.data.split(":")[1])
    try:
        res = await run_db(purchase.approve, req_id)
    except GameError as exc:
        await query.answer(alert_text(exc), show_alert=True)
        return
    await query.answer("✅ تأیید شد و اعمال شد.")
    bits = []
    if res.get("subscription_tier"):
        from game.subscription import SUBSCRIPTION_TIERS
        sub = SUBSCRIPTION_TIERS.get(res["subscription_tier"])
        name = sub["name"] if sub else res["subscription_tier"]
        badge = sub["badge"] if sub else f"{get_emoji('star')}"
        bits.append(f"{badge} <b>{name} (۳۰ روزه)</b> فعال شد! 🎉")
    if res["coins"]:
        bits.append(f"{res['coins']:,} {get_emoji('coin')}")
    if res["dna"]:
        bits.append(f"{res['dna']:,} {get_emoji('dna')}")
    if res["diamonds"]:
        bits.append(f"{res['diamonds']:,} {get_emoji('diamond')}")
    notify_text = f"{get_emoji('confirm')} <b>خریدت تأیید شد!</b>\n{get_emoji('gift')} " + ("\n".join(bits) if res.get("subscription_tier") else ("به حسابت اضافه شد: " + " · ".join(bits)))
    rows = []
    if res.get("item"):   # a pack: only THIS pack's warehouse bits (chests / pick tickets)
        stash = []
        if res.get("chests"):
            stash.append("جعبه‌ها")
        if res.get("picks"):
            stash.append("بلیت انتخاب هیولا")
        note = (
            f"\n\n<i>{' و '.join(stash)} توی «انبار»ت ذخیره شدن — همین الان باز کن یا بعداً از انبار.</i>"
            if stash else ""
        )
        notify_text = (
            f"{get_emoji('confirm')} <b>خریدت تأیید شد!</b>\n"
            f"{get_emoji('gift')} <b>{html.escape(res['item'])}</b>\n"
            + "\n".join(f"• {html.escape(n)}" for n in res["notes"])
            + note
        )
        if res.get("chests"):
            rows.append([btn("باز کردن جعبه‌ها", emoji_key="btn_chest_open", style=CONFIRM,
                             callback_data="rc:open:offer")])
        if res.get("picks"):
            from game import constants as _c

            nxt = (res.get("pick_rarity") or "mythic")
            rar_key = f"btn_rarity_{nxt}" if nxt in _c.RARITY_ORDER else "btn_rarity_mythic"
            rows.append([btn(
                res.get("pick_label") or f"انتخاب هیولای دلخواه ({res['picks']})",
                emoji_key=rar_key, style=PRIMARY, callback_data="cpick:open",
            )])
    if rows or res.get("item"):
        rows.append([btn("انبار", emoji_key="btn_backpack", style=SHOP, callback_data="bp:home")])
    markup = InlineKeyboardMarkup(rows) if rows else None
    await _notify_user(context, res["user_id"], notify_text, markup)
    if query.message is not None and query.message.caption is not None:
        await query.edit_message_caption(
            caption=(query.message.caption_html or "") + f"\n\n{get_emoji('confirm')} <b>تأیید شد.</b>", parse_mode="HTML"
        )
    await _update_channel_status(context, res, f"{get_emoji('confirm')} <b>وضعیت: تأیید شد</b>")


async def buy_reject_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_reviewer(update):
        await query.answer()
        return
    req_id = int(query.data.split(":")[1])
    try:
        res = await run_db(purchase.reject, req_id)
    except GameError as exc:
        await query.answer(alert_text(exc), show_alert=True)
        return
    await query.answer("❌ رد شد.")
    await _notify_user(
        context, res["user_id"],
        f"{get_emoji('cancel')} <b>رسید خریدت تأیید نشد.</b> اگه واریز کردی، عکس واضح‌ترِ رسید رو برای پشتیبانی بفرست تا دوباره بررسی بشه.",
    )
    if query.message is not None and query.message.caption is not None:
        await query.edit_message_caption(
            caption=(query.message.caption_html or "") + f"\n\n{get_emoji('cancel')} <b>رد شد.</b>", parse_mode="HTML"
        )
    await _update_channel_status(context, res, f"{get_emoji('cancel')} <b>وضعیت: رد شد</b>")


async def buy_block_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_reviewer(update):
        await query.answer()
        return
    parts = query.data.split(":")
    block = parts[0] == "buyblk"
    user_id = int(parts[1])
    try:
        await run_db(purchase.set_receipt_block, user_id, block)
    except GameError as exc:
        await query.answer(alert_text(exc), show_alert=True)
        return
    await query.answer("🚫 بلاک شد." if block else "♻️ آنبلاک شد.")
    if query.message is not None:
        tag = f"{get_emoji('banned')} <b>ثبت رسید این کاربر بلاک شد.</b>" if block else "♻️ <b>بلاک رسید برداشته شد.</b>"
        await query.edit_message_caption(
            caption=(query.message.caption_html or "") + f"\n\n{tag}", parse_mode="HTML"
        )


async def buy_manage_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """«مدیریت کاربر» — open the same user-management card the admin search shows."""
    query = update.callback_query
    if not _is_reviewer(update):
        await query.answer()
        return
    user_id = query.data.split(":")[1]
    from bot.handlers.owner import _user_info_text, _user_manage_keyboard
    from game.moderation import user_info

    try:
        data = await run_db(user_info, user_id)
    except GameError as exc:
        await query.answer(alert_text(exc), show_alert=True)
        return
    await query.answer()
    u = data["user"]
    # the review message is a photo; a fresh text message is cleaner than editing a caption
    await context.bot.send_message(
        chat_id=query.message.chat_id, text=_user_info_text(data), parse_mode="HTML",
        reply_markup=_user_manage_keyboard(u.id, u.is_banned),
    )


# ── creature pick («اساطیری دلخواه») ──────────────────────────────────────────
def _cpick_screen_sync(tg_user, element: str | None, page: int):
    from bot.handlers.serums import loot_nav_rows
    from game import constants, creature_pick

    user, _ = get_or_create_user(tg_user)
    left = creature_pick.pending_count(user)
    if left <= 0:
        raise GameError("بلیت انتخاب هیولا نداری — اگه خریدی، از «انبار» چک کن.")
    data = creature_pick.options(element, page)
    nxt = creature_pick.next_rarity(user) or "mythic"
    rar_label = constants.RARITY_LABELS.get(nxt, nxt)  # «🟡 افسانه‌ای» / «🔴 اساطیری»
    rar_emoji_key = f"btn_rarity_{nxt}" if nxt in constants.RARITY_ORDER else "btn_rarity_mythic"
    lines = [
        f"🦕 <b>{rar_label} دلخواه</b> — {left} بلیت باقی",
        "یه گونه انتخاب کن؛ عنصرش از روی اسم مشخص می‌شه.",
        "<i>بلیت‌ها توی انبار می‌مونن تا مصرف بشن.</i>",
        "",
    ]
    rows = []
    # element filter row
    el_row = [btn(
        "همه" if element else "✅ همه",
        emoji_key="btn_nav", style=NAV if element else PRIMARY,
        callback_data="cpick:e:all:0",
    )]
    for el in constants.ELEMENTS:
        mark = "✅ " if element == el else ""
        el_row.append(btn(
            f"{mark}{constants.ELEMENT_WORDS[el]}",
            emoji_key=f"element_{el}", style=PRIMARY if element == el else NAV,
            callback_data=f"cpick:e:{el}:0",
        ))
        if len(el_row) == 3:
            rows.append(el_row)
            el_row = []
    if el_row:
        rows.append(el_row)
    for it in data["items"]:
        rows.append([btn(
            f"{constants.ELEMENT_WORDS[it['element']]} · {it['name']}",
            emoji_key=rar_emoji_key, style=CONFIRM,
            callback_data=f"cpick:n:{it['idx']}",
        )])
    nav = []
    if data["page"] > 0:
        el_key = element or "all"
        nav.append(btn("‹ قبل", emoji_key="btn_back", style=NAV,
                       callback_data=f"cpick:e:{el_key}:{data['page'] - 1}"))
    if data["page"] + 1 < data["pages"]:
        el_key = element or "all"
        nav.append(btn("بعد ›", emoji_key="btn_nav", style=NAV,
                       callback_data=f"cpick:e:{el_key}:{data['page'] + 1}"))
    if nav:
        rows.append(nav)
    rows.extend(loot_nav_rows(user, skip={"picks"}))
    text = "\n".join(lines)
    if data["pages"] > 1:
        text += f"\n\nصفحه {data['page'] + 1}/{data['pages']}"
    return text, InlineKeyboardMarkup(rows)


async def cpick_open_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    # fresh open = new picking session (don't merge with an older half-finished run)
    context.user_data.pop("cpick_session", None)
    try:
        text, kb = await run_db(_cpick_screen_sync, update.effective_user, None, 0)
    except GameError as exc:
        await query.answer(alert_text(exc), show_alert=True)
        return
    await query.answer()
    await safe_edit_message_text(query, text, reply_markup=kb, parse_mode="HTML")


async def cpick_element_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    # cpick:e:<element|all>:<page>
    parts = query.data.split(":")
    el = None if parts[2] == "all" else parts[2]
    page = int(parts[3]) if len(parts) > 3 else 0
    try:
        text, kb = await run_db(_cpick_screen_sync, update.effective_user, el, page)
    except GameError as exc:
        await query.answer(alert_text(exc), show_alert=True)
        return
    await query.answer()
    await safe_edit_message_text(query, text, reply_markup=kb, parse_mode="HTML")


def _cpick_redeem_sync(tg_user, idx: int):
    from game import creature_pick

    user, _ = get_or_create_user(tg_user)
    return creature_pick.redeem(user, idx)


def _cpick_line(name: str, rarity: str, element: str) -> str:
    from game import constants

    return (
        f"✅ <b>{html.escape(name)}</b> "
        f"({constants.RARITY_LABELS[rarity]} · {constants.ELEMENT_WORDS[element]})"
    )


def _cpick_session_lines(session: list[dict]) -> str:
    """All picks redeemed in this go — so 2 tickets don't end on only the last name."""
    lines = [_cpick_line(p["name"], p["rarity"], p["element"]) for p in session]
    if len(session) <= 1:
        return lines[0] + " به کلکسیت اضافه شد." if lines else ""
    return "\n".join(lines) + "\nبه کلکسیت اضافه شدن."


async def cpick_pick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    idx = int(query.data.split(":")[2])
    try:
        res = await run_db(_cpick_redeem_sync, update.effective_user, idx)
    except GameError as exc:
        await query.answer(alert_text(exc), show_alert=True)
        return

    session = context.user_data.setdefault("cpick_session", [])
    session.append({"name": res["name"], "rarity": res["rarity"], "element": res["element"]})
    left = res["left"]
    msg = _cpick_session_lines(session)
    if left:
        msg += f"\nهنوز {left} بلیت داری — یکی دیگه انتخاب کن."
        text, kb = await run_db(_cpick_screen_sync, update.effective_user, None, 0)
        await query.answer("✅ ساخته شد")
        await safe_edit_message_text(query, msg + "\n\n" + text, reply_markup=kb, parse_mode="HTML")
    else:
        context.user_data.pop("cpick_session", None)

        def _done_nav(tg_user):
            from bot.handlers.serums import loot_nav_rows
            user, _ = get_or_create_user(tg_user)
            return loot_nav_rows(user, skip={"picks"})

        rows = await run_db(_done_nav, update.effective_user)
        await query.answer("✅ ساخته شد")
        await safe_edit_message_text(
            query, msg, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(rows) if rows else None,
        )


def register(application) -> None:
    application.add_handler(CallbackQueryHandler(buy_open_callback, pattern=r"^buy_open$"))
    application.add_handler(CallbackQueryHandler(buy_custom_home_callback, pattern=r"^buy_custom_home$"))
    application.add_handler(CallbackQueryHandler(buy_pack_detail_callback, pattern=r"^buy_pack:\d+$"))
    application.add_handler(CallbackQueryHandler(buy_pack_go_callback, pattern=r"^buy_pack_go:\d+$"))
    application.add_handler(CallbackQueryHandler(buy_deal_callback, pattern=r"^buy_deal:\d+$"))
    application.add_handler(CallbackQueryHandler(buy_noop_callback, pattern=r"^buy_noop$"))
    application.add_handler(CallbackQueryHandler(buy_adjust_callback, pattern=r"^buy_adj:(coins|dna|diamonds):[+-]$"))
    application.add_handler(CallbackQueryHandler(buy_custom_callback, pattern=r"^buy_custom:(coins|dna|diamonds)$"))
    application.add_handler(CallbackQueryHandler(buy_reset_callback, pattern=r"^buy_reset$"))
    application.add_handler(CallbackQueryHandler(buy_submit_callback, pattern=r"^buy_submit$"))
    application.add_handler(CallbackQueryHandler(buy_cancel_callback, pattern=r"^buy_cancel$"))
    application.add_handler(CallbackQueryHandler(buy_approve_callback, pattern=r"^buyok:\d+$"))
    application.add_handler(CallbackQueryHandler(buy_reject_callback, pattern=r"^buyno:\d+$"))
    application.add_handler(CallbackQueryHandler(buy_block_callback, pattern=r"^(buyblk|buyunblk):\d+$"))
    application.add_handler(CallbackQueryHandler(buy_manage_callback, pattern=r"^buymgr:\d+$"))
    application.add_handler(CallbackQueryHandler(cpick_open_callback, pattern=r"^cpick:open$"))
    application.add_handler(CallbackQueryHandler(cpick_element_callback, pattern=r"^cpick:e:(all|[a-z]+):\d+$"))
    application.add_handler(CallbackQueryHandler(cpick_pick_callback, pattern=r"^cpick:n:\d+$"))
    # Photo or image/PDF document in private. group=0 so it runs with the other private
    # message handlers; Document.IMAGE won't steal owner .json.gz restore uploads.
    _receipt_media = filters.PHOTO | filters.Document.IMAGE | filters.Document.MimeType("application/pdf")
    application.add_handler(
        MessageHandler(filters.ChatType.PRIVATE & _receipt_media, receipt_photo_handler),
    )


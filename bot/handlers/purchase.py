"""«🛒 خرید درون‌بازی» — the in-bot purchase flow.

Player side: pick amounts with steppers → see the Toman price and the owner's card →
upload a receipt photo. Owner side: gets the receipt with تایید / رد / بلاک / آنبلاک /
مدیریت کاربر buttons. Approval credits the resources (game.purchase).
"""

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes, MessageHandler, filters

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
_RES_TITLE = {"coins": "🪙 طلا", "dna": "🧬 دی‌ان‌ای (DNA)", "diamonds": "💎 الماس"}
_RES_UNIT_WORD = {"coins": "طلا", "dna": "عدد", "diamonds": "عدد"}


def _amount_screen(context) -> tuple[str, InlineKeyboardMarkup]:
    prices = botconfig.get_buy_prices()
    amounts = _amounts(context)
    sellable = _sellable(prices)
    lines = [
        "🛒 <b>خرید درون‌بازی</b>",
        _RULE,
        "مقدار مورد نظرت رو با دکمه‌های ➖ و ➕ تنظیم کن:",
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
            btn(f"{_RES_TITLE[res]}", style=NAV, callback_data="buy_noop"),
            btn(f"➕ {step:,}", style=CONFIRM, callback_data=f"buy_adj:{res}:+"),
        ])
        rows.append([btn(f"🔢 عدد دلخواه ({purchase.RES_LABEL[res]})", style=NAV, callback_data=f"buy_custom:{res}")])
    total = purchase.price_for(amounts["coins"], amounts["dna"], amounts["diamonds"])
    minimum = botconfig.get_buy_min()
    lines += [_RULE, f"💳 <b>مبلغ قابل پرداخت: {total:,} تومان</b>"]
    if minimum > 0:
        lines.append(f"<i>حداقل خرید: {minimum:,} تومان</i>")
    if total > 0 and total >= minimum:
        rows.append([btn("✅ ثبت و مشاهده‌ی کارت", emoji_key="btn_confirm", style=PRIMARY, callback_data="buy_submit")])
    elif total > 0 and minimum > 0:
        lines.append(f"⚠️ <b>برای ثبت خرید حداقل {minimum:,} تومان لازمه</b> — کمی بیشتر انتخاب کن.")
    if total > 0:
        rows.append([btn("♻️ صفر کردن", style=DANGER, callback_data="buy_reset")])
    rows.append([back_btn("menu:me", "بازگشت به منو")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def _store_screen(packs: list[dict], custom_ok: bool) -> tuple[str, InlineKeyboardMarkup]:
    """The in-bot store home: owner-authored packs (one-tap buy) + a «مقدار دلخواه»
    option for the classic stepper flow."""
    lines = ["🛒 <b>فروشگاه درون‌بازی</b>", _RULE]
    rows = []
    if packs:
        lines.append("یکی از پک‌های آماده رو انتخاب کن یا مقدار دلخواه بساز:")
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
            label = f"{p['emoji']} {p['title']} — {p['price']:,}ت"
            rows.append([btn(label, style=SHOP, callback_data=f"buy_pack:{p['id']}")])
    else:
        lines.append("مقدار مورد نظرت رو بساز و پرداخت کن:")
    if custom_ok:
        rows.append([btn("🔢 مقدار دلخواه (خودم انتخاب می‌کنم)", style=NAV, callback_data="buy_custom_home")])
    rows.append([back_btn("menu:me", "بازگشت به منو")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def _store_state_sync() -> tuple[list[dict], bool]:
    return purchase.list_active_packs(), botconfig.inbot_purchase_ready()


async def buy_open_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not botconfig.store_ready():
        await query.answer("خرید درون‌بازی هنوز فعال نشده.", show_alert=True)
        return
    context.user_data[_AMOUNTS_KEY] = {"coins": 0, "dna": 0, "diamonds": 0}
    context.user_data.pop(_AWAIT_RECEIPT_KEY, None)
    await query.answer()
    packs, custom_ok = await run_db(_store_state_sync)
    if packs:
        text, kb = _store_screen(packs, custom_ok)
    else:
        # no packs → go straight to the classic stepper screen (preserves old behaviour)
        text, kb = _amount_screen(context)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=kb)


async def buy_custom_home_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """«مقدار دلخواه» — open the classic stepper amount screen."""
    query = update.callback_query
    if not botconfig.inbot_purchase_ready():
        await query.answer("خرید مقدار دلخواه فعال نیست.", show_alert=True)
        return
    context.user_data[_AMOUNTS_KEY] = {"coins": 0, "dna": 0, "diamonds": 0}
    await query.answer()
    text, kb = _amount_screen(context)
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
        [btn("✅ خرید این پک", emoji_key="btn_confirm", style=PRIMARY, callback_data=f"buy_pack_go:{p['id']}")],
        [back_btn("buy_open", "بازگشت به فروشگاه")],
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
        await query.answer(str(exc), show_alert=True)
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
    text, kb = _amount_screen(context)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=kb)


async def buy_reset_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data[_AMOUNTS_KEY] = {"coins": 0, "dna": 0, "diamonds": 0}
    await update.callback_query.answer("صفر شد.")
    text, kb = _amount_screen(context)
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
    text, kb = _amount_screen(context)
    await message.reply_text(text, parse_mode="HTML", reply_markup=kb)


def _create_pending_sync(tg_user, coins, dna, diamonds):
    user, _ = get_or_create_user(tg_user)
    return purchase.create_pending(user, coins, dna, diamonds)


def _receipt_screen(req) -> tuple[str, InlineKeyboardMarkup]:
    """The «pay & send receipt» screen for a pending purchase — shared by the custom
    stepper flow and the one-tap pack flow."""
    card_number, holder = botconfig.get_buy_card()
    lines = [
        "🧾 <b>پرداخت و ارسال رسید</b>",
        "",
        f"سفارش تو: {purchase.request_summary(req)}",
        f"💰 مبلغ: <b>{req.price_toman:,} تومان</b>",
        "",
        "💳 <b>مبلغ رو به این کارت واریز کن:</b>",
        f"<code>{card_number}</code>",
    ]
    if holder:
        lines.append(f"به نام: <b>{holder}</b>")
    lines += [
        "",
        "📸 بعد از واریز، <b>عکس رسید</b> رو همین‌جا بفرست تا برای تأیید ارسال بشه.",
        "<i>پس از تأیید توسط پشتیبانی، موجودی بلافاصله به حسابت اضافه می‌شه.</i>",
    ]
    kb = InlineKeyboardMarkup([[btn("انصراف", style=BACK, callback_data="buy_open")]])
    return "\n".join(lines), kb


async def buy_submit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    amounts = _amounts(context)
    try:
        req = await run_db(_create_pending_sync, update.effective_user,
                           amounts["coins"], amounts["dna"], amounts["diamonds"])
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
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


async def receipt_photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A photo sent in private WHILE a purchase is awaiting its receipt is treated as
    that receipt. Otherwise ignored (so ordinary photos aren't captured)."""
    req_id = context.user_data.get(_AWAIT_RECEIPT_KEY)
    if req_id is None:
        return
    message = update.effective_message
    if message is None or not message.photo:
        return
    file_id = message.photo[-1].file_id  # largest size
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
        [btn("✅ تأیید", style=CONFIRM, callback_data=f"buyok:{req.id}"),
         btn("❌ رد", style=DANGER, callback_data=f"buyno:{req.id}")],
        [btn("⛔ بلاک رسید", style=DANGER, callback_data=f"buyblk:{user.id}"),
         btn("♻️ آنبلاک", style=NAV, callback_data=f"buyunblk:{user.id}")],
        [btn("👤 مدیریت کاربر", style=ADMIN, callback_data=f"buymgr:{user.id}")],
    ])
    try:
        await context.bot.send_photo(chat_id=OWNER_TELEGRAM_ID, photo=file_id,
                                     caption=base_caption, parse_mode="HTML", reply_markup=kb)
    except Exception:  # noqa: BLE001 — never fail the user's flow over a delivery hiccup
        pass

    # also post a report to the configured purchase-report channel (no buttons — review
    # happens in the owner's DM; the channel message is edited on approve/reject)
    from game import botconfig

    channel_id = botconfig.get_buy_channel_id()
    if channel_id:
        try:
            msg = await context.bot.send_photo(
                chat_id=channel_id, photo=file_id,
                caption=base_caption + "\n\n⏳ <b>وضعیت: در حال انتظار</b>", parse_mode="HTML",
            )
            await run_db(purchase.set_channel_message, req.id, channel_id, msg.message_id)
        except Exception:  # noqa: BLE001 — a channel delivery hiccup must not break the flow
            pass


# ── owner: review actions ─────────────────────────────────────────────────────
async def _notify_user(context, user_id: int, text: str) -> None:
    try:
        await context.bot.send_message(chat_id=user_id, text=text, parse_mode="HTML")
    except Exception:  # noqa: BLE001
        pass


async def _update_channel_status(context, res: dict, status_html: str) -> None:
    """Edit the purchase-report channel message so its status reflects approve/reject."""
    chat_id, msg_id = res.get("channel_chat_id"), res.get("channel_message_id")
    if not chat_id or not msg_id:
        return
    bits = []
    if res.get("subscription_tier"):
        from game.subscription import SUBSCRIPTION_TIERS
        sub = SUBSCRIPTION_TIERS.get(res["subscription_tier"])
        name = sub["name"] if sub else res["subscription_tier"]
        badge = sub["badge"] if sub else "⭐"
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
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer("✅ تأیید شد و اعمال شد.")
    bits = []
    if res.get("subscription_tier"):
        from game.subscription import SUBSCRIPTION_TIERS
        sub = SUBSCRIPTION_TIERS.get(res["subscription_tier"])
        name = sub["name"] if sub else res["subscription_tier"]
        badge = sub["badge"] if sub else "⭐"
        bits.append(f"{badge} <b>{name} (۳۰ روزه)</b> فعال شد! 🎉")
    if res["coins"]:
        bits.append(f"{res['coins']:,} {get_emoji('coin')}")
    if res["dna"]:
        bits.append(f"{res['dna']:,} {get_emoji('dna')}")
    if res["diamonds"]:
        bits.append(f"{res['diamonds']:,} {get_emoji('diamond')}")
    notify_text = "✅ <b>خریدت تأیید شد!</b>\n🎁 " + ("\n".join(bits) if res.get("subscription_tier") else ("به حسابت اضافه شد: " + " · ".join(bits)))
    await _notify_user(
        context, res["user_id"],
        notify_text,
    )
    if query.message is not None and query.message.caption is not None:
        await query.edit_message_caption(
            caption=(query.message.caption or "") + "\n\n✅ <b>تأیید شد.</b>", parse_mode="HTML"
        )
    await _update_channel_status(context, res, "✅ <b>وضعیت: تأیید شد</b>")


async def buy_reject_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_reviewer(update):
        await query.answer()
        return
    req_id = int(query.data.split(":")[1])
    try:
        res = await run_db(purchase.reject, req_id)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer("❌ رد شد.")
    await _notify_user(
        context, res["user_id"],
        "❌ <b>رسید خریدت تأیید نشد.</b> اگه فکر می‌کنی اشتباهی رخ داده، با پشتیبانی در تماس باش.",
    )
    if query.message is not None and query.message.caption is not None:
        await query.edit_message_caption(
            caption=(query.message.caption or "") + "\n\n❌ <b>رد شد.</b>", parse_mode="HTML"
        )
    await _update_channel_status(context, res, "❌ <b>وضعیت: رد شد</b>")


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
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer("⛔ بلاک شد." if block else "♻️ آنبلاک شد.")
    if query.message is not None:
        tag = "⛔ <b>ثبت رسید این کاربر بلاک شد.</b>" if block else "♻️ <b>بلاک رسید برداشته شد.</b>"
        await query.edit_message_caption(
            caption=(query.message.caption or "") + f"\n\n{tag}", parse_mode="HTML"
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
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer()
    u = data["user"]
    # the review message is a photo; a fresh text message is cleaner than editing a caption
    await context.bot.send_message(
        chat_id=query.message.chat_id, text=_user_info_text(data), parse_mode="HTML",
        reply_markup=_user_manage_keyboard(u.id, u.is_banned),
    )


def register(application) -> None:
    application.add_handler(CallbackQueryHandler(buy_open_callback, pattern=r"^buy_open$"))
    application.add_handler(CallbackQueryHandler(buy_custom_home_callback, pattern=r"^buy_custom_home$"))
    application.add_handler(CallbackQueryHandler(buy_pack_detail_callback, pattern=r"^buy_pack:\d+$"))
    application.add_handler(CallbackQueryHandler(buy_pack_go_callback, pattern=r"^buy_pack_go:\d+$"))
    application.add_handler(CallbackQueryHandler(buy_noop_callback, pattern=r"^buy_noop$"))
    application.add_handler(CallbackQueryHandler(buy_adjust_callback, pattern=r"^buy_adj:(coins|dna|diamonds):[+-]$"))
    application.add_handler(CallbackQueryHandler(buy_custom_callback, pattern=r"^buy_custom:(coins|dna|diamonds)$"))
    application.add_handler(CallbackQueryHandler(buy_reset_callback, pattern=r"^buy_reset$"))
    application.add_handler(CallbackQueryHandler(buy_submit_callback, pattern=r"^buy_submit$"))
    application.add_handler(CallbackQueryHandler(buy_approve_callback, pattern=r"^buyok:\d+$"))
    application.add_handler(CallbackQueryHandler(buy_reject_callback, pattern=r"^buyno:\d+$"))
    application.add_handler(CallbackQueryHandler(buy_block_callback, pattern=r"^(buyblk|buyunblk):\d+$"))
    application.add_handler(CallbackQueryHandler(buy_manage_callback, pattern=r"^buymgr:\d+$"))
    # receipt photo (private only), lower priority so it doesn't shadow other photo flows
    application.add_handler(
        MessageHandler(filters.PHOTO & filters.ChatType.PRIVATE, receipt_photo_handler), group=1
    )

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


def _amount_screen(context) -> tuple[str, InlineKeyboardMarkup]:
    prices = botconfig.get_buy_prices()
    amounts = _amounts(context)
    sellable = _sellable(prices)
    lines = [
        "🛒 <b>خرید درون‌بازی</b>",
        "با دکمه‌های ➖ و ➕ مقداری که می‌خوای بخری رو تنظیم کن:",
        "",
    ]
    rows = []
    for res in sellable:
        amt = amounts.get(res, 0)
        unit = prices[res]
        lines.append(
            f"{purchase.RES_EMOJI[res]} {purchase.RES_LABEL[res]}: <b>{amt:,}</b>"
            f"  <i>(هر {purchase.STEP[res]:,} = {round(unit * purchase.STEP[res]):,} تومان)</i>"
        )
        rows.append([
            btn(f"➖ {purchase.STEP[res]:,}", style=NAV, callback_data=f"buy_adj:{res}:-"),
            btn(f"{purchase.RES_EMOJI[res]} {purchase.RES_LABEL[res]}", style=NAV, callback_data="buy_noop"),
            btn(f"➕ {purchase.STEP[res]:,}", style=CONFIRM, callback_data=f"buy_adj:{res}:+"),
        ])
    total = purchase.price_for(amounts["coins"], amounts["dna"], amounts["diamonds"])
    lines += ["", f"💰 <b>مبلغ قابل پرداخت: {total:,} تومان</b>"]
    if total > 0:
        rows.append([btn("✅ ثبت و مشاهده‌ی کارت", emoji_key="btn_confirm", style=PRIMARY, callback_data="buy_submit")])
        rows.append([btn("♻️ صفر کردن", style=DANGER, callback_data="buy_reset")])
    rows.append([back_btn("menu:me", "بازگشت به منو")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def buy_open_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not botconfig.inbot_purchase_ready():
        await query.answer("خرید درون‌بازی هنوز فعال نشده.", show_alert=True)
        return
    context.user_data[_AMOUNTS_KEY] = {"coins": 0, "dna": 0, "diamonds": 0}
    context.user_data.pop(_AWAIT_RECEIPT_KEY, None)
    await query.answer()
    text, kb = _amount_screen(context)
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


def _create_pending_sync(tg_user, coins, dna, diamonds):
    user, _ = get_or_create_user(tg_user)
    return purchase.create_pending(user, coins, dna, diamonds)


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
    card_number, holder = botconfig.get_buy_card()
    await query.answer()
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
    await safe_edit_message_text(query, "\n".join(lines), parse_mode="HTML", reply_markup=kb)


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
    caption = (
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
        await context.bot.send_photo(chat_id=OWNER_TELEGRAM_ID, photo=file_id, caption=caption,
                                     parse_mode="HTML", reply_markup=kb)
    except Exception:  # noqa: BLE001 — never fail the user's flow over a delivery hiccup
        pass


# ── owner: review actions ─────────────────────────────────────────────────────
async def _notify_user(context, user_id: int, text: str) -> None:
    try:
        await context.bot.send_message(chat_id=user_id, text=text, parse_mode="HTML")
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
    await query.answer("✅ تأیید شد و موجودی اضافه شد.")
    bits = []
    if res["coins"]:
        bits.append(f"{res['coins']:,} {get_emoji('coin')}")
    if res["dna"]:
        bits.append(f"{res['dna']:,} {get_emoji('dna')}")
    if res["diamonds"]:
        bits.append(f"{res['diamonds']:,} {get_emoji('diamond')}")
    await _notify_user(
        context, res["user_id"],
        "✅ <b>خریدت تأیید شد!</b>\n🎁 به حسابت اضافه شد: " + " · ".join(bits),
    )
    if query.message is not None:
        await query.edit_message_caption(
            caption=(query.message.caption or "") + "\n\n✅ <b>تأیید شد.</b>", parse_mode="HTML"
        )


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
    if query.message is not None:
        await query.edit_message_caption(
            caption=(query.message.caption or "") + "\n\n❌ <b>رد شد.</b>", parse_mode="HTML"
        )


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
    application.add_handler(CallbackQueryHandler(buy_noop_callback, pattern=r"^buy_noop$"))
    application.add_handler(CallbackQueryHandler(buy_adjust_callback, pattern=r"^buy_adj:(coins|dna|diamonds):[+-]$"))
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

"""«مبادله» — the gold ↔ DNA exchange screens.

Flow: the opener first picks WHICH currency to buy (طلا / دی‌ان‌ای), then an amount —
a quick-pick preset or a free-form custom number — then a final confirm. Works in
the DM and (scoped) in a group: every button carries the opener's id, so a keyboard
visible to a whole group can only be driven by the person who opened it. The swap
itself is a single atomic, balance-checked, row-locked update in game/exchange.py,
re-checked at the moment of confirmation (a stale/spammed tap can't over-convert).

Directions (as the user experiences them):
* «دی‌ان‌ای»  → buy_dna  (pay gold, get DNA)
* «طلا»       → buy_gold (pay DNA, get gold)
"""

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

from bio_lab.repository import get_or_create_user
from bot.buttons import BUILD, CONFIRM, DANGER, NAV, PRIMARY, SHOP, back_btn, btn
from bot.utils import run_db, safe_edit_message_text, send_screen
from config import BOT_USERNAME
from game import exchange
from game.creature import GameError
from game.emoji import get_emoji

_DISABLED_MSG = "🔄 مبادله فعلاً غیرفعاله."


def _bal_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    return user.coins, user.dna_fragments


def _is_group(update: Update) -> bool:
    chat = update.effective_chat
    return chat is not None and chat.type in ("group", "supergroup")


def _guard_owner(update: Update, oid) -> bool:
    return update.effective_user is not None and update.effective_user.id == int(oid)


def _leave_row(in_group: bool):
    if in_group:
        return [btn("برو به پیوی ربات", style=PRIMARY, url=f"https://t.me/{BOT_USERNAME}?start=group")]
    return [back_btn("menu:cat_shop", "بازگشت به فروشگاه")]


# ── screen 1: which currency to buy? ─────────────────────────────────────────
def _home_render(oid: int, coins: int, dna: int, in_group: bool) -> tuple[str, InlineKeyboardMarkup]:
    text = (
        f"{get_emoji('coin')} <b>مبادله‌ی طلا و DNA</b>\n"
        f"<blockquote>موجودی: {get_emoji('coin')} <b>{coins:,}</b> طلا · "
        f"{get_emoji('dna')} <b>{dna:,}</b> DNA\n"
        f"نرخ: هر <b>{exchange.GOLD_PER_DNA_BUY}</b> طلا = ۱ DNA · "
        f"هر ۱ DNA = <b>{exchange.GOLD_PER_DNA_SELL}</b> طلا</blockquote>\n\n"
        "می‌خوای کدوم رو بخری؟"
    )
    rows = [
        [btn("💰 طلا", style=BUILD, callback_data=f"exch:pick:buy_gold:{oid}")],
        [btn("🧬 دی‌ان‌ای (DNA)", style=SHOP, callback_data=f"exch:pick:buy_dna:{oid}")],
        _leave_row(in_group),
    ]
    return text, InlineKeyboardMarkup(rows)


# ── screen 2: pick an amount for the chosen direction ────────────────────────
def _dir_labels(direction: str) -> tuple[str, str]:
    """(what you're buying, what you're paying-with) in Persian."""
    if direction == "buy_gold":
        return "طلا", "DNA"
    return "DNA", "طلا"


def _amount_render(oid: int, direction: str, coins: int, dna: int, in_group: bool) -> tuple[str, InlineKeyboardMarkup]:
    buying, paying = _dir_labels(direction)
    if direction == "buy_gold":
        rate_line = f"هر ۱ DNA که بدی → <b>{exchange.GOLD_PER_DNA_SELL}</b> طلا می‌گیری."
        prompt = "چند DNA می‌خوای بدی؟"
    else:
        rate_line = f"هر ۱ DNA → <b>{exchange.GOLD_PER_DNA_BUY}</b> طلا می‌دی."
        prompt = "چند DNA می‌خوای بگیری؟"
    lines = [
        f"🔄 <b>خرید {buying}</b>  <i>(با دادن {paying})</i>",
        f"<blockquote>{rate_line}\n"
        f"موجودی: {get_emoji('coin')} <b>{coins:,}</b> طلا · {get_emoji('dna')} <b>{dna:,}</b> DNA</blockquote>",
        "",
        prompt,
    ]
    rows = []
    for amt in exchange.PRESET_DNA:
        pack = exchange.describe(direction, amt)
        if direction == "buy_gold":
            label = f"🧬 {amt} DNA  →  💰 {pack['gold']:,} طلا"
        else:
            label = f"💰 {pack['gold']:,} طلا  →  🧬 {amt} DNA"
        rows.append([btn(label, style=BUILD, callback_data=f"exch:amt:{direction}:{amt}:{oid}")])
    rows.append([btn("✏️ عدد دلخواه", style=NAV, callback_data=f"exch:custom:{direction}:{oid}")])
    rows.append([btn("↩️ بازگشت", style=NAV, callback_data=f"exch:home:{oid}")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


# ── screen 3: confirm ────────────────────────────────────────────────────────
def build_confirm(oid: int, pack: dict, coins: int, dna: int) -> tuple[str, InlineKeyboardMarkup]:
    d = pack["direction"]
    if d == "buy_dna":
        deal = (f"<b>{pack['dna']:,}</b> {get_emoji('dna')} DNA می‌گیری و "
                f"<b>{pack['gold']:,}</b> {get_emoji('coin')} طلا می‌دی.")
    else:
        deal = (f"<b>{pack['gold']:,}</b> {get_emoji('coin')} طلا می‌گیری و "
                f"<b>{pack['dna']:,}</b> {get_emoji('dna')} DNA می‌دی.")
    text = (
        "🔄 <b>تأیید مبادله</b>\n"
        f"<blockquote>{deal}\n\n"
        f"موجودی فعلی: {coins:,} طلا · {dna:,} DNA</blockquote>"
    )
    rows = [[
        btn("تأیید", emoji_key="btn_confirm", style=CONFIRM, callback_data=f"exchgo:{d}:{pack['dna']}:{oid}"),
        btn("لغو", emoji_key="btn_cancel", style=DANGER, callback_data=f"exch:pick:{d}:{oid}"),
    ]]
    return text, InlineKeyboardMarkup(rows)


# ── entry point ──────────────────────────────────────────────────────────────
async def exchange_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """PV command / menu button / group «مبادله» word → the 'buy which?' screen."""
    if not exchange.ENABLED:
        await send_screen(update, _DISABLED_MSG, parse_mode="HTML")
        return
    coins, dna = await run_db(_bal_sync, update.effective_user)
    text, keyboard = _home_render(update.effective_user.id, coins, dna, _is_group(update))
    await send_screen(update, text, parse_mode="HTML", reply_markup=keyboard)


# ── navigation callbacks (exch:…) ────────────────────────────────────────────
async def exchange_nav_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not exchange.ENABLED:
        await query.answer("مبادله غیرفعال شده.", show_alert=True)
        return
    parts = query.data.split(":")
    step = parts[1]
    oid = parts[-1]
    if not _guard_owner(update, oid):
        await query.answer("این پنل مال تو نیست — خودت «مبادله» رو بفرست.", show_alert=True)
        return
    coins, dna = await run_db(_bal_sync, update.effective_user)

    if step == "home":
        await query.answer()
        text, kb = _home_render(int(oid), coins, dna, _is_group(update))
        await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=kb)
        return

    if step == "pick":
        direction = parts[2]
        if direction not in exchange.DIRECTIONS:
            await query.answer("جهت نامعتبر.", show_alert=True)
            return
        await query.answer()
        text, kb = _amount_render(int(oid), direction, coins, dna, _is_group(update))
        await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=kb)
        return

    if step == "amt":
        direction, amt = parts[2], int(parts[3])
        try:
            pack = exchange.describe(direction, amt)
        except GameError as exc:
            await query.answer(str(exc), show_alert=True)
            return
        await query.answer()
        text, kb = build_confirm(int(oid), pack, coins, dna)
        await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=kb)
        return

    if step == "custom":
        direction = parts[2]
        if direction not in exchange.DIRECTIONS:
            await query.answer("جهت نامعتبر.", show_alert=True)
            return
        # remember what the next number this user types is for (works in PV, and in a
        # group when they REPLY to this prompt — see group_words._maybe_capture...)
        from bot.handlers.private import AWAITING_PLAYER_KEY

        context.user_data[AWAITING_PLAYER_KEY] = {
            "action": "exchange_custom", "direction": direction, "oid": str(oid),
        }
        await query.answer()
        buying, _paying = _dir_labels(direction)
        hint = "همینجا عدد رو بفرست." if not _is_group(update) else "روی همین پیام <b>ریپلای</b> کن و عدد رو بفرست."
        await safe_edit_message_text(
            query,
            f"✏️ چند <b>DNA</b> برای «خرید {buying}»؟\n{hint}\n<i>مثلاً <code>120</code></i>",
            parse_mode="HTML",
        )
        return


# ── execute (exchgo:…) ───────────────────────────────────────────────────────
def _do_sync(tg_user, direction, dna):
    user, _ = get_or_create_user(tg_user)
    return exchange.exchange(user, direction, dna)


async def exchange_do_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not exchange.ENABLED:
        await query.answer("مبادله غیرفعال شده.", show_alert=True)
        return
    _, direction, dna, oid = query.data.split(":")
    if not _guard_owner(update, oid):
        await query.answer("این پنل مال تو نیست — خودت «مبادله» رو بفرست.", show_alert=True)
        return
    try:
        result = await run_db(_do_sync, update.effective_user, direction, int(dna))
    except GameError as exc:
        # balance changed since the button was drawn → back to the home screen with
        # the error, so a stale/spammed tap fails cleanly instead of over-converting
        await query.answer(str(exc), show_alert=True)
        coins, dbal = await run_db(_bal_sync, update.effective_user)
        text, kb = _home_render(int(oid), coins, dbal, _is_group(update))
        await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=kb)
        return
    if direction == "buy_dna":
        done = f"✅ <b>{result['dna']:,} DNA گرفتی</b> ({result['gold']:,} طلا دادی)."
    else:
        done = f"✅ <b>{result['gold']:,} طلا گرفتی</b> ({result['dna']:,} DNA دادی)."
    await query.answer("✅ انجام شد!")
    text, kb = _home_render(int(oid), result["new_coins"], result["new_dna"], _is_group(update))
    await safe_edit_message_text(query, f"{done}\n━━━━━━━━━━\n{text}", parse_mode="HTML", reply_markup=kb)


# ── custom-amount capture (called from capture_player_text_reply) ─────────────
async def handle_custom_amount(update: Update, context: ContextTypes.DEFAULT_TYPE, awaiting: dict) -> None:
    """The user typed a number after tapping «عدد دلخواه». Validate it and show the
    confirm screen (the actual swap still happens on the confirm button, atomically).
    Re-arms the awaiting flag on bad input so the next number is still captured."""
    from bot.handlers.private import AWAITING_PLAYER_KEY

    direction = awaiting.get("direction")
    oid = awaiting.get("oid")
    message = update.effective_message
    raw = (message.text or "").strip()
    norm = raw.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
    if not norm.isdigit() or int(norm) <= 0:
        context.user_data[AWAITING_PLAYER_KEY] = awaiting  # keep waiting
        await message.reply_text("⚠️ یه عدد درست بفرست (مثلاً 120).")
        return
    try:
        pack = exchange.describe(direction, int(norm))
    except GameError as exc:
        context.user_data[AWAITING_PLAYER_KEY] = awaiting
        await message.reply_text(str(exc))
        return
    coins, dbal = await run_db(_bal_sync, update.effective_user)
    text, kb = build_confirm(int(oid), pack, coins, dbal)
    await message.reply_text(text, parse_mode="HTML", reply_markup=kb)


def register(application) -> None:
    application.add_handler(CommandHandler("exchange", exchange_panel, filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(exchange_nav_callback, pattern=r"^exch:"))
    application.add_handler(CallbackQueryHandler(exchange_do_callback, pattern=r"^exchgo:"))

"""«مبادله» — the gold ↔ DNA exchange screen.

Works in the DM and (scoped) in a group. Every button carries the owner's id, so a
keyboard visible to a whole group can only be driven by the person who opened it.
The actual swap is a single atomic, balance-checked, row-locked update in
game/exchange.py — the confirm step re-checks funds at the moment of confirmation,
so a stale button or a spammed tap can never over-convert.
"""

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

from bio_lab.repository import get_or_create_user
from bot.buttons import BUILD, CONFIRM, DANGER, PRIMARY, SHOP, back_btn, btn
from bot.utils import run_db, safe_edit_message_text, send_screen
from config import BOT_USERNAME
from game import exchange
from game.creature import GameError
from game.emoji import get_emoji


def _bal_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    return user.coins, user.dna_fragments


def _leave_row(in_group: bool):
    if in_group:
        return [btn("برو به پیوی ربات", style=PRIMARY, url=f"https://t.me/{BOT_USERNAME}?start=group")]
    return [back_btn("menu:cat_shop", "بازگشت به فروشگاه")]


def _panel_render(oid: int, coins: int, dna: int, in_group: bool) -> tuple[str, InlineKeyboardMarkup]:
    lines = [
        f"{get_emoji('coin')} <b>مبادله‌ی طلا و DNA</b>",
        f"<blockquote>موجودی: {get_emoji('coin')} <b>{coins:,}</b> طلا · "
        f"{get_emoji('dna')} <b>{dna:,}</b> DNA\n"
        f"نرخ: خرید هر DNA <b>{exchange.GOLD_PER_DNA_BUY}</b> طلا · "
        f"فروش هر DNA <b>{exchange.GOLD_PER_DNA_SELL}</b> طلا</blockquote>",
        "",
        f"{get_emoji('dna')} <b>خرید DNA با طلا</b>",
    ]
    rows = []
    for i, amt in enumerate(exchange.BUY_PACKS):
        gold = exchange.buy_gold_cost(amt)
        lines.append(f"• <b>{amt}</b> DNA — {gold:,} طلا")
        rows.append([btn(f"🧬 {amt} DNA  ←  {gold:,} طلا", style=BUILD, callback_data=f"exch:buy:{i}:{oid}")])
    lines.append(f"\n{get_emoji('coin')} <b>فروش DNA برای طلا</b>")
    for i, amt in enumerate(exchange.SELL_PACKS):
        gold = exchange.sell_gold_gain(amt)
        lines.append(f"• <b>{amt}</b> DNA — {gold:,} طلا")
        rows.append([btn(f"💰 {gold:,} طلا  ←  {amt} DNA", style=SHOP, callback_data=f"exch:sell:{i}:{oid}")])
    rows.append(_leave_row(in_group))
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def _confirm_render(oid: int, pack: dict, coins: int, dna: int) -> tuple[str, InlineKeyboardMarkup]:
    d, idx = pack["direction"], pack["idx"]
    if d == "buy":
        deal = (f"<b>{pack['dna']}</b> {get_emoji('dna')} DNA می‌خری و "
                f"<b>{pack['gold']:,}</b> {get_emoji('coin')} طلا می‌دی.")
    else:
        deal = (f"<b>{pack['dna']}</b> {get_emoji('dna')} DNA می‌فروشی و "
                f"<b>{pack['gold']:,}</b> {get_emoji('coin')} طلا می‌گیری.")
    text = (
        "🔄 <b>تأیید مبادله</b>\n"
        f"<blockquote>{deal}\n\n"
        f"موجودی فعلی: {coins:,} طلا · {dna:,} DNA</blockquote>"
    )
    rows = [[
        btn("تأیید", emoji_key="btn_confirm", style=CONFIRM, callback_data=f"exchgo:{d}:{idx}:{oid}"),
        btn("لغو", emoji_key="btn_cancel", style=DANGER, callback_data=f"exch:home:{oid}"),
    ]]
    return text, InlineKeyboardMarkup(rows)


def _is_group(update: Update) -> bool:
    chat = update.effective_chat
    return chat is not None and chat.type in ("group", "supergroup")


async def exchange_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Entry point: PV command / menu button / group «مبادله» word."""
    coins, dna = await run_db(_bal_sync, update.effective_user)
    text, keyboard = _panel_render(update.effective_user.id, coins, dna, _is_group(update))
    await send_screen(update, text, parse_mode="HTML", reply_markup=keyboard)


def _guard_owner(update: Update, oid: str) -> bool:
    return update.effective_user is not None and update.effective_user.id == int(oid)


async def exchange_nav_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`exch:home:<oid>` re-renders the panel; `exch:<dir>:<idx>:<oid>` shows confirm."""
    query = update.callback_query
    parts = query.data.split(":")
    # exch:home:<oid>  OR  exch:<dir>:<idx>:<oid>
    oid = parts[-1]
    if not _guard_owner(update, oid):
        await query.answer("این پنل مال تو نیست — خودت «مبادله» رو بفرست.", show_alert=True)
        return
    coins, dna = await run_db(_bal_sync, update.effective_user)
    if parts[1] == "home":
        await query.answer()
        text, keyboard = _panel_render(int(oid), coins, dna, _is_group(update))
        await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)
        return
    direction, idx = parts[1], int(parts[2])
    try:
        pack = exchange.describe(direction, idx)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer()
    text, keyboard = _confirm_render(int(oid), pack, coins, dna)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


def _do_sync(tg_user, direction, idx):
    user, _ = get_or_create_user(tg_user)
    return exchange.exchange(user, direction, idx)


async def exchange_do_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`exchgo:<dir>:<idx>:<oid>` — the final confirm; performs the atomic swap."""
    query = update.callback_query
    _, direction, idx, oid = query.data.split(":")
    if not _guard_owner(update, oid):
        await query.answer("این پنل مال تو نیست — خودت «مبادله» رو بفرست.", show_alert=True)
        return
    try:
        result = await run_db(_do_sync, update.effective_user, direction, int(idx))
    except GameError as exc:
        # balance changed since the button was drawn → re-render the panel with the
        # error, so a stale/spammed tap fails cleanly instead of over-converting
        await query.answer(str(exc), show_alert=True)
        coins, dna = await run_db(_bal_sync, update.effective_user)
        text, keyboard = _panel_render(int(oid), coins, dna, _is_group(update))
        await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)
        return
    if direction == "buy":
        done = f"✅ <b>{result['dna']} DNA خریدی</b> ({result['gold']:,} طلا دادی)."
    else:
        done = f"✅ <b>{result['dna']} DNA فروختی</b> ({result['gold']:,} طلا گرفتی)."
    await query.answer("✅ انجام شد!")
    text, keyboard = _panel_render(int(oid), result["new_coins"], result["new_dna"], _is_group(update))
    await safe_edit_message_text(query, f"{done}\n━━━━━━━━━━\n{text}", parse_mode="HTML", reply_markup=keyboard)


def register(application) -> None:
    application.add_handler(CommandHandler("exchange", exchange_panel, filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(exchange_nav_callback, pattern=r"^exch:"))
    application.add_handler(CallbackQueryHandler(exchange_do_callback, pattern=r"^exchgo:"))

"""🎟 مبادله تجهیزات با بلیط — trade spare legendary/mythic gear for genetic-box tickets.

Rarity-tabbed, paginated multi-select panel: filter by rarity (اساطیری/افسانه‌ای), page
through the list, toggle items (or «انتخاب همه»), then «تبدیل». Equipped gear is never
listed. If any picked item is above +1, a confirm step names those items and their levels
first, so an upgraded piece isn't scrapped by accident.
"""

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes

from bio_lab.repository import get_or_create_user
from bot.buttons import BUILD, CONFIRM, DANGER, LIST, NAV, back_btn, btn
from bot.utils import run_db, safe_edit_message_text, send_screen
from game import constants
from game.creature import GameError
from game.emoji import get_emoji
from game.equipment import exchangeable_equipment, exchange_for_tickets, ticket_value

_SEL_KEY = "etx_sel"     # set[int] of selected equipment ids
_FILT_KEY = "etx_filt"   # rarity filter: "all" | "mythic" | "legendary"
_PAGE_KEY = "etx_page"
_PAGE_SIZE = 8
# rarity tabs, rarest first (only the ticket-worthy rarities)
_TABS = ["all"] + list(reversed(list(constants.EQUIP_TICKET_VALUE)))  # ["all","mythic","legendary"]


def _sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    return user.biocrate_tickets, exchangeable_equipment(user)


def _selection(context) -> set:
    sel = context.user_data.get(_SEL_KEY)
    if not isinstance(sel, set):
        sel = set()
        context.user_data[_SEL_KEY] = sel
    return sel


def _back_button(update: Update):
    """The «بازگشت» button target: in a group the panel was opened from «مبادله», so it
    returns to that exchange home (an exch: callback that works in groups); in the DM it
    goes back to the shop category. Fixes the dead back button reported in groups."""
    chat = update.effective_chat
    if chat is not None and chat.type in ("group", "supergroup"):
        return btn("↩️ بازگشت به مبادله", emoji_key="btn_back", style=NAV,
                   callback_data=f"exch:home:{update.effective_user.id}")
    return back_btn("menu:cat_shop", "بازگشت به فروشگاه")


def _render(tickets, items, selected: set, filt: str, page: int, back=None):
    if back is None:
        back = back_btn("menu:cat_shop", "بازگشت به فروشگاه")
    selected &= {it.id for it in items}  # drop ids that are gone (converted/equipped)
    picked = [it for it in items if it.id in selected]
    gain = sum(ticket_value(it) for it in picked)
    counts = {"all": len(items)}
    for r in constants.EQUIP_TICKET_VALUE:
        counts[r] = sum(1 for it in items if it.rarity == r)
    shown = items if filt == "all" else [it for it in items if it.rarity == filt]
    pages = max(1, (len(shown) + _PAGE_SIZE - 1) // _PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    chunk = shown[page * _PAGE_SIZE:(page + 1) * _PAGE_SIZE]

    lines = [
        "🎟 <b>مبادله تجهیزات با بلیط</b>",
        f"بلیط‌های تو: <b>{tickets}</b> 🎟",
        "",
        "هر تجهیزِ اساطیری = <b>۲</b> بلیط · هر افسانه‌ای = <b>۱</b> بلیط",
        "<i>هر بلیط = یه باکس ژنتیکیِ رایگان. تجهیزاتِ فعال قابل‌مبادله نیستن.</i>",
    ]
    if not items:
        lines += ["", "<i>هیچ تجهیزِ افسانه‌ای یا اساطیریِ غیرفعالی برای مبادله نداری.</i>"]
        return "\n".join(lines), InlineKeyboardMarkup([[back]])
    lines.append(f"\n✅ انتخاب‌شده: <b>{len(picked)}</b> = <b>{gain}</b> 🎟" + (f"  ·  صفحه {page + 1}/{pages}" if pages > 1 else ""))

    rows = []
    # rarity tabs (colored via RARITY_LABELS: 🔴 mythic, 🟠 legendary, …)
    tab_row = []
    for t in _TABS:
        label = f"همه ({counts['all']})" if t == "all" else f"{constants.RARITY_LABELS[t]} ({counts[t]})"
        tab_row.append(btn(("• " if filt == t else "") + label, style=NAV, callback_data=f"etx:tab:{t}"))
    rows.append(tab_row)
    # item toggles for this page
    for it in chunk:
        mark = "✅" if it.id in selected else "⬜️"
        lvl = f" +{it.level}" if it.level > 1 else ""
        rows.append([btn(
            f"{mark} {constants.RARITY_LABELS[it.rarity]} {it.name}{lvl} → {ticket_value(it)}🎟",
            style=LIST, callback_data=f"etx:t:{it.id}",
        )])
    if pages > 1:
        rows.append([
            btn("قبلی", emoji_key="btn_prev", style=NAV, callback_data=f"etx:pg:{page - 1}"),
            btn("بعدی", emoji_key="btn_next", style=NAV, callback_data=f"etx:pg:{page + 1}"),
        ])
    rows.append([
        btn("انتخاب همه", emoji_key="btn_confirm", style=NAV, callback_data="etx:all"),
        btn("پاک‌کردن", emoji_key="btn_cancel", style=NAV, callback_data="etx:clear"),
    ])
    if picked:
        rows.append([btn(f"♻️ تبدیل به {gain} بلیط", emoji_key="btn_confirm", style=BUILD, callback_data="etx:go")])
    rows.append([back])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def equip_exchange_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data[_SEL_KEY] = set()
    context.user_data[_FILT_KEY] = "all"
    context.user_data[_PAGE_KEY] = 0
    tickets, items = await run_db(_sync, update.effective_user)
    text, kb = _render(tickets, items, set(), "all", 0, back=_back_button(update))
    await send_screen(update, text, parse_mode="HTML", reply_markup=kb)


async def _rerender(update, context):
    tickets, items = await run_db(_sync, update.effective_user)
    text, kb = _render(
        tickets, items, _selection(context),
        context.user_data.get(_FILT_KEY, "all"), context.user_data.get(_PAGE_KEY, 0),
        back=_back_button(update),
    )
    await safe_edit_message_text(update.callback_query, text, parse_mode="HTML", reply_markup=kb)


async def etx_toggle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    item_id = int(update.callback_query.data.split(":")[2])
    sel = _selection(context)
    sel.discard(item_id) if item_id in sel else sel.add(item_id)
    await update.callback_query.answer()
    await _rerender(update, context)


async def etx_tab_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data[_FILT_KEY] = update.callback_query.data.split(":")[2]
    context.user_data[_PAGE_KEY] = 0
    await update.callback_query.answer()
    await _rerender(update, context)


async def etx_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data[_PAGE_KEY] = int(update.callback_query.data.split(":")[2])
    await update.callback_query.answer()
    await _rerender(update, context)


async def etx_all_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _tickets, items = await run_db(_sync, update.effective_user)
    context.user_data[_SEL_KEY] = {it.id for it in items}
    await update.callback_query.answer("همه انتخاب شدن.")
    await _rerender(update, context)


async def etx_clear_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data[_SEL_KEY] = set()
    await update.callback_query.answer("انتخاب پاک شد.")
    await _rerender(update, context)


async def etx_go_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _tickets, items = await run_db(_sync, update.effective_user)
    sel = _selection(context) & {it.id for it in items}
    picked = [it for it in items if it.id in sel]
    if not picked:
        await query.answer("چیزی انتخاب نکردی.", show_alert=True)
        return
    gain = sum(ticket_value(it) for it in picked)
    upgraded = [it for it in picked if it.level > 1]
    if upgraded:
        names = "، ".join(f"«{it.name} +{it.level}»" for it in upgraded[:8])
        more = f" و {len(upgraded) - 8} مورد دیگه" if len(upgraded) > 8 else ""
        await query.answer()
        await safe_edit_message_text(
            query,
            f"⚠️ <b>توجه:</b> این تجهیزات ارتقایافته‌ن (لِوِلشون بالای ۱ هست):\n"
            f"{names}{more}\n\n"
            f"با تبدیل، همه‌ی <b>{len(picked)}</b> تجهیزِ انتخاب‌شده <b>حذف</b> می‌شن و "
            f"<b>{gain}</b> 🎟 می‌گیری. مطمئنی؟",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                btn(f"✅ بله، تبدیل کن ({gain}🎟)", emoji_key="btn_confirm", style=CONFIRM, callback_data="etx:confirm"),
                btn("❌ نه", emoji_key="btn_cancel", style=DANGER, callback_data="etx:back"),
            ]]),
        )
        return
    await _do_exchange(update, context)


async def etx_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _do_exchange(update, context)


async def etx_back_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    await _rerender(update, context)


async def _do_exchange(update, context):
    query = update.callback_query
    try:
        result = await run_db(exchange_for_tickets, update.effective_user, list(_selection(context)))
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        await _rerender(update, context)
        return
    context.user_data[_SEL_KEY] = set()
    context.user_data[_PAGE_KEY] = 0
    await query.answer(f"🎟 +{result['tickets']} بلیط!")
    tickets, items = await run_db(_sync, update.effective_user)
    text, kb = _render(tickets, items, set(), context.user_data.get(_FILT_KEY, "all"), 0, back=_back_button(update))
    text = (f"✅ <b>{result['count']}</b> تجهیز تبدیل شد و <b>{result['tickets']}</b> 🎟 گرفتی "
            f"(جمعاً {result['total']} بلیط).\n\n" + text)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=kb)


def register(application) -> None:
    application.add_handler(CallbackQueryHandler(etx_toggle_callback, pattern=r"^etx:t:"))
    application.add_handler(CallbackQueryHandler(etx_tab_callback, pattern=r"^etx:tab:"))
    application.add_handler(CallbackQueryHandler(etx_page_callback, pattern=r"^etx:pg:"))
    application.add_handler(CallbackQueryHandler(etx_all_callback, pattern=r"^etx:all$"))
    application.add_handler(CallbackQueryHandler(etx_clear_callback, pattern=r"^etx:clear$"))
    application.add_handler(CallbackQueryHandler(etx_go_callback, pattern=r"^etx:go$"))
    application.add_handler(CallbackQueryHandler(etx_confirm_callback, pattern=r"^etx:confirm$"))
    application.add_handler(CallbackQueryHandler(etx_back_callback, pattern=r"^etx:back$"))

"""🎟 مبادله تجهیزات با بلیط — trade spare legendary/mythic gear for genetic-box tickets.

Multi-select panel: toggle items (or «انتخاب همه»), then «تبدیل». Equipped gear is never
listed (can't be scrapped). If any picked item is above +1, a confirm step names those
items and their levels first, so an upgraded piece isn't scrapped by accident.
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

_SEL_KEY = "etx_sel"          # set[int] of selected equipment ids
_MAX_SHOWN = 24               # toggle rows shown at once (select-all still covers all)


def _sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    items = exchangeable_equipment(user)
    return user.biocrate_tickets, items


def _selection(context) -> set:
    sel = context.user_data.get(_SEL_KEY)
    if not isinstance(sel, set):
        sel = set()
        context.user_data[_SEL_KEY] = sel
    return sel


def _render(tickets: int, items, selected: set):
    valid_ids = {it.id for it in items}
    selected &= valid_ids  # drop ids that are gone (converted/equipped since)
    picked = [it for it in items if it.id in selected]
    gain = sum(ticket_value(it) for it in picked)
    lines = [
        "🎟 <b>مبادله تجهیزات با بلیط</b>",
        f"بلیط‌های تو: <b>{tickets}</b> 🎟",
        "",
        "هر تجهیزِ اساطیری = <b>۲</b> بلیط · هر افسانه‌ای = <b>۱</b> بلیط",
        "<i>هر بلیط = یه باکس ژنتیکیِ رایگان. تجهیزاتِ فعال قابل‌مبادله نیستن.</i>",
        "",
    ]
    if not items:
        lines.append("<i>هیچ تجهیزِ افسانه‌ای یا اساطیریِ غیرفعالی برای مبادله نداری.</i>")
        return "\n".join(lines), InlineKeyboardMarkup([[back_btn("menu:shop", "بازگشت به فروشگاه")]])
    lines.append(f"✅ انتخاب‌شده: <b>{len(picked)}</b> تجهیز = <b>{gain}</b> 🎟")
    rows = []
    for it in items[:_MAX_SHOWN]:
        mark = "✅" if it.id in selected else "⬜️"
        lvl = f" +{it.level}" if it.level > 1 else ""
        rows.append([btn(
            f"{mark} {constants.RARITY_LABELS[it.rarity]} {it.name}{lvl} → {ticket_value(it)}🎟",
            style=LIST, callback_data=f"etx:t:{it.id}",
        )])
    if len(items) > _MAX_SHOWN:
        lines.append(f"<i>… و {len(items) - _MAX_SHOWN} تای دیگه (با «انتخاب همه» همه‌شون حساب می‌شن).</i>")
    rows.append([
        btn("انتخاب همه", style=NAV, callback_data="etx:all"),
        btn("پاک‌کردن انتخاب", style=NAV, callback_data="etx:clear"),
    ])
    if picked:
        rows.append([btn(f"♻️ تبدیل به {gain} بلیط", emoji_key="btn_confirm", style=BUILD, callback_data="etx:go")])
    rows.append([back_btn("menu:shop", "بازگشت به فروشگاه")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def equip_exchange_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data[_SEL_KEY] = set()  # fresh selection each time the panel opens
    tickets, items = await run_db(_sync, update.effective_user)
    text, kb = _render(tickets, items, set())
    await send_screen(update, text, parse_mode="HTML", reply_markup=kb)


async def _rerender(update, context):
    tickets, items = await run_db(_sync, update.effective_user)
    text, kb = _render(tickets, items, _selection(context))
    await safe_edit_message_text(update.callback_query, text, parse_mode="HTML", reply_markup=kb)


async def etx_toggle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    item_id = int(query.data.split(":")[2])
    sel = _selection(context)
    sel.discard(item_id) if item_id in sel else sel.add(item_id)
    await query.answer()
    await _rerender(update, context)


async def etx_all_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _tickets, items = await run_db(_sync, update.effective_user)
    context.user_data[_SEL_KEY] = {it.id for it in items}
    await query.answer("همه انتخاب شدن.")
    await _rerender(update, context)


async def etx_clear_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data[_SEL_KEY] = set()
    await update.callback_query.answer("انتخاب پاک شد.")
    await _rerender(update, context)


async def etx_go_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    tickets, items = await run_db(_sync, update.effective_user)
    sel = _selection(context) & {it.id for it in items}
    picked = [it for it in items if it.id in sel]
    if not picked:
        await query.answer("چیزی انتخاب نکردی.", show_alert=True)
        return
    gain = sum(ticket_value(it) for it in picked)
    upgraded = [it for it in picked if it.level > 1]
    if upgraded:
        # warn about above-+1 items before scrapping them
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
                btn(f"✅ بله، تبدیل کن ({gain}🎟)", style=CONFIRM, callback_data="etx:confirm"),
                btn("❌ نه", style=DANGER, callback_data="etx:back"),
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
    sel = list(_selection(context))
    try:
        result = await run_db(exchange_for_tickets, update.effective_user, sel)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        await _rerender(update, context)
        return
    context.user_data[_SEL_KEY] = set()
    await query.answer(f"🎟 +{result['tickets']} بلیط!")
    tickets, items = await run_db(_sync, update.effective_user)
    text, kb = _render(tickets, items, set())
    text = (f"✅ <b>{result['count']}</b> تجهیز تبدیل شد و <b>{result['tickets']}</b> 🎟 گرفتی "
            f"(جمعاً {result['total']} بلیط).\n\n" + text)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=kb)


def register(application) -> None:
    application.add_handler(CallbackQueryHandler(etx_toggle_callback, pattern=r"^etx:t:"))
    application.add_handler(CallbackQueryHandler(etx_all_callback, pattern=r"^etx:all$"))
    application.add_handler(CallbackQueryHandler(etx_clear_callback, pattern=r"^etx:clear$"))
    application.add_handler(CallbackQueryHandler(etx_go_callback, pattern=r"^etx:go$"))
    application.add_handler(CallbackQueryHandler(etx_confirm_callback, pattern=r"^etx:confirm$"))
    application.add_handler(CallbackQueryHandler(etx_back_callback, pattern=r"^etx:back$"))

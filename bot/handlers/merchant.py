"""«تابلوی سفارش تاجر» — the bot screen of game/merchant.py.

Callbacks «mo:»: home · d:<slot>:<id> deliver (asks first when a creature would go) ·
dd:<slot>:<id> deliver, confirmed · x:<slot>:<id> turn down (asks first) · xx:<slot>:<id>
turned down. <slot> is 0-2, or «s» for today's special order.
"""

import html

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes

from bio_lab.repository import get_or_create_user
from bot.buttons import CONFIRM, DANGER, NAV, PRIMARY, back_btn, btn
from bot.utils import run_db, safe_edit_message_text, send_screen
from game.emoji import get_emoji
from game import merchant as M
from game.blackmarket import format_time_remaining as _left
from game.creature import GameError

_DIV = "──────────"
_NUM = {0: "۱", 1: "۲", 2: "۳"}


def _order_lines(o: dict, title: str) -> list[str]:
    lines = [f"{title} <b>{html.escape(o['name'])}</b>" + (" · <b>آماده‌ی تحویل</b>" if o["ready"] else "")]
    for r in o["reqs"]:
        if r["ok"]:
            lines.append(f"   ✅ {r['label']}")
        else:
            lines.append(f"   ▫️ {r['label']} · <code>{r['have']:,}</code> داری")
    reward = [f"💰 {o['coins']:,}", f"⭐ {o['xp']} XP"]
    if o["extra_label"]:
        reward.append(f"🎁 {o['extra_label']}")
    if o["diamonds"]:
        reward.append(f"💎 {o['diamonds']}")
    lines.append("   " + " · ".join(reward))
    return lines


def _render(st: dict, note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    if not st["unlocked"]:
        text = "\n".join([f"{get_emoji('merchant')} <b>تابلوی سفارش تاجر</b>", _DIV,
                          f"🔒 از سطح {st['unlock_hall']} تالار مِهر باز می‌شه.", _DIV,
                          "<i>تاجرها سرم، هیولا و تجهیزات اضافه‌ات رو می‌خرن و طلا و XP می‌دن.</i>"])
        return text, InlineKeyboardMarkup([[back_btn("menu:hub_base", "پایگاه")]])
    lines = [f"{get_emoji('merchant')} <b>تابلوی سفارش تاجر</b>", _DIV,
             f"📦 تحویل امروز: <code>{st['done_today']}/{st['daily']}</code>",
             f"💎 الماس امروز: <code>{st['diamonds_today']}/{st['diamond_cap']}</code>"]
    if note:
        lines += [_DIV, note]
    if st.get("idle"):
        lines += [_DIV, "<i>تاجرها فقط سرم، هیولای اضافه‌ی یک‌ستاره و تجهیزات پوشیده‌نشده می‌خرن؛ "
                        "فعلاً چیزی برای فروش نداری. یه سرم بساز یا هیولا و تجهیزات اضافه جمع کن.</i>"]
    rows = []
    for s in st["slots"]:
        lines.append(_DIV)
        i = s["slot"]
        if "wait" in s:
            lines.append(f"{_NUM[i]}. ⏳ تاجر بعدی تا {_left(s['wait'])} دیگه میاد")
            continue
        lines += _order_lines(s, f"{_NUM[i]}.")
        row = []
        if s["ready"]:
            row.append(btn(f"تحویل {_NUM[i]}", emoji_key="btn_merchant_deliver", style=CONFIRM, callback_data=f"mo:d:{i}:{s['id']}"))
        row.append(btn(f"رد {_NUM[i]}", emoji_key="btn_cancel", style=NAV, callback_data=f"mo:x:{i}:{s['id']}"))
        rows.append(row)
    lines.append(_DIV)
    sp = st["special"]
    if sp:
        lines += _order_lines(sp, "⭐ سفارش ویژه ·")
        if sp["ready"]:
            rows.append([btn("تحویل سفارش ویژه", emoji_key="btn_merchant_deliver", style=PRIMARY, callback_data=f"mo:d:s:{sp['id']}")])
    else:
        lines.append(f"⭐ سفارش ویژه‌ی بعدی تا {_left(st['special_in'])} دیگه")
    rows.append([btn("بروزرسانی", emoji_key="btn_merchant", style=NAV, callback_data="mo:home"), back_btn("menu:hub_base", "پایگاه")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def _sync(tg_user, action: str, slot, order_id: int | None, creature_id: int | None = None):
    user, _ = get_or_create_user(tg_user)
    note, ask = "", None
    try:
        if action == "d":
            names = M.preview_handover(user, slot, order_id)
            if names:
                ask = names
            else:
                action = "dd"
        if action == "dd":
            got = M.deliver(user, slot, order_id, [creature_id] if creature_id else None)
            bits = [f"💰 +{got['coins']:,}", f"⭐ +{got['xp']} XP"]
            if got["extra"]:
                bits.append(f"🎁 {html.escape(got['extra'])}")
            if got["diamonds"]:
                bits.append(f"💎 +{got['diamonds']}")
            note = f"🤝 <b>{html.escape(got['name'])}</b> راضی رفت\n" + "\n".join(bits)
            if got.get("levelup"):
                note += f"\n🎉 آزمایشگاهت رسید به سطح <b>{got['levelup']['to']}</b>"
        elif action == "xx":
            M.reject(user, slot, order_id)
            note = "👋 سفارش رد شد · تاجر بعدی یک ساعت دیگه میاد"
    except GameError as e:
        note = f"⚠️ {html.escape(str(e))}"
    return M.state(user), note, ask


async def merchant_board(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    data = q.data if q and q.data.startswith("mo:") else "mo:home"
    parts = data.split(":")
    action = parts[1]
    slot = parts[2] if len(parts) > 2 else None
    order_id = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else None
    creature_id = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else None
    own = bool(q and q.data.startswith("mo:"))   # «menu:merchant» was answered by the menu router
    if own:
        await q.answer()
    if action == "x":   # turning down costs an hour: ask first
        await safe_edit_message_text(
            q, "👋 <b>این سفارش رو رد کنی؟</b>\n\nتاجرش می‌ره و تاجر بعدی یک ساعت دیگه میاد.", parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                btn("رد کن", emoji_key="btn_confirm", style=DANGER, callback_data=f"mo:xx:{slot}:{order_id}"),
                btn("نگهش دار", emoji_key="btn_cancel", style=NAV, callback_data="mo:home")]]))
        return
    st, note, ask = await run_db(_sync, update.effective_user, action, slot, order_id, creature_id)
    if ask:
        who = "\n".join(f"   • {html.escape(n)}" for _cid, n in ask)
        await safe_edit_message_text(
            q, f"🤝 <b>تحویل سفارش</b>\n\nاین هیولا به تاجر داده می‌شه (ضعیف‌ترین هیولای اضافه‌ات؛ تجهیزاتش برمی‌گرده به انبار):\n{who}\n\nمطمئنی؟",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                btn("تحویل بده", emoji_key="btn_merchant_deliver", style=CONFIRM, callback_data=f"mo:dd:{slot}:{order_id}:{ask[0][0]}"),
                btn("بی‌خیال", emoji_key="btn_cancel", style=NAV, callback_data="mo:home")]]))
        return
    text, kb = _render(st, note)
    if own:
        await safe_edit_message_text(q, text, parse_mode="HTML", reply_markup=kb)
    else:
        from game.media import get_feature_image_path

        await send_screen(update, text, photo=get_feature_image_path("merchant"), parse_mode="HTML", reply_markup=kb)


def register(application) -> None:
    application.add_handler(CallbackQueryHandler(merchant_board, pattern=r"^mo:(home|(d|dd|x|xx):(s|[0-2]):\d+(:\d+)?)$"))

"""«🎁 تقویم تازه‌وارد» screen (game/newbie.py): the seven days, and the button that takes
today's gift. Opened from the main menu (while the calendar runs) and from the daily
reminder DM."""

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes

from bio_lab.repository import get_or_create_user
from bot.buttons import CONFIRM, NAV, back_btn, btn
from bot.utils import run_db, send_screen
from game import newbie
from game.creature import GameError


def _state_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    return newbie.state(user)


def render(st: dict, note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    lines = []
    if note:
        lines += [note, "━━━━━━━━━━"]
    lines += [
        "🎁 <b>تقویم تازه‌وارد</b>",
        "<blockquote>هر روز که بیای یه هدیه می‌گیری — ۷ روز، هر روز بزرگ‌تر، "
        "و <b>روز ۷ یه کایجوی حماسی تضمینی!</b>\n<i>لازم نیست پشت‌سرهم باشه؛ فقط روزی یکی.</i></blockquote>",
    ]
    for d in st["days"]:
        if d["taken"]:
            mark = "✅"
        elif st["can_claim"] and d["day"] == st["next_day"]:
            mark = "🎁"
        else:
            mark = "🔒"
        today = "  ← <b>امروز</b>" if st["can_claim"] and d["day"] == st["next_day"] else ""
        star = " 🌟" if d["big"] else ""
        lines.append(f"{mark} <b>روز {d['day']}</b>{star}: {d['reward']}{today}")
    rows = []
    if st["can_claim"]:
        rows.append([btn(f"گرفتن هدیه‌ی روز {st['next_day']}", emoji_key="btn_confirm", style=CONFIRM,
                         callback_data="nb:claim")])
    elif st["eligible"]:
        lines.append(f"\n⏳ <i>هدیه‌ی امروز رو گرفتی — فردا برگرد برای هدیه‌ی روز {st['next_day']}.</i>")
    else:
        lines.append("\n🏁 <i>همه‌ی هدیه‌های تقویم تازه‌وارد رو گرفتی. خوش اومدی فرمانده!</i>")
    rows.append([back_btn("menu:me", "منوی اصلی")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def newbie_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is not None:
        await query.answer()
    st = await run_db(_state_sync, update.effective_user)
    text, kb = render(st)
    await send_screen(update, text, photo=None, parse_mode="HTML", reply_markup=kb)


def _claim_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    try:
        res = newbie.claim(user)
    except GameError as e:
        return None, str(e), newbie.state(user)
    user.refresh_from_db()
    if res["creature"] is not None:
        from bio_lab.repository import creature_name

        res["creature_label"] = creature_name(res["creature"])
    return res, None, newbie.state(user)


async def newbie_claim(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    res, err, st = await run_db(_claim_sync, update.effective_user)
    if err:
        await query.answer(err, show_alert=True)
        return
    await query.answer(f"🎁 هدیه‌ی روز {res['day']} گرفته شد!")
    note = [f"🎉 <b>هدیه‌ی روز {res['day']} مال تو شد:</b>", res["reward"]]
    if res.get("creature_label"):
        note.append(f"🦖 کایجوی جدیدت: <b>{res['creature_label']}</b> — از «هیولا ← کلکسیون» فعالش کن!")
    if res["next"]:
        note.append(f"<i>🗓 فردا: {res['next']}</i>")
    text, kb = render(st, "\n".join(note))
    await send_screen(update, text, photo=None, parse_mode="HTML", reply_markup=kb)


def register(application) -> None:
    application.add_handler(CallbackQueryHandler(newbie_panel, pattern=r"^nb:open$"))
    application.add_handler(CallbackQueryHandler(newbie_claim, pattern=r"^nb:claim$"))

"""«🏟 جام آخر هفته» — the private-chat screens for game/tournament.py.

Screens are kept short on purpose: they may be shown as a photo caption (~1,000 chars)."""

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

from bio_lab.repository import creature_name, get_or_create_user
from bot.buttons import BATTLE, CONFIRM, DANGER, LIST, NAV, back_btn, btn
from bot.utils import alert_text, run_db, safe_edit_message_text, send_screen
from game import constants, tournament
from game.creature import GameError

_DIV = "━━━━━━━━━━━━━━━━━━━━"
PICK_PAGE_SIZE = 6


def _fmt(seconds: int) -> str:
    d, rem = divmod(max(0, int(seconds)), 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return f"{d} روز و {h} ساعت"
    if h:
        return f"{h} ساعت و {m} دقیقه"
    return f"{max(1, m)} دقیقه"


def _panel_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    return tournament.panel_state(user)


def _who(c: dict) -> str:
    return f"{c['name']} — {constants.element_label(c['element'])} · قدرت <code>{c['power']:,}</code>"


def _render(d: dict, note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    lines = ["🏟 <b>جام آخر هفته</b>", _DIV]
    if note:
        lines = [note, ""] + lines
    rows = []
    intro = ("<blockquote>هر هفته یه جام حذفی: توی گروه‌های ۸ نفره‌ی هم‌سطح قرعه می‌خوری و جمعه‌شب "
             "ساعت ۲۰، ۲۱ و ۲۲ سه دور بازی می‌شه. قبل از هر دور می‌تونی هیولات رو عوض کنی.</blockquote>")
    if d["status"] == "running" and d["entered"]:
        lines.append(f"👥 گروه <b>{d['group']}</b>")
        if d["my"]:
            lines.append(f"🦖 هیولای تو: {_who(d['my'])}")
        if d["alive"] and "match_round" in d:
            lines.append("")
            lines.append(f"⚔️ <b>{tournament.ROUND_NAMES[d['match_round']]}</b> — {_fmt(d['round_in'])} دیگه")
            if d.get("foe") is None:
                lines.append("حریف نداری؛ این دور رو بدون بازی بالا می‌ری.")
            else:
                f = d["foe"]
                lines.append(f"حریف: <b>{f['name']}</b>")
                lines.append(f"هیولای فعلی‌ش: {f['creature']} — {constants.element_label(f['element'])} · قدرت <code>{f['power']:,}</code>")
                lines.append("<i>اون هم می‌تونه تا شروع دور هیولاش رو عوض کنه. برتری عنصری = +۲۰٪ قدرت.</i>")
            rows.append([btn("عوض‌کردن هیولا", emoji_key="btn_swap", style=BATTLE, callback_data="tour:pick:0")])
        elif not d["alive"]:
            lines.append(f"\nاز جام کنار رفتی — جایگاه: <b>{tournament.PLACE_NAMES.get(d['place'] or 5)}</b>. جایزه آخر شب می‌رسه.")
    elif d["status"] == "finished" and d["entered"]:
        lines.append(f"جام این هفته تموم شد. جایگاه تو در گروه {d['group']}: <b>{tournament.PLACE_NAMES.get(d['place'] or 5)}</b>")
        if d.get("champion"):
            lines.append(f"🏆 قهرمان گروه: {d['champion']}")
        lines.append(f"⏳ ثبت‌نام جام بعدی: <b>{_fmt(d['next_reg_in'])}</b> دیگه")
    elif d["registration_open"]:
        lines.append(intro)
        lines.append(f"📝 ثبت‌نام بازه — قرعه‌کشی <b>{_fmt(d['draw_in'])}</b> دیگه · 👥 <code>{d.get('players', 0)}</code> نفر تا الان")
        if d["entered"]:
            lines.append("✅ <b>ثبت‌نام کردی.</b>")
            if d["my"]:
                lines.append(f"🦖 هیولای تو: {_who(d['my'])}")
            rows.append([btn("عوض‌کردن هیولا", emoji_key="btn_swap", style=BATTLE, callback_data="tour:pick:0")])
            rows.append([btn("انصراف از جام", emoji_key="btn_cancel", style=DANGER, callback_data="tour:leave")])
        else:
            rows.append([btn("ثبت‌نام (رایگان)", emoji_key="btn_confirm", style=CONFIRM, callback_data="tour:join")])
    else:
        lines.append(intro)
        lines.append(f"⏳ ثبت‌نام جام بعدی: <b>{_fmt(d['next_reg_in'])}</b> دیگه (پنجشنبه تا جمعه ۱۹:۳۰)")
    lines.append("")
    lines.append("🎁 قهرمان: جعبه‌ی جادویی + ۴۰ الماس · نایب‌قهرمان: طلایی + ۲۰ · نیمه‌نهایی: طلایی + ۱۰ · بقیه: نقره‌ای")
    rows.append([btn("بروزرسانی", emoji_key="btn_recheck", style=NAV, callback_data="tour:home")])
    rows.append([back_btn("menu:hub_battle", "بازگشت به نبرد")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def tournament_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    d = await run_db(_panel_sync, update.effective_user)
    text, keyboard = _render(d)
    from game.media import get_feature_image_path

    await send_screen(update, text, photo=get_feature_image_path("tournament"), parse_mode="HTML", reply_markup=keyboard)


async def _refresh(update: Update, note: str = "") -> None:
    query = update.callback_query
    d = await run_db(_panel_sync, update.effective_user)
    text, keyboard = _render(d, note)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


async def tournament_home_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    await _refresh(update)


def _join_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    tournament.register(user)


def _leave_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    tournament.unregister(user)


async def tournament_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        await run_db(_join_sync, update.effective_user)
    except GameError as exc:
        await query.answer(alert_text(exc), show_alert=True)
        return
    await query.answer("✅ ثبت‌نام شدی!")
    await _refresh(update, "✅ <b>توی جام این هفته ثبت‌نام شدی.</b> جمعه ساعت ۱۹:۳۰ قرعه‌کشی می‌شه.")


async def tournament_leave_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        await run_db(_leave_sync, update.effective_user)
    except GameError as exc:
        await query.answer(alert_text(exc), show_alert=True)
        return
    await query.answer("انصراف دادی.")
    await _refresh(update, "↩️ از جام این هفته انصراف دادی.")


def _choices_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    entry = tournament.my_entry(user)
    if entry is None:
        raise GameError("توی جام این هفته نیستی.")
    scored = tournament.creature_choices(user)
    return [(c.id, creature_name(c), c.element, c.star_level, p) for c, p in scored], entry.creature_id


async def tournament_pick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    page = int(query.data.split(":")[2])
    try:
        choices, current_id = await run_db(_choices_sync, update.effective_user)
    except GameError as exc:
        await query.answer(alert_text(exc), show_alert=True)
        return
    await query.answer()
    pages = max(1, (len(choices) + PICK_PAGE_SIZE - 1) // PICK_PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    rows = []
    for cid, name, element, star, power in choices[page * PICK_PAGE_SIZE:(page + 1) * PICK_PAGE_SIZE]:
        mark = "✅ " if cid == current_id else ""
        rows.append([btn(f"{mark}{name} · {constants.ELEMENT_WORDS.get(element, element)} · {star}⭐ · {power:,}",
                         style=LIST, callback_data=f"tour:set:{cid}")])
    nav = []
    if page > 0:
        nav.append(btn("قبلی", emoji_key="btn_prev", style=NAV, callback_data=f"tour:pick:{page - 1}"))
    if page < pages - 1:
        nav.append(btn("بعدی", emoji_key="btn_next", style=NAV, callback_data=f"tour:pick:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([back_btn("tour:home", "بازگشت")])
    await safe_edit_message_text(
        query,
        "🦖 <b>کدوم هیولا برای دور بعد بازی کنه؟</b>\n<i>قوی‌ترین‌ها اول. برتری عنصری روی حریف = +۲۰٪ قدرت.</i>",
        parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows),
    )


def _set_sync(tg_user, creature_id):
    user, _ = get_or_create_user(tg_user)
    entry = tournament.set_creature(user, creature_id)
    return creature_name(entry.creature)


async def tournament_set_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        name = await run_db(_set_sync, update.effective_user, int(query.data.split(":")[2]))
    except GameError as exc:
        await query.answer(alert_text(exc), show_alert=True)
        return
    await query.answer("انتخاب شد.")
    await _refresh(update, f"🦖 <b>{name}</b> برای دور بعد انتخاب شد.")


def register(application) -> None:
    application.add_handler(CommandHandler("tournament", tournament_panel, filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(tournament_home_callback, pattern=r"^tour:home$"))
    application.add_handler(CallbackQueryHandler(tournament_join_callback, pattern=r"^tour:join$"))
    application.add_handler(CallbackQueryHandler(tournament_leave_callback, pattern=r"^tour:leave$"))
    application.add_handler(CallbackQueryHandler(tournament_pick_callback, pattern=r"^tour:pick:\d+$"))
    application.add_handler(CallbackQueryHandler(tournament_set_callback, pattern=r"^tour:set:\d+$"))

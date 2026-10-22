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
    """The panel data, plus the bracket picture of the player's group once the draw is done."""
    from game.media import get_feature_image_path, render_tournament_bracket

    user, _ = get_or_create_user(tg_user)
    d = tournament.panel_state(user)
    photo = None
    t = tournament.current()
    if t is not None and d.get("entered") and d.get("group"):
        photo = render_tournament_bracket(tournament.group_bracket(t, d["group"]), user.id)
    return d, photo or get_feature_image_path("tournament")


def _who(c: dict) -> str:
    return f"{c['name']} [{constants.element_label(c['element'])}] · قدرت <code>{c['power']:,}</code>"


_PRIZE_LINES = [
    "🏆 قهرمان گروه: جعبه‌ی جادویی + <code>40</code> الماس",
    "🥈 نایب‌قهرمان: جعبه‌ی طلایی + <code>20</code> الماس",
    "🥉 نیمه‌نهایی: جعبه‌ی طلایی + <code>10</code> الماس",
    "🎟 بقیه‌ی شرکت‌کننده‌ها: جعبه‌ی نقره‌ای",
]


def _render(d: dict, note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    """Short on purpose (photo caption). One state at a time: registration → waiting for the
    draw → my next match → out / finished. The full rules are behind «راهنمای جام»."""
    lines = ["🏟 <b>جام آخر هفته</b>", _DIV]
    if note:
        lines = [note, ""] + lines
    rows = []
    status = d["status"]
    if status == "running" and d["entered"]:
        lines.append(f"👥 گروه <b>{d['group']}</b> · <code>{d.get('group_size', 0)}</code> نفر · جدول گروهت توی عکس بالاست")
        if d["my"]:
            lines.append(f"🦖 هیولای تو: {_who(d['my'])}")
        lines.append(_DIV)
        if d["alive"] and "match_round" in d:
            lines.append(f"⚔️ <b>{tournament.ROUND_NAMES[d['match_round']]}</b>")
            lines.append(f"⏰ شروع: <b>{_fmt(d['round_in'])}</b> دیگه")
            if d.get("foe") is None:
                lines.append("😎 این دور حریف نداری؛ بدون بازی صعود می‌کنی.")
            else:
                f = d["foe"]
                lines.append(f"🆚 حریف: <b>{f['name']}</b>")
                lines.append(f"👹 هیولای فعلی‌ش: {f['creature']} [{constants.element_label(f['element'])}] · قدرت <code>{f['power']:,}</code>")
                lines.append("<i>💡 هیولایی بفرست که بر عنصر حریف برتری داره (+۲۰٪ قدرت). اون هم تا شروع دور می‌تونه عوض کنه.</i>")
            rows.append([btn("عوض‌کردن هیولا", emoji_key="btn_swap", style=BATTLE, callback_data="tour:pick:0")])
        elif d["alive"]:
            lines.append("⏳ منتظر نتیجه‌ی بقیه‌ی بازی‌های این دور…")
        else:
            lines.append(f"❌ از جام کنار رفتی · جایگاه: <b>{tournament.PLACE_NAMES.get(d['place'] or 5)}</b>")
            lines.append("🎁 جایزه‌ات بعد از فینال (حدود ساعت ۲۲) می‌رسه.")
    elif status == "finished" and d["entered"]:
        lines.append(f"🏁 جام این هفته تموم شد · گروه <b>{d['group']}</b>")
        lines.append(f"🎖 جایگاه تو: <b>{tournament.PLACE_NAMES.get(d['place'] or 5)}</b>")
        if d.get("champion"):
            lines.append(f"🏆 قهرمان گروه: <b>{d['champion']}</b>")
        lines.append(_DIV)
        lines.append(f"⏳ ثبت‌نام جام بعدی: <b>{_fmt(d['next_reg_in'])}</b> دیگه")
    elif d["registration_open"]:
        lines.append(f"📝 <b>ثبت‌نام بازه</b> · 👥 <code>{d.get('players', 0)}</code> نفر تا الان")
        lines.append(f"🎲 قرعه‌کشی: جمعه ساعت ۱۹:۳۰ (<b>{_fmt(d['draw_in'])}</b> دیگه)")
        lines.append("⚔️ بازی‌ها: جمعه ساعت ۲۰، ۲۱ و ۲۲")
        lines.append(_DIV)
        if d["entered"]:
            lines.append("✅ <b>ثبت‌نام کردی.</b> بعد از قرعه‌کشی جدول گروهت اینجا نشون داده می‌شه.")
            if d["my"]:
                lines.append(f"🦖 هیولای تو: {_who(d['my'])}")
            rows.append([btn("عوض‌کردن هیولا", emoji_key="btn_swap", style=BATTLE, callback_data="tour:pick:0")])
            rows.append([btn("انصراف از جام", emoji_key="btn_cancel", style=DANGER, callback_data="tour:leave")])
        else:
            lines.append("<blockquote>رایگانه. با بازیکن‌های هم‌سطح خودت توی یه گروه ۸ نفره می‌افتی؛ "
                         "حتی بازیکن ضعیف‌تر هم می‌تونه قهرمان گروهش بشه.</blockquote>")
            rows.append([btn("ثبت‌نام (رایگان)", emoji_key="btn_confirm", style=CONFIRM, callback_data="tour:join")])
    else:
        lines.append("<blockquote>هر هفته یه جام حذفی بین بازیکن‌های هم‌سطح. پنجشنبه و جمعه ثبت‌نام کن، "
                     "جمعه‌شب سه دور بازی می‌شه و قهرمان هر گروه جایزه می‌گیره.</blockquote>")
        lines.append(f"⏳ ثبت‌نام جام بعدی: <b>{_fmt(d['next_reg_in'])}</b> دیگه")
    lines.append(_DIV)
    lines += _PRIZE_LINES
    if d.get("unopened"):
        lines += [_DIV, f"🎁 <b>{d['unopened']} جعبه‌ی جایزه‌ی جام</b> منتظر باز شدنه!"]
        rows.insert(0, [btn("باز کردن جعبه‌ی جایزه", emoji_key="btn_chest_open", style=CONFIRM, callback_data="tour:open")])
    rows.append([btn("راهنمای جام", emoji_key="btn_report", style=NAV, callback_data="tour:help"),
                 btn("بروزرسانی", emoji_key="btn_recheck", style=NAV, callback_data="tour:home")])
    rows.append([back_btn("menu:hub_battle", "بازگشت به نبرد")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


_HELP = "\n".join([
    "📖 <b>راهنمای جام آخر هفته</b>", _DIV,
    "<b>۱. ثبت‌نام</b>",
    "پنجشنبه ۰۰:۰۰ تا جمعه ۱۹:۳۰، رایگان.",
    "",
    "<b>۲. قرعه‌کشی (جمعه ۱۹:۳۰)</b>",
    "بر اساس کاپ، گروه‌های حداکثر ۸ نفره‌ی هم‌سطح ساخته می‌شه؛ هر گروه جدول و قهرمان خودش رو داره "
    "(اسم هم‌گروهی‌هات توی عکس صفحه‌ی جام).",
    "",
    "<b>۳. سه دور، هر ساعت یکی</b>",
    "ساعت ۲۰ یک‌چهارم نهایی، ۲۱ نیمه‌نهایی، ۲۲ فینال. برنده‌ی هر بازی به دور بعد می‌ره و بازنده کنار می‌ره.",
    "",
    "<b>۴. کی می‌بره؟</b>",
    "قدرت بیشتر می‌بره. هیولایی که بر عنصر حریف برتری داره <code>+20%</code> قدرت می‌گیره. "
    "مساوی بشه، کسی که زودتر ثبت‌نام کرده می‌بره. شانسی در کار نیست.",
    "",
    "<b>۵. ترفند اصلی</b>",
    "تا شروع هر دور هیولای حریفت رو می‌بینی و می‌تونی هیولات رو عوض کنی؛ اون هم می‌تونه.",
    "",
    "<b>۶. استراحت</b>",
    "اگه گروهت کمتر از ۸ نفر باشه، بعضیا دور اول حریف ندارن و بدون بازی صعود می‌کنن.",
    "",
    "<b>۷. جایزه‌ها (بعد از فینال)</b>",
    *_PRIZE_LINES,
])


async def tournament_help_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await safe_edit_message_text(query, _HELP, parse_mode="HTML",
                                 reply_markup=InlineKeyboardMarkup([[back_btn("tour:home", "بازگشت به جام")]]))


async def tournament_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    d, photo = await run_db(_panel_sync, update.effective_user)
    text, keyboard = _render(d)
    await send_screen(update, text, photo=photo, parse_mode="HTML", reply_markup=keyboard)


async def _refresh(update: Update, note: str = "") -> None:
    d, photo = await run_db(_panel_sync, update.effective_user)
    text, keyboard = _render(d, note)
    await send_screen(update, text, photo=photo, parse_mode="HTML", reply_markup=keyboard)


async def tournament_home_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    await _refresh(update)


def _open_sync(tg_user):
    from game.worldboss import _chest_line

    user, _ = get_or_create_user(tg_user)
    return [_chest_line(r) for r in tournament.open_prizes(user)]


async def tournament_open_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """«باز کردن جعبه‌ی جایزه» — from the end-of-cup message or the cup screen."""
    query = update.callback_query
    try:
        lines = await run_db(_open_sync, update.effective_user)
    except GameError as exc:
        await query.answer(alert_text(exc), show_alert=True)
        return
    await query.answer("🎁 باز شد!")
    await _refresh(update, "🎁 <b>جعبه‌ی جایزه باز شد!</b>\n" + "\n".join(lines))


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
    application.add_handler(CallbackQueryHandler(tournament_help_callback, pattern=r"^tour:help$"))
    application.add_handler(CallbackQueryHandler(tournament_open_callback, pattern=r"^tour:open$"))
    application.add_handler(CallbackQueryHandler(tournament_join_callback, pattern=r"^tour:join$"))
    application.add_handler(CallbackQueryHandler(tournament_leave_callback, pattern=r"^tour:leave$"))
    application.add_handler(CallbackQueryHandler(tournament_pick_callback, pattern=r"^tour:pick:\d+$"))
    application.add_handler(CallbackQueryHandler(tournament_set_callback, pattern=r"^tour:set:\d+$"))

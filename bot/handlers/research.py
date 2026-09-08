"""🔬 آزمایشگاه — the research-lab panel (private chat).

Lists every research track with its level, effect and next-level cost, and drives the
start / diamond-finish flow. The heavy lifting (caps, costs, timers, effects) lives in
game/research.py; this module is only the Telegram UI.
"""

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes

from bio_lab.repository import get_or_create_user
from bot.buttons import BUILD, CONFIRM, DANGER, LIST, SHOP, back_btn, btn
from bot.utils import run_db, safe_edit_message_text, send_screen
from django.utils import timezone
from game import constants, research
from game.creature import GameError
from game.emoji import get_emoji


def _fmt_remaining(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m = rem // 60
    if h:
        return f"{h} ساعت و {m} دقیقه"
    if m:
        return f"{m} دقیقه"
    return "چند لحظه"


def _panel_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    research.check_and_apply(user)
    lab_level = research.research_cap(user)
    levels = research.research_levels(user)
    upgrading = {u.key: u for u in research.active_upgrades(user)}
    now = timezone.now()
    rows = []
    for key, d in research.RESEARCH_DEFS.items():
        up = upgrading.get(key)
        rows.append({
            "key": key, "emoji": d["emoji"], "label": d["label"],
            "level": levels.get(key, 0),
            "remaining": (up.finishes_at - now).total_seconds() if up else None,
            "target": up.target_level if up else None,
        })
    return lab_level, rows, user.diamonds


def _panel_text(lab_level: int) -> str:
    if lab_level <= 0:
        return (
            f"🔬 <b>آزمایشگاه</b>\n\n"
            "این‌جا با پژوهش روی عناصر و توانایی‌ها به <b>همه‌ی هیولاهات</b> بونوسِ دائمی می‌دی.\n\n"
            "⛔ هنوز ساختمونِ «🔬 آزمایشگاه» رو نساختی — از سطح <b>۵ تالار مِهر</b> باز می‌شه. "
            "اول از بخش «ساختمون‌ها» بسازش."
        )
    return (
        f"🔬 <b>آزمایشگاه</b> — سطح ساختمون <b>{lab_level}/{constants.BUILDING_MAX_LEVEL}</b>\n\n"
        "روی هر پژوهش بزن تا اثر و هزینه‌ش رو ببینی و شروعش کنی.\n"
        f"<blockquote>لِوِلِ هر پژوهش نمی‌تونه از لِوِلِ ساختمون ({lab_level}) جلو بزنه — "
        "برای بالاتر رفتن اول خودِ آزمایشگاه رو ارتقا بده.</blockquote>"
    )


def _panel_keyboard(lab_level: int, rows) -> InlineKeyboardMarkup:
    kb = []
    if lab_level > 0:
        for r in rows:
            if r["remaining"] is not None:
                tag = f"⏳ تا سطح {r['target']}"
            elif r["level"] >= constants.RESEARCH_MAX_LEVEL:
                tag = "🏆 مکس"
            else:
                tag = f"سطح {r['level']}/{lab_level}"
            kb.append([btn(f"{r['emoji']} {r['label']} — {tag}", style=LIST, callback_data=f"rsch:pick:{r['key']}")])
    kb.append([back_btn("menu:buildings", "بازگشت به ساختمون‌ها")])
    return InlineKeyboardMarkup(kb)


async def research_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lab_level, rows, _diamonds = await run_db(_panel_sync, update.effective_user)
    await send_screen(
        update, _panel_text(lab_level), parse_mode="HTML",
        reply_markup=_panel_keyboard(lab_level, rows),
    )


def _detail_sync(tg_user, key):
    user, _ = get_or_create_user(tg_user)
    research.check_and_apply(user)
    if key not in research.RESEARCH_DEFS:
        raise GameError("این پژوهش وجود نداره.")
    lab_level = research.research_cap(user)
    level = research.research_levels(user).get(key, 0)
    up = research.upgrade_for(user, key)
    remaining = (up.finishes_at - timezone.now()).total_seconds() if up else None
    fin_price = research.diamond_finish_price(up) if up else 0
    target = up.target_level if up else None
    return lab_level, level, remaining, target, fin_price, user.diamonds


def _detail_text(key, lab_level, level, remaining, target) -> str:
    d = research.RESEARCH_DEFS[key]
    lines = [
        f"{d['emoji']} <b>{d['label']}</b> — سطح <b>{level}/{constants.RESEARCH_MAX_LEVEL}</b>",
        "",
        research.RESEARCH_DESC[key],
        "",
    ]
    # current total effect
    per = d["per_level"]
    if level > 0:
        lines.append(f"✅ اثرِ فعلی: <b>{level * per * 100:.0f}٪</b>")
    if remaining is not None:
        lines.append(f"\n⏳ در حال پژوهش تا سطح {target} — <b>{_fmt_remaining(remaining)}</b> مونده.")
        lines.append("<i>این پژوهش فقط با الماس زودتر تموم می‌شه.</i>")
    elif level >= constants.RESEARCH_MAX_LEVEL:
        lines.append("\n🏆 به سقف نهایی (سطح ۵) رسیده.")
    elif level >= lab_level:
        lines.append(f"\n🔒 برای سطح بعدی، اول ساختمونِ آزمایشگاه رو به سطح {level + 1} ارتقا بده.")
    else:
        target_level = level + 1
        gold, dna = research.next_cost(target_level)
        secs = constants.research_seconds(target_level)
        lines.append(f"\n🔼 <b>ارتقا به سطح {target_level}</b> (اثر می‌شه {target_level * per * 100:.0f}٪):")
        lines.append(f"{get_emoji('coin')} <b>{gold:,}</b> طلا  ┃  {get_emoji('dna')} <b>{dna:,}</b> DNA")
        lines.append(f"⏳ زمان: <b>{_fmt_remaining(secs)}</b>")
    return "\n".join(lines)


def _detail_keyboard(key, lab_level, level, remaining, fin_price) -> InlineKeyboardMarkup:
    kb = []
    if remaining is not None:
        kb.append([btn(f"💎 تمومش کن ({fin_price} الماس)", style=SHOP, callback_data=f"rsch:finishask:{key}")])
    elif level < constants.RESEARCH_MAX_LEVEL and level < lab_level:
        kb.append([btn("🔬 شروع پژوهش", style=BUILD, callback_data=f"rsch:start:{key}")])
    kb.append([back_btn("menu:research", "بازگشت به آزمایشگاه")])
    return InlineKeyboardMarkup(kb)


async def research_pick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    key = query.data.split(":", 2)[2]
    try:
        lab_level, level, remaining, target, fin_price, _d = await run_db(_detail_sync, update.effective_user, key)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer()
    await safe_edit_message_text(
        query, _detail_text(key, lab_level, level, remaining, target), parse_mode="HTML",
        reply_markup=_detail_keyboard(key, lab_level, level, remaining, fin_price),
    )


def _start_sync(tg_user, key):
    user, _ = get_or_create_user(tg_user)
    research.start_research(user, key)
    return _detail_sync(tg_user, key)


async def research_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    key = query.data.split(":", 2)[2]
    try:
        lab_level, level, remaining, target, fin_price, _d = await run_db(_start_sync, update.effective_user, key)
    except GameError as exc:
        from bot.handlers.shop import show_gold_error

        if await show_gold_error(query, exc):
            return
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer("🔬 پژوهش شروع شد!")
    await safe_edit_message_text(
        query, _detail_text(key, lab_level, level, remaining, target), parse_mode="HTML",
        reply_markup=_detail_keyboard(key, lab_level, level, remaining, fin_price),
    )


async def research_finish_ask_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    key = query.data.split(":", 2)[2]
    try:
        _lab, _lvl, remaining, target, fin_price, _d = await run_db(_detail_sync, update.effective_user, key)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    if remaining is None:
        await query.answer("این پژوهش در حال انجام نیست.", show_alert=True)
        return
    await query.answer()
    d = research.RESEARCH_DEFS[key]
    await safe_edit_message_text(
        query,
        f"💎 <b>تمام‌کردن فوریِ پژوهش</b>\n\n"
        f"{d['emoji']} «{d['label']}» تا سطح {target} با <b>{fin_price}</b> الماس همین الان تموم می‌شه. تأیید می‌کنی؟",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            btn(f"✅ بله ({fin_price} 💎)", style=SHOP, callback_data=f"rsch:finish:{key}"),
            btn("❌ نه", style=DANGER, callback_data=f"rsch:pick:{key}"),
        ]]),
    )


def _finish_sync(tg_user, key):
    user, _ = get_or_create_user(tg_user)
    cost = research.finish_with_diamonds(user, key)
    lab_level, level, remaining, target, fin_price, diamonds = _detail_sync(tg_user, key)
    return cost, lab_level, level, remaining, target, fin_price


async def research_finish_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    key = query.data.split(":", 2)[2]
    try:
        cost, lab_level, level, remaining, target, fin_price = await run_db(_finish_sync, update.effective_user, key)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer(f"💎 −{cost} — تموم شد!")
    await safe_edit_message_text(
        query,
        f"💎 <b>با {cost} الماس تموم شد!</b>\n\n" + _detail_text(key, lab_level, level, remaining, target),
        parse_mode="HTML",
        reply_markup=_detail_keyboard(key, lab_level, level, remaining, fin_price),
    )


def register(application) -> None:
    application.add_handler(CallbackQueryHandler(research_pick_callback, pattern=r"^rsch:pick:"))
    application.add_handler(CallbackQueryHandler(research_start_callback, pattern=r"^rsch:start:"))
    application.add_handler(CallbackQueryHandler(research_finish_ask_callback, pattern=r"^rsch:finishask:"))
    application.add_handler(CallbackQueryHandler(research_finish_callback, pattern=r"^rsch:finish:"))

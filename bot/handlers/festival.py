"""«🎪 جشنواره» — the private-chat screens for game/festival.py (home / shop / how-to).

Screens are kept short on purpose: they may be shown as a photo caption (~1,000 chars).
Layout rules the owner asked for: divider lines between sections, ONE fact per line,
numbers in <code> (tap to copy) with Latin digits, «دی‌ان‌ای» spelled in Persian."""

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

from bio_lab.repository import get_or_create_user
from bot.buttons import CONFIRM, LIST, NAV, SHOP, back_btn, btn
from bot.utils import alert_text, run_db, safe_edit_message_text, send_screen
from game import constants, festival
from game.creature import GameError

_DIV = "━━━━━━━━━━━━━━━━━━━━"
COIN = "🎟"
_BONUS = f"+{int(festival.VIP_COIN_BONUS * 100)}%"

# short button labels for the shop (the full title is in the text above the buttons)
_SHORT = {
    "capsule": "3 موش", "speedup": "کارت سرعت", "gold": "کیسه‌ی طلا", "dna": "دی‌ان‌ای", "diamonds": "10 الماس",
    "golden": "جعبه‌ی طلایی", "magical": "جعبه‌ی جادویی", "grand": "هیولای افسانه‌ای",
    "vip_chest": "هدیه‌ی اشتراک", "vip_mythic": "اساطیری کریستال",
}
_BTN_EMOJI = {
    "diamonds": "btn_diamond", "speedup": "btn_speed_card", "golden": "btn_chest_golden", "magical": "btn_chest_magical",
    "grand": "btn_chest_mega", "vip_chest": "btn_chest_open", "vip_mythic": "btn_vip",
}
# shop sections: (heading, item keys)
_SECTIONS = (
    ("🧺 <b>منابع</b>", ("capsule", "speedup", "gold", "dna", "diamonds")),
    ("📦 <b>جعبه‌ها</b>", ("golden", "magical")),
    ("👑 <b>جایزه‌ی بزرگ</b>", ("grand",)),
    ("⭐ <b>ویژه‌ی اشتراک</b>", festival.VIP_ONLY),
)


def _fmt(seconds: int) -> str:
    d, rem = divmod(max(0, int(seconds)), 86400)
    h = rem // 3600
    return f"{d} روز و {h} ساعت" if d else f"{max(1, h)} ساعت"


def _bar(have: int, need: int, width: int = 10) -> str:
    ratio = 0 if need <= 0 else max(0.0, min(1.0, have / need))
    full = int(ratio * width)
    return "▰" * full + "▱" * (width - full) + f" <code>{int(ratio * 100)}%</code>"


def _vip_lines(vip: bool) -> list[str]:
    mythic = festival.SHOP["vip_mythic"]
    mark = "✅" if vip else "🔒"
    lines = [
        "⭐ <b>ویژه‌ی اشتراک</b>" + ("" if vip else " (اشتراک نداری)"),
        f"{mark} سکه‌ی جشنواره: <code>{_BONUS}</code>",
        f"{mark} جعبه‌ی جادویی رایگان",
        f"{mark} هیولای اساطیری کریستال",
        f"{COIN} قیمت اساطیری: <code>{mythic[2]:,}</code>",
    ]
    return lines


def _home_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    return festival.status(user)


def _home_render(st: dict, note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    th = st["theme"]
    rows = []
    if not st["active"]:
        lines = [
            "🎪 <b>جشنواره‌ی ماهانه</b>", _DIV,
            "<blockquote>هر ماه یه هفته جشنواره‌ست: از همه‌ی کارهات «سکه‌ی جشنواره» می‌افته و می‌تونی "
            "از فروشگاه مخصوصش خرید کنی. سکه‌ها آخر جشنواره باطل می‌شن.</blockquote>",
            f"⏳ جشنواره‌ی بعدی: <b>{_fmt(st['seconds_until_next'])}</b> دیگه",
            f"🗓 هر ماه از روز <code>{festival.FESTIVAL_START_DAY}</code> تا <code>{festival.FESTIVAL_END_DAY}</code>",
            _DIV,
            *_vip_lines(st["vip"]),
        ]
    else:
        grand_cost = festival.SHOP["grand"][2]
        lines = [
            f"{th['emoji']} <b>جشنواره‌ی «{th['title']}»</b>", _DIV,
            f"⏳ زمان باقی‌مانده: <b>{_fmt(st['seconds_left'])}</b>",
            _DIV,
            f"{COIN} سکه‌ی تو: <code>{st['coins']:,}</code>",
            f"📈 کل سکه‌ی جمع‌شده: <code>{st['earned']:,}</code>",
        ]
        if st["rank"]:
            lines.append(f"🏅 رتبه‌ی تو: <code>{st['rank']}</code>")
        lines += [
            _DIV,
            "👑 <b>جایزه‌ی بزرگ</b>",
            f"هیولای افسانه‌ایِ {constants.element_label(th['element'])}",
            f"{COIN} قیمت: <code>{grand_cost:,}</code>",
            _bar(st["coins"], grand_cost),
            _DIV,
            *_vip_lines(st["vip"]),
        ]
        if st["top"]:
            lines += [_DIV, "🏆 <b>برترین‌ها</b>"]
            for r in st["top"][:3]:
                lines.append(f"{r['rank']}. {r['name']}: <code>{r['earned']:,}</code>")
        lines += [_DIV, "<i>سکه‌ها آخر جشنواره باطل می‌شن؛ قبلش خرجشون کن.</i>"]
        rows.append([btn("فروشگاه جشنواره", emoji_key="btn_shop", style=SHOP, callback_data="fest:shop")])
        rows.append([btn("چطور سکه بگیرم؟", emoji_key="btn_report", style=NAV, callback_data="fest:how"),
                     btn("جایزه‌ی رتبه‌ها", emoji_key="btn_rank", style=NAV, callback_data="fest:top")])
    if not st["vip"]:
        rows.append([btn("خرید اشتراک", emoji_key="btn_vip", style=SHOP, callback_data="menu:subscription")])
    if note:
        lines = [note, ""] + lines
    rows.append([back_btn("menu:hub_city", "بازگشت به شهر")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def festival_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    st = await run_db(_home_sync, update.effective_user)
    text, keyboard = _home_render(st)
    from game.media import get_feature_image_path

    await send_screen(update, text, photo=get_feature_image_path("festival"), parse_mode="HTML", reply_markup=keyboard)


async def festival_home_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    st = await run_db(_home_sync, update.effective_user)
    await query.answer()
    text, keyboard = _home_render(st)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


def _how_text(vip: bool) -> str:
    lines = [f"{COIN} <b>چطور سکه‌ی جشنواره بگیرم؟</b>", _DIV]
    for action, (per, cap) in festival.EARN.items():
        lines.append(f"• {festival.EARN_LABELS[action]}: <code>{per}</code> سکه (سقف روز <code>{per * cap}</code>)")
    lines += [
        f"• هر مأموریت روزانه: <code>{festival.MISSION_DAILY_COINS}</code> سکه",
        f"• هر مأموریت هفتگی: <code>{festival.MISSION_WEEKLY_COINS}</code> سکه",
        _DIV,
        (f"⭐ اشتراکت فعاله: روی همه‌ی این‌ها <code>{_BONUS}</code> سکه‌ی بیشتر می‌گیری."
         if vip else f"⭐ با اشتراک، روی همه‌ی این‌ها <code>{_BONUS}</code> سکه‌ی بیشتر می‌گیری."),
        _DIV,
        "<i>هر کار سقف روزانه داره؛ برای سکه‌ی بیشتر باید به همه‌ی بخش‌های بازی سر بزنی.</i>",
    ]
    return "\n".join(lines)


async def festival_how_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    st = await run_db(_home_sync, update.effective_user)
    await query.answer()
    await safe_edit_message_text(
        query, _how_text(st["vip"]), parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[back_btn("fest:home", "بازگشت")]]),
    )


def _top_text(st: dict) -> str:
    lines = ["🏆 <b>جایزه‌ی رتبه‌ها</b>", _DIV,
             "آخر جشنواره به کسایی که بیشترین سکه رو <b>جمع کردن</b> الماس می‌رسه (خرج کردن سکه رتبه رو کم نمی‌کنه).",
             _DIV]
    prev = 0
    for max_rank, diamonds in festival.RANK_PRIZES:
        who = f"رتبه‌ی {max_rank}" if max_rank == prev + 1 else f"رتبه‌ی {prev + 1} تا {max_rank}"
        lines.append(f"💎 {who}: <code>{diamonds}</code> الماس")
        prev = max_rank
    if st.get("top"):
        lines += [_DIV, "🏅 <b>جدول الان</b>"]
        for r in st["top"]:
            lines.append(f"{r['rank']}. {r['name']}: <code>{r['earned']:,}</code>")
    if st.get("rank"):
        lines += [_DIV, f"🏅 رتبه‌ی تو: <code>{st['rank']}</code>"]
    return "\n".join(lines)


async def festival_top_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    st = await run_db(_home_sync, update.effective_user)
    await query.answer()
    await safe_edit_message_text(
        query, _top_text(st), parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[back_btn("fest:home", "بازگشت")]]),
    )


def _shop_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    return festival.shop_state(user)


def _shop_render(shop: dict, note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    th = shop["theme"]
    items = {it["key"]: it for it in shop["items"]}
    lines = [f"{th['emoji']} <b>فروشگاه جشنواره</b>", _DIV, f"{COIN} سکه‌ی تو: <code>{shop['coins']:,}</code>"]
    if note:
        lines = [note, ""] + lines
    small, big = [], []          # buttons: everyday items two per row, the big prizes on their own row
    for heading, keys in _SECTIONS:
        lines += [_DIV, heading]
        for key in keys:
            it = items.get(key)
            if it is None:
                continue
            price = f"<code>{it['cost']:,}</code> {COIN}" if it["cost"] else "رایگان"
            if it["locked"]:
                state = " 🔒"
            elif not it["left"]:
                state = " ✔️ گرفتی" if key in ("grand", "vip_chest", "vip_mythic") else " (تموم شد)"
            else:
                state = ""
            lines.append(f"{it['emoji']} {it['title']}: {price}{state}")
            if it["locked"] or not it["left"] or shop["coins"] < it["cost"]:
                continue
            label = f"{_SHORT.get(key, it['title'])} · {it['cost']:,}" if it["cost"] else f"{_SHORT.get(key, it['title'])} · رایگان"
            button = btn(label, emoji_key=_BTN_EMOJI.get(key), style=(CONFIRM if key in ("grand", "vip_chest", "vip_mythic") else LIST),
                         callback_data=f"fest:buy:{key}")
            (big if key in ("grand", "vip_chest", "vip_mythic") else small).append(button)
    rows = [small[i:i + 2] for i in range(0, len(small), 2)] + [[b] for b in big]
    if not rows:
        lines += [_DIV, "<i>فعلاً سکه‌ات به هیچ‌کدوم نمی‌رسه.</i>"]
    if not shop["vip"]:
        rows.append([btn("خرید اشتراک", emoji_key="btn_vip", style=SHOP, callback_data="menu:subscription")])
    rows.append([back_btn("fest:home", "بازگشت")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def festival_shop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        shop = await run_db(_shop_sync, update.effective_user)
    except GameError as exc:
        await query.answer(alert_text(exc), show_alert=True)
        return
    await query.answer()
    text, keyboard = _shop_render(shop)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


def _buy_sync(tg_user, item_key):
    user, _ = get_or_create_user(tg_user)
    res = festival.buy(user, item_key)
    return f"✅ <b>گرفتی:</b> {res['got']}", festival.shop_state(user)


async def festival_buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    item_key = query.data.split(":")[2]
    try:
        note, shop = await run_db(_buy_sync, update.effective_user, item_key)
    except GameError as exc:
        await query.answer(alert_text(exc), show_alert=True)
        return
    await query.answer("✅ انجام شد!")
    text, keyboard = _shop_render(shop, note)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


def register(application) -> None:
    application.add_handler(CommandHandler("festival", festival_panel, filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(festival_home_callback, pattern=r"^fest:home$"))
    application.add_handler(CallbackQueryHandler(festival_how_callback, pattern=r"^fest:how$"))
    application.add_handler(CallbackQueryHandler(festival_top_callback, pattern=r"^fest:top$"))
    application.add_handler(CallbackQueryHandler(festival_shop_callback, pattern=r"^fest:shop$"))
    application.add_handler(CallbackQueryHandler(festival_buy_callback, pattern=r"^fest:buy:[a-z_]+$"))

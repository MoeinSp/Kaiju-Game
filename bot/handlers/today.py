"""«📋 امروز» — the private-chat screen for game/today.py: what's ready right now, one
«دریافت همه» button for everything that is a plain collect, and a shortcut to the rest.

Kept short on purpose: it is shown as a photo caption (~1,000 chars)."""

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

from bio_lab.repository import get_or_create_user
from bot.buttons import BATTLE, CONFIRM, NAV, SHOP, back_btn, btn
from bot.utils import mission_reward_text, run_db, send_screen
from game import today
from game.emoji import get_emoji

_DIV = "━━━━━━━━━━━━━━━━━━━━"
_RES = {"coins": ("coin", "طلا"), "dna_fragments": ("dna", "DNA"), "diamonds": ("diamond", "الماس")}


def _state_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    return today.state(user)


def _amounts(totals: dict) -> str:
    parts = [f"{get_emoji(_RES[k][0])} <code>+{v:,}</code>" for k, v in totals.items() if v and k in _RES]
    return " ┃ ".join(parts)


def _render(st: dict, note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    lines = ["📋 <b>امروز</b>", _DIV]
    if note:
        lines = [note, ""] + lines
    rows = []

    # ── live, time-limited things first ──
    if st["boss"]:
        left = st["boss"]["hits_left"]
        lines.append(f"👹 <b>غول سرگردان اینجاست!</b> " + (f"<code>{left}</code> ضربه داری." if left else "ضربه‌هات رو زدی."))
        if left:
            rows.append([btn("ضربه به غول", emoji_key="btn_worldboss", style=BATTLE, callback_data="menu:worldboss")])
    if st["tournament_open"]:
        lines.append("🏟 ثبت‌نام <b>جام آخر هفته</b> بازه (رایگان).")
        rows.append([btn("ثبت‌نام در جام", emoji_key="btn_tournament", style=BATTLE, callback_data="menu:tournament")])
    if st["festival"]:
        lines.append(f"{st['festival']['emoji']} جشنواره‌ی «{st['festival']['title']}» در جریانه.")
    if st["rule"]:
        lines.append(st["rule"])
    if len(lines) > 2 + (2 if note else 0):
        lines.append("")

    # ── ready to take ──
    ready = []
    if st["pending"]:
        ready.append("🏗 ساختمان‌ها: " + _amounts(st["pending"]))
    if st["dispatch_ready"]:
        ready.append(f"🧭 <code>{st['dispatch_ready']}</code> هیولا از مأموریت برگشته")
    if st["event_daily"]:
        ready.append("🎁 جایزه‌ی روزانه‌ی رویداد")
    if st["story_ready"]:
        ready.append("🎯 پاداش مأموریت داستانی")
    if ready:
        lines += ["✅ <b>آماده‌ی دریافت:</b>"] + ready
        rows.append([btn(f"دریافت همه ({today.collectable_count(st)})", emoji_key="btn_confirm", style=CONFIRM,
                         callback_data="today:all")])
    else:
        lines.append("<i>چیزی برای جمع کردن نمونده.</i>")

    # ── one tap away ──
    extra = []
    if st["free_boxes"]:
        extra.append([btn(f"باکس رایگان ({len(st['free_boxes'])})", emoji_key="btn_diamond_box", style=SHOP,
                          callback_data="menu:diamond_box")])
    if st["wheel"]:
        extra.append([btn("گردونه‌ی رایگان", emoji_key="btn_wheel", style=SHOP, callback_data="menu:wheel")])
    if st["chests_ready"]:
        extra.append([btn(f"جعبه‌ی آرنا آماده ({st['chests_ready']})", emoji_key="btn_chests", style=SHOP,
                          callback_data="menu:arena_chests")])
    if st["boxes_ready"]:
        extra.append([btn(f"باکس مأموریت ({st['boxes_ready']})", emoji_key="btn_missions", style=SHOP,
                          callback_data="menu:missions")])
    flat = [b for row in extra for b in row]
    rows += [flat[i:i + 2] for i in range(0, len(flat), 2)]

    lines += [
        "",
        f"🎯 مأموریت‌های امروز: <code>{st['missions_done']}/{st['missions_total']}</code>"
        + (f" · تا باکس بعدی <code>{max(0, st['next_box']['need'] - st['points'])}</code> امتیاز" if st["next_box"] else ""),
        f"{get_emoji('energy')} انرژی: <code>{st['energy']}/{st['max_energy']}</code>",
    ]
    quick = [btn("شکار", emoji_key="btn_hunt", style=BATTLE, callback_data="menu:hunt"),
             btn("آرنا", emoji_key="btn_arena", style=BATTLE, callback_data="menu:arena")]
    rows.append(quick)
    more = [btn("مأموریت‌ها", emoji_key="btn_missions", style=NAV, callback_data="menu:missions")]
    if st["dispatch_open"]:
        more.append(btn("اعزام", emoji_key="btn_dispatch", style=NAV, callback_data="menu:dispatch"))
    rows.append(more)
    rows.append([back_btn("menu:me", "منوی اصلی")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def today_panel(update: Update, context: ContextTypes.DEFAULT_TYPE, note: str = "") -> None:
    st = await run_db(_state_sync, update.effective_user)
    text, keyboard = _render(st, note)
    from game.media import get_feature_image_path

    await send_screen(update, text, photo=get_feature_image_path("missions"), parse_mode="HTML", reply_markup=keyboard)


def _collect_all_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    return today.collect_all(user)


async def today_all_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    res = await run_db(_collect_all_sync, update.effective_user)
    if not res["lines"]:
        await query.answer("چیزی برای دریافت نبود.")
        await today_panel(update, context)
        return
    await query.answer("🎁 گرفتی!")
    note = ["🎉 <b>همه رو گرفتی!</b>"]
    amounts = _amounts(res["totals"])
    if amounts:
        note.append(amounts)
    note += res["lines"][:6]
    for m in res["missions"][:3]:
        note.append(f"{get_emoji('mission')} مأموریت «{m['label']}» تکمیل شد! {mission_reward_text(m)}")
    await today_panel(update, context, note="\n".join(note))


async def today_home_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    await today_panel(update, context)


def register(application) -> None:
    application.add_handler(CommandHandler("today", today_panel, filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(today_home_callback, pattern=r"^today:home$"))
    application.add_handler(CallbackQueryHandler(today_all_callback, pattern=r"^today:all$"))

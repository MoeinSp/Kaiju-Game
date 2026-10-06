"""«📋 امروز» — the private-chat screen for game/today.py: what's ready right now and one
«دریافت همه» button that takes all of it (it also spins the wheel and opens the boxes and
chests, then shows everything won in one collage).

Kept short on purpose: it is shown as a photo caption (~1,000 chars)."""

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

from bio_lab.repository import get_or_create_user
from bot.buttons import BATTLE, CONFIRM, NAV, back_btn, btn
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
    lines = ["📋 <b>پاداش امروز</b>", _DIV]
    if note:
        lines = [note, ""] + lines
    rows = []

    # ── live, time-limited things first ──
    if st["boss"]:  # only while a boss is here AND the player still has a hit
        lines.append(f"👹 <b>غول سرگردان اینجاست!</b> <code>{st['boss']['hits_left']}</code> ضربه داری.")
        rows.append([btn("ضربه به غول", emoji_key="btn_worldboss", style=BATTLE, callback_data="tdy:worldboss")])
    if st["tournament_open"]:
        lines.append("🏟 ثبت‌نام <b>جام آخر هفته</b> بازه (رایگان).")
        rows.append([btn("ثبت‌نام در جام", emoji_key="btn_tournament", style=BATTLE, callback_data="tdy:tournament")])
    if st["festival"]:
        lines.append(f"{st['festival']['emoji']} جشنواره‌ی «{st['festival']['title']}» در جریانه.")
    if st["rule"]:
        lines.append(st["rule"])
    if len(lines) > 2 + (2 if note else 0):
        lines.append("")

    # ── ready to take ──
    ready = [f"{label}: <code>+{amount:,}</code>" for label, amount, _resource in st["pending"]]
    if st["dispatch_ready"]:
        ready.append(f"🧭 <code>{st['dispatch_ready']}</code> هیولا از مأموریت برگشته")
    if st["event_daily"]:
        ready.append("🎁 جایزه‌ی روزانه‌ی رویداد")
    if st["story_ready"]:
        ready.append("🎯 پاداش مأموریت داستانی")
    if st["wheel"]:
        ready.append("🎡 گردونه‌ی رایگان امروز")
    if st["free_boxes"]:
        ready.append(f"📦 <code>{len(st['free_boxes'])}</code> باکس رایگان")
    if st["chests_ready"]:
        ready.append(f"🎁 <code>{st['chests_ready']}</code> جعبه‌ی آرنا")
    if st["boxes_ready"]:
        ready.append(f"🎯 <code>{st['boxes_ready']}</code> باکس مأموریت")
    if ready:
        lines += ["✅ <b>آماده‌ی دریافت:</b>"] + ready
        rows.append([btn(f"دریافت همه ({today.collectable_count(st)})", emoji_key="btn_confirm", style=CONFIRM,
                         callback_data="today:all")])
    else:
        lines.append("<i>چیزی برای جمع کردن نمونده.</i>")

    # things still worth doing today — each disappears once there's nothing left of it:
    # the missions when all of today's are done, dispatch when nothing can be sent
    missions_left = st["missions_total"] - st["missions_done"]
    lines.append("")
    if missions_left > 0:
        lines.append(
            f"🎯 مأموریت‌های امروز: <code>{st['missions_done']}/{st['missions_total']}</code>"
            + (f" · تا باکس بعدی <code>{max(0, st['next_box']['need'] - st['points'])}</code> امتیاز" if st["next_box"] else "")
        )
    if st["can_dispatch"]:
        lines.append("🧭 می‌تونی یه هیولا بفرستی مأموریت.")
    lines.append(f"{get_emoji('energy')} انرژی: <code>{st['energy']}/{st['max_energy']}</code>")
    quick = [btn("شکار", emoji_key="btn_hunt", style=BATTLE, callback_data="tdy:hunt"),
             btn("آرنا", emoji_key="btn_arena", style=BATTLE, callback_data="tdy:arena")]
    rows.append(quick)
    more = []
    if missions_left > 0:
        more.append(btn("مأموریت‌ها", emoji_key="btn_missions", style=NAV, callback_data="tdy:missions"))
    if st["can_dispatch"]:
        more.append(btn("اعزام", emoji_key="btn_dispatch", style=NAV, callback_data="tdy:dispatch"))
    if more:
        rows.append(more)
    rows.append([back_btn("menu:me", "منوی اصلی")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def today_panel(update: Update, context: ContextTypes.DEFAULT_TYPE, note: str = "", photo: str | None = None) -> None:
    st = await run_db(_state_sync, update.effective_user)
    text, keyboard = _render(st, note)
    from game.media import get_feature_image_path

    await send_screen(update, text, photo=photo or get_feature_image_path("missions"), parse_mode="HTML",
                      reply_markup=keyboard)


def _collect_all_sync(tg_user):
    """Collect everything, and build the collage of the creatures/items won HERE (sync):
    it reads image files and may draw a new one."""
    from game.media import composite_lootbox_batch_image

    user, _ = get_or_create_user(tg_user)
    res = today.collect_all(user)
    try:
        res["photo"] = composite_lootbox_batch_image(res["rolls"], "جایزه‌های امروز") if res["rolls"] else None
    except Exception:  # noqa: BLE001 — the picture is a nicety; the rewards are already paid
        res["photo"] = None
    return res


def _result_note(res: dict) -> str:
    """The result of «دریافت همه»: totals first, then one tidy block per kind of reward."""
    out = ["🎉 <b>همه رو گرفتی!</b>"]
    amounts = _amounts(res["totals"])
    if amounts:
        out.append(amounts)
    budget = 18  # lines — the screen is a photo caption
    for title, items in res["sections"]:
        out += ["", f"<b>{title}</b>"]
        shown = items[:max(1, budget)]
        out += shown
        if len(items) > len(shown):
            out.append(f"<i>و {len(items) - len(shown)} مورد دیگه</i>")
        budget -= len(shown)
    done = res["missions"]
    if done:
        out += ["", "<b>مأموریت‌های تکمیل‌شده</b>"]
        out += [f"{get_emoji('mission')} {m['label']}: {mission_reward_text(m)}" for m in done[:4]]
    return "\n".join(out)


async def today_all_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    res = await run_db(_collect_all_sync, update.effective_user)
    if not res["sections"]:
        await query.answer("چیزی برای دریافت نبود.")
        await today_panel(update, context)
        return
    await query.answer("🎁 گرفتی!")
    await today_panel(update, context, note=_result_note(res), photo=res["photo"])


async def today_home_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    await today_panel(update, context)


def register(application) -> None:
    application.add_handler(CommandHandler("today", today_panel, filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(today_home_callback, pattern=r"^today:home$"))
    application.add_handler(CallbackQueryHandler(today_all_callback, pattern=r"^today:all$"))

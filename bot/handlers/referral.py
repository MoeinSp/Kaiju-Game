"""«🎁 دعوت دوستان» — the referral screen: your invite link, how it's doing, and
a button to collect any rewards that are ready.

Rewards also pay out automatically via the notification job once an invited friend
crosses the lab-level milestone, but the button lets a referrer claim on demand and
see exactly where each invite stands.
"""

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

from bio_lab.repository import get_or_create_user
from bot.buttons import CONFIRM, NAV, back_btn, btn
from bot.utils import run_db, safe_edit_message_text, send_screen
from game import referral

_REF_PAGE = 10


def _panel_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    return referral.stats(user)


def _friend_status(f: dict, milestone: int) -> str:
    if f["paid"]:
        return "✅ جایزه گرفته شد"
    if f["reached"]:
        return "🎉 آماده‌ی دریافت جایزه!"
    return f"⛔ شرایط ناقص — سطح {f['level']}/{milestone}"


def _render(st: dict, filt: str = "all", page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    friends = st["friends"]
    incomplete = [f for f in friends if not f["paid"] and not f["reached"]]
    successful = [f for f in friends if f["paid"]]
    ready = [f for f in friends if f["reached"] and not f["paid"]]
    shown = {"all": friends, "success": successful, "incomplete": incomplete, "ready": ready}.get(filt, friends)

    lines = [
        "🎁 <b>دعوت دوستان</b>",
        "<blockquote>لینکت رو برای دوستات بفرست. وقتی یکی با لینک تو بیاد و به "
        f"<b>سطح آزمایشگاه {st['milestone_level']}</b> برسه، <b>هردوتون</b> جایزه می‌گیرین:\n"
        f"• تو: <b>{st['referrer_reward']}</b> 💎   • دوستت: <b>{st['friend_reward']}</b> 💎</blockquote>",
        "",
        f"👥 کل: <b>{st['total']}</b>   ✅ موفق: <b>{len(successful)}</b>   "
        f"🎉 آماده: <b>{len(ready)}</b>   ⛔ شرایط ناقص: <b>{len(incomplete)}</b>",
    ]

    if not friends:
        lines.append("\n<i>هنوز کسی رو دعوت نکردی. لینکت رو بفرست!</i>")
    else:
        titles = {"all": "همه", "success": "موفق‌ها", "incomplete": "شرایط ناقص", "ready": "آماده‌ی جایزه"}
        total_pages = max(1, (len(shown) + _REF_PAGE - 1) // _REF_PAGE)
        page = max(0, min(page, total_pages - 1))
        chunk = shown[page * _REF_PAGE : (page + 1) * _REF_PAGE]
        head = f"\n<b>📋 {titles.get(filt, 'همه')}</b> ({len(shown)})"
        if total_pages > 1:
            head += f"  <i>صفحه {page + 1}/{total_pages}</i>"
        lines.append(head)
        if not chunk:
            lines.append("<i>موردی نیست.</i>")
        for f in chunk:
            lines.append(f"• {f['name']} — {_friend_status(f, st['milestone_level'])}")

    lines.append("\n🔗 <b>لینک دعوت تو:</b>")
    lines.append(f"<code>{st['link']}</code>")

    rows = []
    if friends:
        # filter "menu" — tap to switch which list you're looking at
        rows.append([
            btn(("• " if filt == "all" else "") + "همه", style=NAV, callback_data="ref_view:all:0"),
            btn(("• " if filt == "success" else "") + "✅ موفق", style=NAV, callback_data="ref_view:success:0"),
            btn(("• " if filt == "incomplete" else "") + "⛔ ناقص", style=NAV, callback_data="ref_view:incomplete:0"),
        ])
        total_pages = max(1, (len(shown) + _REF_PAGE - 1) // _REF_PAGE)
        nav = []
        if page > 0:
            nav.append(btn("◀️ قبلی", style=NAV, callback_data=f"ref_view:{filt}:{page - 1}"))
        if page < total_pages - 1:
            nav.append(btn("بعدی ▶️", style=NAV, callback_data=f"ref_view:{filt}:{page + 1}"))
        if nav:
            rows.append(nav)
    if st["claimable"] > 0:
        rows.append([btn(
            f"🎉 دریافت جایزه ({st['claimable']} دعوت آماده)",
            emoji_key="btn_confirm", style=CONFIRM, callback_data="ref_claim",
        )])
    rows.append([back_btn("menu:cat_rewards", "بازگشت به جایزه‌ها")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def referral_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    st = await run_db(_panel_sync, update.effective_user)
    text, keyboard = _render(st)
    await send_screen(update, text, parse_mode="HTML", reply_markup=keyboard)


async def referral_view_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, filt, page = query.data.split(":")
    st = await run_db(_panel_sync, update.effective_user)
    await query.answer()
    text, keyboard = _render(st, filt, int(page))
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


def _claim_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    result = referral.claim_ready(user)
    return result, referral.stats(user)


async def referral_claim_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    result, st = await run_db(_claim_sync, update.effective_user)
    if result["claimed"] <= 0:
        await query.answer("جایزه‌ی آماده‌ای نداری.", show_alert=True)
        return
    await query.answer(f"🎉 {result['diamonds']} 💎 گرفتی!")
    text, keyboard = _render(st)
    await safe_edit_message_text(
        query,
        f"🎉 <b>{result['diamonds']} 💎 از {result['claimed']} دعوت موفق گرفتی!</b>\n━━━━━━━━━━\n" + text,
        parse_mode="HTML",
        reply_markup=keyboard,
    )


def register(application) -> None:
    application.add_handler(CommandHandler("invite", referral_panel, filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(referral_claim_callback, pattern=r"^ref_claim$"))
    application.add_handler(CallbackQueryHandler(referral_view_callback, pattern=r"^ref_view:"))

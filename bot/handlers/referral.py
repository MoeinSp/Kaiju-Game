"""«دعوت دوستان» — the referral screen: your invite link, how it's doing, and
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
from game.emoji import get_emoji

_REF_PAGE = 10


def _panel_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    return referral.stats(user)


def _friend_status(f: dict, milestone: int) -> str:
    if f["paid"]:
        return f"{get_emoji('confirm')} معتبر — جایزه گرفته شد"
    if f.get("banned"):
        return "🚫 مسدود — حساب نمی‌شه"
    if f["reached"]:
        return "🎉 آماده‌ی دریافت جایزه!"
    need_level = f.get("need_level", milestone)
    parts = [f"سطح {min(f['level'], need_level)}/{need_level}"]
    if f.get("need_days"):
        parts.append(f"روز {min(f['days'], f['need_days'])}/{f['need_days']}")
    return "⏳ " + " · ".join(parts)


def _render(st: dict, filt: str = "all", page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    friends = st["friends"]
    incomplete = [f for f in friends if not f["paid"] and not f["reached"]]
    successful = [f for f in friends if f["paid"]]
    ready = [f for f in friends if f["reached"] and not f["paid"]]
    shown = {"all": friends, "success": successful, "incomplete": incomplete, "ready": ready}.get(filt, friends)

    lines = [
        f"{get_emoji('gift')} <b>دعوت دوستان</b>",
        "<blockquote>لینکت رو برای دوستات بفرست. دعوت وقتی <b>معتبر</b> می‌شه که دوستت:\n"
        f"• به <b>سطح آزمایشگاه {st['milestone_level']}</b> برسه، و\n"
        f"• <b>{st['min_days']} روز جدا</b> بازی کنه.\n"
        "اون‌وقت <b>هردوتون</b> جایزه می‌گیرین:\n"
        f"• تو: <b>{st['referrer_reward_text']}</b>\n"
        f"• دوستت: <b>{st['friend_reward_text']}</b>\n"
        f"💰 تا <b>{st['commission_days']} روز</b>، <b>{st['commission']}٪</b> هر خرید دوستت هم الماس برای تو.</blockquote>",
        "",
        f"👥 کل: <b>{st['total']:,}</b>   ✅ معتبر: <b>{len(successful):,}</b>   "
        f"🎉 آماده: <b>{len(ready):,}</b>   ⏳ در راه: <b>{len(incomplete):,}</b>",
    ]
    if filt == "all" and page == 0:
        lines.append("")
        lines.append("🏅 <b>جایزه‌های پله‌ای</b>")
        for need, text, done in st["tiers"]:
            lines.append(f"{'✅' if done else '▫️'} {need} دعوت معتبر ← {text}")
        nxt = st.get("next_tier")
        if nxt:
            left = nxt[0] - len(successful)
            lines.append(f"<i>🎯 فقط <b>{left}</b> دعوت معتبر دیگه تا پله‌ی بعدی!</i>")
        p1, p2, p3 = st["week_prizes"]
        lines.append("")
        lines.append(f"🏆 <b>برترین دعوت‌کننده‌های این هفته</b>")
        lines.append(f"<i>جایزه‌ی آخر هفته: 🥇 {p1:,} · 🥈 {p2:,} · 🥉 {p3:,} 💎 (حداقل {st['week_min']} دعوت معتبر)</i>")
        if st["week_top"]:
            for i, row in enumerate(st["week_top"], 1):
                me = " ← تو" if row["me"] else ""
                lines.append(f"{i}. {row['name']} — <b>{row['n']}</b>{me}")
        else:
            lines.append("<i>این هفته هنوز کسی دعوت معتبر نداره — نفر اول تو باش!</i>")
        lines.append("")
        lines.append("🛡 <i>حساب فیک حساب نمی‌شه: فقط بازیکنی که واقعاً چند روز بازی کنه معتبره، "
                     "حساب‌های مسدود حذف می‌شن و روزی حداکثر ۱۰ دعوت تأیید می‌شه.</i>")

    if not friends:
        lines.append("\n<i>هنوز کسی رو دعوت نکردی. لینکت رو بفرست!</i>")
    else:
        titles = {"all": "همه", "success": "معتبرها", "incomplete": "در راه", "ready": "آماده‌ی جایزه"}
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

    rows = [[btn("فرستادن لینک برای دوستان", emoji_key="btn_referral", style=CONFIRM, url=st["share_url"])]]
    if friends:
        # filter "menu" — tap to switch which list you're looking at
        rows.append([
            btn(("• " if filt == "all" else "") + "همه", style=NAV, callback_data="ref_view:all:0"),
            btn(("• " if filt == "success" else "") + "✅ معتبر", style=NAV, callback_data="ref_view:success:0"),
            btn(("• " if filt == "incomplete" else "") + "⏳ در راه", style=NAV, callback_data="ref_view:incomplete:0"),
        ])
        total_pages = max(1, (len(shown) + _REF_PAGE - 1) // _REF_PAGE)
        nav = []
        if page > 0:
            nav.append(btn("قبلی", emoji_key="btn_prev", style=NAV, callback_data=f"ref_view:{filt}:{page - 1}"))
        if page < total_pages - 1:
            nav.append(btn("بعدی", emoji_key="btn_next", style=NAV, callback_data=f"ref_view:{filt}:{page + 1}"))
        if nav:
            rows.append(nav)
    if st["claimable"] > 0:
        rows.append([btn(
            f"🎉 دریافت جایزه ({st['claimable']} دعوت آماده)",
            emoji_key="btn_confirm", style=CONFIRM, callback_data="ref_claim",
        )])
    rows.append([back_btn("menu:hub_city", "بازگشت به شهر")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def referral_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    st = await run_db(_panel_sync, update.effective_user)
    text, keyboard = _render(st)
    from game.media import get_feature_image_path
    photo = get_feature_image_path("referral")
    await send_screen(update, text, photo=photo, parse_mode="HTML", reply_markup=keyboard)


async def referral_view_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, filt, page = query.data.split(":")
    st = await run_db(_panel_sync, update.effective_user)
    await query.answer()
    text, keyboard = _render(st, filt, int(page))
    from game.media import get_feature_image_path
    photo = get_feature_image_path("referral")
    await send_screen(update, text, photo=photo, parse_mode="HTML", reply_markup=keyboard)


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
    from game.media import get_feature_image_path
    photo = get_feature_image_path("referral")
    extra = "".join(line + "\n" for line in result.get("tier_lines", []))
    await send_screen(
        update,
        f"🎉 <b>{result['diamonds']} 💎 از {result['claimed']} دعوت معتبر گرفتی!</b>\n" + extra + "━━━━━━━━━━\n" + text,
        photo=photo,
        parse_mode="HTML",
        reply_markup=keyboard,
    )


def register(application) -> None:
    application.add_handler(CommandHandler("invite", referral_panel, filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(referral_claim_callback, pattern=r"^ref_claim$"))
    application.add_handler(CallbackQueryHandler(referral_view_callback, pattern=r"^ref_view:"))

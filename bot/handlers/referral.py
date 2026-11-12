"""«دعوت دوستان» — the referral screen: your invite link, how each invited friend is
growing, and a button to collect any rewards that are ready.

Rewards also pay out automatically via the notification job once an invited friend
reaches a growth step (game/referral.py), but the button lets a referrer claim on demand
and see exactly where each invite stands. The reward tables live on their own text page
(«جایزه‌ها و قوانین»): a photo caption only holds ~1000 characters.
"""

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

from bio_lab.repository import get_or_create_user
from bot.buttons import CONFIRM, NAV, back_btn, btn
from bot.utils import run_db, send_screen
from game import referral
from game.emoji import get_emoji

_REF_PAGE = 8


def _panel_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    return referral.stats(user)


def _friend_status(f: dict) -> str:
    if f.get("banned"):
        return "🚫 مسدود — حساب نمی‌شه"
    if f["reached"]:
        return "🎉 آماده‌ی دریافت جایزه!"
    if f.get("done"):
        return f"{get_emoji('confirm')} همه‌ی جایزه‌ها گرفته شد"
    parts = [f"پله {f['step']}/{f['steps']}", f"سطح {f['level']}"]
    if f.get("next_level"):
        nxt = f"بعدی: سطح {f['next_level']}"
        if f.get("next_days") and f["days"] < f["next_days"]:
            nxt += f" + روز {f['days']}/{f['next_days']}"
        parts.append(nxt)
    icon = f"{get_emoji('confirm')} " if f["started"] else "⏳ "
    return icon + " · ".join(parts)


def _render_rules(st: dict) -> tuple[str, InlineKeyboardMarkup]:
    """«جایزه‌ها و قوانین» — every reward table on one text page."""
    lines = [
        f"{get_emoji('gift')} <b>جایزه‌ها و قوانین دعوت</b>",
        "",
        "📈 <b>جایزه‌ی رشد دوستت</b>",
        "<i>هر پله وقتی باز می‌شه که دوستت به اون سطح برسه و اون‌قدر روزِ جدا بازی کرده باشه.</i>",
    ]
    for lvl, days, ref_text, fr_text in st["steps"]:
        lines.append(f"\n• <b>سطح {lvl}</b> + {days} روز\n   👤 تو: {ref_text}\n   🤝 دوستت: {fr_text}")
    lines += [
        "",
        f"🎁 <b>روی هم از هر دوست برای تو:</b>\n{st['steps_total']}",
        f"🤝 <b>روی هم برای خود دوستت:</b>\n{st['friend_total']}",
        "",
    ]
    p1, p2, p3 = st["week_prizes"]
    lines += ["🏆 <b>برترین دعوت‌کننده‌های این هفته</b>",
              f"<i>آخر هفته: 🥇 {p1:,} · 🥈 {p2:,} · 🥉 {p3:,} 💎 — رتبه با تعداد دوست‌هایی که اون هفته "
              f"به سطح {st['active_level']} رسیدن (حداقل {st['week_min']} نفر)</i>"]
    if st["week_top"]:
        for i, row in enumerate(st["week_top"], 1):
            me = " ← تو" if row["me"] else ""
            lines.append(f"{i}. {row['name']} — <b>{row['n']}</b>{me}")
    else:
        lines.append("<i>این هفته هنوز کسی نیست — نفر اول تو باش!</i>")
    lines += ["", "➕ <b>ربات رو به یه گروه شلوغ اضافه کن</b>",
              "<i>اولین کسی که ربات رو به یه گروه اضافه می‌کنه، با رشد گروه جایزه می‌گیره. بازیکن واقعی: توی اون "
              "گروه بازی کرده، سطح ۵+ و حداقل ۳ روز جدا بازی کرده.</i>"]
    for need, text in st["group_steps"]:
        lines.append(f"• {need} بازیکن واقعی ← {text}")
    for g in st["my_groups"][:5]:
        nxt = f" · بعدی: {g['next']}" if g["next"] else " · ✅ همه گرفته شد"
        lines.append(f"   👥 {g['title']} — {g['real']} بازیکن واقعی{nxt}")
    lines += [
        "",
        f"💰 <b>پورسانت:</b> تا {st['commission_days']} روز، {st['commission']}٪ هر خرید دوستت به‌صورت الماس به تو می‌رسه.",
        "",
        "🛡 <b>حساب فیک به درد نمی‌خوره</b>",
        "• جایزه‌ها با <b>رشد واقعی</b> دوستت باز می‌شن و هر پله چند <b>روزِ جدا</b> بازی می‌خواد.",
        "• بیشترِ جایزه توی سطح‌های بالاست؛ حساب فیک باید هفته‌ها بازی بشه.",
        "• حساب مسدود هیچ جایزه‌ای نمی‌ده.",
        f"• هر روز حداکثر <b>{st['daily_cap']}</b> دوستت جایزه‌ی سطح {st['active_level']} رو می‌دن (بقیه فردا، از بین نمی‌رن).",
    ]
    rows = [
        [btn("فرستادن لینک دعوت", emoji_key="btn_referral", style=CONFIRM, url=st["share_url"])],
        [back_btn("ref_view:all:0", "بازگشت به دعوت")],
    ]
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def _render(st: dict, filt: str = "all", page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    if filt == "rules":
        return _render_rules(st)
    friends = st["friends"]
    started = [f for f in friends if f["started"]]
    waiting = [f for f in friends if not f["started"] and not f["banned"]]
    ready = [f for f in friends if f["reached"]]
    shown = {"all": friends, "started": started, "waiting": waiting, "ready": ready}.get(filt, friends)

    lines = [
        f"{get_emoji('gift')} <b>دعوت دوستان</b>",
        "<blockquote>دوستت هر بار رشد کنه، <b>هر دوتون</b> جایزه می‌گیرین."
        f"\nاز هر دوست برای تو (سطح {st['steps'][0][0]} تا {st['steps'][-1][0]}):\n"
        + "\n".join(st["steps_total_lines"])
        + f"\n💰 به‌علاوه‌ی {st['commission']}% خریدهاش تا {st['commission_days']} روز</blockquote>",
        "━━━━━━━━━━",
        f"👥 دعوتی‌ها: <b>{st['total']:,}</b>",
        f"✅ در حال رشد {len(started):,} · 🎉 آماده {len(ready):,} · ⏳ هنوز {len(waiting):,}",
    ]

    if not friends:
        lines.append("<i>هنوز کسی رو دعوت نکردی؛ لینکت رو بفرست!</i>")
    else:
        titles = {"all": "همه", "started": "در حال رشد", "waiting": "هنوز جایزه نداده", "ready": "آماده‌ی جایزه"}
        total_pages = max(1, (len(shown) + _REF_PAGE - 1) // _REF_PAGE)
        page = max(0, min(page, total_pages - 1))
        chunk = shown[page * _REF_PAGE: (page + 1) * _REF_PAGE]
        head = f"\n<b>📋 {titles.get(filt, 'همه')}</b> · {len(shown)}"
        if total_pages > 1:
            head += f" · <i>صفحه {page + 1}/{total_pages}</i>"
        lines.append(head)
        if not chunk:
            lines.append("<i>موردی نیست.</i>")
        for f in chunk:
            lines.append(f"• {f['name']} — {_friend_status(f)}")

    lines += ["━━━━━━━━━━", "🔗 لینک دعوت تو:", f"<code>{st['link']}</code>"]

    from telegram import InlineKeyboardButton

    from bot.handlers.growth import add_group_url

    rows = [
        [btn("فرستادن لینک دعوت", emoji_key="btn_referral", style=CONFIRM, url=st["share_url"])],
        [btn("🥊 چالش رفیق", style=NAV, callback_data="ch:new"),
         InlineKeyboardButton("➕ ربات توی گروهت", url=add_group_url())],
        [btn("🏅 جایزه‌ها و قوانین", style=NAV, callback_data="ref_view:rules:0")],
    ]
    if friends:
        rows.append([
            btn(("• " if filt == "all" else "") + "همه", style=NAV, callback_data="ref_view:all:0"),
            btn(("• " if filt == "started" else "") + "✅ در حال رشد", style=NAV, callback_data="ref_view:started:0"),
            btn(("• " if filt == "waiting" else "") + "⏳ هنوز نه", style=NAV, callback_data="ref_view:waiting:0"),
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
            f"🎉 دریافت جایزه · {st['claimable']}",
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
    photo = None if filt == "rules" else get_feature_image_path("referral")   # the rules page is too long for a caption
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
    got = referral.reward_text(result["reward"])
    await query.answer("🎉 جایزه‌ها گرفته شد!")
    text, keyboard = _render(st)
    from game.media import get_feature_image_path
    photo = get_feature_image_path("referral")
    await send_screen(
        update,
        f"🎉 <b>جایزه‌ی رشد {result['claimed']} دوستت رو گرفتی:</b>\n{got}\n━━━━━━━━━━\n" + text,
        photo=photo,
        parse_mode="HTML",
        reply_markup=keyboard,
    )


def register(application) -> None:
    application.add_handler(CommandHandler("invite", referral_panel, filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(referral_claim_callback, pattern=r"^ref_claim$"))
    application.add_handler(CallbackQueryHandler(referral_view_callback, pattern=r"^ref_view:"))

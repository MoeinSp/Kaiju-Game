"""«🏰 پرک‌های اتحاد» + «⚔️ جنگ هفتگی» — the alliance-depth screens.

Perks: the leader spends treasury gold on alliance-wide XP / Pass boosts that
benefit every member. War: members' activity earns war points; the weekly
leaderboard shows the standings, and the top alliance's treasury wins a bonus at
week's end (settled by the notification job).
"""

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes

from bio_lab.models import Alliance
from bio_lab.repository import creature_name, get_or_create_user
from bot.buttons import BATTLE, BUILD, CONFIRM, DANGER, back_btn, btn
from bot.utils import run_db, safe_edit_message_text
from game import alliance
from game.creature import GameError


def _perks_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    if user.alliance_id is None:
        return None
    al = Alliance.objects.get(id=user.alliance_id)
    info = alliance.buildings_info(al)
    # leader OR deputy may upgrade alliance buildings (buy_perk enforces the same)
    info["is_manager"] = alliance._is_manager(user, al)
    info["name"] = al.name
    return info


def _perks_render(info: dict) -> tuple[str, InlineKeyboardMarkup]:
    lines = [
        f"🏰 <b>ساختمون‌های اتحاد {info['name']}</b>",
        f"<blockquote>💰 خزانه: <b>{info['treasury']:,}</b> طلا\n"
        "ساختمون‌ها از خزانه ارتقا می‌گیرن و مزایاشون برای <b>همه‌ی اعضا</b>ست.</blockquote>",
    ]
    rows = []
    for b in info["buildings"]:
        cap = " (تکمیل)" if b["maxed"] else ""
        b_max = b.get("max_level", info["max_level"])
        lines.append(
            f"\n{b['emoji']} <b>{b['title']}</b> — سطح <b>{b['level']}</b>/{b_max}{cap}\n"
            f"   <i>{b['desc']}: {b['effect']}</i>"
        )
        if info["is_manager"] and not b["maxed"]:
            rows.append([btn(
                f"{b['emoji']} ارتقای {b['title']} → {b['next_effect']} ({b['cost']:,} طلا)",
                style=BUILD, callback_data=f"ally_perk_buy:{b['key']}",
            )])
    if info["vault_income"] > 0:
        rows.append([btn(f"🏦 جمع‌آوری درآمد خزانه ({info['vault_income']} طلا/روز)", style=BUILD, callback_data="ally_vault_collect")])
    if not info["is_manager"]:
        lines.append("\n<i>فقط رهبر یا قائم‌مقام اتحاد می‌تونه ساختمون ارتقا بده.</i>")
    rows.append([back_btn("menu:alliance_info")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def perks_panel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    info = await run_db(_perks_sync, update.effective_user)
    await query.answer()
    if info is None:
        await safe_edit_message_text(query, "توی هیچ اتحادی نیستی.", reply_markup=InlineKeyboardMarkup([[back_btn("menu:me")]]))
        return
    text, keyboard = _perks_render(info)
    from game.media import get_alliance_building_image_path
    hall_b = next((b for b in info["buildings"] if b["key"] == "hall"), None)
    hall_lvl = hall_b["level"] if hall_b else 1
    photo_path = get_alliance_building_image_path("hall", hall_lvl)
    await safe_edit_message_text(query, text, photo=photo_path, parse_mode="HTML", reply_markup=keyboard)


def _vault_collect_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    result = alliance.collect_vault(user)
    return result, _perks_sync(tg_user)


async def vault_collect_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        result, info = await run_db(_vault_collect_sync, update.effective_user)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer(f"🏦 {result['income']} طلا به خزانه اضافه شد!")
    text, keyboard = _perks_render(info)
    from game.media import get_alliance_building_image_path
    photo_path = get_alliance_building_image_path("vault", next((b["level"] for b in info["buildings"] if b["key"] == "vault"), 1))
    await safe_edit_message_text(query, text, photo=photo_path, parse_mode="HTML", reply_markup=keyboard)


def _buy_sync(tg_user, perk_key):
    user, _ = get_or_create_user(tg_user)
    alliance.buy_perk(user, perk_key)
    return _perks_sync(tg_user)


async def perk_buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    perk_key = query.data.split(":")[1]
    try:
        info = await run_db(_buy_sync, update.effective_user, perk_key)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer("✅ پرک ارتقا یافت!")
    text, keyboard = _perks_render(info)
    from game.media import get_alliance_building_image_path
    b_lvl = next((b["level"] for b in info["buildings"] if b["key"] == perk_key), 1)
    photo_path = get_alliance_building_image_path(perk_key, b_lvl)
    await safe_edit_message_text(query, text, photo=photo_path, parse_mode="HTML", reply_markup=keyboard)


def _war_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    board = alliance.war_leaderboard()
    my_id = user.alliance_id
    my_name = None
    if my_id:
        my_name = Alliance.objects.filter(id=my_id).values_list("name", flat=True).first()
    return {"board": board, "my_id": my_id, "my_name": my_name, "bonus": alliance.WAR_WINNER_TREASURY_BONUS}


async def war_panel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    view = await run_db(_war_sync, update.effective_user)
    await query.answer()
    lines = [
        "⚔️ <b>جنگ هفتگی اتحادها</b>",
        f"<blockquote>هر فعالیتِ اعضا امتیاز جنگ می‌ده. آخر هفته خزانه‌ی اتحاد اول "
        f"<b>{view['bonus']} طلا</b> جایزه می‌گیره!</blockquote>",
        "",
    ]
    medals = ["🥇", "🥈", "🥉"]
    if view["board"]:
        for i, row in enumerate(view["board"]):
            tag = medals[i] if i < 3 else f"{i + 1}."
            mine = " ⬅️ <b>اتحاد تو</b>" if row["id"] == view["my_id"] else ""
            lines.append(f"{tag} <b>{row['name']}</b> — {row['war_points']} امتیاز{mine}")
        if view["my_id"] and not any(r["id"] == view["my_id"] for r in view["board"]):
            lines.append(f"\n<i>اتحاد تو ({view['my_name']}) هنوز توی جدول نیست — فعالیت کنید!</i>")
    else:
        lines.append("<i>این هفته هنوز هیچ اتحادی امتیاز نگرفته. اولین باشید!</i>")
    await safe_edit_message_text(
        query, "\n".join(lines), parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[back_btn("menu:alliance_info")]]),
    )


# ── One-day war panel ─────────────────────────────────────────────────────────

def _fmt_remaining(seconds: int) -> str:
    hours, rem = divmod(max(0, seconds), 3600)
    minutes = rem // 60
    if hours:
        return f"{hours} ساعت و {minutes} دقیقه"
    return f"{minutes} دقیقه"


def _war1d_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    if user.alliance_id is None:
        return {"in_alliance": False}
    view = alliance.war_view(user)
    # leader OR deputy may start the war (the deputy has the leader's full powers)
    al = Alliance.objects.filter(id=user.alliance_id).first()
    can_start = al is not None and user.id in (al.leader_id, al.deputy_id)
    return {"in_alliance": True, "view": view, "is_leader": can_start}


def _war1d_render(data: dict) -> tuple[str, InlineKeyboardMarkup]:
    view = data["view"]
    if view is None:
        lines = [
            "🔥 <b>جنگ یک‌روزه‌ی اتحادها</b>",
            "<blockquote>یه حریف هم‌قدرت پیدا می‌شه و ۲۴ ساعت باهاش می‌جنگید. "
            "امتیاز شروع فقط یه بخش کوچیک از قدرت اتحاده — <b>بقیه‌شو باید اعضا با «شرکت» بسازن</b>. "
            "پس هر چی اعضای بیشتری بیان، شانس بردتون بیشتره!\n\n"
            "🎁 هر کسی که شرکت کنه پاداش می‌گیره؛ تیم برنده پاداش خیلی بیشتر، و بهترین "
            "جنگجو (MVP) الماس جایزه می‌گیره.</blockquote>",
        ]
        rows = []
        if data["is_leader"]:
            rows.append([btn("🔎 پیدا کردن حریف و شروع جنگ", style=BATTLE, callback_data="ally_war_start")])
        else:
            lines.append("\n<i>فقط رهبر یا قائم‌مقام اتحاد می‌تونه جنگ رو شروع کنه.</i>")
        rows.append([back_btn("menu:alliance_info")])
        return "\n".join(lines), InlineKeyboardMarkup(rows)

    lead = "🟢 جلویی" if view["my_score"] > view["foe_score"] else ("🔴 عقبی" if view["my_score"] < view["foe_score"] else "🟡 مساوی")
    lines = [
        "🔥 <b>جنگ یک‌روزه در جریانه!</b>",
        f"\n⚔️ <b>{view['my_name']}</b>  vs  <b>{view['foe_name']}</b>",
        f"\n📊 امتیاز شما: <b>{view['my_score']}</b>  ({lead})",
        f"📊 امتیاز حریف: <b>{view['foe_score']}</b>",
        f"\n👥 شرکت‌کننده‌ها: شما <b>{view['my_participants']}</b> ┃ حریف <b>{view['foe_participants']}</b>",
        f"⏳ <b>{_fmt_remaining(view['remaining_seconds'])}</b> تا پایان",
    ]
    if view["contributors"]:
        lines.append("\n🏅 <b>جنگجوهای اتحاد تو:</b>")
        medals = ["🥇", "🥈", "🥉"]
        for i, c in enumerate(view["contributors"]):
            tag = medals[i] if i < 3 else "▫️"
            lines.append(f"{tag} {c['name']} — <b>{c['power']:,}</b> امتیاز")
    rows = []
    if view["ended"]:
        lines.append("\n<i>جنگ تموم شده — نتیجه و پاداش‌ها به‌زودی اعلام می‌شه.</i>")
    elif view["already_rallied"]:
        lines.append(f"\n✅ تو شرکت کردی و <b>{view['my_contribution']:,}</b> امتیاز اضافه کردی. بقیه‌ی اعضا رو هم خبر کن!")
    else:
        lines.append("\n<i>هنوز شرکت نکردی — قدرتتو اضافه کن و پاداش بگیر!</i>")
        rows.append([btn("💪 شرکت در جنگ (قدرتمو اضافه کن)", style=BATTLE, callback_data="ally_war_rally")])
    rows.append([back_btn("menu:alliance_info")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def war1d_panel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    data = await run_db(_war1d_sync, update.effective_user)
    await query.answer()
    if not data["in_alliance"]:
        await safe_edit_message_text(query, "توی هیچ اتحادی نیستی.", reply_markup=InlineKeyboardMarkup([[back_btn("menu:me")]]))
        return
    text, keyboard = _war1d_render(data)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


def _war_start_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    alliance.start_war(user)
    return _war1d_sync(tg_user)


async def war_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        data = await run_db(_war_start_sync, update.effective_user)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer("🔥 جنگ شروع شد!")
    text, keyboard = _war1d_render(data)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


def _war_rally_preview_sync(tg_user):
    """Validate the member can rally, then auto-pick their strongest usable kaiju so the
    confirm step can show it (and warn if the very strongest is stuck in the cave)."""
    user, _ = get_or_create_user(tg_user)
    view = alliance.war_view(user)
    if view is None:
        raise GameError("اتحادت الان توی هیچ جنگی نیست.")
    if view.get("ended"):
        raise GameError("این جنگ تموم شده، منتظر اعلام نتیجه باش.")
    if view.get("already_rallied"):
        raise GameError("تو قبلاً توی این جنگ شرکت کردی.")
    pick = alliance.war_creature_pick(user)
    if pick["chosen"] is None or pick["chosen_power"] <= 0:
        raise GameError("هیچ کایجوی قابل‌استفاده‌ای نداری (شاید همه توی غارن). اول یه کایجو آزاد کن.")
    return {
        "chosen_name": creature_name(pick["chosen"]),
        "chosen_power": pick["chosen_power"],
        "strongest_name": creature_name(pick["strongest"]) if pick["strongest"] else None,
        "strongest_power": pick["strongest_power"],
        "strongest_in_cave": pick["strongest_in_cave"],
    }


def _war_rally_confirm(p: dict) -> tuple[str, InlineKeyboardMarkup]:
    lines = ["⚔️ <b>شرکت در جنگ یک‌روزه</b>", ""]
    if p["strongest_in_cave"]:
        lines += [
            f"⚠️ قوی‌ترین کایجوت «<b>{p['strongest_name']}</b>» "
            f"(💪 {p['strongest_power']:,}) الان توی 🥚 <b>غار هیولا</b>ست و نمی‌تونه بجنگه.",
            "برای استفاده ازش، اول غار رو تموم کن و والدها رو آزاد کن.",
            "",
            f"🦅 الان با این کایجو اتک می‌زنی: <b>{p['chosen_name']}</b> — 💪 قدرت <b>{p['chosen_power']:,}</b>",
        ]
    else:
        lines.append(
            f"🦅 با قوی‌ترین کایجوت اتک می‌زنی: <b>{p['chosen_name']}</b> — 💪 قدرت <b>{p['chosen_power']:,}</b>"
        )
    lines += ["", "تأیید می‌کنی؟"]
    rows = InlineKeyboardMarkup([
        [btn("✅ تأیید و شرکت", emoji_key="btn_confirm", style=CONFIRM, callback_data="ally_war_rally_go")],
        [btn("↩️ بازگشت", emoji_key="btn_back", style=DANGER, callback_data="ally_war1d")],
    ])
    return "\n".join(lines), rows


async def war_rally_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """«شرکت در جنگ» → auto-pick the strongest usable kaiju and CONFIRM before rallying."""
    query = update.callback_query
    try:
        p = await run_db(_war_rally_preview_sync, update.effective_user)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer()
    text, keyboard = _war_rally_confirm(p)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


def _war_rally_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    result = alliance.rally_war(user)
    return result, _war1d_sync(tg_user)


async def war_rally_go_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        result, data = await run_db(_war_rally_sync, update.effective_user)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer(f"💪 +{result['contribution']} امتیاز (با {result['creature_name']})")
    text, keyboard = _war1d_render(data)
    note = (f"✅ با <b>{result['creature_name']}</b> (💪 {result['creature_power']:,}) شرکت کردی و "
            f"<b>{result['contribution']:,}</b> امتیاز اضافه شد.\n\n")
    await safe_edit_message_text(query, note + text, parse_mode="HTML", reply_markup=keyboard)


def register(application) -> None:
    application.add_handler(CallbackQueryHandler(perks_panel_callback, pattern=r"^ally_perks$"))
    application.add_handler(CallbackQueryHandler(perk_buy_callback, pattern=r"^ally_perk_buy:"))
    application.add_handler(CallbackQueryHandler(vault_collect_callback, pattern=r"^ally_vault_collect$"))
    application.add_handler(CallbackQueryHandler(war_panel_callback, pattern=r"^ally_war$"))
    application.add_handler(CallbackQueryHandler(war1d_panel_callback, pattern=r"^ally_war1d$"))
    application.add_handler(CallbackQueryHandler(war_start_callback, pattern=r"^ally_war_start$"))
    application.add_handler(CallbackQueryHandler(war_rally_callback, pattern=r"^ally_war_rally$"))
    application.add_handler(CallbackQueryHandler(war_rally_go_callback, pattern=r"^ally_war_rally_go$"))

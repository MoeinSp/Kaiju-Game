"""«🏰 پرک‌های اتحاد» + «⚔️ جنگ هفتگی» — the alliance-depth screens.

Perks: the leader spends treasury gold on alliance-wide XP / Pass boosts that
benefit every member. War: members' activity earns war points; the weekly
leaderboard shows the standings, and the top alliance's treasury wins a bonus at
week's end (settled by the notification job).
"""

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes

from bio_lab.models import Alliance
from bio_lab.repository import get_or_create_user
from bot.buttons import BUILD, back_btn, btn
from bot.utils import run_db, safe_edit_message_text
from game import alliance
from game.creature import GameError


def _perks_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    if user.alliance_id is None:
        return None
    al = Alliance.objects.get(id=user.alliance_id)
    info = alliance.perks_info(al)
    info["is_leader"] = al.leader_id == user.id
    info["name"] = al.name
    return info


def _perks_render(info: dict) -> tuple[str, InlineKeyboardMarkup]:
    xp_bonus = round(info["xp_level"] * alliance.XP_PERK_PER_LEVEL * 100)
    pass_bonus = round(info["pass_level"] * alliance.PASS_PERK_PER_LEVEL * 100)
    lines = [
        f"🏰 <b>پرک‌های اتحاد {info['name']}</b>",
        f"<blockquote>💰 خزانه: <b>{info['treasury']}</b> طلا\n"
        "پرک‌ها از خزانه خریداری می‌شن و برای <b>همه‌ی اعضا</b> کار می‌کنن.</blockquote>",
        f"\n⭐ بونوس XP: سطح <b>{info['xp_level']}</b>/{info['max_level']}  (+{xp_bonus}٪ XP)",
        f"🎟 بونوس پاس: سطح <b>{info['pass_level']}</b>/{info['max_level']}  (+{pass_bonus}٪ امتیاز پاس)",
    ]
    rows = []
    if info["is_leader"]:
        if info["xp_level"] < info["max_level"]:
            rows.append([btn(f"⭐ ارتقای بونوس XP ({info['xp_cost']} طلا)", style=BUILD, callback_data="ally_perk_buy:xp")])
        if info["pass_level"] < info["max_level"]:
            rows.append([btn(f"🎟 ارتقای بونوس پاس ({info['pass_cost']} طلا)", style=BUILD, callback_data="ally_perk_buy:pass")])
    else:
        lines.append("\n<i>فقط رهبر اتحاد می‌تونه پرک بخره.</i>")
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
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


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
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


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


def register(application) -> None:
    application.add_handler(CallbackQueryHandler(perks_panel_callback, pattern=r"^ally_perks$"))
    application.add_handler(CallbackQueryHandler(perk_buy_callback, pattern=r"^ally_perk_buy:"))
    application.add_handler(CallbackQueryHandler(war_panel_callback, pattern=r"^ally_war$"))

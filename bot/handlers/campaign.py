"""«🗺 کمپین» — the PvE stage ladder, fought with your 3v3 team."""

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

from bio_lab.models import Team
from bio_lab.repository import get_or_create_user
from bot.buttons import BATTLE, PRIMARY, back_btn, back_only_keyboard, btn
from bot.utils import run_db, safe_edit_message_text, send_screen
from game import campaign
from game.creature import GameError
from game.emoji import get_emoji
from game.energy import sync_energy
from game.teambattle import battle_summary, team_power


def _reward_text(reward: dict) -> str:
    parts = []
    if reward.get("coins"):
        parts.append(f"{reward['coins']} طلا")
    if reward.get("dna"):
        parts.append(f"{reward['dna']} DNA")
    if reward.get("diamonds"):
        parts.append(f"{reward['diamonds']} 💎")
    if reward.get("speedup"):
        parts.append(f"کارت {reward['speedup']}د")
    return " + ".join(parts) or "—"


def _team_creatures(user):
    team = Team.objects.filter(owner=user).first()
    return team.creatures() if team else []


def _panel_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    st = campaign.status(user)
    members = _team_creatures(user)
    return {
        "status": st,
        "energy": sync_energy(user),
        "has_team": bool(members),
        "team_power": team_power(members) if members else 0,
    }


def _render(view: dict) -> tuple[str, InlineKeyboardMarkup]:
    st = view["status"]
    if st["next_stage"] is None:
        text = "🗺 <b>کمپین</b>\n\n🏆 <b>کل کمپین رو فتح کردی!</b> منتظر مراحل جدید باش."
        return text, InlineKeyboardMarkup([[back_btn("menu:me")]])

    boss = " 👹 <b>(باس!)</b>" if st["next_is_boss"] else ""
    lines = [
        f"🗺 <b>کمپین</b> — مرحله‌ی <b>{st['next_stage']}</b>/{st['max_stage']}{boss}",
        f"<blockquote>✅ فتح‌شده: {st['cleared']} مرحله\n"
        f"👾 قدرت دشمن این مرحله: <b>{st['enemy_power']}</b>\n"
        f"💪 قدرت تیم تو: <b>{view['team_power']}</b>\n"
        f"🎁 جایزه‌ی فتح: {_reward_text(st['next_reward'])}</blockquote>",
        f"{get_emoji('energy')} انرژی: {view['energy']}  ·  هزینه: {campaign.ENERGY_COST}",
    ]
    rows = []
    if not view["has_team"]:
        lines.append("\n⚠️ اول از «⚔️ تیم من» یه تیم بچین.")
        rows.append([btn("⚔️ چیدن تیم", style=PRIMARY, callback_data="menu:team")])
    else:
        rows.append([btn("⚔️ حمله به مرحله", emoji_key="btn_hunt", style=BATTLE, callback_data="camp_fight")])
        rows.append([btn("⚔️ تیم من", style=PRIMARY, callback_data="menu:team")])
    rows.append([back_btn("menu:me")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def campaign_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    view = await run_db(_panel_sync, update.effective_user)
    text, keyboard = _render(view)
    await send_screen(update, text, parse_mode="HTML", reply_markup=keyboard)


def _fight_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    members = _team_creatures(user)
    result = campaign.attempt(user, members)
    return result, _panel_sync(tg_user)


async def campaign_fight_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        result, view = await run_db(_fight_sync, update.effective_user)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return

    if result["won"]:
        await query.answer("🎉 مرحله فتح شد!")
        header = (
            f"🎉 <b>مرحله‌ی {result['stage']} فتح شد!</b>"
            + (" 👹" if result["is_boss"] else "")
            + f"\n🎁 <b>{_reward_text(result['reward'])}</b>"
            + (f"\n🛡 {result['survivors']} هیولا زنده موند." if result["survivors"] else "")
        )
        if result["cleared_all"]:
            header = "🏆 <b>آخرین مرحله‌ی کمپین رو هم فتح کردی!</b>\n" + header
    else:
        await query.answer("💀 شکست خوردی")
        header = f"💀 <b>تیمت توی مرحله‌ی {result['stage']} شکست خورد.</b>\nتیمت رو قوی‌تر کن و دوباره امتحان کن."

    log = battle_summary(result["log"])
    text, keyboard = _render(view)
    await safe_edit_message_text(
        query,
        f"{header}\n\n<blockquote>{log}</blockquote>\n━━━━━━━━━━\n" + text,
        parse_mode="HTML",
        reply_markup=keyboard,
    )


def register(application) -> None:
    application.add_handler(CommandHandler("campaign", campaign_panel, filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(campaign_fight_callback, pattern=r"^camp_fight$"))

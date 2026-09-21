"""«🗺 دانجن» — the PvE stage ladder, fought with your 3v3 team."""

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


def _reward_lines(reward: dict) -> list[str]:
    lines = []
    if reward.get("coins"):
        lines.append(f"{get_emoji('coin')} طلا: <code>+{reward['coins']:,}</code>")
    if reward.get("dna"):
        lines.append(f"{get_emoji('dna')} DNA: <code>+{reward['dna']:,}</code>")
    if reward.get("diamonds"):
        lines.append(f"{get_emoji('diamond')} الماس: <code>+{reward['diamonds']:,}</code>")
    if reward.get("speedup"):
        lines.append(f"{get_emoji('speedup')} کارت سرعت: <code>{reward['speedup']:,}</code> دقیقه‌ای")
    return lines or ["▫️ بدون پاداش"]


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
        text = f"{get_emoji('dungeon')} <b>دانجن</b>\n━━━━━━━━━━━━━━━━━━━━\n{get_emoji('trophy')} <b>کل دانجن رو فتح کردی!</b>\n<i>منتظر مراحل جدید باش.</i>"
        return text, InlineKeyboardMarkup([[back_btn("menu:me")]])

    boss = f" {get_emoji('hunt')} <b>(باس!)</b>" if st["next_is_boss"] else ""
    rew_lines = _reward_lines(st["next_reward"])
    lines = [
        f"{get_emoji('dungeon')} <b>دانجن</b> — مرحله‌ی <code>{st['next_stage']}</code> از <code>{st['max_stage']}</code>{boss}",
        "━━━━━━━━━━━━━━━━━━━━",
        "<blockquote>"
        f"{get_emoji('confirm')} مراحل فتح‌شده: <code>{st['cleared']}</code>\n"
        f"{get_emoji('atk')} قدرت دشمن این مرحله: <code>{st['enemy_power']:,}</code>\n"
        f"{get_emoji('power')} قدرت تیم شما: <code>{view['team_power']:,}</code>\n\n"
        f"{get_emoji('gift')} <b>پاداش فتح این مرحله:</b>\n"
        + "\n".join(rew_lines)
        + f"\n\n{get_emoji('energy')} <b>هزینه ورود:</b> <code>{campaign.ENERGY_COST:,}</code> انرژی\n"
        + f"{get_emoji('energy')} <b>انرژی فعلی:</b> <code>{view['energy']:,}</code>"
        + "</blockquote>",
    ]
    rows = []
    if not view["has_team"]:
        lines.append(f"\n⚠️ <i>اول از «چیدن تیم» یک تیم ۳ نفره بچین.</i>")
        rows.append([btn("چیدن تیم", emoji_key="btn_team", style=PRIMARY, callback_data="menu:team")])
    else:
        rows.append([
            btn("شروع نبرد", emoji_key="btn_hunt", style=BATTLE, callback_data="camp_fight"),
            btn("مدیریت تیم", emoji_key="btn_team", style=PRIMARY, callback_data="menu:team"),
        ])
    rows.append([back_btn("menu:me")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def campaign_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    view = await run_db(_panel_sync, update.effective_user)
    text, keyboard = _render(view)
    from game.media import get_feature_image_path
    photo = get_feature_image_path("campaign")
    await send_screen(update, text, photo=photo, parse_mode="HTML", reply_markup=keyboard)


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
        from bot.handlers.energy import show_energy_error

        if not await show_energy_error(query, exc):
            await query.answer(str(exc), show_alert=True)
        return

    if result["won"]:
        await query.answer("🎉 مرحله فتح شد!")
        rew_lines = _reward_lines(result["reward"])
        header_lines = [
            f"{get_emoji('celebrate')} <b>مرحله‌ی <code>{result['stage']}</code> فتح شد!</b>" + (f" {get_emoji('hunt')}" if result["is_boss"] else ""),
            "━━━━━━━━━━━━━━━━━━━━",
            f"<blockquote>{get_emoji('gift')} <b>پاداش دریافتی:</b>\n"
            + "\n".join(rew_lines)
            + (f"\n\n{get_emoji('def')} <b>بازماندگان:</b> <code>{result['survivors']}</code> هیولا زنده ماندند." if result["survivors"] else "")
            + "</blockquote>"
        ]
        if result["cleared_all"]:
            header_lines.insert(0, f"{get_emoji('trophy')} <b>تمام مراحل دانجن با موفقیت فتح شدند!</b>\n━━━━━━━━━━━━━━━━━━━━")
        header = "\n".join(header_lines)
    else:
        await query.answer("💀 شکست خوردی")
        header = (
            f"💀 <b>شکست در مرحله‌ی <code>{result['stage']}</code>!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<i>تیم خود را ارتقا داده و دوباره تلاش کنید.</i>"
        )

    log = battle_summary(result["log"])
    text, keyboard = _render(view)
    await safe_edit_message_text(
        query,
        f"{header}\n\n<blockquote>{log}</blockquote>\n━━━━━━━━━━━━━━━━━━━━\n{text}",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


def register(application) -> None:
    application.add_handler(CommandHandler("campaign", campaign_panel, filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(campaign_fight_callback, pattern=r"^camp_fight$"))

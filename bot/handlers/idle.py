"""«💤 پاداش آفلاین» + «🗝 دخمه‌ی روزانه» — the idle chest and rotating daily dungeon."""

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

from bio_lab.repository import get_or_create_user
from bot.buttons import BATTLE, BUILD, CONFIRM, back_btn, btn
from bot.utils import run_db, safe_edit_message_text, send_screen
from game import idle
from game.creature import GameError
from game.emoji import get_emoji


def _reward_text(reward: dict) -> str:
    parts = []
    if reward.get("coins"):
        parts.append(f"{reward['coins']} طلا")
    if reward.get("dna"):
        parts.append(f"{reward['dna']} DNA")
    if reward.get("xp"):
        parts.append(f"{reward['xp']} XP آزمایشگاه")
    return " + ".join(parts) or "—"


def _panel_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    return idle.idle_status(user), idle.dungeon_status(user)


def _render(idle_st: dict, dg_st: dict) -> tuple[str, InlineKeyboardMarkup]:
    dg = dg_st["dungeon"]
    hrs = idle_st["hours"]
    cap_note = " (پُر شد! بیا و بردار)" if idle_st["capped"] else ""
    lines = [
        "💤 <b>پاداش آفلاین و دخمه‌ی روزانه</b>",
        f"<blockquote>🕰 <b>صندوق آفلاین</b> — {hrs:.1f} ساعت جمع شده{cap_note}\n"
        f"{get_emoji('coin')} {idle_st['coins']} طلا  +  {get_emoji('dna')} {idle_st['dna']} DNA\n"
        f"<i>تا سقف {idle_st['cap_hours']} ساعت جمع می‌شه؛ هرچی قوی‌تر باشی، بیشتر.</i></blockquote>",
        f"\n{dg['emoji']} <b>دخمه‌ی امروز: {dg['title']}</b>",
        f"<blockquote>{dg['boss_flavor']}\n"
        f"🎁 جایزه (در صورت پیروزی): <b>{_reward_text(dg_st['reward'])}</b></blockquote>",
    ]
    rows = []
    if idle_st["coins"] or idle_st["dna"]:
        rows.append([btn("🕰 برداشت صندوق آفلاین", emoji_key="btn_confirm", style=CONFIRM, callback_data="idle_collect")])
    if dg_st["can_run"]:
        rows.append([btn(f"{dg['emoji']} ورود به نبرد دخمه", style=BATTLE, callback_data="idle_dungeon")])
    else:
        lines.append("\n✅ دخمه‌ی امروزو رفتی. فردا دوباره بیا.")
    rows.append([back_btn("menu:cat_rewards", "بازگشت به جایزه‌ها")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def idle_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    idle_st, dg_st = await run_db(_panel_sync, update.effective_user)
    text, keyboard = _render(idle_st, dg_st)
    await send_screen(update, text, parse_mode="HTML", reply_markup=keyboard)


def _collect_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    got = idle.collect_idle(user)
    return got, idle.idle_status(user), idle.dungeon_status(user)


async def idle_collect_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    got, idle_st, dg_st = await run_db(_collect_sync, update.effective_user)
    if not (got["coins"] or got["dna"]):
        await query.answer("چیزی برای برداشت نیست.", show_alert=True)
        return
    text, keyboard = _render(idle_st, dg_st)
    await safe_edit_message_text(
        query,
        f"🕰 <b>صندوق آفلاین برداشته شد!</b>\n🎁 <b>{_reward_text(got)}</b>\n\n━━━━━━━━━━\n" + text,
        parse_mode="HTML",
        reply_markup=keyboard,
    )


def _dungeon_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    result = idle.run_dungeon(user)
    return result, idle.idle_status(user), idle.dungeon_status(user)


async def idle_dungeon_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    try:
        result, idle_st, dg_st = await run_db(_dungeon_sync, update.effective_user)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return

    if result is None:
        await query.answer("دخمه‌ی امروزو قبلاً رفتی.", show_alert=True)
        return

    dg = result["dungeon"]
    if result["won"]:
        header = (
            f"⚔️ <b>دخمه فتح شد!</b> {dg['emoji']}\n"
            f"<b>{result['boss_name']}</b> از پا دراومد!\n"
            f"🎁 <b>{_reward_text(result['reward'])}</b>"
        )
    else:
        header = (
            f"💀 <b>شکست خوردی!</b> {dg['emoji']}\n"
            f"<b>{result['boss_name']}</b> خیلی قوی بود — فردا دوباره بیا و انتقام بگیر.\n"
            f"🎁 جایزه‌ی تسلی: <b>{_reward_text(result['reward'])}</b>"
        )

    text, keyboard = _render(idle_st, dg_st)
    await safe_edit_message_text(
        query,
        f"{header}\n\n<blockquote>{result['log_text']}</blockquote>\n━━━━━━━━━━\n" + text,
        parse_mode="HTML",
        reply_markup=keyboard,
    )


def register(application) -> None:
    application.add_handler(CommandHandler("idle", idle_panel, filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(idle_collect_callback, pattern=r"^idle_collect$"))
    application.add_handler(CallbackQueryHandler(idle_dungeon_callback, pattern=r"^idle_dungeon$"))

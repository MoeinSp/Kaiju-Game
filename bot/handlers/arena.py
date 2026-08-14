from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

from bio_lab.repository import get_or_create_user
from bot.buttons import BATTLE, DANGER, NAV, PRIMARY, back_btn, btn
from bot.utils import run_db, safe_edit_message_text
from game import constants
from game.arena import (
    active_power,
    attack,
    deserved_cup,
    expected_loot,
    find_opponent,
    is_shielded,
    recent_attacks_received,
    shield_remaining_seconds,
    top_by_cup,
)
from game.creature import GameError
from game.daily import check_missions, record_action
from game.emoji import get_emoji
from game.energy import spend_energy
from game.season import (
    close_due_season,
    current_week,
    last_season_results,
    reset_floor,
    seconds_until_next_week,
    standings,
)

# the pending opponent lives in user_data between "find" and "attack" so the fight
# resolves against exactly the opponent that was shown, not a freshly rolled one
PENDING_OPPONENT_KEY = "arena_pending_opponent"


def _format_remaining(seconds: int) -> str:
    hours, rem = divmod(max(0, seconds), 3600)
    minutes = rem // 60
    if hours:
        return f"{hours} ساعت و {minutes} دقیقه"
    return f"{minutes} دقیقه"


def _arena_home_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    # settle last week's season lazily on any arena read — no cron anywhere in this bot
    close_due_season()
    user.refresh_from_db()
    return (
        user,
        active_power(user),
        shield_remaining_seconds(user),
        recent_attacks_received(user),
        current_week(),
        seconds_until_next_week(),
    )


def _arena_home_text(user, power, shield_secs, history, week, season_secs) -> str:
    lines = [
        f"{get_emoji('trophy')} <b>آرنا</b>",
        f"🗓 فصل <code>{week}</code> — <b>{_format_remaining(season_secs)}</b> تا پایان",
        "",
        f"🏆 کاپ تو: <b>{user.cup}</b>",
        f"💪 قدرت: <b>{power}</b>",
    ]
    ceiling = deserved_cup(power)
    if user.cup > ceiling:
        lines.append("<i>⚠️ کاپت از قدرت موجودت جلو زده — بردها کاپ کمتری می‌دن تا هیولات قوی‌تر بشه.</i>")
    if shield_secs > 0:
        lines.append(f"🛡 سپر محافظ: <b>{_format_remaining(shield_secs)}</b> باقی‌مونده")
        lines.append("<i>اگه خودت حمله کنی سپرت می‌پره.</i>")
    else:
        lines.append("🛡 سپر محافظ: نداری — ممکنه بهت حمله بشه")

    if history:
        lines.append("\n<b>آخرین حمله‌ها بهت:</b>")
        for log in history:
            mark = "🔴" if log.attacker_won else "🟢"
            loot = f" −{log.loot_gold} {get_emoji('coin')}" if log.loot_gold else ""
            lines.append(f"{mark} {log.defender_label or '—'}{loot}")
    lines.append(
        f"\n<blockquote>هر حمله {constants.ARENA_ATTACK_ENERGY_COST} انرژی می‌بره و "
        f"{int(constants.ARENA_LOOT_PERCENT * 100)}٪ طلای حریف رو غارت می‌کنه.\n"
        "آخر هر هفته کاپ‌ها ریست می‌شن — هرچی رتبه‌ت بالاتر باشه، از کاپ بالاتری شروع می‌کنی.</blockquote>"
    )
    return "\n".join(lines)


def _arena_home_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [btn("پیدا کردن حریف", emoji_key="btn_attack", style=BATTLE, callback_data="arena_find")],
            [btn("جدول این هفته", emoji_key="btn_rank", style=NAV, callback_data="arena_top")],
            [btn("🗓 نتایج هفته‌ی قبل", style=NAV, callback_data="arena_last_season")],
            [back_btn("menu:me")],
        ]
    )


async def arena_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user, power, shield_secs, history, week, season_secs = await run_db(
        _arena_home_sync, update.effective_user
    )
    await update.effective_message.reply_text(
        _arena_home_text(user, power, shield_secs, history, week, season_secs),
        parse_mode="HTML",
        reply_markup=_arena_home_keyboard(),
    )


def _find_sync(tg_user):
    """Everything the preview screen needs, resolved here in sync context — the
    async callback must not touch the ORM (see the async-safety rule in CLAUDE.md),
    so my_power and the loot estimate are computed up front, not at render time."""
    from bio_lab.models import Creature

    user, _ = get_or_create_user(tg_user)
    opponent = find_opponent(user)
    creature = Creature.objects.filter(owner=user, is_active=True).first()
    level = creature.level if creature is not None else 1
    return user, opponent, active_power(user), expected_loot(opponent, level)


async def arena_find_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        user, opponent, my_power, loot = await run_db(_find_sync, update.effective_user)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return

    # stash only what attack() needs, since user_data must stay JSON-ish/simple
    context.user_data[PENDING_OPPONENT_KEY] = {
        "is_fake": opponent["is_fake"],
        "user_id": None if opponent["is_fake"] else opponent["user"].id,
        "label": opponent["label"],
        "cup": opponent["cup"],
        "power": opponent["power"],
        "loot_pool": opponent["loot_pool"],
    }

    await query.answer()
    gap = opponent["power"] - my_power
    odds = "🟢 شانس بالا" if gap < -15 else ("🔴 خطرناک" if gap > 15 else "🟡 سرتاسری")
    lines = [
        f"{get_emoji('battle')} <b>حریف پیدا شد!</b>\n",
        f"🏭 <b>{opponent['label']}</b>",
        f"🏆 کاپ: <b>{opponent['cup']}</b>",
        f"💪 قدرت: <b>{opponent['power']}</b>  (تو: {my_power} — {odds})",
        f"{get_emoji('coin')} غنیمت در صورت برد: حدود <b>{loot}</b>",
    ]
    keyboard = InlineKeyboardMarkup(
        [
            [btn("حمله!", emoji_key="btn_attack", style=BATTLE, callback_data="arena_attack")],
            [btn("حریف بعدی", emoji_key="btn_recheck", style=NAV, callback_data="arena_find")],
            [back_btn("menu:arena")],
        ]
    )
    await safe_edit_message_text(query, "\n".join(lines), parse_mode="HTML", reply_markup=keyboard)


def _attack_sync(tg_user, pending):
    user, _ = get_or_create_user(tg_user)
    spend_energy(user, constants.ARENA_ATTACK_ENERGY_COST, "حمله")
    user.save(update_fields=["energy", "energy_updated_at"])

    opponent = dict(pending)
    if opponent["is_fake"]:
        opponent["user"] = None
    else:
        from bio_lab.models import User as UserModel

        target = UserModel.objects.filter(id=pending["user_id"]).first()
        if target is None:
            raise GameError("این حریف دیگه در دسترس نیست، یکی دیگه پیدا کن.")
        opponent["user"] = target

    result = attack(user, opponent)
    record_action(user, "arena_attack")
    completed_missions = check_missions(user, "arena_attack")
    return result, completed_missions


async def arena_attack_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    pending = context.user_data.get(PENDING_OPPONENT_KEY)
    if pending is None:
        await query.answer("اول یه حریف پیدا کن.", show_alert=True)
        return

    try:
        result, completed_missions = await run_db(_attack_sync, update.effective_user, pending)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return

    context.user_data.pop(PENDING_OPPONENT_KEY, None)

    if result["won"]:
        summary = (
            f"{get_emoji('celebrate')} <b>بردی!</b>\n"
            f"{get_emoji('coin')} +{result['loot']} غنیمت از {result['opponent_label']}\n"
            f"🏆 +{result['cup_delta']} کاپ (الان: {result['new_cup']})"
        )
    else:
        summary = (
            f"😔 <b>باختی.</b>\n"
            f"🏆 {result['cup_delta']} کاپ (الان: {result['new_cup']})"
        )

    keyboard = InlineKeyboardMarkup(
        [
            [btn("حریف بعدی", emoji_key="btn_attack", style=NAV, callback_data="arena_find")],
            [back_btn("menu:arena", "بازگشت به آرنا")],
        ]
    )
    await query.answer("🟢 بردی!" if result["won"] else "🔴 باختی.")
    await safe_edit_message_text(
        query,
        result["log_text"] + "\n\n" + f"<tg-spoiler>{summary}</tg-spoiler>",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


def _top_sync():
    close_due_season()
    # each row carries the floor its rank would reset to, so the table doubles as
    # the explanation of what climbing one more place is actually worth
    return [
        {**row, "reset_to": reset_floor(row["rank"], row["cup"])}
        for row in standings(10)
    ], current_week(), seconds_until_next_week()


async def arena_top_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    ranked, week, season_secs = await run_db(_top_sync)
    await query.answer()
    medals = [get_emoji("medal_gold"), get_emoji("medal_silver"), get_emoji("medal_bronze")]
    lines = [
        f"{get_emoji('trophy')} <b>جدول فصل {week}</b>",
        f"⏳ <b>{_format_remaining(season_secs)}</b> تا ریست\n",
    ]
    if not ranked:
        lines.append("<i>هنوز کسی کاپ نگرفته — اولین نفر باش!</i>")
    for row in ranked:
        rank = medals[row["rank"] - 1] if row["rank"] <= 3 else f"{row['rank']}."
        label = row["user"].lab_name or f"آزمایشگاه {row['user'].id}"
        lines.append(f"{rank} {label} — 🏆 <b>{row['cup']}</b>  <i>(ریست به {row['reset_to']})</i>")
    keyboard = InlineKeyboardMarkup([[back_btn("menu:arena")]])
    await safe_edit_message_text(query, "\n".join(lines), parse_mode="HTML", reply_markup=keyboard)


def _last_season_sync():
    close_due_season()
    return last_season_results(10)


async def arena_last_season_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    week, results = await run_db(_last_season_sync)
    await query.answer()
    keyboard = InlineKeyboardMarkup([[back_btn("menu:arena")]])
    if not results:
        await safe_edit_message_text(
            query,
            "🗓 هنوز هیچ فصلی تموم نشده — اولین ریست آخر همین هفته‌ست.",
            reply_markup=keyboard,
        )
        return
    medals = [get_emoji("medal_gold"), get_emoji("medal_silver"), get_emoji("medal_bronze")]
    lines = [f"🗓 <b>نتایج فصل {week}</b>\n"]
    for r in results:
        rank = medals[r.rank - 1] if r.rank <= 3 else f"{r.rank}."
        label = r.user.lab_name or f"آزمایشگاه {r.user_id}"
        lines.append(f"{rank} {label} — 🏆 {r.cup_before} → {r.cup_after}")
    await safe_edit_message_text(query, "\n".join(lines), parse_mode="HTML", reply_markup=keyboard)


def register(application) -> None:
    application.add_handler(CommandHandler("arena", arena_panel, filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(arena_find_callback, pattern=r"^arena_find$"))
    application.add_handler(CallbackQueryHandler(arena_attack_callback, pattern=r"^arena_attack$"))
    application.add_handler(CallbackQueryHandler(arena_top_callback, pattern=r"^arena_top$"))
    application.add_handler(
        CallbackQueryHandler(arena_last_season_callback, pattern=r"^arena_last_season$")
    )

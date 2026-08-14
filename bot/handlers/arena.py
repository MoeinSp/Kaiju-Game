from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

from bio_lab.repository import get_or_create_user
from bot.buttons import DANGER, PRIMARY, SUCCESS, back_btn, btn
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
    return user, active_power(user), shield_remaining_seconds(user), recent_attacks_received(user)


def _arena_home_text(user, power, shield_secs, history) -> str:
    lines = [
        f"{get_emoji('trophy')} <b>آرنا</b>",
        f"🏆 کاپ تو: <b>{user.cup}</b>   💪 قدرت: {power}",
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
        f"{int(constants.ARENA_LOOT_PERCENT * 100)}٪ طلای حریف رو غارت می‌کنه.</blockquote>"
    )
    return "\n".join(lines)


def _arena_home_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [btn("پیدا کردن حریف", emoji_key="btn_attack", style=DANGER, callback_data="arena_find")],
            [btn("برترین‌های کاپ", emoji_key="btn_rank", style=PRIMARY, callback_data="arena_top")],
            [back_btn("menu:me")],
        ]
    )


async def arena_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user, power, shield_secs, history = await run_db(_arena_home_sync, update.effective_user)
    await update.effective_message.reply_text(
        _arena_home_text(user, power, shield_secs, history),
        parse_mode="HTML",
        reply_markup=_arena_home_keyboard(),
    )


def _find_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    opponent = find_opponent(user)
    return user, opponent


async def arena_find_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        user, opponent = await run_db(_find_sync, update.effective_user)
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
    loot = expected_loot(opponent)
    lines = [
        f"{get_emoji('battle')} <b>حریف پیدا شد!</b>\n",
        f"🏭 <b>{opponent['label']}</b>",
        f"🏆 کاپ: {opponent['cup']}   💪 قدرت: {opponent['power']}",
        f"{get_emoji('coin')} غنیمت در صورت برد: حدود <b>{loot}</b>",
    ]
    keyboard = InlineKeyboardMarkup(
        [
            [btn("حمله!", emoji_key="btn_attack", style=DANGER, callback_data="arena_attack")],
            [btn("حریف بعدی", emoji_key="btn_recheck", style=PRIMARY, callback_data="arena_find")],
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
            [btn("حریف بعدی", emoji_key="btn_attack", style=DANGER, callback_data="arena_find")],
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
    return top_by_cup(10)


async def arena_top_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    ranked = await run_db(_top_sync)
    await query.answer()
    medals = [get_emoji("medal_gold"), get_emoji("medal_silver"), get_emoji("medal_bronze")]
    lines = [f"{get_emoji('trophy')} <b>برترین‌های کاپ</b>\n"]
    for i, u in enumerate(ranked, start=1):
        rank = medals[i - 1] if i <= 3 else f"{i}."
        label = u.lab_name or f"آزمایشگاه {u.id}"
        lines.append(f"{rank} {label} — 🏆 {u.cup}")
    keyboard = InlineKeyboardMarkup([[back_btn("menu:arena")]])
    await safe_edit_message_text(query, "\n".join(lines), parse_mode="HTML", reply_markup=keyboard)


def register(application) -> None:
    application.add_handler(CommandHandler("arena", arena_panel, filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(arena_find_callback, pattern=r"^arena_find$"))
    application.add_handler(CallbackQueryHandler(arena_attack_callback, pattern=r"^arena_attack$"))
    application.add_handler(CallbackQueryHandler(arena_top_callback, pattern=r"^arena_top$"))

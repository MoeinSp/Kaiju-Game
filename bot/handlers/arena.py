import logging

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

from bio_lab.models import AttackLog
from bio_lab.repository import get_or_create_user, lab_display, mention
from bot.buttons import BATTLE, DANGER, NAV, back_btn, btn
from bot.utils import run_db, safe_edit_message_text, send_screen
from game import constants
import datetime

from django.db import transaction
from django.utils import timezone as tz

from game.arena import (
    active_power,
    attack,
    creature_power,
    deserved_cup,
    expected_loot,
    find_opponent,
    mark_revenge_taken,
    recent_attacks_received,
    revengeable_attacks,
    shield_remaining_seconds,
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

logger = logging.getLogger(__name__)

PENDING_OPPONENT_KEY = "arena_pending_opponent"


def _format_remaining(seconds: int) -> str:
    hours, rem = divmod(max(0, seconds), 3600)
    minutes = rem // 60
    if hours:
        return f"{hours} ساعت و {minutes} دقیقه"
    return f"{minutes} دقیقه"


def _arena_home_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    close_due_season()
    user.refresh_from_db()
    return (
        user,
        active_power(user),
        shield_remaining_seconds(user),
        recent_attacks_received(user),
        current_week(),
        seconds_until_next_week(),
        revengeable_attacks(user),
    )


def _arena_home_text(user, power, shield_secs, history, week, season_secs, revenges) -> str:
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
            attacker_name = log.attacker_label or lab_display(log.attacker)
            pwr = f"  💪{log.attacker_power}" if log.attacker_power else ""
            loot = f"  −{log.loot_gold} {get_emoji('coin')}" if log.loot_gold else ""
            lines.append(f"{mark} <b>{attacker_name}</b>{pwr}{loot}")

    if revenges:
        lines.append(f"\n⚔️ <b>{len(revenges)} انتقام</b> در انتظار — مهلت ۳ روزه")

    lines.append(
        f"\n<blockquote>هر حمله {constants.ARENA_ATTACK_ENERGY_COST} انرژی می‌بره و "
        f"{int(constants.ARENA_LOOT_PERCENT * 100)}٪ طلای حریف رو غارت می‌کنه.\n"
        "آخر هر هفته کاپ‌ها ریست می‌شن — هرچی رتبه‌ت بالاتر باشه، از کاپ بالاتری شروع می‌کنی.</blockquote>"
    )
    return "\n".join(lines)


def _arena_home_keyboard(has_revenges: bool) -> InlineKeyboardMarkup:
    rows = [
        [btn("پیدا کردن حریف", emoji_key="btn_attack", style=BATTLE, callback_data="arena_find")],
    ]
    if has_revenges:
        rows.append([btn("⚔️ انتقام‌ها", style=DANGER, callback_data="arena_revenges")])
    rows += [
        [btn("جدول این هفته", emoji_key="btn_rank", style=NAV, callback_data="arena_top")],
        [btn("🗓 نتایج هفته‌ی قبل", style=NAV, callback_data="arena_last_season")],
        [back_btn("menu:me")],
    ]
    return InlineKeyboardMarkup(rows)


async def arena_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user, power, shield_secs, history, week, season_secs, revenges = await run_db(
        _arena_home_sync, update.effective_user
    )
    await send_screen(update,
        _arena_home_text(user, power, shield_secs, history, week, season_secs, revenges),
        parse_mode="HTML",
        reply_markup=_arena_home_keyboard(bool(revenges)),
    )


def _find_sync(tg_user):
    from bio_lab.models import Creature

    user, _ = get_or_create_user(tg_user)
    opponent = find_opponent(user)
    creature = Creature.objects.filter(owner=user, is_active=True).first()
    level = creature.level if creature is not None else 1
    my_element = creature.element if creature is not None else None
    return user, opponent, active_power(user), expected_loot(opponent, level), my_element


async def arena_find_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    try:
        user, opponent, my_power, loot, my_element = await run_db(_find_sync, update.effective_user)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    except Exception:  # noqa: BLE001 — never let a raid-matchmaking bug silently kill the button
        logger.exception("arena_find failed for user %s", update.effective_user.id)
        await query.answer("یه مشکل پیش اومد، دوباره امتحان کن.", show_alert=True)
        return

    context.user_data[PENDING_OPPONENT_KEY] = {
        "is_fake": opponent["is_fake"],
        "user_id": None if opponent["is_fake"] else opponent["user"].id,
        "label": opponent["label"],
        "cup": opponent["cup"],
        "power": opponent["power"],
        "element": opponent.get("element"),
        "loot_pool": opponent["loot_pool"],
    }

    gap = opponent["power"] - my_power
    odds = "🟢 شانس بالا" if gap < -15 else ("🔴 خطرناک" if gap > 15 else "🟡 سرتاسری")
    opp_element = opponent.get("element")
    elem_tag = f"  ({constants.element_label(opp_element)})" if opp_element else ""
    lines = [
        f"{get_emoji('battle')} <b>حریف پیدا شد!</b>\n",
        f"🏭 <b>{opponent['label']}</b>{elem_tag}",
        f"🏆 کاپ: <b>{opponent['cup']}</b>",
        f"💪 قدرت: <b>{opponent['power']}</b>  (تو: {my_power} — {odds})",
        f"{get_emoji('coin')} غنیمت در صورت برد: حدود <b>{loot}</b>",
    ]
    if my_element and opp_element:
        note = constants.element_matchup_note(my_element, opp_element)
        if note:
            lines.append(note)
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


# ── Revenge panel ─────────────────────────────────────────────────────────────

def _revenges_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    return user, revengeable_attacks(user)


async def arena_revenges_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user, revenges = await run_db(_revenges_sync, update.effective_user)

    if not revenges:
        keyboard = InlineKeyboardMarkup([[back_btn("menu:arena")]])
        await safe_edit_message_text(
            query,
            "⚔️ هیچ انتقام باز‌ی نداری. مهلت انتقام ۳ روزه.",
            reply_markup=keyboard,
        )
        return

    now = tz.now()

    lines = [f"⚔️ <b>انتقام‌های باز</b> ({len(revenges)} مورد)\n"]
    rows = []
    for log in revenges:
        attacker_name = log.attacker_label or lab_display(log.attacker)
        pwr = f"  💪{log.attacker_power}" if log.attacker_power else ""
        loot = f"  −{log.loot_gold} {get_emoji('coin')}" if log.loot_gold else ""
        deadline = log.created_at + datetime.timedelta(days=3)
        hrs_left = max(0, int((deadline - now).total_seconds() // 3600))
        lines.append(f"🔴 <b>{attacker_name}</b>{pwr}{loot}  — {hrs_left}h مهلت")
        rows.append([btn(
            f"⚔️ انتقام از {attacker_name}",
            style=DANGER,
            callback_data=f"arena_revenge:{log.id}",
        )])

    rows.append([back_btn("menu:arena")])
    await safe_edit_message_text(
        query,
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(rows),
    )


def _revenge_find_sync(tg_user, log_id: int):
    user, _ = get_or_create_user(tg_user)
    try:
        log = AttackLog.objects.select_related("attacker").get(
            id=log_id, defender=user, revenge_taken=False, is_fake_defender=False
        )
    except AttackLog.DoesNotExist:
        raise GameError("این انتقام دیگه در دسترس نیست.")

    if log.created_at < tz.now() - datetime.timedelta(days=3):
        raise GameError("مهلت ۳ روزه‌ی انتقام گذشته.")

    my_power = active_power(user)
    opponent_power = active_power(log.attacker) if log.attacker else 0
    attacker_name = log.attacker_label or lab_display(log.attacker)
    return user, log, my_power, opponent_power, attacker_name


async def arena_revenge_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    log_id = int(query.data.split(":")[1])

    try:
        user, log, my_power, opp_power, attacker_name = await run_db(
            _revenge_find_sync, update.effective_user, log_id
        )
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return

    gap = opp_power - my_power
    odds = "🟢 شانس بالا" if gap < -15 else ("🔴 خطرناک" if gap > 15 else "🟡 سرتاسری")
    lines = [
        f"⚔️ <b>انتقام از {attacker_name}</b>\n",
        f"💪 قدرت حریف: <b>{opp_power}</b>  (تو: {my_power} — {odds})",
        f"{get_emoji('coin')} اون از تو {log.loot_gold} طلا دزدید",
        f"\n<i>حمله {constants.ARENA_ATTACK_ENERGY_COST} انرژی می‌بره.</i>",
    ]
    keyboard = InlineKeyboardMarkup([
        [btn("⚔️ حمله کن!", style=BATTLE, callback_data=f"arena_revenge_atk:{log_id}")],
        [back_btn("arena_revenges", "بازگشت به انتقام‌ها")],
    ])
    await safe_edit_message_text(query, "\n".join(lines), parse_mode="HTML", reply_markup=keyboard)


@transaction.atomic
def _revenge_attack_sync(tg_user, log_id: int):
    from bio_lab.models import Creature
    user, _ = get_or_create_user(tg_user)

    log = mark_revenge_taken(log_id, user)
    if log is None:
        raise GameError("این انتقام قبلاً گرفته شده یا دیگه معتبر نیست.")

    if log.created_at < tz.now() - datetime.timedelta(days=3):
        raise GameError("مهلت ۳ روزه‌ی انتقام گذشته.")

    target = log.attacker
    if target is None:
        raise GameError("حریف دیگه در دسترس نیست.")

    target_creature = Creature.objects.filter(owner=target, is_active=True).first()

    spend_energy(user, constants.ARENA_ATTACK_ENERGY_COST, "انتقام")
    user.save(update_fields=["energy", "energy_updated_at"])

    opponent = {
        "is_fake": False,
        "user": target,
        "label": log.attacker_label or lab_display(target),
        "cup": target.cup,
        "power": creature_power(target_creature) if target_creature else 0,
        "loot_pool": target.coins,
    }
    result = attack(user, opponent)
    record_action(user, "arena_attack")
    return result


async def arena_revenge_attack_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    log_id = int(query.data.split(":")[1])
    try:
        result = await run_db(_revenge_attack_sync, update.effective_user, log_id)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return

    if result["won"]:
        summary = (
            f"{get_emoji('celebrate')} <b>انتقام گرفتی!</b>\n"
            f"{get_emoji('coin')} +{result['loot']} غنیمت\n"
            f"🏆 +{result['cup_delta']} کاپ (الان: {result['new_cup']})"
        )
    else:
        summary = (
            f"😔 <b>باختی — انتقام گرفته نشد.</b>\n"
            f"🏆 {result['cup_delta']} کاپ (الان: {result['new_cup']})"
        )

    keyboard = InlineKeyboardMarkup([
        [back_btn("menu:arena", "بازگشت به آرنا")],
    ])
    await query.answer("🟢 بردی!" if result["won"] else "🔴 باختی.")
    await safe_edit_message_text(
        query,
        result["log_text"] + "\n\n" + f"<tg-spoiler>{summary}</tg-spoiler>",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


# ── Leaderboard ───────────────────────────────────────────────────────────────

def _top_sync():
    close_due_season()
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
        lines.append(f"{rank} {mention(row['user'])} — 🏆 <b>{row['cup']}</b>  <i>(ریست به {row['reset_to']})</i>")
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
        lines.append(f"{rank} {mention(r.user)} — 🏆 {r.cup_before} → {r.cup_after}")
    await safe_edit_message_text(query, "\n".join(lines), parse_mode="HTML", reply_markup=keyboard)


def register(application) -> None:
    application.add_handler(CommandHandler("arena", arena_panel, filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(arena_find_callback, pattern=r"^arena_find$"))
    application.add_handler(CallbackQueryHandler(arena_attack_callback, pattern=r"^arena_attack$"))
    application.add_handler(CallbackQueryHandler(arena_top_callback, pattern=r"^arena_top$"))
    application.add_handler(
        CallbackQueryHandler(arena_last_season_callback, pattern=r"^arena_last_season$")
    )
    application.add_handler(
        CallbackQueryHandler(arena_revenges_callback, pattern=r"^arena_revenges$")
    )
    application.add_handler(
        CallbackQueryHandler(arena_revenge_callback, pattern=r"^arena_revenge:\d+$")
    )
    application.add_handler(
        CallbackQueryHandler(arena_revenge_attack_callback, pattern=r"^arena_revenge_atk:\d+$")
    )

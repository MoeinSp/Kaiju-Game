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
    cup_delta,
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


def _fmt_hm(seconds: int) -> str:
    hours, rem = divmod(max(0, int(seconds)), 3600)
    minutes = rem // 60
    if hours and minutes:
        return f"{hours} ساعت و {minutes} دقیقه"
    if hours:
        return f"{hours} ساعت"
    return f"{minutes} دقیقه"
ARENA_DETAIL_KEY = "arena_last_detail"


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
        f"\n<blockquote>هر حمله {constants.ARENA_ATTACK_ENERGY_COST} انرژی می‌بره. اگه ببری "
        f"{int(constants.ARENA_LOOT_PERCENT * 100)}٪ طلای حریف رو غارت می‌کنی و کاپ می‌گیری؛ اگه ببازی فقط کاپ کم می‌شه.\n"
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


RECENT_OPPONENTS_KEY = "arena_recent_opps"
_RECENT_OPPONENTS_MAX = 12  # remember this many recent picks, so «بعدی» keeps finding fresh faces


def _find_sync(tg_user, exclude_ids=None):
    from bio_lab.models import Creature

    user, _ = get_or_create_user(tg_user)
    opponent = find_opponent(user, exclude_ids=exclude_ids)
    creature = Creature.objects.filter(owner=user, is_active=True).first()
    level = creature.level if creature is not None else 1
    my_element = creature.element if creature is not None else None
    dna_win = round(constants.ARENA_WIN_DNA_BASE + level * constants.ARENA_WIN_DNA_PER_LEVEL)
    from game.energy import sync_energy

    cname = creature.name if creature is not None else "—"
    return (user, opponent, active_power(user), expected_loot(opponent, level), my_element,
            dna_win, cname, sync_energy(user))


async def arena_find_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    recent = context.user_data.get(RECENT_OPPONENTS_KEY, [])
    try:
        user, opponent, my_power, loot, my_element, dna_win, cname, energy = await run_db(
            _find_sync, update.effective_user, recent
        )
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
    # remember real picks so the next «حریف بعدی» skips them (fresh faces, and no
    # identical-screen no-op edit that made the button look dead).
    if not opponent["is_fake"]:
        recent = [i for i in recent if i != opponent["user"].id] + [opponent["user"].id]
        context.user_data[RECENT_OPPONENTS_KEY] = recent[-_RECENT_OPPONENTS_MAX:]

    text, keyboard = _render_opponent(user, opponent, my_power, loot, my_element, dna_win, cname, energy)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


def _render_opponent(user, opponent, my_power, loot, my_element, dna_win,
                     cname="—", energy=None) -> tuple[str, InlineKeyboardMarkup]:
    """The 'opponent found' arena screen — shared by matchmaking and the «بازگشت» from
    the opponent-details view so the same screen is rebuilt identically."""
    from bot.handlers.private import (element_advantage_line, pct_bar, win_chance_pct, win_label)
    from game.energy import sync_energy

    if energy is None:
        energy = sync_energy(user)
    opp_element = opponent.get("element")
    my_elem_tag = f" [{constants.element_label(my_element)}]" if my_element else ""
    opp_elem_tag = f" [{constants.element_label(opp_element)}]" if opp_element else ""
    pct = win_chance_pct(my_power, opponent["power"])
    adv = element_advantage_line(my_element, opp_element)
    win_cup = cup_delta(user, opponent["cup"], True, my_power)
    loss_cup = cup_delta(user, opponent["cup"], False, my_power)
    lines = [
        f"{get_emoji('battle')} <b>حریف شناسایی شد | Battle Arena</b>",
        "",
        f"👹 موجود حریف: <b>{opponent['label']}</b>{opp_elem_tag}",
        f"💀 قدرت حریف: <b>{opponent['power']:,}</b> ┃ {get_emoji('trophy')} کاپ: <b>{opponent['cup']:,}</b>",
        "",
        _ARENA_DIV,
        "",
        f"🦅 موجود شما: <b>{cname}</b>{my_elem_tag}",
        f"💪 قدرت شما: <b>{my_power:,}</b> ┃ {get_emoji('trophy')} کاپ: <b>{user.cup:,}</b>",
        f"{get_emoji('energy')} انرژی فعلی: {pct_bar(energy, constants.MAX_ENERGY)} ({energy}/{constants.MAX_ENERGY})",
        "",
        _ARENA_DIV,
        "",
        "🎯 تحلیل تاکتیکی نبرد:",
        f"شانس پیروزی: {pct_bar(pct, 100)} {win_label(pct)}",
    ]
    if adv:
        lines.append(f"🔮 مزیت عنصری: {adv}")
    lines += [
        "",
        "🎁 جوایز و تغییرات نبرد:",
        f"{get_emoji('coin')} طلا: <b>~+{loot:,}</b> ┃ {get_emoji('dna')} دی‌ان‌ای: <b>+{dna_win:,}</b>",
        f"{get_emoji('trophy')} تغییر رنک: برد <b>+{win_cup}</b> | باخت <b>{loss_cup}</b>",
        "",
        _ARENA_DIV,
        f"{get_emoji('energy')} هزینه حمله: {constants.ARENA_ATTACK_ENERGY_COST} انرژی",
    ]
    keyboard = InlineKeyboardMarkup(
        [
            [btn(f"⚔️ شروع حمله (-{constants.ARENA_ATTACK_ENERGY_COST}⚡)", emoji_key="btn_attack", style=BATTLE, callback_data="arena_attack")],
            [btn("🔍 جزییات حریف", style=NAV, callback_data="arena_opp_details"),
             btn("حریف بعدی", emoji_key="btn_recheck", style=NAV, callback_data="arena_find")],
            [back_btn("menu:arena")],
        ]
    )
    return "\n".join(lines), keyboard


_ARENA_DIV = "──────────────"


def _opponent_details_sync(pending: dict) -> dict:
    """Full readout of the matched opponent — level, stars, every equipped item with
    its rarity/enhancement, the gear's power contribution, and body-part upgrades.
    Bots have no creature row, so they only report power/element."""
    from bio_lab.models import Creature, User
    from game.creature import effective_stats
    from game.equipment import get_equipped_items

    def _bot(pending):
        from game.arena import _bot_display_tier

        rarity, star = _bot_display_tier(int(pending.get("cup", 0)))
        return {"is_fake": True, "label": pending["label"], "power": pending["power"],
                "element": pending.get("element"),
                "rarity": constants.RARITY_LABELS.get(rarity, rarity), "star_level": star}

    if pending.get("is_fake") or not pending.get("user_id"):
        return _bot(pending)

    target = User.objects.filter(id=pending["user_id"]).first()
    creature = Creature.objects.filter(owner=target, is_active=True).first() if target else None
    if creature is None:
        return _bot(pending)

    items = get_equipped_items(creature)
    stats = effective_stats(creature, items)
    bare_power = creature_power(creature, [])
    full_power = creature_power(creature, items)
    gear = [
        {
            "slot": constants.EQUIPMENT_SLOT_LABELS.get(it.slot, it.slot),
            "name": it.name,
            "rarity": constants.RARITY_LABELS.get(it.rarity, it.rarity),
            "level": it.level,
        }
        for it in items
    ]
    parts = [
        (constants.BODY_PARTS["fangs"]["label"], creature.fangs_lvl),
        (constants.BODY_PARTS["armor"]["label"], creature.armor_lvl),
        (constants.BODY_PARTS["wings"]["label"], creature.wings_lvl),
        (constants.BODY_PARTS["poison"]["label"], creature.poison_lvl),
    ]
    return {
        "is_fake": False,
        "label": pending["label"],
        "name": creature.name,
        "element": creature.element,
        "rarity": constants.RARITY_LABELS.get(creature.rarity, creature.rarity),
        "level": creature.level,
        "star_level": creature.star_level,
        "stats": stats,
        "gear": gear,
        "gear_power": full_power - bare_power,
        "full_power": full_power,
        "parts": parts,
    }


async def arena_opp_details_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    pending = context.user_data.get(PENDING_OPPONENT_KEY)
    if pending is None:
        await query.answer("اول «پیدا کردن حریف» رو بزن.", show_alert=True)
        return
    await query.answer()
    d = await run_db(_opponent_details_sync, pending)
    keyboard = InlineKeyboardMarkup([[btn("↩️ بازگشت به حریف", style=NAV, callback_data="arena_opp_back")]])
    await safe_edit_message_text(query, opponent_details_text(d), parse_mode="HTML", reply_markup=keyboard)


def opponent_details_text(d: dict) -> str:
    """Render a full opponent readout from _opponent_details_sync's dict. Shared with
    the group «اتک» flow so both show the same detailed card."""
    if d["is_fake"]:
        tier = f"{d['rarity']} · {'⭐' * d['star_level']}\n" if d.get("rarity") else ""
        return (
            f"🔍 <b>جزییات حریف</b>\n\n🏭 <b>{d['label']}</b>\n"
            f"{tier}"
            f"💪 قدرت کل: <b>{d['power']}</b>\n"
            f"{constants.element_label(d['element']) if d.get('element') else ''}\n\n"
            "<i>این یه آزمایشگاه بات هم‌ردهٔ کاپته — هرچی کاپت بالاتر، قوی‌تر و مجهزتره "
            "(نزدیک کاپ ۵۰۰۰ کاملاً مکس و فول‌تجهیزات می‌شه).</i>"
        )
    s = d["stats"]
    lines = [
        "🔍 <b>جزییات حریف</b>",
        f"🏭 <b>{d['label']}</b>\n",
        f"{get_emoji('creature')} <b>{d['name']}</b> · {constants.element_label(d['element'])}",
        f"{d['rarity']} · {'⭐' * d['star_level']} · سطح <b>{d['level']}</b>",
        f"💪 قدرت کل: <b>{d['full_power']}</b>  <i>(از تجهیزات: +{d['gear_power']})</i>\n",
        f"❤️ HP <b>{s['hp']}</b> · ⚔️ ATK <b>{s['atk']}</b> · 🛡 DEF <b>{s['def']}</b> · 💨 SPD <b>{s['spd']}</b>"
        + (f" · ☠️ {round(s['poison'])}" if s.get('poison') else ""),
        "",
        "<b>🦴 ارتقای اعضا:</b>",
    ]
    lines += [f"　{label} — <b>{lvl}</b>" for label, lvl in d["parts"]]
    lines.append("")
    if d["gear"]:
        lines.append("<b>🎒 تجهیزات:</b>")
        lines += [f"　{g['slot']} — {g['name']} +{g['level']} ({g['rarity']})" for g in d["gear"]]
    else:
        lines.append("<i>🎒 هیچ تجهیزاتی نداره.</i>")
    return "\n".join(lines)


def _opponent_reshow_sync(tg_user, pending):
    from bio_lab.models import Creature

    user, _ = get_or_create_user(tg_user)
    creature = Creature.objects.filter(owner=user, is_active=True).first()
    level = creature.level if creature is not None else 1
    my_element = creature.element if creature is not None else None
    dna_win = round(constants.ARENA_WIN_DNA_BASE + level * constants.ARENA_WIN_DNA_PER_LEVEL)
    from game.energy import sync_energy

    cname = creature.name if creature is not None else "—"
    return (user, active_power(user), expected_loot(pending, level), my_element, dna_win,
            cname, sync_energy(user))


async def arena_opp_back_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    pending = context.user_data.get(PENDING_OPPONENT_KEY)
    if pending is None:
        await query.answer("اول «پیدا کردن حریف» رو بزن.", show_alert=True)
        return
    await query.answer()
    user, my_power, loot, my_element, dna_win, cname, energy = await run_db(_opponent_reshow_sync, update.effective_user, pending)
    text, keyboard = _render_opponent(user, pending, my_power, loot, my_element, dna_win, cname, energy)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


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


def _attacker_shield_secs_sync(tg_user) -> int:
    user, _ = get_or_create_user(tg_user)
    return shield_remaining_seconds(user)


async def arena_attack_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    confirmed = query.data.split(":")[1:] == ["c"]  # "arena_attack:c" = shield warning accepted

    # If the attacker currently holds a shield, warn FIRST — attacking spends
    # SHIELD_ATTACK_COST_HOURS off it, and players kept losing their shield without
    # realising it ("سپر مشکل داره، بازم اتک می‌خوریم"). Only attack once they accept.
    if not confirmed:
        if context.user_data.get(PENDING_OPPONENT_KEY) is None:
            await query.answer("اول یه حریف پیدا کن.", show_alert=True)
            return
        shield_secs = await run_db(_attacker_shield_secs_sync, update.effective_user)
        if shield_secs > 0:
            await query.answer()
            keyboard = InlineKeyboardMarkup([
                [btn("✅ بله، حمله کن", style=BATTLE, callback_data="arena_attack:c")],
                [btn("🛡 نه، سپرم بمونه", style=NAV, callback_data="arena_opp_back")],
            ])
            await safe_edit_message_text(
                query,
                f"⚠️ <b>الان سپر محافظ داری</b> ({_fmt_hm(shield_secs)} مونده).\n"
                f"اگه حمله کنی <b>{constants.SHIELD_ATTACK_COST_HOURS} ساعت</b> از سپرت کم می‌شه "
                "و ممکنه دوباره غارت بشی.\nبازم حمله می‌کنی؟",
                parse_mode="HTML", reply_markup=keyboard,
            )
            return

    # CLAIM the pending opponent up front (pop before the await), so a rapid double-tap
    # on «حمله» can't attack — and loot — the same opponent twice. On failure we put it
    # back so the player can retry.
    pending = context.user_data.pop(PENDING_OPPONENT_KEY, None)
    if pending is None:
        await query.answer("اول یه حریف پیدا کن.", show_alert=True)
        return

    try:
        result, completed_missions = await run_db(_attack_sync, update.effective_user, pending)
    except GameError as exc:
        from bot.handlers.energy import show_energy_error

        context.user_data[PENDING_OPPONENT_KEY] = pending  # restore for a retry
        if not await show_energy_error(query, exc):
            await query.answer(str(exc), show_alert=True)
        return

    context.user_data[ARENA_DETAIL_KEY] = result.get("detail_log", "")

    # INSTANT defense report to the raided player (no 5-minute delay)
    from bot.handlers.notify import send_defense_report_now

    await send_defense_report_now(context, result.get("defense"))

    if result["won"]:
        summary = (
            f"{get_emoji('celebrate')} <b>بردی!</b>\n"
            f"{get_emoji('coin')} +{result['loot']} غنیمت + {result.get('dna', 0)} {get_emoji('dna')} از {result['opponent_label']}\n"
            f"🏆 +{result['cup_delta']} کاپ (الان: {result['new_cup']})"
        )
    else:
        summary = (
            f"😔 <b>باختی.</b>\n"
            f"🏆 {result['cup_delta']} کاپ (الان: {result['new_cup']})"
        )

    keyboard = InlineKeyboardMarkup(
        [
            [btn("🔍 جزییات حمله", style=NAV, callback_data="arena_detail")],
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


async def arena_detail_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    detail = context.user_data.get(ARENA_DETAIL_KEY)
    if not detail:
        await query.answer("جزییاتی ذخیره نشده — یه حمله‌ی تازه بزن.", show_alert=True)
        return
    await query.answer()
    keyboard = InlineKeyboardMarkup([[back_btn("menu:arena", "بازگشت به آرنا")]])
    await safe_edit_message_text(query, detail, parse_mode="HTML", reply_markup=keyboard)


# ── shared: view any player's active-creature details (defense reports + revenge) ─
def _user_details_sync(user_id: int) -> dict:
    from bio_lab.models import Creature, User
    from bio_lab.repository import lab_display
    from game.equipment import get_equipped_items

    u = User.objects.filter(id=user_id).first()
    if u is None:
        return {"is_fake": True, "label": "این بازیکن", "power": 0, "element": None}
    creature = Creature.objects.filter(owner=u, is_active=True).first()
    power = creature_power(creature, get_equipped_items(creature)) if creature else 0
    element = creature.element if creature else None
    return _opponent_details_sync({
        "is_fake": creature is None, "user_id": user_id,
        "label": lab_display(u), "power": power, "element": element,
    })


async def defense_opp_details_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """«🔍 جزییات حریف» from a defense report or the revenge list — shows the
    attacker's active creature (level/stars/gear/parts)."""
    query = update.callback_query
    user_id = int(query.data.split(":")[1])
    await query.answer()
    d = await run_db(_user_details_sync, user_id)
    keyboard = InlineKeyboardMarkup([[back_btn("arena_revenges", "بازگشت به انتقام‌ها")]])
    await safe_edit_message_text(query, opponent_details_text(d), parse_mode="HTML", reply_markup=keyboard)


# ── Revenge panel ─────────────────────────────────────────────────────────────

def _revenges_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    now = tz.now()
    items = []
    for log in revengeable_attacks(user):
        atk = log.attacker
        items.append({
            "log_id": log.id,
            "attacker_id": log.attacker_id,
            "name": log.attacker_label or (lab_display(atk) if atk else "یه مهاجم"),
            "power": log.attacker_power or 0,
            "loot": log.loot_gold or 0,
            "won": log.attacker_won,
            "hrs_left": max(0, int((log.created_at + datetime.timedelta(days=3) - now).total_seconds() // 3600)),
            "shield_secs": shield_remaining_seconds(atk) if atk is not None else 0,
        })
    # revenge-able (unshielded) first, so the actionable ones are at the top
    items.sort(key=lambda it: (it["shield_secs"] > 0, -it["power"]))
    return user, items


async def arena_revenges_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user, items = await run_db(_revenges_sync, update.effective_user)

    if not items:
        keyboard = InlineKeyboardMarkup([[back_btn("menu:arena")]])
        await safe_edit_message_text(
            query,
            "⚔️ <b>انتقام‌ها</b>\n\nهیچ‌کس اخیراً بهت حمله نکرده — لیست خالیه.\n"
            "<i>هر حمله‌ای که بهت بشه (چه ببازی چه دفاع کنی) تا ۳ روز اینجا قابل انتقامه.</i>",
            parse_mode="HTML", reply_markup=keyboard,
        )
        return

    ready = [it for it in items if it["shield_secs"] <= 0]
    shielded = [it for it in items if it["shield_secs"] > 0]
    lines = [f"⚔️ <b>انتقام‌ها</b> — {len(items)} مورد ({len(ready)} آماده)\n"]
    rows = []
    for it in ready:
        res = "غارتت کرد" if it["won"] else "دفاع کردی"
        loot = f" · −{it['loot']} {get_emoji('coin')}" if it["loot"] else ""
        lines.append(f"🔴 <b>{it['name']}</b> · 💪{it['power']} · {res}{loot} · ⏳{it['hrs_left']}h")
        row = [btn(f"⚔️ انتقام", style=DANGER, callback_data=f"arena_revenge:{it['log_id']}")]
        if it["attacker_id"]:
            row.append(btn("🔍 جزییات", style=NAV, callback_data=f"defrep_opp:{it['attacker_id']}"))
        rows.append(row)
    if shielded:
        lines.append("\n🛡 <b>الان سپر دارن</b> <i>(تا سپرشون نپره نمی‌شه انتقام گرفت):</i>")
        for it in shielded:
            lines.append(f"　🛡 <b>{it['name']}</b> · 💪{it['power']} — {_fmt_hm(it['shield_secs'])} مونده")

    rows.append([back_btn("menu:arena")])
    await safe_edit_message_text(
        query, "\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows),
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

    from bot.handlers.notify import send_defense_report_now

    await send_defense_report_now(context, result.get("defense"))

    if result["won"]:
        summary = (
            f"{get_emoji('celebrate')} <b>انتقام گرفتی!</b>\n"
            f"{get_emoji('coin')} +{result['loot']} غنیمت + {result.get('dna', 0)} {get_emoji('dna')}\n"
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
    application.add_handler(CallbackQueryHandler(arena_opp_details_callback, pattern=r"^arena_opp_details$"))
    application.add_handler(CallbackQueryHandler(arena_opp_back_callback, pattern=r"^arena_opp_back$"))
    application.add_handler(CallbackQueryHandler(arena_attack_callback, pattern=r"^arena_attack(:c)?$"))
    application.add_handler(CallbackQueryHandler(arena_detail_callback, pattern=r"^arena_detail$"))
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
    application.add_handler(
        CallbackQueryHandler(defense_opp_details_callback, pattern=r"^defrep_opp:\d+$")
    )

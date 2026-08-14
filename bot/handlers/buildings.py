from django.utils import timezone
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

from bio_lab.models import Building
from bio_lab.repository import get_or_create_user
from bot.buttons import DANGER, PRIMARY, SUCCESS, back_btn, btn
from bot.utils import run_db, safe_edit_message_text
from game import constants
from game.buildings import (
    active_upgrade,
    apply_speedup,
    collect,
    get_or_create_buildings,
    list_speedup_cards,
    main_hall_level,
    max_level_for,
    pending_amount,
    produces,
    start_upgrade,
    upgrade_cost_and_minutes,
)
from game.creature import GameError
from game.emoji import get_emoji

_RESOURCE_EMOJI_KEY = {"coins": "coin", "diamonds": "diamond", "dna_fragments": "dna"}
_RESOURCE_NAMES = {"coins": "طلا", "diamonds": "الماس", "dna_fragments": "DNA"}


def _format_remaining(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, _ = divmod(rem, 60)
    if hours:
        return f"{hours} ساعت و {minutes} دقیقه"
    if minutes:
        return f"{minutes} دقیقه"
    return "چند لحظه"


def _buildings_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    buildings = get_or_create_buildings(user)
    upgrade = active_upgrade(user)  # lazily finishes a due upgrade first
    # main hall first, then the rest — it's the gate everything else waits on
    buildings.sort(key=lambda b: (b.building_type != constants.MAIN_BUILDING, b.building_type))
    return buildings, upgrade, main_hall_level(user)


def _buildings_keyboard(buildings: list[Building], upgrade) -> InlineKeyboardMarkup:
    rows = []
    for b in buildings:
        label = constants.BUILDING_LABELS[b.building_type]
        if b.level == 0:
            state = "🔒 ساخته‌نشده"
        else:
            pending = pending_amount(b)
            state = f"Lv{b.level}" + (f" (+{pending})" if pending else "")
        busy_tag = " ⏳" if upgrade is not None and upgrade.building_id == b.id else ""
        rows.append([btn(f"{label} — {state}{busy_tag}", callback_data=f"bld_pick:{b.id}")])
    rows.append([back_btn("menu:me")])
    return InlineKeyboardMarkup(rows)


def _buildings_text(upgrade, hall_level: int) -> str:
    hall = constants.BUILDING_LABELS[constants.MAIN_BUILDING]
    lines = [
        f"{get_emoji('building')} <b>ساختمون‌های تو</b>",
        f"{hall}: سطح <b>{hall_level}</b>/{constants.BUILDING_MAX_LEVEL}",
        "",
    ]
    if upgrade is not None:
        lines.append("<i>⏳ کارگرت الان مشغول یه کاره.</i>\n")
    lines.append("رو هرکدوم بزن تا جزئیاتش رو ببینی:")
    lines.append(
        f"\n<blockquote>هیچ ساختمونی نمی‌تونه از سطح {hall} جلو بزنه — "
        "برای باز شدن بقیه، اول اون رو ارتقا بده.</blockquote>"
    )
    return "\n".join(lines)


async def buildings_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    buildings, upgrade, hall_level = await run_db(_buildings_sync, update.effective_user)
    await update.effective_message.reply_text(
        _buildings_text(upgrade, hall_level),
        parse_mode="HTML",
        reply_markup=_buildings_keyboard(buildings, upgrade),
    )


def _building_detail_sync(tg_user, building_id):
    user, _ = get_or_create_user(tg_user)
    try:
        building = Building.objects.get(id=building_id, owner=user)
    except Building.DoesNotExist:
        raise GameError("این ساختمون پیدا نشد.")
    upgrade = active_upgrade(user)
    pending = pending_amount(building)
    return building, upgrade, pending, max_level_for(user, building.building_type)


def _building_detail_text(building: Building, upgrade, pending: int, cap: int) -> str:
    btype = building.building_type
    label = constants.BUILDING_LABELS[btype]
    level_txt = "🔒 ساخته‌نشده" if building.level == 0 else f"سطح {building.level}/{constants.BUILDING_MAX_LEVEL}"
    lines = [f"{label} — {level_txt}", f"<i>{constants.BUILDING_DESCRIPTIONS[btype]}</i>", ""]

    if produces(btype) and building.level > 0:
        cfg = constants.BUILDING_PRODUCTION[btype]
        resource_emoji = get_emoji(_RESOURCE_EMOJI_KEY[cfg["resource"]])
        lines.append(
            f"⚙️ تولید: {cfg['rate_per_hour'] * building.level:g} در ساعت "
            f"(سقف انبار: {cfg['cap_base'] * building.level})"
        )
        lines.append(f"{resource_emoji} در انتظار جمع‌آوری: <b>{pending}</b>")

    if btype == "blacksmith" and building.level > 0:
        cap_items = building.level * constants.EQUIPMENT_LEVELS_PER_BLACKSMITH_LEVEL
        lines.append(f"🔨 سقف سطح تجهیزات: <b>+{cap_items}</b>")
    if btype == constants.MAIN_BUILDING:
        lines.append(f"⭐ سقف ستاره‌ی هیولاها: <b>{building.level}</b>")

    if upgrade is not None and upgrade.building_id == building.id:
        remaining = (upgrade.finishes_at - timezone.now()).total_seconds()
        verb = "ساخت" if building.level == 0 else "ارتقا"
        lines.append(f"\n⏳ در حال {verb} تا سطح {upgrade.target_level} — {_format_remaining(remaining)} مونده")
    elif upgrade is not None:
        lines.append("\n⏳ کارگرت الان مشغول یه ساختمون دیگه‌ست.")
    elif building.level >= constants.BUILDING_MAX_LEVEL:
        lines.append("\n🏆 این ساختمون به سقف سطح رسیده.")
    elif building.level >= cap:
        hall = constants.BUILDING_LABELS[constants.MAIN_BUILDING]
        lines.append(f"\n🔒 برای ادامه اول باید {hall} رو ارتقا بدی.")
    else:
        cost, minutes = upgrade_cost_and_minutes(building)
        verb = "🏗 ساخت" if building.level == 0 else "🔧 ارتقا به سطح"
        target = "" if building.level == 0 else f" {building.level + 1}"
        lines.append(f"\n{verb}{target}: {cost} {get_emoji('coin')} · {_format_remaining(minutes * 60)}")
    return "\n".join(lines)


def _building_detail_keyboard(building: Building, upgrade, cap: int) -> InlineKeyboardMarkup:
    rows = []
    if produces(building.building_type) and building.level > 0:
        rows.append([btn("جمع‌آوری", emoji_key="btn_collect", style=SUCCESS, callback_data=f"bld_collect:{building.id}")])
    if upgrade is not None and upgrade.building_id == building.id:
        rows.append([btn("سریع‌ترش کن", emoji_key="btn_speedup", style=SUCCESS, callback_data=f"bld_speedup_list:{building.id}")])
    elif upgrade is None and building.level < min(cap, constants.BUILDING_MAX_LEVEL):
        label = "🏗 ساخت" if building.level == 0 else "🔧 شروع ارتقا"
        rows.append([btn(label, emoji_key="btn_build", style=SUCCESS, callback_data=f"bld_upgrade:{building.id}")])
    rows.append([back_btn("menu:buildings")])
    return InlineKeyboardMarkup(rows)


async def building_pick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    building_id = int(query.data.split(":")[1])
    try:
        building, upgrade, pending, cap = await run_db(_building_detail_sync, update.effective_user, building_id)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer()
    await safe_edit_message_text(
        query,
        _building_detail_text(building, upgrade, pending, cap),
        parse_mode="HTML",
        reply_markup=_building_detail_keyboard(building, upgrade, cap),
    )


def _collect_sync(tg_user, building_id):
    user, _ = get_or_create_user(tg_user)
    try:
        building = Building.objects.get(id=building_id, owner=user)
    except Building.DoesNotExist:
        raise GameError("این ساختمون پیدا نشد.")
    amount, resource = collect(user, building)
    upgrade = active_upgrade(user)
    pending = pending_amount(building)
    return building, upgrade, pending, amount, resource, max_level_for(user, building.building_type)


async def building_collect_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    building_id = int(query.data.split(":")[1])
    try:
        building, upgrade, pending, amount, resource, cap = await run_db(
            _collect_sync, update.effective_user, building_id
        )
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer(f"+{amount} {_RESOURCE_NAMES[resource]}!")
    await safe_edit_message_text(
        query,
        _building_detail_text(building, upgrade, pending, cap),
        parse_mode="HTML",
        reply_markup=_building_detail_keyboard(building, upgrade, cap),
    )


def _start_upgrade_sync(tg_user, building_id):
    user, _ = get_or_create_user(tg_user)
    try:
        building = Building.objects.get(id=building_id, owner=user)
    except Building.DoesNotExist:
        raise GameError("این ساختمون پیدا نشد.")
    start_upgrade(user, building)
    upgrade = active_upgrade(user)
    pending = pending_amount(building)
    return building, upgrade, pending, max_level_for(user, building.building_type)


async def building_upgrade_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    building_id = int(query.data.split(":")[1])
    try:
        building, upgrade, pending, cap = await run_db(_start_upgrade_sync, update.effective_user, building_id)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer("🏗 ساخت شروع شد!" if building.level == 0 else "🔧 ارتقا شروع شد!")
    await safe_edit_message_text(
        query,
        _building_detail_text(building, upgrade, pending, cap),
        parse_mode="HTML",
        reply_markup=_building_detail_keyboard(building, upgrade, cap),
    )


def _speedup_list_sync(tg_user, building_id):
    user, _ = get_or_create_user(tg_user)
    cards = list_speedup_cards(user)
    return cards


async def building_speedup_list_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    building_id = int(query.data.split(":")[1])
    cards = await run_db(_speedup_list_sync, update.effective_user, building_id)
    if not cards:
        await query.answer("هیچ کارت سرعتی نداری.", show_alert=True)
        return
    await query.answer()
    rows = [
        [
            btn(
                f"{constants.SPEEDUP_LABELS[c.minutes]} ×{c.count}",
                style=SUCCESS,
                callback_data=f"bld_speedup_do:{building_id}:{c.minutes}",
            )
        ]
        for c in cards
    ]
    rows.append([back_btn(f"bld_pick:{building_id}")])
    await safe_edit_message_text(
        query, f"{get_emoji('speedup')} کدوم کارت سرعت رو استفاده کنم؟", reply_markup=InlineKeyboardMarkup(rows)
    )


def _speedup_do_sync(tg_user, building_id, minutes):
    user, _ = get_or_create_user(tg_user)
    try:
        building = Building.objects.get(id=building_id, owner=user)
    except Building.DoesNotExist:
        raise GameError("این ساختمون پیدا نشد.")
    _, completed = apply_speedup(user, minutes)
    upgrade = active_upgrade(user)
    building.refresh_from_db()
    pending = pending_amount(building)
    return building, upgrade, pending, completed, max_level_for(user, building.building_type)


async def building_speedup_do_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, building_id, minutes = query.data.split(":")
    try:
        building, upgrade, pending, completed, cap = await run_db(
            _speedup_do_sync, update.effective_user, int(building_id), int(minutes)
        )
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer("🏆 ارتقا تموم شد!" if completed else "⚡ سرعت گرفت!")
    await safe_edit_message_text(
        query,
        _building_detail_text(building, upgrade, pending, cap),
        parse_mode="HTML",
        reply_markup=_building_detail_keyboard(building, upgrade, cap),
    )


def register(application) -> None:
    application.add_handler(CommandHandler("buildings", buildings_panel, filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(building_pick_callback, pattern=r"^bld_pick:"))
    application.add_handler(CallbackQueryHandler(building_collect_callback, pattern=r"^bld_collect:"))
    application.add_handler(CallbackQueryHandler(building_upgrade_callback, pattern=r"^bld_upgrade:"))
    application.add_handler(
        CallbackQueryHandler(building_speedup_list_callback, pattern=r"^bld_speedup_list:")
    )
    application.add_handler(CallbackQueryHandler(building_speedup_do_callback, pattern=r"^bld_speedup_do:"))

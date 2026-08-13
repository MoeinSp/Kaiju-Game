from django.utils import timezone
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

from bio_lab.models import Building
from bio_lab.repository import get_or_create_user
from bot.utils import run_db, safe_edit_message_text
from game import constants
from game.buildings import (
    active_upgrade,
    apply_speedup,
    collect,
    get_or_create_buildings,
    list_speedup_cards,
    pending_amount,
    start_upgrade,
)
from game.creature import GameError
from game.emoji import get_emoji

_RESOURCE_EMOJI_KEY = {"coins": "coin", "diamonds": "diamond"}


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
    return buildings, upgrade


def _buildings_keyboard(buildings: list[Building], upgrade) -> InlineKeyboardMarkup:
    rows = []
    for b in buildings:
        pending = pending_amount(b)
        busy_tag = " ⏳" if upgrade is not None and upgrade.building_id == b.id else ""
        rows.append(
            [
                InlineKeyboardButton(
                    f"{constants.BUILDING_LABELS[b.building_type]} Lv{b.level}{busy_tag}"
                    + (f" (+{pending})" if pending else ""),
                    callback_data=f"bld_pick:{b.id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton("◀️ بازگشت", callback_data="menu:me")])
    return InlineKeyboardMarkup(rows)


def _buildings_text(upgrade) -> str:
    header = f"{get_emoji('building')} <b>ساختمون‌های تو</b>\n"
    footer = "رو هرکدوم بزن تا جزئیاتش رو ببینی:"
    if upgrade is None:
        return header + footer
    return header + f"<i>⏳ کارگرت الان مشغول یه ارتقاست.</i>\n\n" + footer


async def buildings_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    buildings, upgrade = await run_db(_buildings_sync, update.effective_user)
    await update.effective_message.reply_text(
        _buildings_text(upgrade), parse_mode="HTML", reply_markup=_buildings_keyboard(buildings, upgrade)
    )


def _building_detail_sync(tg_user, building_id):
    user, _ = get_or_create_user(tg_user)
    try:
        building = Building.objects.get(id=building_id, owner=user)
    except Building.DoesNotExist:
        raise GameError("این ساختمون پیدا نشد.")
    upgrade = active_upgrade(user)
    pending = pending_amount(building)
    return building, upgrade, pending


def _building_detail_text(building: Building, upgrade, pending: int) -> str:
    cfg = constants.BUILDING_PRODUCTION[building.building_type]
    resource_emoji = get_emoji(_RESOURCE_EMOJI_KEY[cfg["resource"]])
    lines = [
        f"{constants.BUILDING_LABELS[building.building_type]} — سطح {building.level}/{constants.BUILDING_MAX_LEVEL}",
        f"⚙️ تولید: {cfg['rate_per_hour'] * building.level:g} در ساعت (سقف انبار: {cfg['cap_base'] * building.level})",
        f"{resource_emoji} در انتظار جمع‌آوری: {pending}",
    ]
    if upgrade is not None and upgrade.building_id == building.id:
        remaining = (upgrade.finishes_at - timezone.now()).total_seconds()
        lines.append(f"\n⏳ در حال ارتقا به سطح {upgrade.target_level} — {_format_remaining(remaining)} مونده")
    elif upgrade is not None:
        lines.append(f"\n⏳ کارگرت الان مشغول یه ساختمون دیگه‌ست.")
    elif building.level < constants.BUILDING_MAX_LEVEL:
        cost = constants.BUILDING_UPGRADE_BASE_GOLD_COST * building.level
        minutes = constants.BUILDING_UPGRADE_BASE_MINUTES * building.level
        lines.append(
            f"\n🔧 ارتقا به سطح {building.level + 1}: {cost} {get_emoji('coin')} · {_format_remaining(minutes * 60)}"
        )
    else:
        lines.append("\n🏆 این ساختمون به سقف سطح رسیده.")
    return "\n".join(lines)


def _building_detail_keyboard(building: Building, upgrade) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton("💰 جمع‌آوری", callback_data=f"bld_collect:{building.id}")]]
    if upgrade is not None and upgrade.building_id == building.id:
        rows.append([InlineKeyboardButton("⚡ سریع‌ترش کن", callback_data=f"bld_speedup_list:{building.id}")])
    elif upgrade is None and building.level < constants.BUILDING_MAX_LEVEL:
        rows.append([InlineKeyboardButton("🔧 شروع ارتقا", callback_data=f"bld_upgrade:{building.id}")])
    rows.append([InlineKeyboardButton("◀️ بازگشت", callback_data="menu:buildings")])
    return InlineKeyboardMarkup(rows)


async def building_pick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    building_id = int(query.data.split(":")[1])
    try:
        building, upgrade, pending = await run_db(_building_detail_sync, update.effective_user, building_id)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer()
    await safe_edit_message_text(
        query,
        _building_detail_text(building, upgrade, pending),
        parse_mode="HTML",
        reply_markup=_building_detail_keyboard(building, upgrade),
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
    return building, upgrade, pending, amount, resource


async def building_collect_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    building_id = int(query.data.split(":")[1])
    try:
        building, upgrade, pending, amount, resource = await run_db(_collect_sync, update.effective_user, building_id)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer(f"+{amount} {'طلا' if resource == 'coins' else 'الماس'}!")
    await safe_edit_message_text(
        query,
        _building_detail_text(building, upgrade, pending),
        parse_mode="HTML",
        reply_markup=_building_detail_keyboard(building, upgrade),
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
    return building, upgrade, pending


async def building_upgrade_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    building_id = int(query.data.split(":")[1])
    try:
        building, upgrade, pending = await run_db(_start_upgrade_sync, update.effective_user, building_id)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer("🔧 ارتقا شروع شد!")
    await safe_edit_message_text(
        query,
        _building_detail_text(building, upgrade, pending),
        parse_mode="HTML",
        reply_markup=_building_detail_keyboard(building, upgrade),
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
            InlineKeyboardButton(
                f"{constants.SPEEDUP_LABELS[c.minutes]} ×{c.count}",
                callback_data=f"bld_speedup_do:{building_id}:{c.minutes}",
            )
        ]
        for c in cards
    ]
    rows.append([InlineKeyboardButton("◀️ بازگشت", callback_data=f"bld_pick:{building_id}")])
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
    return building, upgrade, pending, completed


async def building_speedup_do_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, building_id, minutes = query.data.split(":")
    try:
        building, upgrade, pending, completed = await run_db(
            _speedup_do_sync, update.effective_user, int(building_id), int(minutes)
        )
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer("🏆 ارتقا تموم شد!" if completed else "⚡ سرعت گرفت!")
    await safe_edit_message_text(
        query,
        _building_detail_text(building, upgrade, pending),
        parse_mode="HTML",
        reply_markup=_building_detail_keyboard(building, upgrade),
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

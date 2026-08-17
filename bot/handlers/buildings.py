from django.utils import timezone
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

from bio_lab.models import Building, Creature
from bio_lab.repository import get_or_create_user
from bot.buttons import BUILD, DANGER, LIST, PRIMARY, SHOP, back_btn, back_only_keyboard, btn
from bot.utils import mission_reward_text, run_db, safe_edit_message_text, send_screen
from game import constants
from game.workers import (assign, assigned_creatures, free_creatures, unassign,
                          worker_bonus, worker_slots)
from game.buildings import (
    active_upgrade,
    apply_speedup,
    collect,
    diamond_finish_price,
    finish_with_diamonds,
    get_or_create_buildings,
    list_speedup_cards,
    main_hall_level,
    is_unlocked,
    max_level_for,
    pending_amount,
    unlock_level_for,
    produces,
    start_upgrade,
    upgrade_cost_and_minutes,
)
from game.creature import GameError
from game.daily import check_missions, record_action
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
    # pending_amount() reads the building's stationed workers, so it must be
    # resolved HERE, in sync context — the keyboard builder runs on the event loop
    # and a lazy query there raises SynchronousOnlyOperation
    rows = [(b, pending_amount(b), is_unlocked(user, b.building_type)) for b in buildings]
    return rows, upgrade, main_hall_level(user)


def _buildings_keyboard(building_rows, upgrade) -> InlineKeyboardMarkup:
    """`building_rows` is [(Building, pending_amount, is_unlocked)] — precomputed
    by _buildings_sync, because working any of it out needs the database."""
    rows = []
    for b, pending, unlocked in building_rows:
        label = constants.BUILDING_LABELS[b.building_type]
        if b.level == 0:
            state = "🔒 ساخته‌نشده" if unlocked else f"🔒 از سطح {unlock_level_for(b.building_type)} تالار"
        else:
            state = f"Lv{b.level}" + (f" (+{pending})" if pending else "")
        busy_tag = " ⏳" if upgrade is not None and upgrade.building_id == b.id else ""
        rows.append([btn(f"{label} — {state}{busy_tag}", style=LIST, callback_data=f"bld_pick:{b.id}")])
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
    building_rows, upgrade, hall_level = await run_db(_buildings_sync, update.effective_user)
    await send_screen(update, 
        _buildings_text(upgrade, hall_level),
        parse_mode="HTML",
        reply_markup=_buildings_keyboard(building_rows, upgrade),
    )


def _building_detail_sync(tg_user, building_id):
    user, _ = get_or_create_user(tg_user)
    try:
        building = Building.objects.get(id=building_id, owner=user)
    except Building.DoesNotExist:
        raise GameError("این ساختمون پیدا نشد.")
    return _detail_view(user, building)


def _detail_view(user, building: Building) -> dict:
    """Everything the detail screen renders, gathered in one sync call.

    Returned as a dict rather than a tuple because five different actions
    (collect, upgrade, speed-up, diamond-finish, plain view) all re-render this
    screen afterwards, and threading seven positional values through each of them
    is how a wrong-order bug gets in."""
    return {
        "building": building,
        "upgrade": active_upgrade(user),
        "pending": pending_amount(building),
        "cap": max_level_for(user, building.building_type),
        "workers": assigned_creatures(building),
        "slots": worker_slots(building),
        "bonus": worker_bonus(building),
        "unlocked": is_unlocked(user, building.building_type),
    }


def _building_detail_text(view: dict) -> str:
    building, upgrade = view["building"], view["upgrade"]
    pending, cap = view["pending"], view["cap"]
    workers, slots, bonus = view["workers"], view["slots"], view["bonus"]
    btype = building.building_type
    label = constants.BUILDING_LABELS[btype]
    unlocked = view["unlocked"]
    if building.level > 0:
        level_txt = f"سطح {building.level}/{cap}"
        if btype != constants.MAIN_BUILDING and cap < constants.BUILDING_MAX_LEVEL:
            level_txt += f" (سقف با تالار مِهر)"
    elif unlocked:
        level_txt = "🔒 ساخته‌نشده"
    else:
        level_txt = f"🔒 قفل — از سطح {unlock_level_for(btype)} تالار مِهر"
    lines = [f"{label} — {level_txt}", f"<i>{constants.BUILDING_DESCRIPTIONS[btype]}</i>", ""]

    if produces(btype) and building.level > 0:
        cfg = constants.BUILDING_PRODUCTION[btype]
        resource_emoji = get_emoji(_RESOURCE_EMOJI_KEY[cfg["resource"]])
        base_rate = cfg["rate_per_hour"] * building.level
        lines.append(
            f"⚙️ تولید: <b>{base_rate * (1 + bonus):g}</b> در ساعت "
            f"(سقف انبار: {cfg['cap_base'] * building.level * (1 + bonus):g})"
        )
        if bonus:
            lines.append(f"   <i>{base_rate:g} پایه + {bonus * 100:.0f}٪ از کارگرها</i>")
        lines.append(f"{resource_emoji} در انتظار جمع‌آوری: <b>{pending}</b>")
        lines.append("")
        lines.append(f"👷 <b>کارگرها</b> ({len(workers)}/{slots})")
        if workers:
            for creature in workers:
                gain = creature.level * constants.WORKER_BONUS_PER_CREATURE_LEVEL * 100
                lines.append(f"   • {creature.name} · سطح {creature.level} → +{gain:.0f}٪")
        else:
            lines.append("   <i>خالیه — هر هیولایی که بذاری تولید رو بیشتر می‌کنه.</i>")
        lines.append(
            "<blockquote>هرچی سطح هیولا بالاتر باشه تولید بیشتره. "
            "موجود فعال و هیولاهایی که توی غار هیولا تخم گذاشتن رو نمی‌شه سر کار گذاشت.</blockquote>"
        )

    if btype == "blacksmith" and building.level > 0:
        cap_items = building.level * constants.EQUIPMENT_LEVELS_PER_BLACKSMITH_LEVEL
        lines.append(f"🔨 سقف سطح تجهیزات: <b>+{cap_items}</b>")
    if btype == constants.MAIN_BUILDING:
        lines.append(f"⭐ سقف ستاره‌ی هیولاها: <b>{building.level}</b>")

    if upgrade is not None and upgrade.building_id == building.id:
        remaining = (upgrade.finishes_at - timezone.now()).total_seconds()
        verb = "ساخت" if building.level == 0 else "ارتقا"
        lines.append(f"\n⏳ در حال {verb} تا سطح {upgrade.target_level} — {_format_remaining(remaining)} مونده")
        lines.append(
            f"<i>می‌تونی با کارت سرعت یا {diamond_finish_price(upgrade)} 💎 همین الان تمومش کنی "
            "(هرچی بیشتر صبر کنی، ارزون‌تر می‌شه).</i>"
        )
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


def _building_detail_keyboard(view: dict) -> InlineKeyboardMarkup:
    building, upgrade, cap = view["building"], view["upgrade"], view["cap"]
    workers, slots = view["workers"], view["slots"]
    rows = []
    if produces(building.building_type) and building.level > 0:
        rows.append([btn("جمع‌آوری", emoji_key="btn_collect", style=BUILD, callback_data=f"bld_collect:{building.id}")])
        rows.append(
            [
                btn(
                    f"👷 کارگرها ({len(workers)}/{slots})",
                    style=PRIMARY,
                    callback_data=f"bld_workers:{building.id}",
                )
            ]
        )
    if upgrade is not None and upgrade.building_id == building.id:
        rows.append([btn("سریع‌ترش کن", emoji_key="btn_speedup", style=SHOP, callback_data=f"bld_speedup_list:{building.id}")])
        rows.append(
            [
                btn(
                    f"💎 تمومش کن ({diamond_finish_price(upgrade)} الماس)",
                    style=SHOP,
                    callback_data=f"bld_finish:{building.id}",
                )
            ]
        )
    elif building.level == 0 and not view["unlocked"]:
        pass  # locked: no build button until the hall catches up
    elif upgrade is None and building.level < min(cap, constants.BUILDING_MAX_LEVEL):
        label = "🏗 ساخت" if building.level == 0 else "🔧 شروع ارتقا"
        rows.append([btn(label, emoji_key="btn_build", style=BUILD, callback_data=f"bld_upgrade:{building.id}")])
    rows.append([back_btn("menu:buildings")])
    return InlineKeyboardMarkup(rows)


async def building_pick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    building_id = int(query.data.split(":")[1])
    try:
        view = await run_db(_building_detail_sync, update.effective_user, building_id)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer()
    await safe_edit_message_text(
        query,
        _building_detail_text(view),
        parse_mode="HTML",
        reply_markup=_building_detail_keyboard(view),
    )


def _collect_sync(tg_user, building_id):
    user, _ = get_or_create_user(tg_user)
    try:
        building = Building.objects.get(id=building_id, owner=user)
    except Building.DoesNotExist:
        raise GameError("این ساختمون پیدا نشد.")
    amount, resource = collect(user, building)
    record_action(user, "collect")
    completed_missions = check_missions(user, "collect")
    return _detail_view(user, building), amount, resource, completed_missions


async def building_collect_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    building_id = int(query.data.split(":")[1])
    try:
        view, amount, resource, completed_missions = await run_db(
            _collect_sync, update.effective_user, building_id
        )
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer(f"+{amount} {_RESOURCE_NAMES[resource]}!")
    text = _building_detail_text(view)
    if completed_missions:
        text += "\n\n" + "\n".join(
            f"{get_emoji('mission')} ماموریت «{m['label']}» تکمیل شد! {mission_reward_text(m)}"
            for m in completed_missions
        )
    await safe_edit_message_text(
        query,
        text,
        parse_mode="HTML",
        reply_markup=_building_detail_keyboard(view),
    )


def _start_upgrade_sync(tg_user, building_id):
    user, _ = get_or_create_user(tg_user)
    try:
        building = Building.objects.get(id=building_id, owner=user)
    except Building.DoesNotExist:
        raise GameError("این ساختمون پیدا نشد.")
    start_upgrade(user, building)
    return _detail_view(user, building)


async def building_upgrade_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    building_id = int(query.data.split(":")[1])
    try:
        view = await run_db(_start_upgrade_sync, update.effective_user, building_id)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer("🏗 ساخت شروع شد!" if view["building"].level == 0 else "🔧 ارتقا شروع شد!")
    await safe_edit_message_text(
        query,
        _building_detail_text(view),
        parse_mode="HTML",
        reply_markup=_building_detail_keyboard(view),
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
                style=BUILD,
                callback_data=f"bld_speedup_do:{building_id}:{c.minutes}",
            )
        ]
        for c in cards
    ]
    rows.append([back_btn(f"bld_pick:{building_id}")])
    await safe_edit_message_text(
        query, f"{get_emoji('speedup')} کدوم کارت سرعت رو استفاده کنم؟", reply_markup=InlineKeyboardMarkup(rows)
    )


def _finish_with_diamonds_sync(tg_user, building_id):
    user, _ = get_or_create_user(tg_user)
    try:
        building = Building.objects.get(id=building_id, owner=user)
    except Building.DoesNotExist:
        raise GameError("این ساختمون پیدا نشد.")
    _, cost = finish_with_diamonds(user)
    building.refresh_from_db()
    return _detail_view(user, building), cost


async def building_finish_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    building_id = int(query.data.split(":")[1])
    try:
        view, cost = await run_db(_finish_with_diamonds_sync, update.effective_user, building_id)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer(f"💎 −{cost} — تموم شد!")
    await safe_edit_message_text(
        query,
        f"💎 <b>با {cost} الماس تموم شد!</b>\n\n" + _building_detail_text(view),
        parse_mode="HTML",
        reply_markup=_building_detail_keyboard(view),
    )


def _speedup_do_sync(tg_user, building_id, minutes):
    user, _ = get_or_create_user(tg_user)
    try:
        building = Building.objects.get(id=building_id, owner=user)
    except Building.DoesNotExist:
        raise GameError("این ساختمون پیدا نشد.")
    _, completed = apply_speedup(user, minutes)
    building.refresh_from_db()
    return _detail_view(user, building), completed


def _workers_sync(tg_user, building_id):
    user, _ = get_or_create_user(tg_user)
    try:
        building = Building.objects.get(id=building_id, owner=user)
    except Building.DoesNotExist:
        raise GameError("این ساختمون پیدا نشد.")
    return user, building, assigned_creatures(building), worker_slots(building), free_creatures(user)


def _workers_text(building: Building, workers, slots: int, free) -> str:
    label = constants.BUILDING_LABELS[building.building_type]
    bonus = sum(c.level for c in workers) * constants.WORKER_BONUS_PER_CREATURE_LEVEL
    lines = [
        f"👷 <b>کارگرهای {label}</b>",
        f"<blockquote>{len(workers)} از {slots} جایگاه پره — هر سطح ساختمون یه جایگاه می‌ده."
        f"\nتولید فعلی: <b>+{bonus * 100:.0f}٪</b></blockquote>",
        "",
    ]
    if workers:
        lines.append("<b>سر کار:</b>")
        for creature in workers:
            gain = creature.level * constants.WORKER_BONUS_PER_CREATURE_LEVEL * 100
            lines.append(f"⛏ {creature.name} · سطح {creature.level} → +{gain:.0f}٪")
        lines.append("")
    if len(workers) >= slots:
        lines.append("<i>جایگاه خالی نداری. برای جای بیشتر ساختمون رو ارتقا بده.</i>")
    elif free:
        lines.append("<b>آماده‌ی کار:</b> یکی رو انتخاب کن")
    else:
        lines.append(
            "<i>هیچ هیولای آزادی نداری. موجود فعال و هیولاهایی که توی غار هیولا تخم گذاشتن نمی‌تونن کار کنن.</i>"
        )
    return "\n".join(lines)


def _workers_keyboard(building: Building, workers, slots: int, free) -> InlineKeyboardMarkup:
    rows = [
        [
            btn(
                f"➖ {c.name} (سطح {c.level})",
                style=DANGER,
                callback_data=f"bld_unassign:{building.id}:{c.id}",
            )
        ]
        for c in workers
    ]
    if len(workers) < slots:
        rows.extend(
            [
                btn(
                    f"➕ {c.name} · سطح {c.level} → +{c.level * constants.WORKER_BONUS_PER_CREATURE_LEVEL * 100:.0f}٪",
                    style=BUILD,
                    callback_data=f"bld_assign:{building.id}:{c.id}",
                )
            ]
            for c in free[:10]
        )
    rows.append([back_btn(f"bld_pick:{building.id}")])
    return InlineKeyboardMarkup(rows)


async def building_workers_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    building_id = int(query.data.split(":")[1])
    try:
        _user, building, workers, slots, free = await run_db(
            _workers_sync, update.effective_user, building_id
        )
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer()
    await safe_edit_message_text(
        query,
        _workers_text(building, workers, slots, free),
        parse_mode="HTML",
        reply_markup=_workers_keyboard(building, workers, slots, free),
    )


def _assign_sync(tg_user, building_id, creature_id, attach: bool):
    user, _ = get_or_create_user(tg_user)
    try:
        building = Building.objects.get(id=building_id, owner=user)
    except Building.DoesNotExist:
        raise GameError("این ساختمون پیدا نشد.")
    try:
        creature = Creature.objects.get(id=creature_id, owner=user)
    except Creature.DoesNotExist:
        raise GameError("این موجود توی کلکسیون تو نیست.")
    if attach:
        assign(user, building, creature)
    else:
        unassign(user, creature)
    return (
        creature,
        building,
        assigned_creatures(building),
        worker_slots(building),
        free_creatures(user),
    )


async def building_assign_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    action, building_id, creature_id = query.data.split(":")
    attach = action == "bld_assign"
    try:
        creature, building, workers, slots, free = await run_db(
            _assign_sync, update.effective_user, int(building_id), int(creature_id), attach
        )
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer(f"{creature.name} {'سر کار رفت' if attach else 'برگشت'}")
    await safe_edit_message_text(
        query,
        _workers_text(building, workers, slots, free),
        parse_mode="HTML",
        reply_markup=_workers_keyboard(building, workers, slots, free),
    )


async def building_speedup_do_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, building_id, minutes = query.data.split(":")
    try:
        view, completed = await run_db(
            _speedup_do_sync, update.effective_user, int(building_id), int(minutes)
        )
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer("🏆 ارتقا تموم شد!" if completed else "⚡ سرعت گرفت!")
    await safe_edit_message_text(
        query,
        _building_detail_text(view),
        parse_mode="HTML",
        reply_markup=_building_detail_keyboard(view),
    )


def register(application) -> None:
    application.add_handler(CommandHandler("buildings", buildings_panel, filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(building_pick_callback, pattern=r"^bld_pick:"))
    application.add_handler(CallbackQueryHandler(building_collect_callback, pattern=r"^bld_collect:"))
    application.add_handler(CallbackQueryHandler(building_upgrade_callback, pattern=r"^bld_upgrade:"))
    application.add_handler(CallbackQueryHandler(building_finish_callback, pattern=r"^bld_finish:"))
    application.add_handler(
        CallbackQueryHandler(building_speedup_list_callback, pattern=r"^bld_speedup_list:")
    )
    application.add_handler(CallbackQueryHandler(building_speedup_do_callback, pattern=r"^bld_speedup_do:"))
    application.add_handler(CallbackQueryHandler(building_workers_callback, pattern=r"^bld_workers:"))
    application.add_handler(
        CallbackQueryHandler(building_assign_callback, pattern=r"^bld_(assign|unassign):")
    )

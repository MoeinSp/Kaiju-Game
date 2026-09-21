from django.utils import timezone
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

from bio_lab.models import Building, Creature
from bio_lab.repository import get_or_create_user
from bot.buttons import BUILD, CONFIRM, DANGER, LIST, NAV, PRIMARY, SHOP, back_btn, back_only_keyboard, btn
from bot.utils import mission_reward_text, run_db, safe_edit_message_text, send_screen
from game import constants
from game.workers import (assign, assigned_creatures, creature_mine_influence,
                          free_creatures, unassign, worker_bonus, worker_slots)
from game.buildings import (
    active_upgrade,
    active_upgrades,
    active_upgrade_count,
    apply_speedup,
    builder_slots,
    buy_second_builder,
    cancel_refund,
    cancel_upgrade,
    collect,
    diamond_finish_price,
    finish_with_diamonds,
    get_or_create_buildings,
    list_speedup_cards,
    main_hall_level,
    is_unlocked,
    max_level_for,
    pending_amount,
    production_rate,
    storage_cap,
    unlock_level_for,
    produces,
    start_upgrade,
    upgrade_cost_and_minutes,
    upgrade_seconds,
    upgrade_for_building,
)

# English subtitle per producing building, for the "🏭 … | Gold Collector" header
_COLLECTOR_EN = {
    "gold_collector": "Gold Collector",
    "dna_lab": "DNA Lab",
    "diamond_collector": "Diamond Mine",
}
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
    alert_txt = ""
    if (user.plundered_alert_gold or 0) > 0 or (user.plundered_alert_dna or 0) > 0:
        alert_txt = (
            f"⚠️ <b>هشدار غارت معدن</b>\n"
            f"در حمله اخیر آرنا به شما، بخشی از منابع ذخیره‌شده به غارت رفت:\n"
            f"💰 طلا: <code>{user.plundered_alert_gold:,}</code>\n"
            f"🧬 DNA: <code>{user.plundered_alert_dna:,}</code>\n\n"
        )
        user.plundered_alert_gold = 0
        user.plundered_alert_dna = 0
        user.save(update_fields=["plundered_alert_gold", "plundered_alert_dna"])
    buildings = get_or_create_buildings(user)
    upgrades = active_upgrades(user)  # lazily finishes due upgrades first
    upgrading_ids = {u.building_id for u in upgrades}
    slots = builder_slots(user)
    # main hall first, then the rest — it's the gate everything else waits on
    buildings.sort(key=lambda b: (b.building_type != constants.MAIN_BUILDING, b.building_type))
    # pending_amount() reads the building's stationed workers, so it must be
    # resolved HERE, in sync context — the keyboard builder runs on the event loop
    # and a lazy query there raises SynchronousOnlyOperation
    rows = [(b, pending_amount(b), is_unlocked(user, b.building_type)) for b in buildings]
    return rows, upgrading_ids, main_hall_level(user), len(upgrades), slots, user.diamonds, alert_txt


def _buildings_keyboard(building_rows, upgrading_ids, busy_count, slots, diamonds) -> InlineKeyboardMarkup:
    """`building_rows` is [(Building, pending_amount, is_unlocked)] — precomputed
    by _buildings_sync, because working any of it out needs the database."""
    rows = []
    for b, pending, unlocked in building_rows:
        label = constants.BUILDING_LABELS[b.building_type]
        if b.level == 0:
            state = "ساخته‌نشده" if unlocked else f"سطح {unlock_level_for(b.building_type)} تالار"
        else:
            state = f"سطح {b.level}" + (f" (+{pending:,})" if pending else "")
        busy_tag = " (ارتقا)" if b.id in upgrading_ids else ""
        rows.append([btn(f"{label} — {state}{busy_tag}", emoji_key=f"btn_bld_{b.building_type}", style=LIST, callback_data=f"bld_pick:{b.id}")])
    if slots < constants.MAX_BUILDER_SLOTS:
        rows.append([btn(
            "خرید کارگر دوم",
            emoji_key="btn_workers",
            style=SHOP, callback_data="bld_buy_builder",
        )])
    rows.append([back_btn("menu:me")])
    return InlineKeyboardMarkup(rows)


def _buildings_text(busy_count, slots, hall_level: int) -> str:
    hall = constants.BUILDING_LABELS[constants.MAIN_BUILDING]
    lines = [
        f"{get_emoji('building')} <b>ساختمون‌های تو</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"🏛 {hall}: سطح <code>{hall_level}</code> / <code>{constants.BUILDING_MAX_LEVEL}</code>",
        f"👷‍♂️ کارگران: <code>{busy_count}</code> / <code>{slots}</code> مشغول",
        "━━━━━━━━━━━━━━━━━━━━",
    ]
    if slots < constants.MAX_BUILDER_SLOTS:
        lines.append(
            f"<i>با خرید کارگر دوم می‌تونی هم‌زمان دو ساختمون رو ارتقا بدی و زمان ساخت‌وساز رو نصف کنی.</i>\n"
        )
    lines.append("رو هرکدوم بزن تا جزئیاتش رو ببینی:")
    lines.append(
        f"\n<blockquote>هیچ ساختمونی نمی‌تونه از سطح <code>{hall_level}</code> جلو بزنه — "
        "برای باز شدن بقیه، اول اون رو ارتقا بده.</blockquote>"
    )
    return "\n".join(lines)


async def buildings_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    building_rows, upgrading_ids, hall_level, busy_count, slots, diamonds, alert_txt = await run_db(
        _buildings_sync, update.effective_user
    )
    from game.media import get_building_image_path
    hall_photo = get_building_image_path("main_hall", hall_level)
    await send_screen(update,
        alert_txt + _buildings_text(busy_count, slots, hall_level),
        photo=hall_photo,
        parse_mode="HTML",
        reply_markup=_buildings_keyboard(building_rows, upgrading_ids, busy_count, slots, diamonds),
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
    is_producer = produces(building.building_type) and building.level > 0
    active_upgrade(user)  # finish any due upgrades before we count/inspect
    workers = assigned_creatures(building)
    return {
        "building": building,
        "upgrade": upgrade_for_building(user, building),
        "busy_count": active_upgrade_count(user),
        "builder_slots": builder_slots(user),
        "pending": pending_amount(building),
        "cap": max_level_for(user, building.building_type),
        "workers": workers,
        "slots": worker_slots(building),
        "bonus": worker_bonus(building),
        # per-worker influence (queries gear) — resolve HERE in sync, never in the async
        # text builder (SynchronousOnlyOperation crash otherwise)
        "worker_influence": {c.id: creature_mine_influence(c, building) for c in workers},
        "unlocked": is_unlocked(user, building.building_type),
        # rate/storage query the DB (worker_bonus) — resolve them HERE in sync context,
        # never inside the async text builder (that was the "mines won't open" crash)
        "rate": production_rate(building) if is_producer else 0.0,
        "store_cap": storage_cap(building) if is_producer else 0,
    }


def _pbar(current, total, width: int = 10) -> str:
    total = max(int(total), 1)
    pct = max(0, min(100, round(current / total * 100)))
    filled = min(width, max(0, round(width * max(current, 0) / total)))
    return f"[{'■' * filled}{'□' * (width - filled)}] <code>{pct}٪</code>"


def _building_detail_text(view: dict) -> str:
    building, upgrade = view["building"], view["upgrade"]
    pending, cap = view["pending"], view["cap"]
    workers, slots, bonus = view["workers"], view["slots"], view["bonus"]
    busy_count, builder_slots_n = view["busy_count"], view["builder_slots"]
    all_builders_busy = upgrade is None and busy_count >= builder_slots_n
    btype = building.building_type
    label = constants.BUILDING_LABELS[btype]
    unlocked = view["unlocked"]
    div = "━━━━━━━━━━━━━━━━━━━━"

    # ── producing buildings get the rich "collector" dashboard ────────────────
    if produces(btype) and building.level > 0:
        cfg = constants.BUILDING_PRODUCTION[btype]
        resource_emoji = get_emoji(_RESOURCE_EMOJI_KEY[cfg["resource"]])
        rate = view["rate"]
        base_rate = cfg["rate_per_hour"] * building.level
        cap_store = view["store_cap"]
        en = _COLLECTOR_EN.get(btype, "")
        lines = [
            f"🏭 <b>{label}</b>" + (f"\n<i>{en}</i>" if en else ""),
            div,
            f"🎖 سطح سازه: <code>{building.level}</code> / <code>{cap}</code>",
            f"📦 وضعیت مخزن: {_pbar(pending, cap_store)}",
            f"📊 ظرفیت ذخیره: <code>{pending:,}</code> / <code>{cap_store:,}</code>",
            f"{resource_emoji} آماده برداشت: <code>+{pending:,}</code>",
            div,
            f"📈 نرخ کل تولید: <code>{rate:.1f}</code> در ساعت",
            f"▫️ تولید پایه: <code>{base_rate:g}</code>",
            f"▫️ بونوس کارگران: <code>+{bonus * 100:.0f}٪</code>",
            div,
            f"👷‍♂️ کارگران مستقر (<code>{len(workers)}</code> / <code>{slots}</code>):",
        ]
        if workers:
            influence = view.get("worker_influence", {})
            w_lines = []
            for c in workers:
                gain = influence.get(c.id, 0.0) * 100
                w_lines.append(f"▫️ {c.name} (<code>+{gain:.0f}٪</code>)")
            lines.append("\n".join(w_lines))
        else:
            lines.append("<i>خالیه — کایجو بذار تا تولید بیشتر بشه.</i>")
        lines.append(div)
        # upgrade / status block for producers
        if upgrade is not None:
            remaining = (upgrade.finishes_at - timezone.now()).total_seconds()
            lines.append(f"⏳ در حال ارتقا تا سطح <code>{upgrade.target_level}</code>")
            lines.append(f"⏱ زمان باقیمانده: <code>{_format_remaining(remaining)}</code>")
            lines.append(f"<i>با کارت سرعت یا <code>{diamond_finish_price(upgrade)} 💎</code> تمومش کن.</i>")
        elif all_builders_busy:
            lines.append(f"⏳ هر دو کارگرت مشغول ساختمون‌های دیگه‌ان (<code>{busy_count}</code> / <code>{builder_slots_n}</code>).")
        elif building.level >= constants.BUILDING_MAX_LEVEL:
            lines.append(f"{get_emoji('trophy')} این سازه به سقف سطح رسیده.")
        elif building.level >= cap:
            hall = constants.BUILDING_LABELS[constants.MAIN_BUILDING]
            lines.append(f"🔒 برای ادامه اول باید {hall} رو ارتقا بدی.")
        else:
            cost, _minutes = upgrade_cost_and_minutes(building)
            lines.append(f"🔧 ارتقا به سطح <code>{building.level + 1}</code>:")
            lines.append(f"💰 هزینه: <code>{cost:,} طلا</code>")
            lines.append(f"⏱ زمان ساخت: <code>{_format_remaining(upgrade_seconds(building))}</code>")
        return "\n".join(lines)

    # ── non-producing / not-yet-built buildings: a clean, consistent "info card" ─────
    hall = constants.BUILDING_LABELS[constants.MAIN_BUILDING]
    # header status tag
    if building.level == 0:
        status_tag = "🔒 <i>(قفل)</i>" if not unlocked else "🔒 <i>(ساخته‌نشده)</i>"
    else:
        status_tag = f"سطح <code>{building.level}</code> / <code>{cap}</code>"
        if btype != constants.MAIN_BUILDING and cap < constants.BUILDING_MAX_LEVEL:
            status_tag += " <i>(سقف با تالار مِهر)</i>"
    lines = [
        f"🏛 <b>{label}</b>",
        f"📌 وضعیت: {status_tag}",
        div,
        f"<i>{constants.BUILDING_DESCRIPTIONS[btype]}</i>",
        div,
    ]

    # info bullets
    if building.level == 0 and not unlocked:
        lines.append(f"🔓 پیش‌نیاز باز شدن: {hall} (سطح <code>{unlock_level_for(btype)}</code>)")
    benefit = constants.BUILDING_UPGRADE_BENEFIT.get(btype)
    if benefit:
        lines.append(f"📈 مزیت ارتقا: {benefit}")
    note = constants.BUILDING_RULE_NOTE.get(btype)
    if note:
        lines.append(f"<blockquote>{note}</blockquote>")
    # live built-state extras
    if btype == "blacksmith" and building.level > 0:
        lines.append(f"🔨 سقفِ فعلیِ سطحِ تجهیزات: <code>+{building.level * constants.EQUIPMENT_LEVELS_PER_BLACKSMITH_LEVEL}</code>")
    if btype == constants.MAIN_BUILDING and building.level > 0:
        lines.append(f"⭐ سقفِ فعلیِ ستاره‌ی هیولاها: <code>{building.level}</code>")
        next_lvl = building.level + 1
        if next_lvl <= constants.BUILDING_MAX_LEVEL:
            _UNLOCKS_BY_HALL = {
                2: [
                    f"{get_emoji('chest_magical')} تالار ادغام و غار هیولا",
                    f"{get_emoji('dna')} ساخت آزمایشگاه DNA و تالار تجارت",
                    f"{get_emoji('ticket')} دسترسی به پاس ماهانه و صرافی طلا/DNA",
                    f"{get_emoji('star')} افزایش سقف ترکیب به ۲ ستاره",
                ],
                3: [
                    "⚒ ساخت و بازگشایی آهنگری تجهیزات",
                    "👥 چیدمان تیم ۳ نفره مبارزات",
                    f"{get_emoji('diamond')} ساخت معدن جمع‌کننده الماس",
                    f"{get_emoji('trophy')} ورود به لیگ رتبه‌بندی و رویدادها",
                    f"{get_emoji('def')} فروشگاه سپر و مبادله تجهیزات",
                    f"{get_emoji('star')} افزایش سقف ترکیب به ۳ ستاره",
                ],
                4: [
                    "🗺 نبردهای کمپین و دانجن داستانی",
                    "🏛 دسترسی به حراجی بازار سیاه",
                    f"{get_emoji('casino')} گردونه و بازی‌های کازینو",
                    "🎖 کسب عناوین افتخاری و رتبه‌بندی خزانه",
                    f"{get_emoji('star')} افزایش سقف ترکیب به ۴ ستاره",
                ],
                5: [
                    f"{get_emoji('dungeon')} صعود به ۱۰۰ طبقه برج موگن",
                    "🔬 ساخت آزمایشگاه تحقیقات ژنتیک",
                    f"{get_emoji('sub_vip')} شرکت در لیگ اتحادها و رتبه‌بندی رید",
                    f"{get_emoji('shop_item')} گردونه بنر ویژه و شاپ آیتم‌های خاص",
                    f"{get_emoji('star')} افزایش سقف ترکیب به ۵ ستاره (نهایی)",
                ],
            }
            unlock_items = _UNLOCKS_BY_HALL.get(next_lvl, [])
            if unlock_items:
                lines.append(f"\n✨ <b>امکانات بازشونده در سطح <code>{next_lvl}</code>:</b>")
                quote_text = "\n".join(f"▫️ {item}" for item in unlock_items)
                lines.append(f"<blockquote>{quote_text}</blockquote>")

    lines += [div]
    # footer: current build/upgrade state
    if upgrade is not None:
        remaining = (upgrade.finishes_at - timezone.now()).total_seconds()
        verb = "ساخت" if building.level == 0 else "ارتقا"
        lines.append(f"⏳ در حال {verb} تا سطح <code>{upgrade.target_level}</code>")
        lines.append(f"⏱ زمان باقیمانده: <code>{_format_remaining(remaining)}</code>")
        lines.append(
            f"<i>می‌تونی با کارت سرعت یا <code>{diamond_finish_price(upgrade)} 💎</code> همین الان تمومش کنی.</i>"
        )
    elif building.level == 0 and not unlocked:
        lines.append(f"🔒 هنوز قفله — از سطح <code>{unlock_level_for(btype)}</code> {hall} باز می‌شه.")
    elif all_builders_busy:
        lines.append(f"⏳ کارگرها: هر دو کارگرت مشغول ساختمون‌های دیگه‌ان (<code>{busy_count}</code> / <code>{builder_slots_n}</code>).")
    elif building.level >= constants.BUILDING_MAX_LEVEL:
        lines.append(f"{get_emoji('trophy')} این ساختمون به سقف سطح رسیده.")
    elif building.level >= cap:
        lines.append(f"🔒 برای ادامه اول باید {hall} رو ارتقا بدی.")
    else:
        cost, _minutes = upgrade_cost_and_minutes(building)
        verb = f"{get_emoji('building')} هزینه ساخت" if building.level == 0 else f"🔧 هزینه ارتقا به سطح <code>{building.level + 1}</code>"
        lines.append(f"{verb}: <code>{cost:,} طلا</code>")
        lines.append(f"⏱ مدت زمان: <code>{_format_remaining(upgrade_seconds(building))}</code>")
    return "\n".join(lines)


def _building_detail_keyboard(view: dict) -> InlineKeyboardMarkup:
    building, upgrade, cap = view["building"], view["upgrade"], view["cap"]
    workers, slots = view["workers"], view["slots"]
    all_builders_busy = upgrade is None and view["busy_count"] >= view["builder_slots"]
    rows = []
    if produces(building.building_type) and building.level > 0:
        res_name = _RESOURCE_NAMES.get(constants.BUILDING_PRODUCTION[building.building_type]["resource"], "")
        rows.append([btn(f"جمع‌آوری {res_name}", emoji_key="btn_collect", style=BUILD, callback_data=f"bld_collect:{building.id}")])
        rows.append(
            [
                btn(
                    f"مدیریت کارگران ({len(workers)}/{slots})",
                    emoji_key="btn_workers",
                    style=PRIMARY,
                    callback_data=f"bld_workers:{building.id}",
                )
            ]
        )
    if upgrade is not None:
        rows.append([
            btn("تسریع با کارت", emoji_key="btn_speedup", style=SHOP, callback_data=f"bld_speedup_list:{building.id}"),
            btn("اتمام فوری", emoji_key="btn_bld_diamond_collector", style=SHOP, callback_data=f"bld_finish_ask:{building.id}"),
        ])
        rows.append(
            [
                btn(
                    "لغو ارتقا",
                    emoji_key="btn_cancel",
                    style=DANGER,
                    callback_data=f"bld_cancel_ask:{building.id}",
                )
            ]
        )
    elif building.level == 0 and not view["unlocked"]:
        pass  # locked: no build button until the hall catches up
    elif all_builders_busy:
        pass  # both builders are working other buildings — text explains it
    elif building.level < min(cap, constants.BUILDING_MAX_LEVEL):
        label = "شروع ساخت" if building.level == 0 else "شروع ارتقا"
        rows.append([btn(label, emoji_key="btn_build", style=BUILD, callback_data=f"bld_upgrade:{building.id}")])
    if building.building_type == "research_lab" and building.level > 0:
        rows.append([btn("پژوهش‌ها", emoji_key="btn_research", style=PRIMARY, callback_data="menu:research")])
    if building.building_type == "blacksmith" and building.level > 0:
        rows.append([btn("آهنگری", emoji_key="btn_forge", style=PRIMARY, callback_data="menu:blacksmith")])
    if building.building_type == "fusion_hall" and building.level > 0:
        rows.append([btn("تالار ادغام", emoji_key="btn_fusion", style=PRIMARY, callback_data="menu:fusion")])
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
    from game.media import get_building_image_path
    photo_path = get_building_image_path(view["building"].building_type, view["building"].level)
    await send_screen(
        update,
        _building_detail_text(view),
        photo=photo_path,
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
    from game.media import get_building_image_path
    photo_path = get_building_image_path(view["building"].building_type, view["building"].level)
    await send_screen(
        update,
        text,
        photo=photo_path,
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
        from bot.handlers.shop import show_gold_error

        if await show_gold_error(query, exc):
            return
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer(f"{get_emoji('building')} ساخت شروع شد!" if view["building"].level == 0 else "🔧 ارتقا شروع شد!")
    from game.media import get_building_image_path
    photo_path = get_building_image_path(view["building"].building_type, view["building"].level)
    await safe_edit_message_text(
        query,
        _building_detail_text(view),
        photo=photo_path,
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
    rows = []
    for c in cards:
        row = [btn(
            f"{constants.speedup_label(c.minutes)} (۱×)",
            emoji_key="btn_speedup",
            style=BUILD, callback_data=f"bld_speedup_do:{building_id}:{c.minutes}",
        )]
        if c.count > 1:
            row.append(btn(
                f"همه ({c.count}×)",
                emoji_key="btn_speedup",
                style=SHOP,
                callback_data=f"bld_speedup_all:{building_id}:{c.minutes}",
            ))
        rows.append(row)
    rows.append([back_btn(f"bld_pick:{building_id}")])
    await safe_edit_message_text(
        query,
        f"{get_emoji('speedup')} <b>انتخاب کارت سرعت</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>کارت مورد نظر را برای کاهش زمان ارتقا انتخاب کنید.</blockquote>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(rows),
    )


def _finish_with_diamonds_sync(tg_user, building_id):
    user, _ = get_or_create_user(tg_user)
    try:
        building = Building.objects.get(id=building_id, owner=user)
    except Building.DoesNotExist:
        raise GameError("این ساختمون پیدا نشد.")
    _, cost = finish_with_diamonds(user, building_id)
    building.refresh_from_db()
    return _detail_view(user, building), cost


def _finish_price_sync(tg_user, building_id):
    user, _ = get_or_create_user(tg_user)
    upgrade = upgrade_for_building(user, Building.objects.get(id=building_id, owner=user))
    if upgrade is None:
        raise GameError("این ساختمون در حال ارتقا نیست.")
    return diamond_finish_price(upgrade)


async def building_finish_ask_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Confirm step before spending diamonds — so nobody finishes an upgrade by a
    mis-tap and loses diamonds without meaning to."""
    query = update.callback_query
    building_id = int(query.data.split(":")[1])
    try:
        cost = await run_db(_finish_price_sync, update.effective_user, building_id)
    except (GameError, Building.DoesNotExist):
        await query.answer("این ساختمون در حال ارتقا نیست.", show_alert=True)
        return
    await query.answer()
    await safe_edit_message_text(
        query,
        f"💎 <b>تمام‌کردن فوری با الماس</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"<blockquote>هزینه اتمام فوری: <code>{cost:,} الماس</code></blockquote>\n\n"
        f"آیا برای پایان ساختمون اطمینان داری؟",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            btn("تأیید", emoji_key="btn_confirm", style=CONFIRM, callback_data=f"bld_finish:{building_id}"),
            btn("انصراف", emoji_key="btn_cancel", style=DANGER, callback_data=f"bld_pick:{building_id}"),
        ]]),
    )


async def building_finish_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    building_id = int(query.data.split(":")[1])
    try:
        view, cost = await run_db(_finish_with_diamonds_sync, update.effective_user, building_id)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer(f"💎 −{cost} — تموم شد!")
    from game.media import get_building_image_path
    photo_path = get_building_image_path(view["building"].building_type, view["building"].level)
    await safe_edit_message_text(
        query,
        f"💎 <b>ارتقا با موفقیت انجام شد!</b>\n"
        f"هزینه پرداختی: <code>{cost} الماس</code>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n" + _building_detail_text(view),
        photo=photo_path,
        parse_mode="HTML",
        reply_markup=_building_detail_keyboard(view),
    )


def _cancel_price_sync(tg_user, building_id):
    user, _ = get_or_create_user(tg_user)
    upgrade = upgrade_for_building(user, Building.objects.get(id=building_id, owner=user))
    if upgrade is None:
        raise GameError("این ساختمون در حال ارتقا نیست.")
    return cancel_refund(upgrade), upgrade.target_level


async def building_cancel_ask_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Confirm step before cancelling an upgrade — half the gold is refunded and the
    build progress is lost, so it must be a deliberate choice, not a mis-tap."""
    query = update.callback_query
    building_id = int(query.data.split(":")[1])
    try:
        refund, target = await run_db(_cancel_price_sync, update.effective_user, building_id)
    except (GameError, Building.DoesNotExist):
        await query.answer("این ساختمون در حال ارتقا نیست.", show_alert=True)
        return
    await query.answer()
    await safe_edit_message_text(
        query,
        f"❌ <b>لغو ارتقا</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"<blockquote>در صورت لغو ارتقا (تا سطح <code>{target}</code>)، پیشرفت ساخت از بین رفته و "
        f"تنها نصف طلای پرداختی (<code>+{refund:,} طلا</code>) برگشت داده می‌شود.</blockquote>\n\n"
        f"آیا برای لغو ارتقا اطمینان داری؟",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            btn("تأیید لغو", emoji_key="btn_confirm", style=DANGER, callback_data=f"bld_cancel:{building_id}"),
            btn("انصراف", emoji_key="btn_cancel", style=CONFIRM, callback_data=f"bld_pick:{building_id}"),
        ]]),
    )


def _cancel_do_sync(tg_user, building_id):
    user, _ = get_or_create_user(tg_user)
    try:
        building = Building.objects.get(id=building_id, owner=user)
    except Building.DoesNotExist:
        raise GameError("این ساختمون پیدا نشد.")
    _, refund = cancel_upgrade(user, building_id)
    building.refresh_from_db()
    return _detail_view(user, building), refund


async def building_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    building_id = int(query.data.split(":")[1])
    try:
        view, refund = await run_db(_cancel_do_sync, update.effective_user, building_id)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer(f"❌ ارتقا لغو شد — {refund:,} طلا برگشت.")
    await safe_edit_message_text(
        query,
        f"❌ <b>ارتقا لغو شد</b>\n"
        f"مبلغ برگشتی: <code>{refund:,} طلا</code>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        + _building_detail_text(view),
        parse_mode="HTML",
        reply_markup=_building_detail_keyboard(view),
    )


def _speedup_do_sync(tg_user, building_id, minutes):
    user, _ = get_or_create_user(tg_user)
    try:
        building = Building.objects.get(id=building_id, owner=user)
    except Building.DoesNotExist:
        raise GameError("این ساختمون پیدا نشد.")
    _, completed = apply_speedup(user, minutes, building_id)
    building.refresh_from_db()
    return _detail_view(user, building), completed, 1


def _speedup_all_sync(tg_user, building_id, minutes):
    from game.buildings import apply_speedup_bulk

    user, _ = get_or_create_user(tg_user)
    try:
        building = Building.objects.get(id=building_id, owner=user)
    except Building.DoesNotExist:
        raise GameError("این ساختمون پیدا نشد.")
    # a big count — bulk clamps it to what's available and to what's needed to finish
    _, completed, used = apply_speedup_bulk(user, minutes, 9999, building_id)
    building.refresh_from_db()
    return _detail_view(user, building), completed, used


def _influence_map(creatures, building) -> dict:
    """{creature_id: mine_influence} precomputed in SYNC context — the workers screens
    render in async handlers where a per-creature gear query would crash. Building-aware
    so the diamond collector's scaled-down influence shows correctly."""
    return {c.id: creature_mine_influence(c, building) for c in creatures}


def _workers_sync(tg_user, building_id):
    user, _ = get_or_create_user(tg_user)
    try:
        building = Building.objects.get(id=building_id, owner=user)
    except Building.DoesNotExist:
        raise GameError("این ساختمون پیدا نشد.")
    workers = assigned_creatures(building)
    free = free_creatures(user)
    return user, building, workers, worker_slots(building), free, _influence_map([*workers, *free], building)


def _workers_text(building: Building, workers, slots: int, free, influence: dict) -> str:
    label = constants.BUILDING_LABELS[building.building_type]
    raw = sum(influence.get(c.id, 0.0) for c in workers)
    cap = constants.BUILDING_PRODUCTION.get(building.building_type, {}).get("worker_bonus_cap")
    bonus = raw if cap is None else min(cap, raw)
    lines = [
        f"👷 <b>کارگرهای {label}</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"<blockquote>جایگاه‌های پر: <code>{len(workers)}</code> از <code>{slots}</code>\n"
        f"تولید فعلی: <code>+{bonus * 100:.0f}٪</code></blockquote>",
        "━━━━━━━━━━━━━━━━━━━━",
    ]
    if workers:
        lines.append("<b>سر کار:</b>")
        for creature in workers:
            gain = influence.get(creature.id, 0.0) * 100
            lines.append(f"⛏ <b>{creature.name}</b> (سطح <code>{creature.level}</code>): <code>+{gain:.0f}٪</code>")
        lines.append("")
    if len(workers) >= slots:
        lines.append("<i>جایگاه خالی نداری. برای جای بیشتر ساختمون رو ارتقا بده.</i>")
    elif free:
        lines.append("<b>آماده‌ی کار:</b> یکی رو انتخاب کن:")
    else:
        lines.append(
            "<i>هیچ هیولای آزادی نداری. موجود فعال و هیولاهایی که توی غار هیولا تخم گذاشتن نمی‌تونن کار کنن.</i>"
        )
    return "\n".join(lines)


_WORKER_PAGE = 8


def _workers_keyboard(building: Building, workers, slots: int, free, influence: dict, filt: str = "all", page: int = 0) -> InlineKeyboardMarkup:
    from bot.handlers.private import creature_picker_frame

    # already-stationed workers (tap to remove) sit at the top
    rows = [
        [btn(f"ترک کار: {c.name} (سطح {c.level})", emoji_key="btn_delete", style=DANGER, callback_data=f"bld_unassign:{building.id}:{c.id}")]
        for c in workers
    ]
    if len(workers) < slots and free:
        # same rarity-tab + pagination frame as the collection screen
        tab_rows, chunk, nav_rows, _tp, page, _n = creature_picker_frame(
            free, filt, page, _WORKER_PAGE,
            tab_cb=lambda f: f"bld_wpage:{building.id}:{f}:0",
            nav_cb=lambda f, p: f"bld_wpage:{building.id}:{f}:{p}",
        )
        rows += tab_rows
        for c in chunk:
            gain = influence.get(c.id, 0.0) * 100
            rows.append([btn(
                f"{c.name} (سطح {c.level}) — +{gain:.0f}٪",
                emoji_key="btn_workers",
                style=BUILD, callback_data=f"bld_assign:{building.id}:{c.id}",
            )])
        rows += nav_rows
    rows.append([back_btn(f"bld_pick:{building.id}")])
    return InlineKeyboardMarkup(rows)


async def building_workers_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    parts = query.data.split(":")
    building_id = int(parts[1])
    # bld_workers:<id>  OR  bld_wpage:<id>:<filt>:<page>
    filt = parts[2] if len(parts) > 3 else "all"
    page = int(parts[3]) if len(parts) > 3 else 0
    try:
        _user, building, workers, slots, free, influence = await run_db(
            _workers_sync, update.effective_user, building_id
        )
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer()
    await safe_edit_message_text(
        query,
        _workers_text(building, workers, slots, free, influence),
        parse_mode="HTML",
        reply_markup=_workers_keyboard(building, workers, slots, free, influence, filt, page),
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
    building.refresh_from_db()  # pick up the re-locked banked_pending for the re-render
    workers = assigned_creatures(building)
    free = free_creatures(user)
    return (
        creature,
        building,
        workers,
        worker_slots(building),
        free,
        _influence_map([*workers, *free], building),
    )


async def building_assign_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    action, building_id, creature_id = query.data.split(":")
    attach = action == "bld_assign"
    try:
        creature, building, workers, slots, free, influence = await run_db(
            _assign_sync, update.effective_user, int(building_id), int(creature_id), attach
        )
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    verb = "سر کار رفت" if attach else "برگشت"
    await query.answer(f"{creature.name} {verb} · تولیدِ جمع‌شده سر جاشه ✅")
    await safe_edit_message_text(
        query,
        _workers_text(building, workers, slots, free, influence),
        parse_mode="HTML",
        reply_markup=_workers_keyboard(building, workers, slots, free, influence),
    )


async def building_speedup_do_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, building_id, minutes = query.data.split(":")
    sync = _speedup_all_sync if query.data.startswith("bld_speedup_all:") else _speedup_do_sync
    try:
        view, completed, used = await run_db(sync, update.effective_user, int(building_id), int(minutes))
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    if completed:
        await query.answer(f"{get_emoji('trophy')} ارتقا تموم شد!")
    else:
        await query.answer(f"⚡ {used} کارت استفاده شد!" if used > 1 else f"{get_emoji('energy')} سرعت گرفت!")
    await safe_edit_message_text(
        query,
        _building_detail_text(view),
        parse_mode="HTML",
        reply_markup=_building_detail_keyboard(view),
    )


def _buy_builder_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    buy_second_builder(user)
    buildings = get_or_create_buildings(user)
    upgrades = active_upgrades(user)
    upgrading_ids = {u.building_id for u in upgrades}
    slots = builder_slots(user)
    buildings.sort(key=lambda b: (b.building_type != constants.MAIN_BUILDING, b.building_type))
    rows = [(b, pending_amount(b), is_unlocked(user, b.building_type)) for b in buildings]
    return rows, upgrading_ids, main_hall_level(user), len(upgrades), slots, user.diamonds


async def building_buy_builder_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """«خرید کارگر دوم» — confirm first (it costs diamonds), then buy on «تأیید»."""
    query = update.callback_query
    await query.answer()
    cost = constants.SECOND_BUILDER_DIAMONDS
    keyboard = InlineKeyboardMarkup([
        [btn("تأیید و خرید", emoji_key="btn_confirm", style=CONFIRM, callback_data="bld_buy_builder_do"),
         btn("انصراف", emoji_key="btn_cancel", style=DANGER, callback_data="menu:buildings")],
    ])
    await safe_edit_message_text(
        query,
        f"👷‍♂️ <b>خرید کارگر دوم</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>با خرید کارگر دوم می‌تونی هم‌زمان دو ساختمون رو ارتقا بدی و سرعت ساخت‌وسازت دو برابر شه.</i>\n\n"
        f"<blockquote>💰 هزینه: <code>{cost:,} الماس</code> (دائمی)</blockquote>\n\n"
        f"آیا برای خرید کارگر دوم اطمینان داری؟",
        parse_mode="HTML", reply_markup=keyboard,
    )


async def building_buy_builder_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        building_rows, upgrading_ids, hall_level, busy_count, slots, diamonds = await run_db(
            _buy_builder_sync, update.effective_user
        )
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer("👷‍♂️ کارگر دوم فعال شد! حالا می‌تونی هم‌زمان دو ساختمون رو ارتقا بدی.", show_alert=True)
    await safe_edit_message_text(
        query,
        _buildings_text(busy_count, slots, hall_level),
        parse_mode="HTML",
        reply_markup=_buildings_keyboard(building_rows, upgrading_ids, busy_count, slots, diamonds),
    )


def register(application) -> None:
    application.add_handler(CommandHandler("buildings", buildings_panel, filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(building_buy_builder_prompt, pattern=r"^bld_buy_builder$"))
    application.add_handler(CallbackQueryHandler(building_buy_builder_callback, pattern=r"^bld_buy_builder_do$"))
    application.add_handler(CallbackQueryHandler(building_pick_callback, pattern=r"^bld_pick:"))
    application.add_handler(CallbackQueryHandler(building_collect_callback, pattern=r"^bld_collect:"))
    application.add_handler(CallbackQueryHandler(building_upgrade_callback, pattern=r"^bld_upgrade:"))
    application.add_handler(CallbackQueryHandler(building_finish_ask_callback, pattern=r"^bld_finish_ask:"))
    application.add_handler(CallbackQueryHandler(building_finish_callback, pattern=r"^bld_finish:"))
    application.add_handler(CallbackQueryHandler(building_cancel_ask_callback, pattern=r"^bld_cancel_ask:"))
    application.add_handler(CallbackQueryHandler(building_cancel_callback, pattern=r"^bld_cancel:"))
    application.add_handler(
        CallbackQueryHandler(building_speedup_list_callback, pattern=r"^bld_speedup_list:")
    )
    application.add_handler(CallbackQueryHandler(building_speedup_do_callback, pattern=r"^bld_speedup_(do|all):"))
    application.add_handler(CallbackQueryHandler(building_workers_callback, pattern=r"^bld_(workers|wpage):"))
    application.add_handler(
        CallbackQueryHandler(building_assign_callback, pattern=r"^bld_(assign|unassign):")
    )

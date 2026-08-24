from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

from bio_lab.models import Equipment
from bio_lab.repository import get_active_creature, get_or_create_user
from bot.buttons import BUILD, CONFIRM, DANGER, LIST, NAV, back_btn, back_only_keyboard, btn
from bot.utils import run_db, safe_edit_message_text, send_screen
from game import constants
from game.blacksmith import forge, forge_preview, forgeable_items
from game.creature import GameError
from game.emoji import get_emoji
from game.equipment import (
    equip_item,
    fuse_equipment,
    list_inventory,
    same_slot_candidates,
    unequip_item,
    upgrade_item,
)


def _item_line(item: Equipment) -> str:
    status = f" · روی #{item.equipped_on_id}" if item.equipped_on_id else ""
    return (
        f"<code>#{item.id}</code> {constants.EQUIPMENT_SLOT_LABELS[item.slot]} — {item.name} "
        f"{constants.RARITY_LABELS[item.rarity]} +{item.level}{status}"
    )


PAGE_SIZE = 10

_RARITY_RANK = {r: i for i, r in enumerate(constants.RARITY_ORDER)}


def _inv_home_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    items = list_inventory(user)
    counts = {slot: 0 for slot in constants.EQUIPMENT_SLOTS}
    for i in items:
        counts[i.slot] = counts.get(i.slot, 0) + 1
    return counts


def _inv_home_render(counts: dict) -> tuple[str, InlineKeyboardMarkup]:
    text = (
        f"{get_emoji('collection')} <b>کوله‌پشتی تجهیزات</b>\n"
        "بر اساس نوع دسته‌بندی شده — یه دسته انتخاب کن (رنگ کنار هر آیتم = نایابی):"
    )
    slots = constants.EQUIPMENT_SLOTS
    rows = []
    for i in range(0, len(slots), 2):
        row = [
            btn(f"{constants.EQUIPMENT_SLOT_LABELS[s]} ({counts.get(s, 0)})", style=NAV, callback_data=f"inv_cat:{s}:0")
            for s in slots[i : i + 2]
        ]
        rows.append(row)
    rows.append([back_btn("menu:me")])
    return text, InlineKeyboardMarkup(rows)


def _inv_cat_sync(tg_user, slot):
    user, _ = get_or_create_user(tg_user)
    items = [i for i in list_inventory(user) if i.slot == slot]
    # rarity (desc) then level (desc), so the best gear is on top
    items.sort(key=lambda i: (_RARITY_RANK.get(i.rarity, 0), i.level), reverse=True)
    return items


def _inv_cat_render(slot, items: list[Equipment], page: int) -> tuple[str, InlineKeyboardMarkup]:
    label = constants.EQUIPMENT_SLOT_LABELS[slot]
    if not items:
        return (
            f"{get_emoji('collection')} <b>کوله‌پشتی — {label}</b>\n\nتوی این دسته چیزی نداری.",
            InlineKeyboardMarkup([[back_btn("menu:inventory", "بازگشت به دسته‌ها")]]),
        )
    total_pages = max(1, (len(items) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    chunk = items[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
    rows = []
    for i in chunk:
        tag = "⚔️ " if i.equipped_on_id else ""
        rows.append([btn(
            f"{tag}{constants.RARITY_LABELS[i.rarity]} {i.name} +{i.level}",
            style=LIST, callback_data=f"inv_pick:{i.id}",
        )])
    nav = []
    if page > 0:
        nav.append(btn("◀️ قبلی", style=NAV, callback_data=f"inv_cat:{slot}:{page - 1}"))
    if page < total_pages - 1:
        nav.append(btn("بعدی ▶️", style=NAV, callback_data=f"inv_cat:{slot}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([back_btn("menu:inventory", "بازگشت به دسته‌ها")])
    page_note = f"  (صفحه {page + 1}/{total_pages})" if total_pages > 1 else ""
    text = f"{get_emoji('collection')} <b>کوله‌پشتی — {label}</b>{page_note}\nرو هرکدوم بزن:"
    return text, InlineKeyboardMarkup(rows)


async def inventory_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    counts = await run_db(_inv_home_sync, update.effective_user)
    if sum(counts.values()) == 0:
        await send_screen(update,
            f"{get_emoji('lab')} کوله‌پشتی‌ات خالیه! از باکس‌های ژنتیکی (📦 باکس ژنتیکی) تجهیزات به‌دست بیار.",
            parse_mode="HTML",
            reply_markup=back_only_keyboard(),
        )
        return
    text, keyboard = _inv_home_render(counts)
    await send_screen(update, text, parse_mode="HTML", reply_markup=keyboard)


async def inventory_cat_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, slot, page = query.data.split(":")
    items = await run_db(_inv_cat_sync, update.effective_user, slot)
    await query.answer()
    text, keyboard = _inv_cat_render(slot, items, int(page))
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


def _item_detail_sync(tg_user, item_id):
    user, _ = get_or_create_user(tg_user)
    try:
        item = Equipment.objects.get(id=item_id, owner=user)
    except Equipment.DoesNotExist:
        raise GameError("این تجهیزات دیگه توی کوله‌پشتیت نیست.")
    dupes = list(
        Equipment.objects.filter(owner=user, slot=item.slot, template_key=item.template_key, rarity=item.rarity)
        .exclude(id=item.id)
    )
    return item, dupes


def _item_detail_keyboard(item: Equipment, dupe_count: int) -> InlineKeyboardMarkup:
    rows = []
    if item.equipped_on_id:
        rows.append([btn("خارج کردن از موجود", emoji_key="btn_inventory", style=DANGER, callback_data=f"inv_unequip:{item.id}")])
    else:
        rows.append([btn("تجهیز روی موجود فعال", emoji_key="btn_attack", style=BUILD, callback_data=f"inv_equip:{item.id}")])
    if item.level < constants.EQUIPMENT_MAX_LEVEL and dupe_count > 0:
        rows.append([btn("✨ ارتقا با یه نمونه‌ی مشابه", style=BUILD, callback_data=f"inv_upgrade:{item.id}")])
    rows.append([back_btn("menu:inventory", "بازگشت به کوله‌پشتی")])
    return InlineKeyboardMarkup(rows)


async def inventory_pick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    item_id = int(query.data.split(":")[1])
    try:
        item, dupes = await run_db(_item_detail_sync, update.effective_user, item_id)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer()
    await safe_edit_message_text(query,
        _item_line(item), parse_mode="HTML", reply_markup=_item_detail_keyboard(item, len(dupes))
    )


def _equip_sync(tg_user, item_id):
    user, _ = get_or_create_user(tg_user)
    creature = get_active_creature(user)
    if creature is None:
        raise GameError("اول یه موجود فعال انتخاب کن.")
    return equip_item(user, creature, item_id)


async def inventory_equip_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    item_id = int(query.data.split(":")[1])
    try:
        item = await run_db(_equip_sync, update.effective_user, item_id)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer("⚔️ تجهیز شد!")
    await safe_edit_message_text(query,
        f"⚔️ {constants.EQUIPMENT_SLOT_LABELS[item.slot]} <b>{item.name}</b> +{item.level} روی موجود فعالت تجهیز شد!\n\n"
        + _item_line(item),
        parse_mode="HTML",
        reply_markup=_item_detail_keyboard(item, 0),
    )


def _unequip_sync(tg_user, item_id):
    user, _ = get_or_create_user(tg_user)
    return unequip_item(user, item_id)


async def inventory_unequip_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    item_id = int(query.data.split(":")[1])
    try:
        item = await run_db(_unequip_sync, update.effective_user, item_id)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    _, dupes = await run_db(_item_detail_sync, update.effective_user, item_id)
    await query.answer("🎒 خارج شد.")
    await safe_edit_message_text(query,
        f"🎒 {item.name} به کوله‌پشتی برگشت.\n\n" + _item_line(item),
        parse_mode="HTML",
        reply_markup=_item_detail_keyboard(item, len(dupes)),
    )


async def inventory_upgrade_list_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    item_id = int(query.data.split(":")[1])
    try:
        item, dupes = await run_db(_item_detail_sync, update.effective_user, item_id)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    if not dupes:
        await query.answer("هیچ نمونه‌ی مشابهی نداری.", show_alert=True)
        return
    await query.answer()
    rows = [
        [btn(f"{d.name} +{d.level} (#{d.id})", style=CONFIRM, callback_data=f"inv_up_do:{item.id}:{d.id}")]
        for d in dupes
    ]
    rows.append([back_btn(f"inv_pick:{item.id}")])
    await safe_edit_message_text(query,
        f"✨ کدوم نمونه رو مصرف کنم تا <b>{item.name}</b> +{item.level} ارتقا پیدا کنه؟",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(rows),
    )


def _upgrade_item_sync(tg_user, item_id, dupe_id):
    user, _ = get_or_create_user(tg_user)
    return upgrade_item(user, item_id, dupe_id)


async def inventory_upgrade_do_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, item_id, dupe_id = query.data.split(":")
    try:
        item = await run_db(_upgrade_item_sync, update.effective_user, int(item_id), int(dupe_id))
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    _, dupes = await run_db(_item_detail_sync, update.effective_user, int(item_id))
    await query.answer("✨ ارتقا یافت!")
    await safe_edit_message_text(query,
        f"✨ {item.name} به <b>+{item.level}</b> ارتقا یافت!\n\n" + _item_line(item),
        parse_mode="HTML",
        reply_markup=_item_detail_keyboard(item, len(dupes)),
    )


async def equip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Power-user shortcut — the advertised path is /inventory's buttons."""
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(f"{get_emoji('collection')} برای تجهیز از /inventory استفاده کن.")
        return
    try:
        item = await run_db(_equip_sync, update.effective_user, int(context.args[0]))
    except GameError as exc:
        await update.message.reply_text(str(exc))
        return
    await update.message.reply_text(
        f"⚔️ {constants.EQUIPMENT_SLOT_LABELS[item.slot]} <b>{item.name}</b> +{item.level} روی موجود فعالت تجهیز شد!",
        parse_mode="HTML",
    )


async def unequip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Power-user shortcut — the advertised path is /inventory's buttons."""
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(f"{get_emoji('collection')} برای خارج کردن از /inventory استفاده کن.")
        return
    try:
        item = await run_db(_unequip_sync, update.effective_user, int(context.args[0]))
    except GameError as exc:
        await update.message.reply_text(str(exc))
        return
    await update.message.reply_text(f"🎒 {item.name} به کوله‌پشتی برگشت.", parse_mode="HTML")


async def upgrade_item_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Power-user shortcut — the advertised path is /inventory's buttons."""
    if len(context.args) != 2 or not all(a.isdigit() for a in context.args):
        await update.message.reply_text(f"{get_emoji('collection')} برای ارتقا از /inventory استفاده کن.")
        return
    try:
        item = await run_db(
            _upgrade_item_sync, update.effective_user, int(context.args[0]), int(context.args[1])
        )
    except GameError as exc:
        await update.message.reply_text(str(exc))
        return
    await update.message.reply_text(f"✨ {item.name} به <b>+{item.level}</b> ارتقا یافت!", parse_mode="HTML")


def _forge_home_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    items = forgeable_items(user)
    counts = {slot: 0 for slot in constants.EQUIPMENT_SLOTS}
    for i in items:
        counts[i.slot] = counts.get(i.slot, 0) + 1
    return user, counts


def _forge_home_render(user, counts: dict) -> tuple[str, InlineKeyboardMarkup]:
    text = (
        "⚒ <b>آهنگری</b>\n"
        f"با <b>طلا</b> سطح تجهیزات رو بالا ببر، یا با «🔗 ترکیب هم‌نوع» یه تجهیزات رو "
        "قربانیِ یکی دیگه کن (ریسک شکست داره).\n\n"
        f"{get_emoji('coin')} طلای تو: <b>{user.coins}</b>\n\n"
        "یه دسته انتخاب کن:"
    )
    # a 2×2 grid of the four equipment types, each showing how many are upgradeable
    slots = constants.EQUIPMENT_SLOTS
    rows = []
    for i in range(0, len(slots), 2):
        row = []
        for slot in slots[i : i + 2]:
            row.append(btn(
                f"{constants.EQUIPMENT_SLOT_LABELS[slot]} ({counts.get(slot, 0)})",
                style=NAV, callback_data=f"forge_cat:{slot}:0",
            ))
        rows.append(row)
    rows.append([back_btn("menu:me")])
    return text, InlineKeyboardMarkup(rows)


def _forge_cat_sync(tg_user, slot):
    user, _ = get_or_create_user(tg_user)
    items = [i for i in forgeable_items(user) if i.slot == slot]
    return user, items


def _forge_cat_render(user, slot, items, page: int) -> tuple[str, InlineKeyboardMarkup]:
    label = constants.EQUIPMENT_SLOT_LABELS[slot]
    if not items:
        return (
            f"⚒ <b>آهنگری — {label}</b>\n\nهیچ موردی برای ارتقا توی این دسته نداری.",
            InlineKeyboardMarkup([[back_btn("menu:blacksmith", "بازگشت به آهنگری")]]),
        )
    total_pages = max(1, (len(items) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    chunk = items[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
    rows = [
        [btn(
            f"{constants.RARITY_LABELS[i.rarity]} {i.name} +{i.level}",
            style=LIST, callback_data=f"forge_pick:{i.id}",
        )]
        for i in chunk
    ]
    nav = []
    if page > 0:
        nav.append(btn("◀️ قبلی", style=NAV, callback_data=f"forge_cat:{slot}:{page - 1}"))
    if page < total_pages - 1:
        nav.append(btn("بعدی ▶️", style=NAV, callback_data=f"forge_cat:{slot}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([back_btn("menu:blacksmith", "بازگشت به دسته‌ها")])
    page_note = f"  (صفحه {page + 1}/{total_pages})" if total_pages > 1 else ""
    text = f"⚒ <b>آهنگری — {label}</b>{page_note}\nکدوم رو ارتقا بدی؟ (رنگ = نایابی)"
    return text, InlineKeyboardMarkup(rows)


async def blacksmith_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user, counts = await run_db(_forge_home_sync, update.effective_user)
    text, keyboard = _forge_home_render(user, counts)
    await send_screen(update, text, parse_mode="HTML", reply_markup=keyboard)


async def forge_cat_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, slot, page = query.data.split(":")
    user, items = await run_db(_forge_cat_sync, update.effective_user, slot)
    await query.answer()
    text, keyboard = _forge_cat_render(user, slot, items, int(page))
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


def _forge_detail_sync(tg_user, item_id):
    user, _ = get_or_create_user(tg_user)
    try:
        item = Equipment.objects.get(id=item_id, owner=user)
    except Equipment.DoesNotExist:
        raise GameError("این تجهیزات پیدا نشد.")
    return user, item, forge_preview(item)


def _forge_detail_text(user, item, preview) -> str:
    fail_pct = round(preview["fail_chance"] * 100)
    risk = "بدون ریسک ✅" if fail_pct == 0 else f"شانس شکست: <b>{fail_pct}٪</b> ⚠️"
    return (
        f"⚒ <b>آهنگری</b>\n\n"
        f"{_item_line(item)}\n\n"
        f"🎯 ارتقا به <b>+{preview['target_level']}</b>\n"
        f"{get_emoji('coin')} هزینه: <b>{preview['cost']}</b> (موجودی تو: {user.coins})\n"
        f"{risk}\n\n"
        "<blockquote>در صورت شکست، طلا خرج می‌شه ولی سطح بالا نمی‌ره. "
        "راه بی‌ریسک، ارتقا با نمونه‌ی تکراری از «🎒 تجهیزات» ـه.</blockquote>"
    )


def _forge_detail_keyboard(item_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [btn("بزن! (ارتقا با طلا)", emoji_key="btn_forge", style=BUILD, callback_data=f"forge_do:{item_id}")],
            [btn("🔗 ترکیب هم‌نوع (قربانی تجهیزات)", style=NAV, callback_data=f"efuse_start:{item_id}")],
            [back_btn("menu:blacksmith", "بازگشت به آهنگری")],
        ]
    )


def _efuse_pick_sync(tg_user, target_id):
    user, _ = get_or_create_user(tg_user)
    try:
        target = Equipment.objects.get(id=target_id, owner=user)
    except Equipment.DoesNotExist:
        raise GameError("این تجهیزات پیدا نشد.")
    return user, target, same_slot_candidates(user, target_id)


def _efuse_pick_render(target, candidates) -> tuple[str, InlineKeyboardMarkup]:
    if not candidates:
        return (
            f"🔗 <b>ترکیب هم‌نوع</b>\n\n{_item_line(target)}\n\n"
            "هیچ تجهیزات هم‌نوع دیگه‌ای برای قربانی کردن نداری.",
            InlineKeyboardMarkup([[back_btn(f"forge_pick:{target.id}", "بازگشت")]]),
        )
    rows = [
        [btn(
            f"{constants.RARITY_LABELS[c.rarity]} {c.name} +{c.level}"
            + (" · درحال‌استفاده" if c.equipped_on_id else ""),
            style=LIST, callback_data=f"efuse_pick:{target.id}:{c.id}",
        )]
        for c in candidates[:PAGE_SIZE]
    ]
    rows.append([back_btn(f"forge_pick:{target.id}", "بازگشت")])
    text = (
        f"🔗 <b>ترکیب هم‌نوع</b>\n\n"
        f"هدف: {_item_line(target)}\n\n"
        "کدوم تجهیزات رو قربانی کنی؟ (هم‌نوع = همون اسلات). "
        "<i>قربانی هرچی نایاب‌تر و بالاتر باشه، شانس موفقیت بیشتره.</i>"
    )
    return text, InlineKeyboardMarkup(rows)


async def efuse_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    target_id = int(query.data.split(":")[1])
    try:
        user, target, candidates = await run_db(_efuse_pick_sync, update.effective_user, target_id)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer()
    text, keyboard = _efuse_pick_render(target, candidates)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


def _efuse_confirm_sync(tg_user, target_id, sac_id):
    user, _ = get_or_create_user(tg_user)
    try:
        target = Equipment.objects.get(id=target_id, owner=user)
        sac = Equipment.objects.get(id=sac_id, owner=user)
    except Equipment.DoesNotExist:
        raise GameError("این تجهیزات پیدا نشد.")
    from game.equipment import _fuse_fail_chance

    return target, sac, _fuse_fail_chance(target, sac)


async def efuse_pick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, target_id, sac_id = query.data.split(":")
    try:
        target, sac, fail = await run_db(_efuse_confirm_sync, update.effective_user, int(target_id), int(sac_id))
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer()
    text = (
        f"🔗 <b>تأیید ترکیب</b>\n\n"
        f"هدف: {_item_line(target)}\n"
        f"قربانی: {_item_line(sac)}\n\n"
        f"🎯 در صورت موفقیت: <b>+{target.level + 1}</b>\n"
        f"⚠️ شانس شکست: <b>{round(fail * 100)}٪</b> (در هر صورت قربانی از بین می‌ره)"
    )
    keyboard = InlineKeyboardMarkup([
        [btn("🔗 ترکیب کن!", style=CONFIRM, callback_data=f"efuse_do:{target.id}:{sac.id}")],
        [back_btn(f"efuse_start:{target.id}", "بازگشت")],
    ])
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


def _efuse_do_sync(tg_user, target_id, sac_id):
    user, _ = get_or_create_user(tg_user)
    return fuse_equipment(user, target_id, sac_id)


async def efuse_do_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, target_id, sac_id = query.data.split(":")
    try:
        result = await run_db(_efuse_do_sync, update.effective_user, int(target_id), int(sac_id))
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    target = result["target"]
    if result["success"]:
        await query.answer("🎉 موفق شد!")
        head = f"🎉 <b>ترکیب موفق!</b> تجهیزات به <b>+{result['new_level']}</b> رسید."
    else:
        await query.answer("💥 شکست خورد!")
        head = "💥 <b>ترکیب شکست خورد.</b> قربانی از بین رفت و سطح بالا نرفت — دفعه‌ی بعد قربانی بهتری بذار."
    await safe_edit_message_text(
        query,
        f"{head}\n\n{_item_line(target)}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [btn("🔗 ترکیب دوباره", style=NAV, callback_data=f"efuse_start:{target.id}")],
            [back_btn("menu:blacksmith", "بازگشت به آهنگری")],
        ]),
    )


async def forge_pick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    item_id = int(query.data.split(":")[1])
    try:
        user, item, preview = await run_db(_forge_detail_sync, update.effective_user, item_id)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer()
    await safe_edit_message_text(
        query,
        _forge_detail_text(user, item, preview),
        parse_mode="HTML",
        reply_markup=_forge_detail_keyboard(item_id),
    )


def _forge_do_sync(tg_user, item_id):
    user, _ = get_or_create_user(tg_user)
    result = forge(user, item_id)
    user.refresh_from_db()
    return user, result, forge_preview(result["item"])


async def forge_do_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    item_id = int(query.data.split(":")[1])
    try:
        user, result, preview = await run_db(_forge_do_sync, update.effective_user, item_id)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return

    item = result["item"]
    if result["success"]:
        header = f"✨ <b>موفق!</b> {item.name} رسید به <b>+{item.level}</b>"
        await query.answer("✨ موفق!")
    else:
        header = f"💥 <b>شکست خورد!</b> {result['cost']} طلا سوخت و سطح تغییر نکرد."
        await query.answer("💥 شکست خورد.")

    await safe_edit_message_text(
        query,
        f"{header}\n\n" + _forge_detail_text(user, item, preview),
        parse_mode="HTML",
        reply_markup=_forge_detail_keyboard(item.id),
    )


def register(application) -> None:
    application.add_handler(CommandHandler("inventory", inventory_cmd, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("blacksmith", blacksmith_panel, filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(forge_pick_callback, pattern=r"^forge_pick:"))
    application.add_handler(CallbackQueryHandler(forge_cat_callback, pattern=r"^forge_cat:"))
    application.add_handler(CallbackQueryHandler(forge_do_callback, pattern=r"^forge_do:"))
    application.add_handler(CallbackQueryHandler(efuse_start_callback, pattern=r"^efuse_start:\d+$"))
    application.add_handler(CallbackQueryHandler(efuse_pick_callback, pattern=r"^efuse_pick:\d+:\d+$"))
    application.add_handler(CallbackQueryHandler(efuse_do_callback, pattern=r"^efuse_do:\d+:\d+$"))
    application.add_handler(CommandHandler("equip", equip_cmd, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("unequip", unequip_cmd, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("upgrade_item", upgrade_item_cmd, filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(inventory_pick_callback, pattern=r"^inv_pick:"))
    application.add_handler(CallbackQueryHandler(inventory_cat_callback, pattern=r"^inv_cat:"))
    application.add_handler(CallbackQueryHandler(inventory_equip_callback, pattern=r"^inv_equip:"))
    application.add_handler(CallbackQueryHandler(inventory_unequip_callback, pattern=r"^inv_unequip:"))
    application.add_handler(CallbackQueryHandler(inventory_upgrade_do_callback, pattern=r"^inv_up_do:"))
    application.add_handler(CallbackQueryHandler(inventory_upgrade_list_callback, pattern=r"^inv_upgrade:"))

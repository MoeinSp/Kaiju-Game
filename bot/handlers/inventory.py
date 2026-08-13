from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

from bio_lab.models import Equipment
from bio_lab.repository import get_active_creature, get_or_create_user
from bot.utils import run_db, safe_edit_message_text
from game import constants
from game.creature import GameError
from game.emoji import get_emoji
from game.equipment import equip_item, list_inventory, unequip_item, upgrade_item


def _item_line(item: Equipment) -> str:
    status = f" · روی #{item.equipped_on_id}" if item.equipped_on_id else ""
    return (
        f"<code>#{item.id}</code> {constants.EQUIPMENT_SLOT_LABELS[item.slot]} — {item.name} "
        f"{constants.RARITY_LABELS[item.rarity]} +{item.level}{status}"
    )


def _inventory_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    return list_inventory(user)


def _inventory_keyboard(items: list[Equipment]) -> InlineKeyboardMarkup:
    rows = []
    for i in items:
        tag = "⚔️ " if i.equipped_on_id else ""
        rows.append(
            [
                InlineKeyboardButton(
                    f"{tag}{constants.EQUIPMENT_SLOT_LABELS[i.slot]} {i.name} +{i.level}",
                    callback_data=f"inv_pick:{i.id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton("◀️ بازگشت", callback_data="menu:me")])
    return InlineKeyboardMarkup(rows)


async def inventory_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    items = await run_db(_inventory_sync, update.effective_user)
    if not items:
        await update.effective_message.reply_text(
            f"{get_emoji('lab')} کوله‌پشتی‌ات خالیه! از باکس‌های ژنتیکی (📦 باکس ژنتیکی) تجهیزات به‌دست بیار.",
            parse_mode="HTML",
        )
        return
    await update.effective_message.reply_text(
        f"{get_emoji('collection')} <b>کوله‌پشتی تجهیزات</b> — {len(items)} قطعه\nرو هرکدوم بزن:",
        parse_mode="HTML",
        reply_markup=_inventory_keyboard(items),
    )


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
        rows.append([InlineKeyboardButton("🎒 خارج کردن از موجود", callback_data=f"inv_unequip:{item.id}")])
    else:
        rows.append([InlineKeyboardButton("⚔️ تجهیز روی موجود فعال", callback_data=f"inv_equip:{item.id}")])
    if item.level < constants.EQUIPMENT_MAX_LEVEL and dupe_count > 0:
        rows.append([InlineKeyboardButton("✨ ارتقا با یه نمونه‌ی مشابه", callback_data=f"inv_upgrade:{item.id}")])
    rows.append([InlineKeyboardButton("◀️ بازگشت به کوله‌پشتی", callback_data="menu:inventory")])
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
        [InlineKeyboardButton(f"{d.name} +{d.level} (#{d.id})", callback_data=f"inv_up_do:{item.id}:{d.id}")]
        for d in dupes
    ]
    rows.append([InlineKeyboardButton("◀️ بازگشت", callback_data=f"inv_pick:{item.id}")])
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


def register(application) -> None:
    application.add_handler(CommandHandler("inventory", inventory_cmd, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("equip", equip_cmd, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("unequip", unequip_cmd, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("upgrade_item", upgrade_item_cmd, filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(inventory_pick_callback, pattern=r"^inv_pick:"))
    application.add_handler(CallbackQueryHandler(inventory_equip_callback, pattern=r"^inv_equip:"))
    application.add_handler(CallbackQueryHandler(inventory_unequip_callback, pattern=r"^inv_unequip:"))
    application.add_handler(CallbackQueryHandler(inventory_upgrade_do_callback, pattern=r"^inv_up_do:"))
    application.add_handler(CallbackQueryHandler(inventory_upgrade_list_callback, pattern=r"^inv_upgrade:"))

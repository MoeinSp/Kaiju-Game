from telegram import Update
from telegram.ext import CommandHandler, ContextTypes, filters

from bio_lab.models import Equipment
from bio_lab.repository import get_active_creature, get_or_create_user
from bot.utils import run_db
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


async def inventory_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    items = await run_db(_inventory_sync, update.effective_user)
    if not items:
        await update.effective_message.reply_text(
            f"{get_emoji('lab')} کوله‌پشتی‌ات خالیه! از باکس‌های ژنتیکی (/biocrate) تجهیزات به‌دست بیار.",
            parse_mode="HTML",
        )
        return
    lines = [f"{get_emoji('collection')} <b>کوله‌پشتی تجهیزات</b> — {len(items)} قطعه\n"]
    lines.extend(_item_line(i) for i in items)
    lines.append(
        "\n<code>/equip شماره</code> تجهیز روی موجود فعال\n"
        "<code>/unequip شماره</code> خارج کردن از موجود\n"
        "<code>/upgrade_item شماره شماره_تکراری</code> ارتقا با یه نمونه‌ی هم‌نوع"
    )
    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")


def _equip_sync(tg_user, item_id):
    user, _ = get_or_create_user(tg_user)
    creature = get_active_creature(user)
    if creature is None:
        raise GameError("اول /start رو بزن تا موجودت رو بگیری.")
    return equip_item(user, creature, item_id)


async def equip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(
            "استفاده درست: <code>/equip 5</code> (شماره از /inventory)", parse_mode="HTML"
        )
        return
    try:
        item = await run_db(_equip_sync, update.effective_user, int(context.args[0]))
    except GameError as exc:
        await update.message.reply_text(str(exc))
        return
    await update.message.reply_text(
        f"✅ {constants.EQUIPMENT_SLOT_LABELS[item.slot]} <b>{item.name}</b> +{item.level} روی موجود فعالت تجهیز شد!",
        parse_mode="HTML",
    )


def _unequip_sync(tg_user, item_id):
    user, _ = get_or_create_user(tg_user)
    return unequip_item(user, item_id)


async def unequip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("استفاده درست: <code>/unequip 5</code>", parse_mode="HTML")
        return
    try:
        item = await run_db(_unequip_sync, update.effective_user, int(context.args[0]))
    except GameError as exc:
        await update.message.reply_text(str(exc))
        return
    await update.message.reply_text(f"🎒 {item.name} به کوله‌پشتی برگشت.", parse_mode="HTML")


def _upgrade_item_sync(tg_user, item_id, dupe_id):
    user, _ = get_or_create_user(tg_user)
    return upgrade_item(user, item_id, dupe_id)


async def upgrade_item_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) != 2 or not all(a.isdigit() for a in context.args):
        await update.message.reply_text(
            "استفاده درست: <code>/upgrade_item 5 9</code> (شماره تجهیزات + شماره‌ی نمونه‌ی تکراری هم‌نوع)",
            parse_mode="HTML",
        )
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

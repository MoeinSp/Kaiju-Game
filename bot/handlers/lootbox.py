from telegram import Update
from telegram.ext import CommandHandler, ContextTypes, filters

from bio_lab.repository import get_or_create_user
from bot.utils import run_db
from game import constants
from game.creature import GameError
from game.emoji import get_emoji
from game.lootbox import open_biocrate


def _biocrate_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    return open_biocrate(user)


async def biocrate_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        result = await run_db(_biocrate_sync, update.effective_user)
    except GameError as exc:
        await update.effective_message.reply_text(str(exc))
        return

    rarity_label = constants.RARITY_LABELS[result["rarity"]]
    if result["kind"] == "creature":
        creature = result["creature"]
        body = (
            f"{get_emoji('egg')} یه موجود تازه از کپسول بیرون اومد: <b>{creature.name}</b>\n"
            f"{constants.element_label(creature.element)} · {rarity_label}\n"
            "با <code>/select</code> می‌تونی فعالش کنی."
        )
    else:
        item = result["item"]
        body = (
            f"{constants.EQUIPMENT_SLOT_LABELS[item.slot]} یه قطعه تجهیزات تازه به‌دست اومد: <b>{item.name}</b>\n"
            f"{rarity_label}\n"
            "با <code>/inventory</code> ببینش و <code>/equip</code> کن."
        )

    await update.effective_message.reply_text(
        f"{get_emoji('biocrate')} <b>باکس ژنتیکی باز شد!</b>\n\n{body}", parse_mode="HTML"
    )


def register(application) -> None:
    application.add_handler(CommandHandler("biocrate", biocrate_cmd, filters.ChatType.PRIVATE))

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

from bio_lab.repository import get_or_create_user
from bot.buttons import DANGER, PRIMARY, SUCCESS, back_btn, btn
from bot.utils import run_db, safe_edit_message_text
from game import constants
from game.creature import GameError
from game.emoji import get_emoji
from game.lootbox import open_biocrate, open_diamond_box


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
        reveal = f"{get_emoji('egg')} <b>{creature.name}</b>\n{constants.element_label(creature.element)} · {rarity_label}"
        hint = "از «🗂 کلکسیون» توی منو می‌تونی فعالش کنی."
    else:
        item = result["item"]
        reveal = f"{constants.EQUIPMENT_SLOT_LABELS[item.slot]} <b>{item.name}</b>\n{rarity_label}"
        hint = "از «🎒 تجهیزات» توی منو می‌تونی تجهیزش کنی."

    await update.effective_message.reply_text(
        f"{get_emoji('biocrate')} <b>باکس ژنتیکی باز شد!</b>\n\n"
        f"<tg-spoiler>{reveal}</tg-spoiler>\n\n"
        f"<blockquote>{hint}</blockquote>",
        parse_mode="HTML",
    )


def _diamond_box_list_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [btn(f"{cfg['label']} — {cfg['cost_diamonds']} 💎", style=PRIMARY, callback_data=f"dbox_pick:{tier}")]
        for tier, cfg in constants.DIAMOND_BOX_TIERS.items()
    ]
    rows.append([back_btn("menu:me")])
    return InlineKeyboardMarkup(rows)


async def diamond_box_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        f"{get_emoji('diamond_box')} <b>جعبه‌های الماسی</b>\n"
        "این جعبه‌ها همیشه یه موجود جدید می‌دن (نه تجهیزات) — هرچی سطح جعبه بالاتر، شانس نایاب‌بودنش بیشتره.\n\n"
        "رو یکی بزن تا احتمالات دقیقش رو ببینی:",
        parse_mode="HTML",
        reply_markup=_diamond_box_list_keyboard(),
    )


def _diamond_box_detail_text(tier: str) -> str:
    cfg = constants.DIAMOND_BOX_TIERS[tier]
    lines = [
        f"{cfg['label']}",
        f"{get_emoji('diamond')} هزینه: {cfg['cost_diamonds']} الماس\n",
        "📊 <b>احتمال هر رده:</b>",
    ]
    for rarity, weight in cfg["weights"].items():
        lines.append(f"{constants.RARITY_LABELS[rarity]} — {weight:g}٪")
    return "\n".join(lines)


def _diamond_box_detail_keyboard(tier: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [btn("خرید و باز کن", emoji_key="btn_confirm", style=SUCCESS, callback_data=f"dbox_buy:{tier}")],
            [back_btn("menu:diamond_box", "بازگشت به لیست")],
        ]
    )


async def diamond_box_pick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    tier = query.data.split(":")[1]
    if tier not in constants.DIAMOND_BOX_TIERS:
        await query.answer("این جعبه دیگه پیدا نشد.", show_alert=True)
        return
    await query.answer()
    await safe_edit_message_text(
        query, _diamond_box_detail_text(tier), parse_mode="HTML", reply_markup=_diamond_box_detail_keyboard(tier)
    )


def _diamond_box_buy_sync(tg_user, tier):
    user, _ = get_or_create_user(tg_user)
    return open_diamond_box(user, tier)


async def diamond_box_buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    tier = query.data.split(":")[1]
    try:
        result = await run_db(_diamond_box_buy_sync, update.effective_user, tier)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return

    creature = result["creature"]
    rarity_label = constants.RARITY_LABELS[result["rarity"]]
    await query.answer("🟢 باز شد!")
    keyboard = InlineKeyboardMarkup(
        [
            [btn("یکی دیگه باز کن", emoji_key="btn_diamond_box", style=SUCCESS, callback_data=f"dbox_pick:{tier}")],
            [back_btn("menu:diamond_box")],
        ]
    )
    await safe_edit_message_text(
        query,
        f"{constants.DIAMOND_BOX_TIERS[tier]['label']} <b>باز شد!</b>\n\n"
        f"<tg-spoiler>{get_emoji('egg')} <b>{creature.name}</b>\n"
        f"{constants.element_label(creature.element)} · {rarity_label}</tg-spoiler>\n\n"
        "<blockquote>از «🗂 کلکسیون» توی منو می‌تونی فعالش کنی.</blockquote>",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


def register(application) -> None:
    application.add_handler(CommandHandler("biocrate", biocrate_cmd, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("diamondbox", diamond_box_panel, filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(diamond_box_pick_callback, pattern=r"^dbox_pick:"))
    application.add_handler(CallbackQueryHandler(diamond_box_buy_callback, pattern=r"^dbox_buy:"))

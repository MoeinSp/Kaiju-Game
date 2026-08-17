from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

from bio_lab.repository import get_or_create_user
from bot.buttons import CONFIRM, DANGER, PRIMARY, SHOP, back_btn, back_only_keyboard, btn
from bot.utils import run_db, safe_edit_message_text, send_screen
from game import constants
from game.creature import GameError
from game.emoji import get_emoji
from game.lootbox import open_biocrate, open_diamond_box


def _biocrate_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    return open_biocrate(user)


def _user_coins_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    return user.coins


async def biocrate_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cost + confirmation screen — opening the crate spends gold, so it must never
    fire on a single tap without the player agreeing to the price first."""
    coins = await run_db(_user_coins_sync, update.effective_user)
    cost = constants.BIOCRATE_GOLD_COST
    text = (
        f"{get_emoji('biocrate')} <b>باکس ژنتیکی</b>\n"
        "<blockquote>یه باکس شانسی: بیشتر مواقع (۹۰٪) یه تجهیزات می‌ده و گاهی (۱۰٪) یه هیولای تازه. "
        "درجه‌ش هم شانسیه — هرچی نایاب‌تر، کمیاب‌تر. برای شکار هیولای نایاب، جعبه‌های الماسی بهترن.</blockquote>\n\n"
        f"{get_emoji('coin')} هزینه: <b>{cost}</b> طلا  (موجودی: {coins})"
    )
    rows = [
        [btn("خرید و باز کن", emoji_key="btn_confirm", style=CONFIRM, callback_data="bc_buy")],
        [back_btn("menu:me")],
    ]
    await send_screen(update, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))


async def biocrate_buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        result = await run_db(_biocrate_sync, update.effective_user)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
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

    await query.answer("🟢 باز شد!")
    keyboard = InlineKeyboardMarkup(
        [
            [btn("یکی دیگه باز کن", emoji_key="btn_biocrate", style=SHOP, callback_data="menu:biocrate")],
            [back_btn("menu:me")],
        ]
    )
    await safe_edit_message_text(
        query,
        f"{get_emoji('biocrate')} <b>باکس ژنتیکی باز شد!</b>\n\n"
        f"<tg-spoiler>{reveal}</tg-spoiler>\n\n"
        f"<blockquote>{hint}</blockquote>",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


def _diamond_box_list_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [btn(f"{cfg['label']} — {cfg['cost_diamonds']} 💎", style=SHOP, callback_data=f"dbox_pick:{tier}")]
        for tier, cfg in constants.DIAMOND_BOX_TIERS.items()
    ]
    rows.append([back_btn("menu:me")])
    return InlineKeyboardMarkup(rows)


async def diamond_box_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await send_screen(update, 
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
            [btn("خرید و باز کن", emoji_key="btn_confirm", style=CONFIRM, callback_data=f"dbox_buy:{tier}")],
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
            [btn("یکی دیگه باز کن", emoji_key="btn_diamond_box", style=SHOP, callback_data=f"dbox_pick:{tier}")],
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
    application.add_handler(CallbackQueryHandler(biocrate_buy_callback, pattern=r"^bc_buy$"))
    application.add_handler(CommandHandler("diamondbox", diamond_box_panel, filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(diamond_box_pick_callback, pattern=r"^dbox_pick:"))
    application.add_handler(CallbackQueryHandler(diamond_box_buy_callback, pattern=r"^dbox_buy:"))

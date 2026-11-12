from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

from bot.utils import alert_text
from bio_lab.repository import get_or_create_user
from bot.buttons import CONFIRM, SHOP, back_btn, btn
from bot.utils import run_db, safe_edit_message_text, send_screen
from game import constants
from game.creature import GameError
from game.emoji import get_emoji
from game.lootbox import (
    BULK_OPEN,
    BULK_PAY,
    open_biocrate,
    open_biocrate_bulk,
    open_diamond_box,
    open_diamond_box_bulk,
)
from game.media import (
    composite_lootbox_batch_image,
    get_creature_image_path,
    get_equipment_image_path,
)


def _bulk_summary_text(header: str, summary: dict, pay: bool = False) -> str:
    """A ×10 / ×11 batch (bot.loot_text.boxes_text) — kept as the name the group cards import."""
    from bot.loot_text import boxes_text

    return boxes_text(header, summary, pay=pay)


def _biocrate_buy_sync(tg_user, tier):
    user, _ = get_or_create_user(tg_user)
    return open_biocrate(user, tier)


def _biocrate_bulk_sync(tg_user, tier):
    user, _ = get_or_create_user(tg_user)
    return open_biocrate_bulk(user, tier)


def _user_coins_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    return user.coins, user.dna_fragments, user.biocrate_tickets


def _biocrate_list_keyboard(tickets: int = 0) -> InlineKeyboardMarkup:
    rows = []
    for tier in constants.BIOCRATE_TIER_ORDER:
        cfg = constants.BIOCRATE_TIERS[tier]
        rows.append([btn(
            cfg['label'],
            style=SHOP, callback_data=f"bc_pick:{tier}",
        )])
    rows.append([back_btn("menu:hub_shop", "بازگشت به فروشگاه")])
    return InlineKeyboardMarkup(rows)


async def biocrate_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    coins, dna, tickets = await run_db(_user_coins_sync, update.effective_user)
    ticket_line = f"\n🎟 موجودی بلیط: <code>{tickets:,}</code>" if tickets else ""
    lines = [
        f"{get_emoji('biocrate')} <b>باکس ژنتیکی</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "<blockquote>یه باکس شانسی حاوی تجهیزات و هیولاهای کمیاب.\n"
        "هرچه سطح باکس بالاتر باشد، شانس دریافت هیولا و نایابی آن بیشتر خواهد بود.</blockquote>",
        "",
        f"💰 موجودی طلا: <code>{coins:,}</code>",
        f"🧬 موجودی DNA: <code>{dna:,}</code>{ticket_line}",
        "",
        "<i>یکی از باکس‌های زیر را انتخاب کنید:</i>",
    ]
    text = "\n".join(lines)
    from game.media import get_feature_image_path
    photo = get_feature_image_path("biocrate")
    await send_screen(update, text, photo=photo, parse_mode="HTML", reply_markup=_biocrate_list_keyboard(tickets))


def _biocrate_open_sync(tg_user, tier, count):
    from game.lootbox import open_biocrate_batch

    user, _ = get_or_create_user(tg_user)
    return open_biocrate_batch(user, tier, count)


def _user_tickets_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    return user.biocrate_tickets


def _biocrate_detail_text(tier: str) -> str:
    cfg = constants.BIOCRATE_TIERS[tier]
    cc = cfg["creature_chance"]
    weights = cfg["weights"]
    total = sum(weights.values())
    lines = [
        f"{cfg['label']}",
        "━━━━━━━━━━━━━━━━━━━━",
        f"<blockquote>💰 هزینه طلا: <code>{cfg['gold']:,}</code> {get_emoji('coin')}\n"
        f"🧬 هزینه DNA: <code>{cfg['dna']:,}</code> {get_emoji('dna')}</blockquote>",
        "",
        f"🎒 <b>تجهیزات</b> (مجموعاً <code>{(1 - cc) * 100:g}٪</code>):",
    ]
    # equipment rarity breakdown (this tier's own gear table)
    ew = cfg.get("equip_weights", constants.LOOTBOX_RARITY_WEIGHTS)
    et = sum(ew.values())
    for rarity, weight in ew.items():
        pct = (1 - cc) * weight / et * 100
        lines.append(f"  • {constants.RARITY_LABELS[rarity]}: <code>{pct:.2g}٪</code>")
    lines.append("")
    lines.append(f"🧬 <b>هیولا</b> (مجموعاً <code>{cc * 100:g}٪</code>):")
    for rarity, weight in weights.items():
        pct = cc * weight / total * 100
        lines.append(f"  • {constants.RARITY_LABELS[rarity]}: <code>{pct:.2g}٪</code>")
    return "\n".join(lines)


def _biocrate_detail_keyboard(tier: str, tickets: int = 0) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            btn("باز کردن (۱×)", emoji_key="btn_confirm", style=CONFIRM, callback_data=f"bc_open:{tier}:1"),
            btn("باز کردن (۱۰×)", emoji_key="btn_biocrate", style=SHOP, callback_data=f"bc_open:{tier}:10"),
        ],
        [back_btn("menu:biocrate", "بازگشت به لیست")],
    ])


def _biocrate_ticket_note(tier: str, tickets: int) -> str:
    if tickets <= 0:
        return ""
    return (
        f"\n\n<blockquote>🎟 موجودی بلیط: <code>{tickets:,}</code>\n"
        "در ابتدا بلیط‌ها مصرف می‌شوند و برای باقیمانده طلا و DNA کسر خواهد شد.</blockquote>"
    )


async def biocrate_pick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    tier = query.data.split(":")[1]
    if tier not in constants.BIOCRATE_TIERS:
        await query.answer("این باکس پیدا نشد.", show_alert=True)
        return
    tickets = await run_db(_user_tickets_sync, update.effective_user)
    await query.answer()
    await safe_edit_message_text(
        query,
        _biocrate_detail_text(tier) + _biocrate_ticket_note(tier, tickets),
        parse_mode="HTML", reply_markup=_biocrate_detail_keyboard(tier, tickets),
    )


async def biocrate_open_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, tier, count = query.data.split(":")
    count = int(count)
    try:
        summary = await run_db(_biocrate_open_sync, update.effective_user, tier, count)
    except GameError as exc:
        from bot.handlers.shop import show_gold_error

        if await show_gold_error(query, exc):
            return
        await query.answer(alert_text(exc), show_alert=True)
        return

    label = constants.BIOCRATE_TIERS[tier]["label"]
    tickets_left = summary.get("tickets_left", 0)
    keyboard = _biocrate_detail_keyboard(tier, tickets_left)
    if count == 1:
        from bot.loot_text import box_reveal

        result = summary["single"]
        photo = (get_creature_image_path(result["creature"]) if result["kind"] == "creature"
                 else get_equipment_image_path(result["item"]))
        await query.answer("🎟 باز شد!" if summary.get("from_tickets") else "📦 باز شد!")
        text = box_reveal(label, result, pay=summary)
    else:
        await query.answer("🎉 باز شد!")
        text = _bulk_summary_text(label, summary, pay=True)
        photo = composite_lootbox_batch_image(summary["rolls"], label)
    await send_screen(update, text, photo=photo, parse_mode="HTML", reply_markup=keyboard)


async def biocrate_bulk_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    tier = query.data.split(":")[1]
    try:
        summary = await run_db(_biocrate_bulk_sync, update.effective_user, tier)
    except GameError as exc:
        from bot.handlers.shop import show_gold_error

        if await show_gold_error(query, exc):
            return
        await query.answer(alert_text(exc), show_alert=True)
        return
    await query.answer("🎉 باز شد!")
    label = constants.BIOCRATE_TIERS[tier]["label"]
    keyboard = InlineKeyboardMarkup([
        [btn(f"باز کردن ×{BULK_PAY} دیگر", emoji_key="btn_biocrate", style=SHOP, callback_data=f"bc_bulk:{tier}")],
        [back_btn("menu:biocrate", "لیست باکس‌ها")],
    ])
    photo = composite_lootbox_batch_image(summary["rolls"], label)
    await send_screen(
        update, _bulk_summary_text(label, summary),
        photo=photo, parse_mode="HTML", reply_markup=keyboard,
    )


def _can_claim_free_diamond_sync(tg_user, tier: str) -> bool:
    from game.lootbox import can_claim_free_diamond_box

    user, _ = get_or_create_user(tg_user)
    return can_claim_free_diamond_box(user, tier)


def _pity_left_sync(tg_user, tier: str) -> int:
    from game.lootbox import pity_left

    user, _ = get_or_create_user(tg_user)
    return pity_left(user, tier)


def _free_diamond_boxes_sync(tg_user) -> set[str]:
    from game.lootbox import can_claim_free_diamond_box

    user, _ = get_or_create_user(tg_user)
    free = set()
    for t in ("bronze", "silver"):
        if can_claim_free_diamond_box(user, t):
            free.add(t)
    return free


def _diamond_box_list_keyboard(free_tiers: set[str] | None = None) -> InlineKeyboardMarkup:
    free_tiers = free_tiers or set()
    rows = []
    for tier, cfg in constants.DIAMOND_BOX_TIERS.items():
        if tier in free_tiers:
            label = f"{cfg['label']} (رایگان)"
        else:
            label = f"{cfg['label']}"
        rows.append([btn(label, style=SHOP, callback_data=f"dbox_pick:{tier}")])
    rows.append([back_btn("menu:hub_shop", "بازگشت به فروشگاه")])
    return InlineKeyboardMarkup(rows)


async def diamond_box_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from game.media import get_feature_image_path

    photo = get_feature_image_path("diamond_box")
    free_tiers = await run_db(_free_diamond_boxes_sync, update.effective_user)
    free_banner = ""
    if free_tiers:
        names = []
        if "bronze" in free_tiers:
            names.append("برنزی")
        if "silver" in free_tiers:
            names.append("نقره‌ای")
        free_banner = f"🎁 <b>باکس هیولا رایگان امروز ({' و '.join(names)}) آماده باز کردن!</b>\n\n"

    lines = [
        f"{get_emoji('diamond_box')} <b>باکس هیولا</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "<blockquote>این باکس‌ها همیشه یک موجود جدید به شما می‌دهند.\n"
        "هرچه سطح باکس بالاتر باشد، شانس نایاب‌بودن آن بیشتر است.</blockquote>",
        "",
        f"{free_banner}<i>یکی از باکس‌های زیر را برای مشاهده جزئیات انتخاب کنید:</i>",
    ]
    await send_screen(
        update,
        "\n".join(lines),
        photo=photo,
        parse_mode="HTML",
        reply_markup=_diamond_box_list_keyboard(free_tiers),
    )


def _diamond_box_detail_text(tier: str, is_free: bool = False, pity_left: int | None = None) -> str:
    cfg = constants.DIAMOND_BOX_TIERS[tier]
    if tier in ("bronze", "silver"):
        if is_free:
            cost_line = f"<blockquote>{get_emoji('diamond')} هزینه: <b>رایگان! 🎁</b> (۱ بار در روز — آماده باز کردن)</blockquote>"
        else:
            cost_line = f"<blockquote>{get_emoji('diamond')} هزینه: <code>{cfg['cost_diamonds']:,}</code> الماس <i>(رایگان امروز مصرف شده)</i></blockquote>"
    else:
        cost_line = f"<blockquote>{get_emoji('diamond')} هزینه: <code>{cfg['cost_diamonds']:,}</code> الماس</blockquote>"

    lines = [
        f"{cfg['label']}",
        "━━━━━━━━━━━━━━━━━━━━",
        cost_line,
        "",
        f"{get_emoji('stats')} <b>احتمال هر رده:</b>",
    ]
    for rarity, weight in cfg["weights"].items():
        lines.append(f"  • {constants.RARITY_LABELS[rarity]}: <code>{weight:g}٪</code>")
    if cfg.get("pity"):
        target = constants.RARITY_LABELS[cfg["pity"]]
        lines += ["", f"🎯 <b>تضمین:</b> از هر <code>{constants.DIAMOND_BOX_PITY}</code> باکس حداقل یکی {target} یا بهتره."]
        if pity_left is not None:
            lines.append("✨ باکس بعدی حتماً " + target + " یا بهتره!" if pity_left <= 1
                         else f"⏳ حداکثر <code>{pity_left}</code> باکس دیگه تا {target} تضمینی.")
    return "\n".join(lines)


def _diamond_box_detail_keyboard(tier: str, is_free: bool = False) -> InlineKeyboardMarkup:
    if tier in ("bronze", "silver") and is_free:
        open_label = "باز کردن رایگان"
    else:
        open_label = "خرید و باز کردن"
    return InlineKeyboardMarkup(
        [
            [
                btn(open_label, emoji_key="btn_confirm", style=CONFIRM, callback_data=f"dbox_buy:{tier}"),
                btn("بسته‌ی 10+1", emoji_key="btn_diamond_box", style=SHOP, callback_data=f"dbox_bulk:{tier}"),
            ],
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
    is_free = False
    if tier in ("bronze", "silver"):
        is_free = await run_db(_can_claim_free_diamond_sync, update.effective_user, tier)
    left = await run_db(_pity_left_sync, update.effective_user, tier)
    await safe_edit_message_text(
        query, _diamond_box_detail_text(tier, is_free, left), parse_mode="HTML", reply_markup=_diamond_box_detail_keyboard(tier, is_free)
    )


def _diamond_box_buy_sync(tg_user, tier, require_free=False):
    user, _ = get_or_create_user(tg_user)
    return open_diamond_box(user, tier, require_free=require_free)


def _diamond_box_bulk_sync(tg_user, tier):
    user, _ = get_or_create_user(tg_user)
    return open_diamond_box_bulk(user, tier)


async def _do_diamond_box_buy(update: Update, context: ContextTypes.DEFAULT_TYPE, tier: str, is_free: bool = False) -> None:
    query = update.callback_query
    try:
        result = await run_db(_diamond_box_buy_sync, update.effective_user, tier, is_free)
    except GameError as exc:
        await query.answer(alert_text(exc), show_alert=True)
        return

    creature = result["creature"]
    is_free = result.get("is_free", is_free)
    if is_free:
        await query.answer("🎁 باکس رایگان امروز باز شد!")
    else:
        await query.answer("📦 باز شد!")
    keyboard = InlineKeyboardMarkup(
        [
            [btn("یکی دیگه باز کن", emoji_key="btn_diamond_box", style=SHOP, callback_data=f"dbox_pick:{tier}")],
            [back_btn("menu:diamond_box")],
        ]
    )
    from bot.loot_text import box_reveal

    photo = get_creature_image_path(creature)
    await send_screen(
        update,
        box_reveal(constants.DIAMOND_BOX_TIERS[tier]["label"], result, free=is_free),
        photo=photo,
        parse_mode="HTML",
        reply_markup=keyboard,
    )


async def diamond_box_buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    tier = query.data.split(":")[1]
    if tier not in constants.DIAMOND_BOX_TIERS:
        await query.answer("این جعبه پیدا نشد.", show_alert=True)
        return
    is_free = False
    if tier in ("bronze", "silver"):
        is_free = await run_db(_can_claim_free_diamond_sync, update.effective_user, tier)
    if is_free:
        return await _do_diamond_box_buy(update, context, tier, is_free=True)

    cfg = constants.DIAMOND_BOX_TIERS[tier]
    cost = cfg["cost_diamonds"]
    await query.answer()
    keyboard = InlineKeyboardMarkup([
        [
            btn("تأیید و باز کردن", emoji_key="btn_confirm", style=CONFIRM, callback_data=f"dbox_do_buy:{tier}"),
            back_btn(f"dbox_pick:{tier}", "انصراف"),
        ],
    ])
    lines = [
        f"{get_emoji('diamond_box')} <b>خرید و باز کردن {cfg['label']}</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"<blockquote>💰 هزینه: <code>{cost:,}</code> الماس {get_emoji('diamond')}</blockquote>",
        "",
        "آیا از خرید و باز کردن این جعبه مطمئن هستید؟",
    ]
    await safe_edit_message_text(
        query,
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=keyboard,
    )


async def diamond_box_do_buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    tier = query.data.split(":")[1]
    if tier not in constants.DIAMOND_BOX_TIERS:
        await query.answer("این جعبه پیدا نشد.", show_alert=True)
        return
    await _do_diamond_box_buy(update, context, tier, is_free=False)


async def diamond_box_bulk_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    tier = query.data.split(":")[1]
    if tier not in constants.DIAMOND_BOX_TIERS:
        await query.answer("این جعبه پیدا نشد.", show_alert=True)
        return
    cfg = constants.DIAMOND_BOX_TIERS[tier]
    cost = BULK_PAY * cfg["cost_diamonds"]
    await query.answer()
    keyboard = InlineKeyboardMarkup([
        [
            btn("تأیید و خرید", emoji_key="btn_confirm", style=CONFIRM, callback_data=f"dbox_do_bulk:{tier}"),
            back_btn(f"dbox_pick:{tier}", "انصراف"),
        ],
    ])
    lines = [
        f"{get_emoji('diamond_box')} <b>خرید بسته‌ای {cfg['label']}</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"<blockquote>📦 تعداد: <code>{BULK_PAY} + ۱ رایگان 🎁</code> (۱۱ جعبه)\n"
        f"💰 مجموع هزینه: <code>{cost:,}</code> الماس {get_emoji('diamond')}</blockquote>",
        "",
        "آیا از خرید بسته‌ای این جعبه مطمئن هستید؟",
    ]
    await safe_edit_message_text(
        query,
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=keyboard,
    )


async def diamond_box_do_bulk_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    tier = query.data.split(":")[1]
    try:
        summary = await run_db(_diamond_box_bulk_sync, update.effective_user, tier)
    except GameError as exc:
        await query.answer(alert_text(exc), show_alert=True)
        return
    await query.answer("🎉 باز شد!")
    label = constants.DIAMOND_BOX_TIERS[tier]["label"]
    keyboard = InlineKeyboardMarkup([
        [btn("باز کردن دوباره", emoji_key="btn_diamond_box", style=SHOP, callback_data=f"dbox_bulk:{tier}")],
        [back_btn("menu:diamond_box", "لیست جعبه‌ها")],
    ])
    photo = composite_lootbox_batch_image(summary["rolls"], label)
    await send_screen(
        update, _bulk_summary_text(label, summary),
        photo=photo, parse_mode="HTML", reply_markup=keyboard,
    )


def register(application) -> None:
    application.add_handler(CommandHandler("biocrate", biocrate_cmd, filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(biocrate_pick_callback, pattern=r"^bc_pick:"))
    application.add_handler(CallbackQueryHandler(biocrate_open_callback, pattern=r"^bc_open:"))
    application.add_handler(CommandHandler("diamondbox", diamond_box_panel, filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(diamond_box_pick_callback, pattern=r"^dbox_pick:"))
    application.add_handler(CallbackQueryHandler(diamond_box_buy_callback, pattern=r"^dbox_buy:"))
    application.add_handler(CallbackQueryHandler(diamond_box_do_buy_callback, pattern=r"^dbox_do_buy:"))
    application.add_handler(CallbackQueryHandler(diamond_box_bulk_callback, pattern=r"^dbox_bulk:"))
    application.add_handler(CallbackQueryHandler(diamond_box_do_bulk_callback, pattern=r"^dbox_do_bulk:"))

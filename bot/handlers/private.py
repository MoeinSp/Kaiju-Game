from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CommandHandler, ContextTypes, filters

from bio_lab.models import Creature
from bio_lab.repository import get_active_creature, get_or_create_user
from bot.utils import run_db
from game import constants
from game.creature import (
    GameError,
    create_starter_creature,
    effective_stats,
    feed,
    list_creatures,
    set_active_creature,
    train,
    upgrade_part,
)
from game.daily import assert_energy_available, check_missions, mission_status, record_action
from game.splice import splice


def creature_card_text(user, creature) -> str:
    stats = effective_stats(creature)
    return (
        f"🧬 <b>{creature.name}</b> (#{creature.id}) — {constants.ELEMENT_LABELS[creature.element]}\n"
        f"{constants.RARITY_LABELS[creature.rarity]} — سطح {creature.level}\n"
        f"XP: {creature.xp}/{constants.XP_PER_LEVEL}\n\n"
        f"❤️ HP: {stats['hp']}\n"
        f"⚔️ ATK: {stats['atk']}\n"
        f"🛡 DEF: {stats['def']}\n"
        f"💨 SPD: {stats['spd']}\n"
        f"☠️ Poison: {stats['poison']}\n\n"
        f"اعضا — بال:{creature.wings_lvl} زره:{creature.armor_lvl} نیش:{creature.fangs_lvl} زهر:{creature.poison_lvl}\n\n"
        f"💰 سکه: {user.coins} | 🧬 DNA: {user.dna_fragments}"
    )


def creature_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🍖 تغذیه", callback_data="feed"),
                InlineKeyboardButton("🏋️ تمرین", callback_data="train"),
            ],
            [
                InlineKeyboardButton("🦋 ارتقا بال", callback_data="upgrade:wings"),
                InlineKeyboardButton("🛡 ارتقا زره", callback_data="upgrade:armor"),
            ],
            [
                InlineKeyboardButton("🦷 ارتقا نیش", callback_data="upgrade:fangs"),
                InlineKeyboardButton("☠️ ارتقا زهر", callback_data="upgrade:poison"),
            ],
        ]
    )


def _start_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    creature = get_active_creature(user)
    is_new = False
    if creature is None:
        creature = create_starter_creature(user)
        is_new = True
    return user, creature, is_new


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user, creature, is_new = await run_db(_start_sync, update.effective_user)
    prefix = (
        "🥚 آزمایشگاه فعال شد! یک موجود تازه از کپسول زیستی بیرون اومد:\n\n"
        if is_new
        else "به آزمایشگاه خوش برگشتی!\n\n"
    )
    await update.message.reply_text(
        prefix + creature_card_text(user, creature), parse_mode="HTML", reply_markup=creature_keyboard()
    )


def _me_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    return user, get_active_creature(user)


async def me(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user, creature = await run_db(_me_sync, update.effective_user)
    if creature is None:
        await update.message.reply_text("هنوز موجودی نداری! دستور /start رو بزن.")
        return
    await update.message.reply_text(
        creature_card_text(user, creature), parse_mode="HTML", reply_markup=creature_keyboard()
    )


def _lab_action_sync(tg_user, action):
    user, _ = get_or_create_user(tg_user)
    creature = get_active_creature(user)
    if creature is None:
        raise GameError("اول /start رو بزن.")

    completed_missions: list[dict] = []
    if action == "feed":
        assert_energy_available(user, "feed")
        levels = feed(user, creature)
        record_action(user, "feed")
        completed_missions = check_missions(user, "feed")
        note = "🍖 تغذیه شد!" + (f" 🎉 سطح {creature.level} شد!" if levels else "")
    elif action == "train":
        levels = train(creature)
        record_action(user, "train")
        completed_missions = check_missions(user, "train")
        note = "🏋️ تمرین کرد!" + (f" 🎉 سطح {creature.level} شد!" if levels else "")
    elif action.startswith("upgrade:"):
        part = action.split(":", 1)[1]
        new_level = upgrade_part(user, creature, part)
        note = f"{constants.BODY_PARTS[part]['label']} به سطح {new_level} ارتقا یافت!"
    else:
        return None

    for m in completed_missions:
        note += f"\n🎯 ماموریت «{m['label']}» کامل شد! +{m['coins']} سکه" + (
            f", +{m['dna']} DNA" if m["dna"] else ""
        )
    return user, creature, note


async def lab_action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        result = await run_db(_lab_action_sync, update.effective_user, query.data)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    if result is None:
        await query.answer()
        return

    user, creature, note = result
    await query.answer()
    await query.edit_message_text(
        note + "\n\n" + creature_card_text(user, creature),
        parse_mode="HTML",
        reply_markup=creature_keyboard(),
    )


def _collection_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    return list_creatures(user)


async def collection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    creatures = await run_db(_collection_sync, update.effective_user)
    if not creatures:
        await update.message.reply_text("کلکسیونت خالیه! دستور /start رو بزن.")
        return

    lines = ["🗂 <b>کلکسیون تو:</b>"]
    for c in creatures:
        active_tag = " ✅فعال" if c.is_active else ""
        lines.append(
            f"#{c.id} {c.name} — {constants.ELEMENT_LABELS[c.element]} — "
            f"{constants.RARITY_LABELS[c.rarity]} — Lv{c.level}{active_tag}"
        )
    lines.append("\nبرای تعویض موجود فعال بنویس: /select و شماره موجود (مثلاً /select 3)")
    lines.append("برای ترکیب دو موجود بنویس: /splice و دو شماره (مثلاً /splice 3 5)")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


def _select_sync(tg_user, creature_id):
    user, _ = get_or_create_user(tg_user)
    return user, set_active_creature(user, creature_id)


async def select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(
            "استفاده درست: /select و شماره موجود (مثلاً /select 3)\nبرای دیدن شماره‌ها: /collection"
        )
        return
    try:
        user, creature = await run_db(_select_sync, update.effective_user, int(context.args[0]))
    except GameError as exc:
        await update.message.reply_text(str(exc))
        return
    await update.message.reply_text(
        f"{creature.name} حالا موجود فعالته!\n\n" + creature_card_text(user, creature),
        parse_mode="HTML",
        reply_markup=creature_keyboard(),
    )


def _splice_sync(tg_user, id_a, id_b):
    user, _ = get_or_create_user(tg_user)
    try:
        parent_a = Creature.objects.get(id=id_a)
        parent_b = Creature.objects.get(id=id_b)
    except Creature.DoesNotExist:
        raise GameError("یکی از این idها پیدا نشد.")
    return user, splice(user, parent_a, parent_b)


async def splice_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) != 2 or not all(a.isdigit() for a in context.args):
        await update.message.reply_text(
            "استفاده درست: /splice و دو شماره موجود (مثلاً /splice 3 5)\nبرای دیدن شماره‌ها: /collection"
        )
        return
    try:
        user, child = await run_db(_splice_sync, update.effective_user, int(context.args[0]), int(context.args[1]))
    except GameError as exc:
        await update.message.reply_text(str(exc))
        return
    await update.message.reply_text(
        "🧪 ترکیب موفق بود! والدین جذب شدن و یک موجود جدید متولد شد:\n\n" + creature_card_text(user, child),
        parse_mode="HTML",
        reply_markup=creature_keyboard(),
    )


def _missions_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    return mission_status(user)


async def missions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    status = await run_db(_missions_sync, update.effective_user)
    lines = ["🎯 <b>ماموریت‌های امروز:</b>"]
    for m in status:
        check = "✅" if m["done"] else f"({m['progress']}/{m['target']})"
        reward = f"+{m['coins']} سکه" + (f", +{m['dna']} DNA" if m["dna"] else "")
        lines.append(f"{check} {m['label']} — {reward}")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


def register(application) -> None:
    application.add_handler(CommandHandler("start", start, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("me", me, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("collection", collection, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("select", select, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("splice", splice_cmd, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("missions", missions, filters.ChatType.PRIVATE))

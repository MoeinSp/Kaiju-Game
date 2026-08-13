from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CommandHandler, ContextTypes, filters

from db.repository import get_active_creature, get_or_create_user
from db.session import get_session
from game import constants
from game.creature import GameError, create_starter_creature, effective_stats, feed, train, upgrade_part


def creature_card_text(user, creature) -> str:
    stats = effective_stats(creature)
    return (
        f"🧬 <b>{creature.name}</b> ({constants.ELEMENT_LABELS[creature.element]})\n"
        f"سطح {creature.level} — نایابی: {creature.rarity}\n"
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


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = get_session()
    try:
        user, created = get_or_create_user(session, update.effective_user)
        creature = get_active_creature(session, user)
        if creature is None:
            creature = create_starter_creature(session, user)
            session.commit()
            await update.message.reply_text(
                f"🥚 آزمایشگاه فعال شد! یک موجود تازه از کپسول زیستی بیرون اومد:\n\n"
                + creature_card_text(user, creature),
                parse_mode="HTML",
                reply_markup=creature_keyboard(),
            )
        else:
            await update.message.reply_text(
                "به آزمایشگاه خوش برگشتی!\n\n" + creature_card_text(user, creature),
                parse_mode="HTML",
                reply_markup=creature_keyboard(),
            )
    finally:
        session.close()


async def me(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = get_session()
    try:
        user, _ = get_or_create_user(session, update.effective_user)
        creature = get_active_creature(session, user)
        if creature is None:
            await update.message.reply_text("هنوز موجودی نداری! دستور /start رو بزن.")
            return
        await update.message.reply_text(
            creature_card_text(user, creature), parse_mode="HTML", reply_markup=creature_keyboard()
        )
    finally:
        session.close()


async def lab_action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    session = get_session()
    try:
        user, _ = get_or_create_user(session, update.effective_user)
        creature = get_active_creature(session, user)
        if creature is None:
            await query.answer("اول /start رو بزن.", show_alert=True)
            return

        action = query.data
        try:
            if action == "feed":
                levels = feed(session, user, creature)
                note = f"🍖 تغذیه شد!" + (f" 🎉 سطح {creature.level} شد!" if levels else "")
            elif action == "train":
                levels = train(session, creature)
                note = "🏋️ تمرین کرد!" + (f" 🎉 سطح {creature.level} شد!" if levels else "")
            elif action.startswith("upgrade:"):
                part = action.split(":", 1)[1]
                new_level = upgrade_part(session, user, creature, part)
                note = f"{constants.BODY_PARTS[part]['label']} به سطح {new_level} ارتقا یافت!"
            else:
                return
        except GameError as exc:
            await query.answer(str(exc), show_alert=True)
            return

        await query.edit_message_text(
            note + "\n\n" + creature_card_text(user, creature),
            parse_mode="HTML",
            reply_markup=creature_keyboard(),
        )
    finally:
        session.close()


def register(application) -> None:
    application.add_handler(CommandHandler("start", start, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("me", me, filters.ChatType.PRIVATE))

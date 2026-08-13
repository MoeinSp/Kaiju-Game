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
from game.daily import apply_daily_login, check_missions, mission_status, record_action
from game.energy import spend_energy, sync_energy
from game.splice import splice


def _mission_lines(completed: list[dict]) -> str:
    if not completed:
        return ""
    lines = []
    for m in completed:
        reward = f"+{m['coins']} 💰"
        if m["dna"]:
            reward += f" +{m['dna']} 🧬"
        lines.append(f"🎯 ماموریت «{m['label']}» تکمیل شد! {reward}")
    return "\n" + "\n".join(lines)


def creature_card_text(user, creature) -> str:
    stats = effective_stats(creature)
    energy = sync_energy(user)
    xp_bar = constants.render_bar(creature.xp, constants.XP_PER_LEVEL, width=12)
    energy_bar = constants.render_bar(energy, constants.MAX_ENERGY, width=12)
    return (
        f"🧬 <b>{creature.name}</b>  <code>#{creature.id}</code>\n"
        f"{constants.ELEMENT_LABELS[creature.element]} · {constants.RARITY_LABELS[creature.rarity]} · سطح {creature.level}\n"
        f"{xp_bar}  {creature.xp}/{constants.XP_PER_LEVEL} XP\n"
        "\n"
        f"❤️ {stats['hp']}   ⚔️ {stats['atk']}   🛡 {stats['def']}   💨 {stats['spd']}   ☠️ {stats['poison']}\n"
        f"<i>🦋 بال {creature.wings_lvl} · 🛡 زره {creature.armor_lvl} · 🦷 نیش {creature.fangs_lvl} · ☠️ زهر {creature.poison_lvl}</i>\n"
        "\n"
        f"{energy_bar}  ⚡ {energy}/{constants.MAX_ENERGY}\n"
        f"💰 {user.coins}   🧬 {user.dna_fragments}"
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
    login_bonus = apply_daily_login(user)
    return user, creature, is_new, login_bonus


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user, creature, is_new, login_bonus = await run_db(_start_sync, update.effective_user)

    lines = []
    if is_new:
        lines.append(
            "🥚 <b>آزمایشگاه فعال شد!</b>\n"
            "یه موجود تازه از کپسول زیستی بیرون اومد — بهش خوش‌آمد بگو 👇\n"
        )
    else:
        lines.append("👋 <b>به آزمایشگاه خوش برگشتی!</b>\n")

    if login_bonus:
        streak_line = f"🔥 <b>{login_bonus['streak']} روز پشت‌سرهم</b> اومدی! +{login_bonus['coins']} 💰"
        if login_bonus["dna"]:
            streak_line += f" +{login_bonus['dna']} 🧬"
        lines.append(streak_line + "\n")

    lines.append(creature_card_text(user, creature))
    await update.message.reply_text(
        "\n".join(lines), parse_mode="HTML", reply_markup=creature_keyboard()
    )


def _me_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    return user, get_active_creature(user)


async def me(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user, creature = await run_db(_me_sync, update.effective_user)
    if creature is None:
        await update.message.reply_text("😅 هنوز موجودی نداری! دستور /start رو بزن تا از آزمایشگاه شروع کنی.")
        return
    await update.message.reply_text(
        creature_card_text(user, creature), parse_mode="HTML", reply_markup=creature_keyboard()
    )


def _lab_action_sync(tg_user, action):
    user, _ = get_or_create_user(tg_user)
    creature = get_active_creature(user)
    if creature is None:
        raise GameError("اول /start رو بزن تا موجودت رو بگیری.")

    completed_missions: list[dict] = []
    if action == "feed":
        spend_energy(user, constants.FEED_ENERGY_COST, "تغذیه")
        levels = feed(user, creature)
        user.save(update_fields=["energy", "energy_updated_at"])
        record_action(user, "feed")
        completed_missions = check_missions(user, "feed")
        note = "🍖 <b>تغذیه شد!</b>" + (f" 🎉 رسید به سطح {creature.level}!" if levels else "")
    elif action == "train":
        levels = train(creature)
        record_action(user, "train")
        completed_missions = check_missions(user, "train")
        note = "🏋️ <b>تمرین کرد!</b>" + (f" 🎉 رسید به سطح {creature.level}!" if levels else "")
    elif action.startswith("upgrade:"):
        part = action.split(":", 1)[1]
        new_level = upgrade_part(user, creature, part)
        note = f"{constants.BODY_PARTS[part]['label']} به سطح {new_level} ارتقا یافت! ✨"
    else:
        return None

    return user, creature, note + _mission_lines(completed_missions)


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
        await update.message.reply_text("📭 کلکسیونت خالیه! دستور /start رو بزن.")
        return

    lines = [f"🗂 <b>کلکسیون تو</b> — {len(creatures)} موجود\n"]
    for c in creatures:
        active_tag = " ✅" if c.is_active else ""
        lines.append(
            f"<code>#{c.id}</code> {c.name} — {constants.ELEMENT_LABELS[c.element]} · "
            f"{constants.RARITY_LABELS[c.rarity]} · Lv{c.level}{active_tag}"
        )
    lines.append("\n🔁 تعویض موجود فعال: <code>/select 3</code>")
    lines.append("🧪 ترکیب دو موجود: <code>/splice 3 5</code>")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


def _select_sync(tg_user, creature_id):
    user, _ = get_or_create_user(tg_user)
    return user, set_active_creature(user, creature_id)


async def select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(
            "استفاده درست: <code>/select 3</code> (شماره‌ی موجود از /collection)", parse_mode="HTML"
        )
        return
    try:
        user, creature = await run_db(_select_sync, update.effective_user, int(context.args[0]))
    except GameError as exc:
        await update.message.reply_text(str(exc))
        return
    await update.message.reply_text(
        f"✅ <b>{creature.name}</b> حالا موجود فعالته!\n\n" + creature_card_text(user, creature),
        parse_mode="HTML",
        reply_markup=creature_keyboard(),
    )


def _splice_sync(tg_user, id_a, id_b):
    user, _ = get_or_create_user(tg_user)
    try:
        parent_a = Creature.objects.get(id=id_a)
        parent_b = Creature.objects.get(id=id_b)
    except Creature.DoesNotExist:
        raise GameError("یکی از این شماره‌ها پیدا نشد.")
    child = splice(user, parent_a, parent_b)
    record_action(user, "splice")
    completed_missions = check_missions(user, "splice")
    return user, child, completed_missions


async def splice_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) != 2 or not all(a.isdigit() for a in context.args):
        await update.message.reply_text(
            "استفاده درست: <code>/splice 3 5</code> (دو شماره‌ی موجود از /collection)", parse_mode="HTML"
        )
        return
    try:
        user, child, completed_missions = await run_db(
            _splice_sync, update.effective_user, int(context.args[0]), int(context.args[1])
        )
    except GameError as exc:
        await update.message.reply_text(str(exc))
        return
    await update.message.reply_text(
        "🧪 <b>ترکیب موفق بود!</b> والدین توی کلکسیونت غیرفعال شدن و یه موجود جدید متولد شد:\n\n"
        + creature_card_text(user, child)
        + _mission_lines(completed_missions),
        parse_mode="HTML",
        reply_markup=creature_keyboard(),
    )


def _missions_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    return mission_status(user)


async def missions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    status = await run_db(_missions_sync, update.effective_user)
    lines = ["🎯 <b>ماموریت‌های امروز</b>\n"]
    for m in status:
        check = "✅" if m["done"] else f"⏳ {m['progress']}/{m['target']}"
        reward = f"+{m['coins']} 💰" + (f" +{m['dna']} 🧬" if m["dna"] else "")
        lines.append(f"{check} — {m['label']} ({reward})")
    lines.append("\n<i>ماموریت‌ها هر روز (ساعت جهانی UTC) ریست می‌شن.</i>")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


def register(application) -> None:
    application.add_handler(CommandHandler("start", start, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("me", me, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("collection", collection, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("select", select, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("splice", splice_cmd, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("missions", missions, filters.ChatType.PRIVATE))

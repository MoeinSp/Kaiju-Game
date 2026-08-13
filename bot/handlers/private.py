from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

from bio_lab.models import Creature
from bio_lab.repository import display_name, get_active_creature, get_or_create_user
from bot.handlers.owner import admin_cmd
from bot.utils import run_db
from config import OWNER_TELEGRAM_ID
from game import constants
from game.alliance import alliance_info, create_alliance, join_alliance, leave_alliance, top_alliances
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
from game.emoji import get_emoji
from game.energy import spend_energy, sync_energy
from game.hunt import resolve_hunt
from game.splice import splice


def _mission_lines(completed: list[dict]) -> str:
    if not completed:
        return ""
    lines = []
    for m in completed:
        reward = f"+{m['coins']} {get_emoji('coin')}"
        if m["dna"]:
            reward += f" +{m['dna']} {get_emoji('dna')}"
        lines.append(f"{get_emoji('mission')} ماموریت «{m['label']}» تکمیل شد! {reward}")
    return "\n" + "\n".join(lines)


def creature_card_text(user, creature) -> str:
    stats = effective_stats(creature)
    energy = sync_energy(user)
    xp_bar = constants.render_bar(creature.xp, constants.XP_PER_LEVEL, width=12)
    energy_bar = constants.render_bar(energy, constants.MAX_ENERGY, width=12)
    hp, atk, def_, spd, poison = (
        get_emoji("hp"),
        get_emoji("atk"),
        get_emoji("def"),
        get_emoji("spd"),
        get_emoji("poison"),
    )
    return (
        f"{get_emoji('creature')} <b>{creature.name}</b>  <code>#{creature.id}</code>\n"
        f"{constants.element_label(creature.element)} · {constants.RARITY_LABELS[creature.rarity]} · سطح {creature.level}\n"
        f"{xp_bar}  {creature.xp}/{constants.XP_PER_LEVEL} XP\n"
        "\n"
        f"{hp} {stats['hp']}   {atk} {stats['atk']}   {def_} {stats['def']}   {spd} {stats['spd']}   {poison} {stats['poison']}\n"
        f"<i>{get_emoji('wings')} بال {creature.wings_lvl} · {def_} زره {creature.armor_lvl} · "
        f"{get_emoji('fangs')} نیش {creature.fangs_lvl} · {poison} زهر {creature.poison_lvl}</i>\n"
        "\n"
        f"{energy_bar}  {get_emoji('energy')} {energy}/{constants.MAX_ENERGY}\n"
        f"{get_emoji('coin')} {user.coins}   {get_emoji('dna')} {user.dna_fragments}"
    )


def creature_keyboard(is_owner: bool = False) -> InlineKeyboardMarkup:
    """Full navigation keyboard shown under the creature card — lab actions on top,
    then shortcuts to every other section, so a player never has to remember a
    slash-command to keep playing. callback_data mixes bare actions (feed/train/
    upgrade:*, handled by lab_action_callback) with menu:* entries (handled by
    menu_callback) — both handlers are always registered together, so this is safe."""
    rows = [
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
        [
            InlineKeyboardButton("🗂 کلکسیون", callback_data="menu:collection"),
            InlineKeyboardButton("🏹 شکار انفرادی", callback_data="menu:hunt"),
        ],
        [
            InlineKeyboardButton("🎯 ماموریت‌ها", callback_data="menu:missions"),
            InlineKeyboardButton("🤝 اتحاد من", callback_data="menu:alliance_info"),
        ],
        [
            InlineKeyboardButton("🏆 رتبه‌بندی", callback_data="menu:rank"),
            InlineKeyboardButton("👤 پروفایل من", callback_data="menu:profile"),
        ],
    ]
    if is_owner:
        rows.append([InlineKeyboardButton("🛠 پنل ادمین", callback_data="menu:admin")])
    return InlineKeyboardMarkup(rows)


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
            f"{get_emoji('egg')} <b>آزمایشگاه فعال شد!</b>\n"
            "یه موجود تازه از کپسول زیستی بیرون اومد — بهش خوش‌آمد بگو 👇\n"
        )
    else:
        lines.append("👋 <b>به آزمایشگاه خوش برگشتی!</b>\n")

    if login_bonus:
        streak_line = f"🔥 <b>{login_bonus['streak']} روز پشت‌سرهم</b> اومدی! +{login_bonus['coins']} {get_emoji('coin')}"
        if login_bonus["dna"]:
            streak_line += f" +{login_bonus['dna']} {get_emoji('dna')}"
        lines.append(streak_line + "\n")

    lines.append(creature_card_text(user, creature))
    is_owner = update.effective_user.id == OWNER_TELEGRAM_ID
    await update.message.reply_text(
        "\n".join(lines), parse_mode="HTML", reply_markup=creature_keyboard(is_owner)
    )


def _me_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    return user, get_active_creature(user)


async def me(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user, creature = await run_db(_me_sync, update.effective_user)
    if creature is None:
        await update.effective_message.reply_text(
            "😅 هنوز موجودی نداری! دستور /start رو بزن تا از آزمایشگاه شروع کنی."
        )
        return
    is_owner = update.effective_user.id == OWNER_TELEGRAM_ID
    await update.effective_message.reply_text(
        creature_card_text(user, creature), parse_mode="HTML", reply_markup=creature_keyboard(is_owner)
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
        note = "🍖 <b>تغذیه شد!</b>" + (
            f" {get_emoji('celebrate')} رسید به سطح {creature.level}!" if levels else ""
        )
    elif action == "train":
        levels = train(creature)
        record_action(user, "train")
        completed_missions = check_missions(user, "train")
        note = "🏋️ <b>تمرین کرد!</b>" + (
            f" {get_emoji('celebrate')} رسید به سطح {creature.level}!" if levels else ""
        )
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
    is_owner = update.effective_user.id == OWNER_TELEGRAM_ID
    await query.edit_message_text(
        note + "\n\n" + creature_card_text(user, creature),
        parse_mode="HTML",
        reply_markup=creature_keyboard(is_owner),
    )


def _collection_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    return list_creatures(user)


async def collection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    creatures = await run_db(_collection_sync, update.effective_user)
    if not creatures:
        await update.effective_message.reply_text("📭 کلکسیونت خالیه! دستور /start رو بزن.")
        return

    lines = [f"{get_emoji('collection')} <b>کلکسیون تو</b> — {len(creatures)} موجود\n"]
    for c in creatures:
        active_tag = " ✅" if c.is_active else ""
        lines.append(
            f"<code>#{c.id}</code> {c.name} — {constants.element_label(c.element)} · "
            f"{constants.RARITY_LABELS[c.rarity]} · Lv{c.level}{active_tag}"
        )
    lines.append("\n🔁 تعویض موجود فعال: <code>/select 3</code>")
    lines.append("🧪 ترکیب دو موجود: <code>/splice 3 5</code>")
    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")


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
    is_owner = update.effective_user.id == OWNER_TELEGRAM_ID
    await update.message.reply_text(
        f"✅ <b>{creature.name}</b> حالا موجود فعالته!\n\n" + creature_card_text(user, creature),
        parse_mode="HTML",
        reply_markup=creature_keyboard(is_owner),
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
    is_owner = update.effective_user.id == OWNER_TELEGRAM_ID
    await update.message.reply_text(
        f"{get_emoji('lab')} <b>ترکیب موفق بود!</b> والدین توی کلکسیونت غیرفعال شدن و یه موجود جدید متولد شد:\n\n"
        + creature_card_text(user, child)
        + _mission_lines(completed_missions),
        parse_mode="HTML",
        reply_markup=creature_keyboard(is_owner),
    )


def _missions_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    return mission_status(user)


async def missions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    status = await run_db(_missions_sync, update.effective_user)
    lines = [f"{get_emoji('mission')} <b>ماموریت‌های امروز</b>\n"]
    for m in status:
        check = "✅" if m["done"] else f"⏳ {m['progress']}/{m['target']}"
        reward = f"+{m['coins']} {get_emoji('coin')}" + (
            f" +{m['dna']} {get_emoji('dna')}" if m["dna"] else ""
        )
        lines.append(f"{check} — {m['label']} ({reward})")
    lines.append("\n<i>ماموریت‌ها هر روز (ساعت جهانی UTC) ریست می‌شن.</i>")
    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")


def _hunt_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    creature = get_active_creature(user)
    if creature is None:
        raise GameError("اول /start رو بزن تا موجودت رو بگیری.")

    spend_energy(user, constants.HUNT_ENERGY_COST, "شکار")
    user.save(update_fields=["energy", "energy_updated_at"])

    result = resolve_hunt(user, creature)
    record_action(user, "hunt")
    completed_missions = check_missions(user, "hunt")
    return creature, result, completed_missions


async def hunt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        creature, result, completed_missions = await run_db(_hunt_sync, update.effective_user)
    except GameError as exc:
        await update.effective_message.reply_text(str(exc))
        return

    if result["won"]:
        reward_line = (
            f"\n\n{get_emoji('celebrate')} <b>بردی!</b> +{result['coins']} {get_emoji('coin')}"
            + (f" +{result['dna']} {get_emoji('dna')}" if result["dna"] else "")
            + f" +{result['xp']} XP"
        )
        if result["levels"]:
            reward_line += f" {get_emoji('celebrate')} رسید به سطح {creature.level}!"
    else:
        reward_line = f"\n\n😔 باختی... +{result['xp']} XP تسلی‌بخش گرفتی."
    reward_line += _mission_lines(completed_missions)

    await update.effective_message.reply_text(result["log_text"] + reward_line, parse_mode="HTML")


def _alliance_create_sync(tg_user, name):
    user, _ = get_or_create_user(tg_user)
    return create_alliance(user, name)


async def alliance_create(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text(
            "استفاده: <code>/alliance_create اسم اتحاد</code>", parse_mode="HTML"
        )
        return
    try:
        alliance = await run_db(_alliance_create_sync, update.effective_user, " ".join(context.args))
    except GameError as exc:
        await update.message.reply_text(str(exc))
        return
    await update.message.reply_text(
        f"{get_emoji('alliance')} اتحاد <b>{alliance.name}</b> ساخته شد! تو رهبرشی {get_emoji('crown')}",
        parse_mode="HTML",
    )


def _alliance_join_sync(tg_user, name):
    user, _ = get_or_create_user(tg_user)
    return join_alliance(user, name)


async def alliance_join(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text(
            "استفاده: <code>/alliance_join اسم اتحاد</code>", parse_mode="HTML"
        )
        return
    try:
        alliance = await run_db(_alliance_join_sync, update.effective_user, " ".join(context.args))
    except GameError as exc:
        await update.message.reply_text(str(exc))
        return
    await update.message.reply_text(
        f"{get_emoji('alliance')} به اتحاد <b>{alliance.name}</b> پیوستی!", parse_mode="HTML"
    )


def _alliance_leave_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    leave_alliance(user)


async def alliance_leave(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        await run_db(_alliance_leave_sync, update.effective_user)
    except GameError as exc:
        await update.message.reply_text(str(exc))
        return
    await update.message.reply_text("👋 از اتحاد خارج شدی.")


def _alliance_info_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    if user.alliance_id is None:
        return None
    return alliance_info(user.alliance)


async def alliance_info_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    info = await run_db(_alliance_info_sync, update.effective_user)
    if info is None:
        await update.effective_message.reply_text(
            f"{get_emoji('alliance')} توی هیچ اتحادی نیستی.\n"
            "<code>/alliance_create اسم</code> برای ساختن، یا <code>/alliance_join اسم</code> برای پیوستن.",
            parse_mode="HTML",
        )
        return
    lines = [
        f"{get_emoji('alliance')} <b>اتحاد {info['name']}</b>",
        f"{get_emoji('crown')} رهبر: {display_name(info['leader']) if info['leader'] else '—'}",
        f"{get_emoji('users')} اعضا: {info['member_count']}   💪 قدرت کل: {info['power']}",
        "",
    ]
    for m in info["members"][:20]:
        lines.append(f"• {display_name(m)}")
    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")


def _alliance_top_sync():
    return top_alliances(10)


async def alliance_top(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ranked = await run_db(_alliance_top_sync)
    if not ranked:
        await update.effective_message.reply_text(
            "هنوز هیچ اتحادی ساخته نشده. با /alliance_create اولیش رو بساز!"
        )
        return
    medals = [get_emoji("medal_gold"), get_emoji("medal_silver"), get_emoji("medal_bronze")]
    lines = [f"{get_emoji('trophy')} <b>برترین اتحادها</b>\n"]
    for i, r in enumerate(ranked, start=1):
        rank = medals[i - 1] if i <= 3 else f"{i}."
        lines.append(f"{rank} {r['alliance'].name} — قدرت {r['power']} ({r['member_count']} عضو)")
    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")


def _rank_sync(tg_user):
    from django.db.models import F

    user, _ = get_or_create_user(tg_user)
    ranked = list(
        Creature.objects.filter(is_active=True)
        .annotate(power=F("base_hp") + F("base_atk") + F("base_def") + F("base_spd"))
        .order_by("-power")
    )
    my_creature = get_active_creature(user)
    my_rank = None
    if my_creature is not None:
        for idx, c in enumerate(ranked, start=1):
            if c.id == my_creature.id:
                my_rank = idx
                break
    return ranked[:10], my_rank, len(ranked)


async def rank(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    top10, my_rank, total = await run_db(_rank_sync, update.effective_user)
    if not top10:
        await update.effective_message.reply_text("هنوز هیچ موجودی ثبت نشده.")
        return
    medals = [get_emoji("medal_gold"), get_emoji("medal_silver"), get_emoji("medal_bronze")]
    lines = [f"{get_emoji('trophy')} <b>رتبه‌بندی سراسری موجودات</b>\n"]
    for i, c in enumerate(top10, start=1):
        rank_icon = medals[i - 1] if i <= 3 else f"{i}."
        lines.append(f"{rank_icon} {c.name} (Lv{c.level}) — قدرت {c.power}")
    if my_rank is not None:
        lines.append(f"\nرتبه‌ی موجود فعال تو: <b>{my_rank}</b> از {total}")
    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")


def _profile_sync(tg_user):
    from django.db.models import Sum

    from bio_lab.models import DailyActionLog, DuelLog, RaidDamageLog

    user, _ = get_or_create_user(tg_user)
    duel_wins = DuelLog.objects.filter(winner=user).count()
    total_raid_damage = RaidDamageLog.objects.filter(user=user).aggregate(t=Sum("damage"))["t"] or 0
    total_hunts = DailyActionLog.objects.filter(user=user, action="hunt").aggregate(t=Sum("count"))["t"] or 0
    creatures_owned = Creature.objects.filter(owner=user).count()
    return user, {
        "duel_wins": duel_wins,
        "total_raid_damage": total_raid_damage,
        "total_hunts": total_hunts,
        "creatures_owned": creatures_owned,
    }


async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user, stats = await run_db(_profile_sync, update.effective_user)
    lines = [
        f"{get_emoji('profile')} <b>پروفایل {display_name(user)}</b>\n",
        f"📅 عضو از: {user.created_at.strftime('%Y-%m-%d')}",
        f"🔥 روزهای ورود پشت‌سرهم: {user.login_streak}",
        f"{get_emoji('creature')} موجودات ساخته‌شده: {stats['creatures_owned']}\n",
        f"{get_emoji('battle')} دوئل‌های برده: {stats['duel_wins']}",
        f"{get_emoji('hunt')} شکارهای انجام‌شده: {stats['total_hunts']}",
        f"{get_emoji('raid_boss')} کل دمیج واردشده به رید باس‌ها: {stats['total_raid_damage']}\n",
        f"{get_emoji('coin')} سکه فعلی: {user.coins}   {get_emoji('dna')} DNA فعلی: {user.dna_fragments}",
    ]
    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🧬 موجود فعال", callback_data="menu:me"),
                InlineKeyboardButton("🗂 کلکسیون", callback_data="menu:collection"),
            ],
            [
                InlineKeyboardButton("🏹 شکار انفرادی", callback_data="menu:hunt"),
                InlineKeyboardButton("🎯 ماموریت‌ها", callback_data="menu:missions"),
            ],
            [
                InlineKeyboardButton("🤝 اتحاد من", callback_data="menu:alliance_info"),
                InlineKeyboardButton("🏆 رتبه‌بندی", callback_data="menu:rank"),
            ],
            [InlineKeyboardButton("👤 پروفایل من", callback_data="menu:profile")],
        ]
    )


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "📋 <b>منوی اصلی</b>\nیکی رو انتخاب کن:", parse_mode="HTML", reply_markup=main_menu_keyboard()
    )


_MENU_ACTIONS = {
    "me": me,
    "collection": collection,
    "hunt": hunt,
    "missions": missions,
    "alliance_info": alliance_info_cmd,
    "rank": rank,
    "admin": admin_cmd,
    "profile": profile,
}


async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    action = query.data.split(":", 1)[1]
    handler = _MENU_ACTIONS.get(action)
    if handler is not None:
        await handler(update, context)


def register(application) -> None:
    application.add_handler(CommandHandler("start", start, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("me", me, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("collection", collection, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("select", select, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("splice", splice_cmd, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("missions", missions, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("hunt", hunt, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("alliance_create", alliance_create, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("alliance_join", alliance_join, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("alliance_leave", alliance_leave, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("alliance_info", alliance_info_cmd, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("alliance_top", alliance_top, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("rank", rank, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("profile", profile, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("menu", menu, filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(menu_callback, pattern=r"^menu:"))

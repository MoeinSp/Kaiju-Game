from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

from bio_lab.models import Alliance, Creature
from bio_lab.repository import display_name, get_active_creature, get_or_create_user
from bot.handlers.inventory import inventory_cmd
from bot.handlers.lootbox import biocrate_cmd
from bot.handlers.owner import admin_cmd
from bot.utils import run_db
from config import OWNER_TELEGRAM_ID
from game import constants
from game.alliance import (
    alliance_info,
    create_alliance,
    deposit_treasury,
    heist,
    join_alliance,
    leave_alliance,
    top_alliances,
)
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
from game.daily import apply_daily_login, assert_energy_available, check_missions, mission_status, record_action
from game.emoji import get_emoji
from game.energy import spend_energy, sync_energy
from game.equipment import get_equipped_items
from game.fusion import fuse
from game.hunt import resolve_hunt


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


def creature_card_text(user, creature, equipped_items: list | None = None) -> str:
    stats = effective_stats(creature, equipped_items)
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
    equip_line = ""
    if equipped_items:
        slots = ", ".join(constants.EQUIPMENT_SLOT_LABELS[i.slot] + f" +{i.level}" for i in equipped_items)
        equip_line = f"\n{get_emoji('crit')} {slots}"
    return (
        f"{get_emoji('creature')} <b>{creature.name}</b>  <code>#{creature.id}</code>\n"
        f"{constants.element_label(creature.element)} · {constants.RARITY_LABELS[creature.rarity]} · سطح {creature.level}\n"
        f"{xp_bar}  {creature.xp}/{constants.XP_PER_LEVEL} XP\n"
        "\n"
        f"{hp} {stats['hp']}   {atk} {stats['atk']}   {def_} {stats['def']}   {spd} {stats['spd']}   {poison} {stats['poison']}\n"
        f"<i>{get_emoji('wings')} بال {creature.wings_lvl} · {def_} زره {creature.armor_lvl} · "
        f"{get_emoji('fangs')} نیش {creature.fangs_lvl} · {poison} زهر {creature.poison_lvl}</i>"
        f"{equip_line}\n"
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
            InlineKeyboardButton("🎒 تجهیزات", callback_data="menu:inventory"),
            InlineKeyboardButton("📦 باکس ژنتیکی", callback_data="menu:biocrate"),
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
    equipped_items = get_equipped_items(creature)
    return user, creature, is_new, login_bonus, equipped_items


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user, creature, is_new, login_bonus, equipped_items = await run_db(_start_sync, update.effective_user)

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

    lines.append(creature_card_text(user, creature, equipped_items))
    is_owner = update.effective_user.id == OWNER_TELEGRAM_ID
    await update.message.reply_text(
        "\n".join(lines), parse_mode="HTML", reply_markup=creature_keyboard(is_owner)
    )


def _me_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    creature = get_active_creature(user)
    equipped_items = get_equipped_items(creature) if creature else []
    return user, creature, equipped_items


async def me(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user, creature, equipped_items = await run_db(_me_sync, update.effective_user)
    if creature is None:
        await update.effective_message.reply_text(
            "😅 هنوز موجودی نداری! دستور /start رو بزن تا از آزمایشگاه شروع کنی."
        )
        return
    is_owner = update.effective_user.id == OWNER_TELEGRAM_ID
    await update.effective_message.reply_text(
        creature_card_text(user, creature, equipped_items),
        parse_mode="HTML",
        reply_markup=creature_keyboard(is_owner),
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

    equipped_items = get_equipped_items(creature)
    return user, creature, note + _mission_lines(completed_missions), equipped_items


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

    user, creature, note, equipped_items = result
    await query.answer()
    is_owner = update.effective_user.id == OWNER_TELEGRAM_ID
    await query.edit_message_text(
        note + "\n\n" + creature_card_text(user, creature, equipped_items),
        parse_mode="HTML",
        reply_markup=creature_keyboard(is_owner),
    )


def _collection_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    return list_creatures(user)


def _collection_keyboard(creatures: list[Creature]) -> InlineKeyboardMarkup:
    rows = []
    for c in creatures:
        tag = "🟢 " if c.is_active else ""
        rows.append(
            [InlineKeyboardButton(f"{tag}{c.name} · Lv{c.level} · {constants.RARITY_LABELS[c.rarity]}", callback_data=f"coll_pick:{c.id}")]
        )
    rows.append([InlineKeyboardButton("◀️ بازگشت", callback_data="menu:me")])
    return InlineKeyboardMarkup(rows)


async def collection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    creatures = await run_db(_collection_sync, update.effective_user)
    if not creatures:
        await update.effective_message.reply_text(f"📭 کلکسیونت خالیه! {get_emoji('egg')} با /start شروع کن.")
        return
    await update.effective_message.reply_text(
        f"{get_emoji('collection')} <b>کلکسیون تو</b> — {len(creatures)} موجود\nرو هرکدوم بزن تا جزئیاتش رو ببینی:",
        parse_mode="HTML",
        reply_markup=_collection_keyboard(creatures),
    )


def _creature_detail_sync(tg_user, creature_id):
    user, _ = get_or_create_user(tg_user)
    try:
        creature = Creature.objects.get(id=creature_id, owner=user)
    except Creature.DoesNotExist:
        raise GameError("این موجود توی کلکسیون تو نیست.")
    return user, creature, get_equipped_items(creature)


def _creature_detail_keyboard(creature_id: int, is_active: bool) -> InlineKeyboardMarkup:
    rows = []
    if not is_active:
        rows.append([InlineKeyboardButton("🟢 انتخاب به‌عنوان موجود فعال", callback_data=f"coll_select:{creature_id}")])
    rows.append([InlineKeyboardButton("🧪 استفاده در فیوژن", callback_data=f"fus_a:{creature_id}")])
    rows.append([InlineKeyboardButton("◀️ بازگشت به کلکسیون", callback_data="menu:collection")])
    return InlineKeyboardMarkup(rows)


async def collection_pick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    creature_id = int(query.data.split(":")[1])
    try:
        user, creature, equipped_items = await run_db(_creature_detail_sync, update.effective_user, creature_id)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer()
    await query.edit_message_text(
        creature_card_text(user, creature, equipped_items),
        parse_mode="HTML",
        reply_markup=_creature_detail_keyboard(creature.id, creature.is_active),
    )


def _select_sync(tg_user, creature_id):
    user, _ = get_or_create_user(tg_user)
    creature = set_active_creature(user, creature_id)
    return user, creature, get_equipped_items(creature)


async def collection_select_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    creature_id = int(query.data.split(":")[1])
    try:
        user, creature, equipped_items = await run_db(_select_sync, update.effective_user, creature_id)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    is_owner = update.effective_user.id == OWNER_TELEGRAM_ID
    await query.answer("🟢 انتخاب شد!")
    await query.edit_message_text(
        f"🟢 <b>{creature.name}</b> حالا موجود فعالته!\n\n" + creature_card_text(user, creature, equipped_items),
        parse_mode="HTML",
        reply_markup=creature_keyboard(is_owner),
    )


async def select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Kept registered as a power-user shortcut — the advertised path is
    /collection's buttons (collection_pick_callback -> collection_select_callback)."""
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(f"{get_emoji('collection')} برای انتخاب موجود از /collection استفاده کن.")
        return
    try:
        user, creature, equipped_items = await run_db(_select_sync, update.effective_user, int(context.args[0]))
    except GameError as exc:
        await update.message.reply_text(str(exc))
        return
    is_owner = update.effective_user.id == OWNER_TELEGRAM_ID
    await update.message.reply_text(
        f"🟢 <b>{creature.name}</b> حالا موجود فعالته!\n\n" + creature_card_text(user, creature, equipped_items),
        parse_mode="HTML",
        reply_markup=creature_keyboard(is_owner),
    )


def _fusion_sync(tg_user, id_a, id_b):
    user, _ = get_or_create_user(tg_user)
    try:
        parent_a = Creature.objects.get(id=id_a)
        parent_b = Creature.objects.get(id=id_b)
    except Creature.DoesNotExist:
        raise GameError("یکی از این شماره‌ها پیدا نشد.")
    child, inherited_item = fuse(user, parent_a, parent_b)
    record_action(user, "fusion")
    completed_missions = check_missions(user, "fusion")
    return user, child, completed_missions, get_equipped_items(child), inherited_item is not None


async def fusion_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) != 2 or not all(a.isdigit() for a in context.args):
        await update.message.reply_text(
            f"استفاده درست: <code>/fusion 3 5</code> (دو شماره‌ی موجود از /collection — {constants.FUSION_GOLD_COST} طلا هزینه داره و والدین سوزانده می‌شن)",
            parse_mode="HTML",
        )
        return
    try:
        user, child, completed_missions, equipped_items, inherited = await run_db(
            _fusion_sync, update.effective_user, int(context.args[0]), int(context.args[1])
        )
    except GameError as exc:
        await update.message.reply_text(str(exc))
        return
    is_owner = update.effective_user.id == OWNER_TELEGRAM_ID
    inherit_note = "\n🧬 یه تجهیزات از والدین به ارث رسید!" if inherited else ""
    await update.message.reply_text(
        f"{get_emoji('lab')} <b>فیوژن موفق بود!</b> والدین سوزانده شدن و یه موجود جدید متولد شد:{inherit_note}\n\n"
        + creature_card_text(user, child, equipped_items)
        + _mission_lines(completed_missions),
        parse_mode="HTML",
        reply_markup=creature_keyboard(is_owner),
    )


def _fusion_candidates_sync(tg_user, exclude_id):
    user, _ = get_or_create_user(tg_user)
    return [c for c in list_creatures(user) if c.id != exclude_id]


async def fusion_pick_a_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    parent_a_id = int(query.data.split(":")[1])
    candidates = await run_db(_fusion_candidates_sync, update.effective_user, parent_a_id)
    if not candidates:
        await query.answer("برای فیوژن حداقل به یه موجود دیگه نیاز داری.", show_alert=True)
        return
    await query.answer()
    rows = [
        [InlineKeyboardButton(f"{c.name} · Lv{c.level}", callback_data=f"fus_b:{parent_a_id}:{c.id}")]
        for c in candidates
    ]
    rows.append([InlineKeyboardButton("◀️ بازگشت", callback_data=f"coll_pick:{parent_a_id}")])
    await query.edit_message_text(
        f"{get_emoji('lab')} موجود دومی که می‌خوای بسوزونی رو انتخاب کن:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def fusion_pick_b_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, a_id, b_id = query.data.split(":")
    await query.answer()
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🟢 تأیید فیوژن", callback_data=f"fus_confirm:{a_id}:{b_id}"),
                InlineKeyboardButton("🔴 لغو", callback_data=f"coll_pick:{a_id}"),
            ]
        ]
    )
    await query.edit_message_text(
        f"{get_emoji('warning')} مطمئنی؟\n\n"
        f"<blockquote>هر دو موجود <b>برای همیشه سوزانده می‌شن</b> و "
        f"{constants.FUSION_GOLD_COST} {get_emoji('coin')} هزینه می‌شه. یه شانس هم هست rarity ارتقا پیدا کنه "
        f"و {int(constants.FUSION_INHERIT_CHANCE * 100)}٪ احتمال داره تجهیزات مجهزشون به ارث برسه.</blockquote>",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


async def fusion_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, a_id, b_id = query.data.split(":")
    try:
        user, child, completed_missions, equipped_items, inherited = await run_db(
            _fusion_sync, update.effective_user, int(a_id), int(b_id)
        )
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    is_owner = update.effective_user.id == OWNER_TELEGRAM_ID
    inherit_note = "\n🧬 یه تجهیزات از والدین به ارث رسید!" if inherited else ""
    await query.answer("🟢 فیوژن موفق بود!")
    await query.edit_message_text(
        f"{get_emoji('lab')} <b>فیوژن موفق بود!</b> والدین سوزانده شدن و یه موجود جدید متولد شد:\n\n"
        f"<tg-spoiler>{constants.RARITY_LABELS[child.rarity]}{inherit_note}</tg-spoiler>\n\n"
        + creature_card_text(user, child, equipped_items)
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


def _alliance_action_keyboard(in_alliance: bool) -> InlineKeyboardMarkup:
    if in_alliance:
        rows = [
            [InlineKeyboardButton("💰 واریز به خزانه", callback_data="ally_deposit")],
            [InlineKeyboardButton("🏴‍☠️ شبیخون به اتحاد دیگه", callback_data="ally_heist_list")],
            [InlineKeyboardButton("🏆 برترین اتحادها", callback_data="ally_top")],
            [InlineKeyboardButton("🔴 خروج از اتحاد", callback_data="ally_leave")],
        ]
    else:
        rows = [
            [InlineKeyboardButton("🟢 ساخت اتحاد جدید", callback_data="ally_create")],
            [InlineKeyboardButton("🔵 پیوستن به اتحاد", callback_data="ally_join")],
            [InlineKeyboardButton("🏆 برترین اتحادها", callback_data="ally_top")],
        ]
    rows.append([InlineKeyboardButton("◀️ بازگشت", callback_data="menu:me")])
    return InlineKeyboardMarkup(rows)


async def alliance_info_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    info = await run_db(_alliance_info_sync, update.effective_user)
    if info is None:
        await update.effective_message.reply_text(
            f"{get_emoji('alliance')} توی هیچ اتحادی نیستی.",
            parse_mode="HTML",
            reply_markup=_alliance_action_keyboard(in_alliance=False),
        )
        return
    lines = [
        f"{get_emoji('alliance')} <b>اتحاد {info['name']}</b>",
        f"{get_emoji('crown')} رهبر: {display_name(info['leader']) if info['leader'] else '—'}",
        f"{get_emoji('users')} اعضا: {info['member_count']}   💪 قدرت کل: {info['power']}",
        f"{get_emoji('coin')} خزانه: {info['treasury_gold']} طلا",
        "",
    ]
    for m in info["members"][:20]:
        lines.append(f"• {display_name(m)}")
    await update.effective_message.reply_text(
        "\n".join(lines), parse_mode="HTML", reply_markup=_alliance_action_keyboard(in_alliance=True)
    )


AWAITING_PLAYER_KEY = "awaiting_player_input"


async def alliance_create_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    context.user_data[AWAITING_PLAYER_KEY] = {"action": "alliance_create"}
    await query.answer()
    await query.edit_message_text(f"🟢 {get_emoji('alliance')} اسم اتحاد جدیدت رو بفرست:", parse_mode="HTML")


async def alliance_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    context.user_data[AWAITING_PLAYER_KEY] = {"action": "alliance_join"}
    await query.answer()
    await query.edit_message_text(
        f"🔵 {get_emoji('alliance')} اسم اتحادی که می‌خوای بهش بپیوندی رو بفرست:", parse_mode="HTML"
    )


async def alliance_deposit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    context.user_data[AWAITING_PLAYER_KEY] = {"action": "alliance_deposit"}
    await query.answer()
    await query.edit_message_text(
        f"💰 چند {get_emoji('coin')} طلا می‌خوای به خزانه واریز کنی؟ یه عدد بفرست:", parse_mode="HTML"
    )


async def alliance_top_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    ranked = await run_db(_alliance_top_sync)
    await query.answer()
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ بازگشت", callback_data="menu:alliance_info")]])
    if not ranked:
        await query.edit_message_text("هنوز هیچ اتحادی ساخته نشده.", reply_markup=keyboard)
        return
    medals = [get_emoji("medal_gold"), get_emoji("medal_silver"), get_emoji("medal_bronze")]
    lines = [f"{get_emoji('trophy')} <b>برترین اتحادها</b>\n"]
    for i, r in enumerate(ranked, start=1):
        rank = medals[i - 1] if i <= 3 else f"{i}."
        lines.append(f"{rank} {r['alliance'].name} — قدرت {r['power']} ({r['member_count']} عضو)")
    await query.edit_message_text("\n".join(lines), parse_mode="HTML", reply_markup=keyboard)


async def alliance_leave_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🔴 بله، خارج شو", callback_data="ally_leave_confirm"),
                InlineKeyboardButton("❌ بی‌خیال", callback_data="menu:alliance_info"),
            ]
        ]
    )
    await query.answer()
    await query.edit_message_text("مطمئنی می‌خوای از اتحادت خارج بشی؟", reply_markup=keyboard)


async def alliance_leave_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        await run_db(_alliance_leave_sync, update.effective_user)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer("👋 خارج شدی.")
    await query.edit_message_text("👋 از اتحاد خارج شدی.")


def _heist_targets_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    if user.alliance_id is None:
        raise GameError("اول باید عضو یه اتحاد باشی.")
    return list(Alliance.objects.exclude(id=user.alliance_id).order_by("name"))


async def alliance_heist_list_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        targets = await run_db(_heist_targets_sync, update.effective_user)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    if not targets:
        await query.answer("هیچ اتحاد دیگه‌ای برای شبیخون نیست.", show_alert=True)
        return
    await query.answer()
    rows = [[InlineKeyboardButton(f"🏴‍☠️ {a.name}", callback_data=f"heist_pick:{a.id}")] for a in targets]
    rows.append([InlineKeyboardButton("◀️ بازگشت", callback_data="menu:alliance_info")])
    await query.edit_message_text(
        f"🏴‍☠️ کدوم اتحاد رو غارت کنم؟ ({int(constants.HEIST_STEAL_PERCENT * 100)}٪ خزانه در صورت برد)",
        reply_markup=InlineKeyboardMarkup(rows),
    )


def _heist_by_id_sync(tg_user, target_alliance_id):
    user, _ = get_or_create_user(tg_user)
    creature = get_active_creature(user)
    if creature is None:
        raise GameError("اول یه موجود فعال انتخاب کن.")
    try:
        target = Alliance.objects.get(id=target_alliance_id)
    except Alliance.DoesNotExist:
        raise GameError("این اتحاد دیگه پیدا نشد.")
    assert_energy_available(user, "heist")
    result = heist(user, creature, target)
    record_action(user, "heist")
    return result, target


async def heist_pick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    target_id = int(query.data.split(":")[1])
    try:
        result, target = await run_db(_heist_by_id_sync, update.effective_user, target_id)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer("🟢 انجام شد!" if result["success"] else "🔴 شکست خوردی.")
    lines = []
    if result["defender_creature"] is not None:
        lines.append(result["log_text"])
    if result["success"]:
        reveal = (
            f"{get_emoji('celebrate')} <b>شبیخون موفق بود!</b> {result['stolen']} {get_emoji('coin')} از خزانه‌ی "
            f"<b>{target.name}</b> دزدیدی!"
        )
    else:
        reveal = f"😔 نگهبان‌های <b>{target.name}</b> دفاع کردن و شبیخونت شکست خورد."
    lines.append(f"<tg-spoiler>{reveal}</tg-spoiler>")
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ بازگشت", callback_data="menu:alliance_info")]])
    await query.edit_message_text("\n\n".join(lines), parse_mode="HTML", reply_markup=keyboard)


async def capture_player_text_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Single dispatcher for every 'awaiting a plain-text reply' player flow (alliance
    name, deposit amount) — PTB only runs the first handler that matches an update
    within a group, so this and owner.capture_owner_text_reply are combined into one
    MessageHandler registration in bot/main.py rather than each registering their own."""
    awaiting = context.user_data.pop(AWAITING_PLAYER_KEY, None)
    if awaiting is None:
        return
    message = update.effective_message
    action = awaiting["action"]
    text = (message.text or "").strip()

    if action == "alliance_create":
        try:
            alliance = await run_db(_alliance_create_sync, update.effective_user, text)
        except GameError as exc:
            context.user_data[AWAITING_PLAYER_KEY] = awaiting
            await message.reply_text(str(exc))
            return
        await message.reply_text(
            f"{get_emoji('alliance')} اتحاد <b>{alliance.name}</b> ساخته شد! تو رهبرشی {get_emoji('crown')}",
            parse_mode="HTML",
        )
        return

    if action == "alliance_join":
        try:
            alliance = await run_db(_alliance_join_sync, update.effective_user, text)
        except GameError as exc:
            context.user_data[AWAITING_PLAYER_KEY] = awaiting
            await message.reply_text(str(exc))
            return
        await message.reply_text(
            f"{get_emoji('alliance')} به اتحاد <b>{alliance.name}</b> پیوستی!", parse_mode="HTML"
        )
        return

    if action == "alliance_deposit":
        if not text.isdigit() or int(text) <= 0:
            context.user_data[AWAITING_PLAYER_KEY] = awaiting
            await message.reply_text("⚠️ یه عدد مثبت بفرست.")
            return
        try:
            alliance = await run_db(_alliance_deposit_sync, update.effective_user, int(text))
        except GameError as exc:
            await message.reply_text(str(exc))
            return
        await message.reply_text(
            f"{get_emoji('coin')} به خزانه‌ی <b>{alliance.name}</b> واریز شد! خزانه فعلی: "
            f"{alliance.treasury_gold} طلا",
            parse_mode="HTML",
        )
        return


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


def _alliance_deposit_sync(tg_user, amount):
    user, _ = get_or_create_user(tg_user)
    return deposit_treasury(user, amount)


async def alliance_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args or not context.args[0].isdigit() or int(context.args[0]) <= 0:
        await update.message.reply_text(
            "استفاده درست: <code>/alliance_deposit 100</code>", parse_mode="HTML"
        )
        return
    try:
        alliance = await run_db(_alliance_deposit_sync, update.effective_user, int(context.args[0]))
    except GameError as exc:
        await update.message.reply_text(str(exc))
        return
    await update.message.reply_text(
        f"{get_emoji('coin')} به خزانه‌ی <b>{alliance.name}</b> واریز شد! خزانه فعلی: {alliance.treasury_gold} طلا",
        parse_mode="HTML",
    )


def _heist_sync(tg_user, target_name):
    user, _ = get_or_create_user(tg_user)
    creature = get_active_creature(user)
    if creature is None:
        raise GameError("اول /start رو بزن تا موجودت رو بگیری.")
    target = Alliance.objects.filter(name__iexact=target_name.strip()).first()
    if target is None:
        raise GameError("همچین اتحادی پیدا نشد.")

    assert_energy_available(user, "heist")
    result = heist(user, creature, target)
    record_action(user, "heist")
    return result, target


async def heist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text(
            f"استفاده درست: <code>/heist اسم اتحاد</code> — روزی {constants.HEIST_DAILY_ATTEMPTS} بار مجازی، "
            f"{int(constants.HEIST_STEAL_PERCENT * 100)}٪ خزانه رو می‌بری اگه ببری.",
            parse_mode="HTML",
        )
        return
    try:
        result, target = await run_db(_heist_sync, update.effective_user, " ".join(context.args))
    except GameError as exc:
        await update.message.reply_text(str(exc))
        return

    if result["defender_creature"] is not None:
        await update.message.reply_text(result["log_text"], parse_mode="HTML")

    if result["success"]:
        await update.message.reply_text(
            f"{get_emoji('celebrate')} <b>شبیخون موفق بود!</b> {result['stolen']} {get_emoji('coin')} از خزانه‌ی "
            f"<b>{target.name}</b> دزدیدی!",
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text(
            f"😔 نگهبان‌های <b>{target.name}</b> دفاع کردن و شبیخونت شکست خورد.", parse_mode="HTML"
        )


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
        f"{get_emoji('coin')} طلای فعلی: {user.coins}   {get_emoji('dna')} DNA فعلی: {user.dna_fragments}",
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
                InlineKeyboardButton("🎒 تجهیزات", callback_data="menu:inventory"),
                InlineKeyboardButton("📦 باکس ژنتیکی", callback_data="menu:biocrate"),
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
    "inventory": inventory_cmd,
    "biocrate": biocrate_cmd,
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
    application.add_handler(CommandHandler("fusion", fusion_cmd, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("missions", missions, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("hunt", hunt, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("alliance_create", alliance_create, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("alliance_join", alliance_join, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("alliance_leave", alliance_leave, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("alliance_info", alliance_info_cmd, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("alliance_top", alliance_top, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("alliance_deposit", alliance_deposit, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("heist", heist_cmd, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("rank", rank, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("profile", profile, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("menu", menu, filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(menu_callback, pattern=r"^menu:"))
    application.add_handler(CallbackQueryHandler(collection_pick_callback, pattern=r"^coll_pick:"))
    application.add_handler(CallbackQueryHandler(collection_select_callback, pattern=r"^coll_select:"))
    application.add_handler(CallbackQueryHandler(fusion_pick_a_callback, pattern=r"^fus_a:"))
    application.add_handler(CallbackQueryHandler(fusion_pick_b_callback, pattern=r"^fus_b:"))
    application.add_handler(CallbackQueryHandler(fusion_confirm_callback, pattern=r"^fus_confirm:"))
    application.add_handler(CallbackQueryHandler(alliance_create_callback, pattern=r"^ally_create$"))
    application.add_handler(CallbackQueryHandler(alliance_join_callback, pattern=r"^ally_join$"))
    application.add_handler(CallbackQueryHandler(alliance_deposit_callback, pattern=r"^ally_deposit$"))
    application.add_handler(CallbackQueryHandler(alliance_top_callback, pattern=r"^ally_top$"))
    application.add_handler(
        CallbackQueryHandler(alliance_leave_confirm_callback, pattern=r"^ally_leave_confirm$")
    )
    application.add_handler(CallbackQueryHandler(alliance_leave_callback, pattern=r"^ally_leave$"))
    application.add_handler(CallbackQueryHandler(alliance_heist_list_callback, pattern=r"^ally_heist_list$"))
    application.add_handler(CallbackQueryHandler(heist_pick_callback, pattern=r"^heist_pick:"))

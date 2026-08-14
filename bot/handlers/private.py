from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

from bio_lab.models import Alliance, Creature
from bio_lab.repository import display_name, get_active_creature, get_or_create_user
from bot.handlers.arena import arena_panel
from bot.handlers.buildings import buildings_panel
from bot.handlers.inventory import blacksmith_panel, inventory_cmd
from bot.handlers.lootbox import biocrate_cmd, diamond_box_panel
from bot.handlers.owner import admin_cmd
from bot.handlers.wheel import wheel_cmd
from bot.buttons import DANGER, PRIMARY, SUCCESS, back_btn, btn
from bot.utils import mission_reward_text, run_db, safe_edit_message_text
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
from game.buildings import get_or_create_buildings, grant_speedup_card
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
from game.hunt import HUNT_TIERS, estimated_reward, resolve_hunt, scout_one


def _mission_lines(completed: list[dict]) -> str:
    if not completed:
        return ""
    lines = [
        f"{get_emoji('mission')} ماموریت «{m['label']}» تکمیل شد! {mission_reward_text(m)}"
        for m in completed
    ]
    return "\n" + "\n".join(lines)


def wallet_line(user, energy: int | None = None) -> str:
    """One compact resource strip, reused by every screen that needs it."""
    energy = sync_energy(user) if energy is None else energy
    # one resource per line: crammed onto a single row the numbers ran together and
    # it was impossible to tell which figure belonged to which currency
    return (
        f"{get_emoji('coin')} طلا: <b>{user.coins:,}</b>\n"
        f"{get_emoji('dna')} DNA: <b>{user.dna_fragments:,}</b>\n"
        f"{get_emoji('diamond')} الماس: <b>{user.diamonds:,}</b>\n"
        f"{get_emoji('energy')} انرژی: <b>{energy}</b>/{constants.MAX_ENERGY}"
    )


def creature_card_text(user, creature, equipped_items: list | None = None) -> str:
    """Deliberately kept short: identity, one stat row, XP, wallet. Body-part levels
    live on the separate «ارتقا» screen (upgrade_panel) so this doesn't turn into a
    wall of numbers on every single /start.

    Every value is labelled — an unlabelled row of emoji+number reads as noise once
    there are more than three of them."""
    stats = effective_stats(creature, equipped_items)
    energy = sync_energy(user)
    xp_bar = constants.render_bar(creature.xp, constants.XP_PER_LEVEL, width=10)
    stars = get_emoji("star") * creature.star_level

    lines = [
        f"{get_emoji('creature')} <b>{creature.name}</b>  <code>#{creature.id}</code>",
        f"{constants.RARITY_LABELS[creature.rarity]} {stars}",
        f"{constants.element_label(creature.element)}",
        "",
        f"📊 سطح <b>{creature.level}</b>",
        f"{xp_bar}  {creature.xp}/{constants.XP_PER_LEVEL} XP",
        "",
        "⚔️ <b>توانایی‌ها</b>",
        f"{get_emoji('hp')} جان: <b>{stats['hp']}</b>      {get_emoji('atk')} حمله: <b>{stats['atk']}</b>",
        f"{get_emoji('def')} دفاع: <b>{stats['def']}</b>      {get_emoji('spd')} سرعت: <b>{stats['spd']}</b>",
    ]
    if equipped_items:
        lines.append("")
        lines.append("🎒 <b>تجهیزات</b>")
        for i in equipped_items:
            lines.append(f"{constants.EQUIPMENT_SLOT_LABELS[i.slot]} {i.name} <b>+{i.level}</b>")
    lines.append("")
    lines.append("━━━━━━━━━━")
    lines.append(wallet_line(user, energy))
    return "\n".join(lines)


def upgrade_panel_text(user, creature, equipped_items: list | None = None) -> str:
    stats = effective_stats(creature, equipped_items)
    stars = get_emoji("star") * creature.star_level
    lines = [
        f"🔧 <b>ارتقای {creature.name}</b>",
        f"{constants.RARITY_LABELS[creature.rarity]} {stars} · سطح {creature.level}",
        "",
        f"{get_emoji('hp')} جان: <b>{stats['hp']}</b>      {get_emoji('atk')} حمله: <b>{stats['atk']}</b>",
        f"{get_emoji('def')} دفاع: <b>{stats['def']}</b>      {get_emoji('spd')} سرعت: <b>{stats['spd']}</b>",
        f"{get_emoji('poison')} زهر: <b>{stats['poison']}</b>",
        "",
        "🧩 <b>اعضای قابل ارتقا</b>",
    ]
    for part, cfg in constants.BODY_PARTS.items():
        level = getattr(creature, f"{part}_lvl")
        cost = constants.upgrade_cost(level)
        lines.append(f"{cfg['label']} — سطح <b>{level}</b> · ارتقا: {cost} {get_emoji('coin')}")
    lines.append("")
    lines.append(wallet_line(user))
    lines.append("\n<blockquote>تغذیه و تمرین XP می‌دن؛ ارتقای اعضا مستقیم استت اضافه می‌کنه.</blockquote>")
    return "\n".join(lines)


def upgrade_panel_keyboard(creature_id: int) -> InlineKeyboardMarkup:
    """Every action carries the creature id, so upgrading a non-active creature
    doesn't silently swap which creature is active for hunting/arena."""
    rows = [
        [
            btn("تغذیه", emoji_key="btn_feed", style=SUCCESS, callback_data=f"lab:feed:{creature_id}"),
            btn("تمرین", emoji_key="btn_train", style=SUCCESS, callback_data=f"lab:train:{creature_id}"),
        ],
        [
            btn("🦋 بال", style=PRIMARY, callback_data=f"lab:up_wings:{creature_id}"),
            btn("🛡 زره", style=PRIMARY, callback_data=f"lab:up_armor:{creature_id}"),
        ],
        [
            btn("🦷 نیش", style=PRIMARY, callback_data=f"lab:up_fangs:{creature_id}"),
            btn("☠️ زهر", style=PRIMARY, callback_data=f"lab:up_poison:{creature_id}"),
        ],
        [back_btn("menu:upgrade", "لیست هیولاها")],
    ]
    return InlineKeyboardMarkup(rows)


def _creature_power(creature, equipped_items: list | None = None) -> int:
    stats = effective_stats(creature, equipped_items)
    return round(stats["hp"] + stats["atk"] + stats["def"] + stats["spd"])


def _upgrade_list_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    creatures = list_creatures(user)
    if not creatures:
        raise GameError("اول /start رو بزن تا موجودت رو بگیری.")
    ranked = sorted(
        ((c, _creature_power(c, get_equipped_items(c))) for c in creatures),
        key=lambda pair: pair[1],
        reverse=True,
    )
    return user, ranked


async def upgrade_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Creature picker, strongest first — the player chooses who to invest in
    rather than the panel silently assuming the active creature."""
    try:
        user, ranked = await run_db(_upgrade_list_sync, update.effective_user)
    except GameError as exc:
        await update.effective_message.reply_text(str(exc))
        return

    rows = []
    for creature, power in ranked:
        active_tag = "🟢 " if creature.is_active else ""
        stars = "⭐" * creature.star_level
        # RARITY_LABELS already carries its own colour dot, which is the fastest way
        # to read rarity at a glance in a long list
        rarity = constants.RARITY_LABELS[creature.rarity]
        rows.append(
            [
                btn(
                    f"{active_tag}{creature.name} {stars} · {rarity} · Lv{creature.level} · 💪{power}",
                    callback_data=f"upg_pick:{creature.id}",
                )
            ]
        )
    rows.append([back_btn("menu:me")])

    await update.effective_message.reply_text(
        f"🔧 <b>ارتقا و پرورش</b>\n"
        f"هیولاهات به ترتیب قدرت مرتب شدن — کدوم رو می‌خوای قوی‌تر کنی؟\n\n{wallet_line(user)}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(rows),
    )


def _upgrade_pick_sync(tg_user, creature_id):
    user, _ = get_or_create_user(tg_user)
    try:
        creature = Creature.objects.get(id=creature_id, owner=user)
    except Creature.DoesNotExist:
        raise GameError("این موجود توی کلکسیون تو نیست.")
    return user, creature, get_equipped_items(creature)


async def upgrade_pick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    creature_id = int(query.data.split(":")[1])
    try:
        user, creature, equipped_items = await run_db(_upgrade_pick_sync, update.effective_user, creature_id)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer()
    await safe_edit_message_text(
        query,
        upgrade_panel_text(user, creature, equipped_items),
        parse_mode="HTML",
        reply_markup=upgrade_panel_keyboard(creature.id),
    )


def creature_keyboard(is_owner: bool = False) -> InlineKeyboardMarkup:
    """Navigation keyboard under the creature card. Body-part upgrades used to live
    here too, but they made both the card and this keyboard unreadable — they're on
    the «🔧 ارتقا» screen now (upgrade_panel)."""
    rows = [
        [btn("ارتقا و پرورش", emoji_key="btn_upgrade", style=PRIMARY, callback_data="menu:upgrade")],
        [
            btn("کلکسیون", emoji_key="btn_collection", style=PRIMARY, callback_data="menu:collection"),
            btn("شکار انفرادی", emoji_key="btn_hunt", style=PRIMARY, callback_data="menu:hunt"),
        ],
        [
            btn("آرنا (کاپ)", emoji_key="btn_arena", style=DANGER, callback_data="menu:arena"),
            btn("تجهیزات", emoji_key="btn_inventory", style=PRIMARY, callback_data="menu:inventory"),
        ],
        [
            btn("آهنگری", emoji_key="btn_forge", style=PRIMARY, callback_data="menu:blacksmith"),
            btn("باکس ژنتیکی", emoji_key="btn_biocrate", style=SUCCESS, callback_data="menu:biocrate"),
        ],
        [
            btn("جعبه‌های الماسی", emoji_key="btn_diamond_box", style=SUCCESS, callback_data="menu:diamond_box"),
            btn("ساختمون‌ها", emoji_key="btn_buildings", style=PRIMARY, callback_data="menu:buildings"),
        ],
        [
            btn("ماموریت‌ها", emoji_key="btn_missions", style=PRIMARY, callback_data="menu:missions"),
            btn("گردونه‌ی شانس", emoji_key="btn_wheel", style=SUCCESS, callback_data="menu:wheel"),
        ],
        [btn("اتحاد من", emoji_key="btn_alliance", style=PRIMARY, callback_data="menu:alliance_info")],
        [
            btn("رتبه‌بندی", emoji_key="btn_rank", style=PRIMARY, callback_data="menu:rank"),
            btn("پروفایل من", emoji_key="btn_profile", style=PRIMARY, callback_data="menu:profile"),
        ],
    ]
    if is_owner:
        rows.append([btn("پنل ادمین", emoji_key="btn_admin", style=DANGER, callback_data="menu:admin")])
    return InlineKeyboardMarkup(rows)


LAB_NAME_MAX_LEN = 32


def _start_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    creature = get_active_creature(user)
    is_new = False
    if creature is None:
        creature = create_starter_creature(user)
        is_new = True
        # starter build-timer cards — the first main-hall upgrades are the slowest
        # part of a new player's first session, so hand them enough to skip past it
        for minutes, count in constants.STARTING_SPEEDUP_CARDS.items():
            grant_speedup_card(user, minutes, count=count)
    login_bonus = apply_daily_login(user)
    equipped_items = get_equipped_items(creature)
    get_or_create_buildings(user)
    return user, creature, is_new, login_bonus, equipped_items


def _set_lab_name_sync(tg_user, name):
    user, _ = get_or_create_user(tg_user)
    user.lab_name = name
    user.save(update_fields=["lab_name"])
    creature = get_active_creature(user)
    return user, creature, get_equipped_items(creature) if creature else []


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user, creature, is_new, login_bonus, equipped_items = await run_db(_start_sync, update.effective_user)

    if user.lab_name is None:
        context.user_data[AWAITING_PLAYER_KEY] = {"action": "set_lab_name"}
        await update.message.reply_text(
            f"{get_emoji('egg')} <b>به Kaiju Bio-Lab خوش اومدی!</b>\n"
            "قبل از هرچیزی، اسم آزمایشگاهت رو انتخاب کن — همینو بفرست:",
            parse_mode="HTML",
        )
        return

    lines = []
    if is_new:
        lines.append(
            f"{get_emoji('egg')} <b>آزمایشگاه «{user.lab_name}» فعال شد!</b>\n"
            "یه موجود تازه از کپسول زیستی بیرون اومد — بهش خوش‌آمد بگو 👇\n"
        )
    else:
        lines.append(f"👋 <b>به آزمایشگاه «{user.lab_name}» خوش برگشتی!</b>\n")

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


def _lab_action_sync(tg_user, action, creature_id):
    user, _ = get_or_create_user(tg_user)
    try:
        creature = Creature.objects.get(id=creature_id, owner=user)
    except Creature.DoesNotExist:
        raise GameError("این موجود توی کلکسیون تو نیست.")

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
    elif action.startswith("up_"):
        part = action[len("up_") :]
        new_level = upgrade_part(user, creature, part)
        note = f"{constants.BODY_PARTS[part]['label']} به سطح {new_level} ارتقا یافت! ✨"
    else:
        return None

    equipped_items = get_equipped_items(creature)
    return user, creature, note + _mission_lines(completed_missions), equipped_items


async def lab_action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        _, action, creature_id = query.data.split(":")
    except ValueError:
        await query.answer()
        return
    try:
        result = await run_db(_lab_action_sync, update.effective_user, action, int(creature_id))
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    if result is None:
        await query.answer()
        return

    user, creature, note, equipped_items = result
    await query.answer()
    # every lab action is reachable only from the upgrade panel now, so re-render
    # that rather than bouncing the player back to the creature card
    await safe_edit_message_text(query,
        note + "\n\n" + upgrade_panel_text(user, creature, equipped_items),
        parse_mode="HTML",
        reply_markup=upgrade_panel_keyboard(creature.id),
    )


def _collection_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    return list_creatures(user)


def _collection_keyboard(creatures: list[Creature]) -> InlineKeyboardMarkup:
    rows = []
    for c in creatures:
        tag = "🟢 " if c.is_active else ""
        rows.append(
            [btn(f"{tag}{c.name} · Lv{c.level} · {constants.RARITY_LABELS[c.rarity]}", callback_data=f"coll_pick:{c.id}")]
        )
    rows.append([back_btn("menu:me")])
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
        rows.append([btn("انتخاب به‌عنوان موجود فعال", emoji_key="btn_confirm", style=SUCCESS, callback_data=f"coll_select:{creature_id}")])
    rows.append([btn("استفاده در فیوژن", emoji_key="btn_fusion", style=PRIMARY, callback_data=f"fus_a:{creature_id}")])
    rows.append([back_btn("menu:collection", "بازگشت به کلکسیون")])
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
    await safe_edit_message_text(query,
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
    await safe_edit_message_text(query,
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
        [btn(f"{c.name} · Lv{c.level}", callback_data=f"fus_b:{parent_a_id}:{c.id}")]
        for c in candidates
    ]
    rows.append([back_btn(f"coll_pick:{parent_a_id}")])
    await safe_edit_message_text(query,
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
                btn("تأیید فیوژن", emoji_key="btn_confirm", style=SUCCESS, callback_data=f"fus_confirm:{a_id}:{b_id}"),
                btn("لغو", emoji_key="btn_cancel", style=DANGER, callback_data=f"coll_pick:{a_id}"),
            ]
        ]
    )
    await safe_edit_message_text(query,
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
    await safe_edit_message_text(query,
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
        lines.append(f"{check} — {m['label']} ({mission_reward_text(m)})")
    lines.append("\n<i>ماموریت‌ها هر روز (ساعت جهانی UTC) ریست می‌شن.</i>")
    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")


def _hunt_scout_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    creature = get_active_creature(user)
    if creature is None:
        raise GameError("اول /start رو بزن تا موجودت رو بگیری.")
    my_stats = effective_stats(creature, get_equipped_items(creature))
    my_power = round(my_stats["hp"] + my_stats["atk"] + my_stats["def"] + my_stats["spd"])
    return creature, my_power, scout_one(creature), sync_energy(user)


def _hunt_scout_text(creature, my_power, target, energy) -> str:
    tier_label = HUNT_TIERS[target["tier"]]["label"]
    lo, hi = estimated_reward(target["tier"], creature.level)
    diff = target["power"] - my_power
    odds = "🟢 شانس بالا" if diff < -15 else ("🔴 خطرناک" if diff > 15 else "🟡 سرتاسری")
    return "\n".join(
        [
            f"{get_emoji('hunt')} <b>شکار انفرادی</b>",
            f"موجودت: <b>{creature.name}</b> · 💪 {my_power}",
            f"{get_emoji('energy')} {energy}/{constants.MAX_ENERGY}\n",
            "🔍 <b>یه حریف پیدا شد:</b>\n",
            f"{tier_label} <b>{target['name']}</b> {constants.element_label(target['element'])}",
            f"💪 قدرت: <b>{target['power']}</b>  ({odds})",
            f"{get_emoji('coin')} غنیمت: {lo}–{hi}",
            f"\n<blockquote>حمله {constants.HUNT_ENERGY_COST} انرژی می‌بره. "
            "جستجوی دوباره رایگانه — تا وقتی حریف دلخواهت رو پیدا نکردی «بعدی» بزن.</blockquote>",
        ]
    )


def _hunt_scout_keyboard(target) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [btn("حمله!", emoji_key="btn_attack", style=DANGER, callback_data=f"hunt_go:{target['tier']}:{target['seed']}")],
            [btn("🔍 بعدی", style=PRIMARY, callback_data="hunt_next")],
            [back_btn("menu:me")],
        ]
    )


async def hunt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scouting step: shows ONE opponent at a time with its power and payout so the
    player can judge the risk *before* any energy is spent. Searching again is free
    ("بعدی"); only committing to a fight costs energy."""
    try:
        creature, my_power, target, energy = await run_db(_hunt_scout_sync, update.effective_user)
    except GameError as exc:
        await update.effective_message.reply_text(str(exc))
        return
    await update.effective_message.reply_text(
        _hunt_scout_text(creature, my_power, target, energy),
        parse_mode="HTML",
        reply_markup=_hunt_scout_keyboard(target),
    )


async def hunt_next_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        creature, my_power, target, energy = await run_db(_hunt_scout_sync, update.effective_user)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer("🔍 جستجوی دوباره…")
    await safe_edit_message_text(
        query,
        _hunt_scout_text(creature, my_power, target, energy),
        parse_mode="HTML",
        reply_markup=_hunt_scout_keyboard(target),
    )


def _hunt_go_sync(tg_user, tier, seed):
    user, _ = get_or_create_user(tg_user)
    creature = get_active_creature(user)
    if creature is None:
        raise GameError("اول /start رو بزن تا موجودت رو بگیری.")

    spend_energy(user, constants.HUNT_ENERGY_COST, "شکار")
    user.save(update_fields=["energy", "energy_updated_at"])

    result = resolve_hunt(user, creature, tier, seed)
    record_action(user, "hunt")
    completed_missions = check_missions(user, "hunt")
    return creature, result, completed_missions


async def hunt_go_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, tier, seed = query.data.split(":")
    try:
        creature, result, completed_missions = await run_db(
            _hunt_go_sync, update.effective_user, tier, int(seed)
        )
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return

    if result["won"]:
        reward_line = (
            f"{get_emoji('celebrate')} <b>بردی!</b> +{result['coins']} {get_emoji('coin')}"
            + (f" +{result['dna']} {get_emoji('dna')}" if result["dna"] else "")
            + f" +{result['xp']} XP"
        )
        if result["levels"]:
            reward_line += f" · رسید به سطح {creature.level}!"
    else:
        reward_line = f"😔 باختی... +{result['xp']} XP تسلی‌بخش گرفتی."
    reward_line += _mission_lines(completed_missions)

    keyboard = InlineKeyboardMarkup(
        [
            [btn("شکار دوباره", emoji_key="btn_hunt", style=PRIMARY, callback_data="hunt_next")],
            [back_btn("menu:me")],
        ]
    )
    await query.answer("🟢 بردی!" if result["won"] else "🔴 باختی.")
    await safe_edit_message_text(
        query,
        result["log_text"] + "\n\n" + f"<tg-spoiler>{reward_line}</tg-spoiler>",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


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
            [btn("واریز به خزانه", emoji_key="btn_deposit", style=SUCCESS, callback_data="ally_deposit")],
            [btn("شبیخون به اتحاد دیگه", emoji_key="btn_heist", style=DANGER, callback_data="ally_heist_list")],
            [btn("برترین اتحادها", emoji_key="btn_rank", style=PRIMARY, callback_data="ally_top")],
            [btn("خروج از اتحاد", emoji_key="btn_cancel", style=DANGER, callback_data="ally_leave")],
        ]
    else:
        rows = [
            [btn("ساخت اتحاد جدید", emoji_key="btn_alliance", style=SUCCESS, callback_data="ally_create")],
            [btn("پیوستن به اتحاد", emoji_key="btn_alliance", style=PRIMARY, callback_data="ally_join")],
            [btn("برترین اتحادها", emoji_key="btn_rank", style=PRIMARY, callback_data="ally_top")],
        ]
    rows.append([back_btn("menu:me")])
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
    await safe_edit_message_text(query, f"🟢 {get_emoji('alliance')} اسم اتحاد جدیدت رو بفرست:", parse_mode="HTML")


async def alliance_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    context.user_data[AWAITING_PLAYER_KEY] = {"action": "alliance_join"}
    await query.answer()
    await safe_edit_message_text(query,
        f"🔵 {get_emoji('alliance')} اسم اتحادی که می‌خوای بهش بپیوندی رو بفرست:", parse_mode="HTML"
    )


async def alliance_deposit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    context.user_data[AWAITING_PLAYER_KEY] = {"action": "alliance_deposit"}
    await query.answer()
    await safe_edit_message_text(query,
        f"💰 چند {get_emoji('coin')} طلا می‌خوای به خزانه واریز کنی؟ یه عدد بفرست:", parse_mode="HTML"
    )


async def alliance_top_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    ranked = await run_db(_alliance_top_sync)
    await query.answer()
    keyboard = InlineKeyboardMarkup([[back_btn("menu:alliance_info")]])
    if not ranked:
        await safe_edit_message_text(query, "هنوز هیچ اتحادی ساخته نشده.", reply_markup=keyboard)
        return
    medals = [get_emoji("medal_gold"), get_emoji("medal_silver"), get_emoji("medal_bronze")]
    lines = [f"{get_emoji('trophy')} <b>برترین اتحادها</b>\n"]
    for i, r in enumerate(ranked, start=1):
        rank = medals[i - 1] if i <= 3 else f"{i}."
        lines.append(f"{rank} {r['alliance'].name} — قدرت {r['power']} ({r['member_count']} عضو)")
    await safe_edit_message_text(query, "\n".join(lines), parse_mode="HTML", reply_markup=keyboard)


async def alliance_leave_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    keyboard = InlineKeyboardMarkup(
        [
            [
                btn("بله، خارج شو", emoji_key="btn_confirm", style=DANGER, callback_data="ally_leave_confirm"),
                btn("بی‌خیال", emoji_key="btn_cancel", callback_data="menu:alliance_info"),
            ]
        ]
    )
    await query.answer()
    await safe_edit_message_text(query, "مطمئنی می‌خوای از اتحادت خارج بشی؟", reply_markup=keyboard)


async def alliance_leave_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        await run_db(_alliance_leave_sync, update.effective_user)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer("👋 خارج شدی.")
    await safe_edit_message_text(query, "👋 از اتحاد خارج شدی.")


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
    rows = [[btn(a.name, emoji_key="btn_heist", style=DANGER, callback_data=f"heist_pick:{a.id}")] for a in targets]
    rows.append([back_btn("menu:alliance_info")])
    await safe_edit_message_text(query,
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
    keyboard = InlineKeyboardMarkup([[back_btn("menu:alliance_info")]])
    await safe_edit_message_text(query, "\n\n".join(lines), parse_mode="HTML", reply_markup=keyboard)


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

    if action == "set_lab_name":
        if not text or len(text) > LAB_NAME_MAX_LEN:
            context.user_data[AWAITING_PLAYER_KEY] = awaiting
            await message.reply_text(f"⚠️ اسم باید بین ۱ تا {LAB_NAME_MAX_LEN} کاراکتر باشه. دوباره بفرست:")
            return
        user, creature, equipped_items = await run_db(_set_lab_name_sync, update.effective_user, text)
        is_owner = update.effective_user.id == OWNER_TELEGRAM_ID
        await message.reply_text(
            f"{get_emoji('egg')} <b>آزمایشگاه «{user.lab_name}» فعال شد!</b>\n"
            "یه موجود تازه از کپسول زیستی بیرون اومد — بهش خوش‌آمد بگو 👇\n\n"
            + creature_card_text(user, creature, equipped_items),
            parse_mode="HTML",
            reply_markup=creature_keyboard(is_owner),
        )
        return

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
        wallet_line(user),
    ]
    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                btn("موجود فعال", emoji_key="btn_creature", style=PRIMARY, callback_data="menu:me"),
                btn("ارتقا و پرورش", emoji_key="btn_upgrade", style=PRIMARY, callback_data="menu:upgrade"),
            ],
            [
                btn("کلکسیون", emoji_key="btn_collection", style=PRIMARY, callback_data="menu:collection"),
                btn("شکار انفرادی", emoji_key="btn_hunt", style=PRIMARY, callback_data="menu:hunt"),
            ],
            [
                btn("ماموریت‌ها", emoji_key="btn_missions", style=PRIMARY, callback_data="menu:missions"),
                btn("گردونه‌ی شانس", emoji_key="btn_wheel", style=SUCCESS, callback_data="menu:wheel"),
            ],
            [
                btn("آرنا (کاپ)", emoji_key="btn_arena", style=DANGER, callback_data="menu:arena"),
                btn("تجهیزات", emoji_key="btn_inventory", style=PRIMARY, callback_data="menu:inventory"),
            ],
            [
                btn("آهنگری", emoji_key="btn_forge", style=PRIMARY, callback_data="menu:blacksmith"),
                btn("باکس ژنتیکی", emoji_key="btn_biocrate", style=SUCCESS, callback_data="menu:biocrate"),
            ],
            [
                btn("جعبه‌های الماسی", emoji_key="btn_diamond_box", style=SUCCESS, callback_data="menu:diamond_box"),
                btn("ساختمون‌ها", emoji_key="btn_buildings", style=PRIMARY, callback_data="menu:buildings"),
            ],
            [
                btn("اتحاد من", emoji_key="btn_alliance", style=PRIMARY, callback_data="menu:alliance_info"),
                btn("رتبه‌بندی", emoji_key="btn_rank", style=PRIMARY, callback_data="menu:rank"),
            ],
            [btn("پروفایل من", emoji_key="btn_profile", style=PRIMARY, callback_data="menu:profile")],
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
    "diamond_box": diamond_box_panel,
    "upgrade": upgrade_panel,
    "arena": arena_panel,
    "blacksmith": blacksmith_panel,
    "buildings": buildings_panel,
    "wheel": wheel_cmd,
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
    application.add_handler(CommandHandler("upgrade", upgrade_panel, filters.ChatType.PRIVATE))
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
    application.add_handler(CallbackQueryHandler(upgrade_pick_callback, pattern=r"^upg_pick:"))
    application.add_handler(CallbackQueryHandler(hunt_go_callback, pattern=r"^hunt_go:"))
    application.add_handler(CallbackQueryHandler(hunt_next_callback, pattern=r"^hunt_next$"))
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

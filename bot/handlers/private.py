from django.utils import timezone
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

from bio_lab.models import Alliance, Creature, User
from bio_lab.repository import (
    display_name,
    get_active_creature,
    get_or_create_user,
    lab_display,
    lab_name_taken,
)
from bot.handlers.achievements import achievements_panel
from bot.handlers.arena import arena_panel
from bot.handlers.banner import banner_panel
from bot.handlers.battlepass import battlepass_panel
from bot.handlers.campaign import campaign_panel
from bot.handlers.codex import codex_panel
from bot.handlers.events import events_panel
from bot.handlers.idle import idle_panel
from bot.handlers.referral import referral_panel
from bot.handlers.team import team_panel
from bot.handlers.breeding import breeding_panel
from bot.handlers.buildings import buildings_panel
from bot.handlers.inventory import blacksmith_panel, inventory_cmd
from bot.handlers.lootbox import biocrate_cmd, diamond_box_panel
from bot.handlers.owner import admin_cmd
from bot.handlers.wheel import wheel_cmd
from bot.buttons import (ADMIN, BACK, BATTLE, BUILD, CONFIRM, DANGER, LIST, NAV, PRIMARY,
                         SHOP, back_btn, back_only_keyboard, btn)
from bot.utils import mission_reward_text, run_db, safe_edit_message_text, send_screen
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
from game.buildings import get_or_create_buildings, grant_speedup_card, is_built, star_cap
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
from game.equipment import (bonus_text, equip_item, get_equipped_items, slot_loadout,
                            unequip_item)
from game.fusion import FUSION_BUILDING, fuse, fusion_partners, ready_pairs
from game import guide
from game.lab import lab_bar, lab_level, lab_progress
from game.hunt import HUNT_TIERS, estimated_reward, resolve_hunt, scout_one


def _mission_lines(completed: list[dict]) -> str:
    if not completed:
        return ""
    lines = [
        f"{get_emoji('mission')} ماموریت «{m['label']}» تکمیل شد! {mission_reward_text(m)}"
        for m in completed
    ]
    return "\n" + "\n".join(lines)


def lab_level_line(user) -> str:
    """The lab's overall level with a progress bar — the one number that answers
    "how far along is this player", independent of any single creature."""
    progress = lab_progress(user)
    if progress["is_max"]:
        return f"🔬 سطح آزمایشگاه: <b>{progress['level']}</b> (بیشینه) {lab_bar(user)}"
    return (
        f"🔬 سطح آزمایشگاه: <b>{progress['level']}</b> {lab_bar(user)} "
        f"{progress['into']:,}/{progress['span']:,}"
    )


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
    xp_needed = constants.xp_for_creature_level(creature.level)
    xp_bar = constants.render_bar(creature.xp, xp_needed, width=10)
    stars = get_emoji("star") * creature.star_level

    lines = [
        f"{get_emoji('creature')} <b>{creature.name}</b>  <code>#{creature.id}</code>",
        f"{constants.RARITY_LABELS[creature.rarity]} {stars}",
        f"{constants.element_label(creature.element)}",
        "",
        f"📊 سطح <b>{creature.level}</b>",
        f"{xp_bar}  {creature.xp}/{xp_needed} XP",
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


def _slot_summary_lines(slots: list[dict]) -> list[str]:
    """One line per equipment slot, empty slots included — an invisible empty slot
    is a feature a player never discovers."""
    lines = ["🎒 <b>تجهیزات</b>"]
    for row in slots:
        if row["is_empty"]:
            spare = len(row["candidates"])
            hint = f" — {spare} گزینه آماده" if spare else ""  # 0 stays silent
            lines.append(f"{row['label']}: <i>خالی</i>{hint}")
        else:
            item = row["item"]
            lines.append(f"{row['label']}: {item.name} <b>+{item.level}</b>")
    return lines


def upgrade_panel_text(user, creature, equipped_items: list | None = None, slots: list | None = None) -> str:
    stats = effective_stats(creature, equipped_items)
    stars = get_emoji("star") * creature.star_level
    active_tag = " · 🟢 پیش‌فرض" if creature.is_active else ""
    lines = [
        f"🔧 <b>ارتقای {creature.name}</b>",
        f"{constants.RARITY_LABELS[creature.rarity]} {stars} · سطح {creature.level}{active_tag}",
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
    if slots is not None:
        lines.append("")
        lines.extend(_slot_summary_lines(slots))
    lines.append("")
    lines.append(wallet_line(user))
    lines.append("\n<blockquote>تغذیه و تمرین XP می‌دن؛ ارتقای اعضا مستقیم استت اضافه می‌کنه.</blockquote>")
    return "\n".join(lines)


def upgrade_panel_keyboard(creature_id: int, is_active: bool = True) -> InlineKeyboardMarkup:
    """Every action carries the creature id, so upgrading a non-active creature
    doesn't silently swap which creature is active for hunting/arena."""
    rows = [
        [
            btn("تغذیه", emoji_key="btn_feed", style=BUILD, callback_data=f"lab:feed:{creature_id}"),
            btn("تمرین", emoji_key="btn_train", style=BUILD, callback_data=f"lab:train:{creature_id}"),
        ],
        [
            btn("🦋 بال", style=BUILD, callback_data=f"lab:up_wings:{creature_id}"),
            btn("🛡 زره", style=BUILD, callback_data=f"lab:up_armor:{creature_id}"),
        ],
        [
            btn("🦷 نیش", style=BUILD, callback_data=f"lab:up_fangs:{creature_id}"),
            btn("☠️ زهر", style=BUILD, callback_data=f"lab:up_poison:{creature_id}"),
        ],
        [btn("مدیریت تجهیزات", emoji_key="btn_inventory", style=PRIMARY, callback_data=f"upg_eq:{creature_id}")],
    ]
    if not is_active:
        # setting the default from here saves a trip through the collection screen,
        # which is where this used to be the only option
        rows.append(
            [btn("این رو پیش‌فرض کن", emoji_key="btn_confirm", style=CONFIRM, callback_data=f"upg_default:{creature_id}")]
        )
    rows.append([back_btn("menu:upgrade", "لیست هیولاها")])
    return InlineKeyboardMarkup(rows)


def equip_panel_text(user, creature, slots: list[dict]) -> str:
    filled = sum(1 for row in slots if not row["is_empty"])
    lines = [
        f"🎒 <b>تجهیزات {creature.name}</b>",
        f"<blockquote>{filled} از {len(slots)} جایگاه پره — روی هر جایگاه بزن تا عوضش کنی.</blockquote>",
        "",
    ]
    for row in slots:
        if row["is_empty"]:
            spare = len(row["candidates"])
            lines.append(
                f"{row['label']}: <i>خالی</i>"
                + (
                    f" — {spare} تجهیزات مناسب داری" if spare > 1
                    else " — ۱ تجهیزات مناسب داری" if spare
                    else " — چیزی برای این جایگاه نداری"
                )
            )
        else:
            item = row["item"]
            bonus = bonus_text(item)
            lines.append(f"{row['label']}: <b>{item.name} +{item.level}</b>" + (f"\n    <i>{bonus}</i>" if bonus else ""))
    lines.append("")
    lines.append(wallet_line(user))
    return "\n".join(lines)


def equip_panel_keyboard(creature_id: int, slots: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for row in slots:
        if row["is_empty"]:
            label = f"{row['label']} — خالی"
            style = CONFIRM if row["candidates"] else NAV
        else:
            label = f"{row['label']} — {row['item'].name} +{row['item'].level}"
            style = LIST
        rows.append([btn(label, style=style, callback_data=f"upg_slot:{creature_id}:{row['slot']}")])
    rows.append([back_btn(f"upg_pick:{creature_id}", "بازگشت به ارتقا")])
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


UPGRADE_PAGE_SIZE = 8


def _upgrade_render(user, ranked, page: int) -> tuple[str, InlineKeyboardMarkup]:
    """One page of the strongest-first creature picker. Paginated because a player
    with a big roster produced a keyboard tall enough to be unwieldy (and, past
    Telegram's limits, to fail outright)."""
    total_pages = max(1, (len(ranked) + UPGRADE_PAGE_SIZE - 1) // UPGRADE_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    chunk = ranked[page * UPGRADE_PAGE_SIZE : (page + 1) * UPGRADE_PAGE_SIZE]

    rows = []
    for creature, power in chunk:
        active_tag = "🟢 " if creature.is_active else ""
        stars = "⭐" * creature.star_level
        # RARITY_LABELS already carries its own colour dot, which is the fastest way
        # to read rarity at a glance in a long list
        rarity = constants.RARITY_LABELS[creature.rarity]
        rows.append(
            [
                btn(
                    f"{active_tag}{creature.name} {stars} · {rarity} · Lv{creature.level} · 💪{power}",
                    style=LIST,
                    callback_data=f"upg_pick:{creature.id}",
                )
            ]
        )
    nav = []
    if page > 0:
        nav.append(btn("◀️ قبلی", style=NAV, callback_data=f"upg_page:{page - 1}"))
    if page < total_pages - 1:
        nav.append(btn("بعدی ▶️", style=NAV, callback_data=f"upg_page:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([back_btn("menu:me")])

    page_note = f"  (صفحه {page + 1}/{total_pages})" if total_pages > 1 else ""
    text = (
        f"🔧 <b>ارتقا و پرورش</b>{page_note}\n"
        f"هیولاهات به ترتیب قدرت مرتب شدن — کدوم رو می‌خوای قوی‌تر کنی؟\n\n{wallet_line(user)}"
    )
    return text, InlineKeyboardMarkup(rows)


async def upgrade_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Creature picker, strongest first — the player chooses who to invest in
    rather than the panel silently assuming the active creature."""
    try:
        user, ranked = await run_db(_upgrade_list_sync, update.effective_user)
    except GameError as exc:
        await send_screen(update, str(exc), parse_mode=None, reply_markup=back_only_keyboard())
        return
    text, keyboard = _upgrade_render(user, ranked, 0)
    await send_screen(update, text, parse_mode="HTML", reply_markup=keyboard)


async def upgrade_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    page = int(query.data.split(":")[1])
    try:
        user, ranked = await run_db(_upgrade_list_sync, update.effective_user)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer()
    text, keyboard = _upgrade_render(user, ranked, page)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


def _upgrade_pick_sync(tg_user, creature_id):
    user, _ = get_or_create_user(tg_user)
    try:
        creature = Creature.objects.get(id=creature_id, owner=user)
    except Creature.DoesNotExist:
        raise GameError("این موجود توی کلکسیون تو نیست.")
    return user, creature, get_equipped_items(creature), slot_loadout(user, creature)


async def upgrade_pick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    creature_id = int(query.data.split(":")[1])
    try:
        user, creature, equipped_items, slots = await run_db(
            _upgrade_pick_sync, update.effective_user, creature_id
        )
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer()
    await safe_edit_message_text(
        query,
        upgrade_panel_text(user, creature, equipped_items, slots),
        parse_mode="HTML",
        reply_markup=upgrade_panel_keyboard(creature.id, creature.is_active),
    )


def _equip_panel_sync(tg_user, creature_id):
    user, _ = get_or_create_user(tg_user)
    try:
        creature = Creature.objects.get(id=creature_id, owner=user)
    except Creature.DoesNotExist:
        raise GameError("این موجود توی کلکسیون تو نیست.")
    return user, creature, slot_loadout(user, creature)


async def equip_panel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Slot overview for one creature: what's worn, what's empty, what fits."""
    query = update.callback_query
    creature_id = int(query.data.split(":")[1])
    try:
        user, creature, slots = await run_db(_equip_panel_sync, update.effective_user, creature_id)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer()
    await safe_edit_message_text(
        query,
        equip_panel_text(user, creature, slots),
        parse_mode="HTML",
        reply_markup=equip_panel_keyboard(creature.id, slots),
    )


async def equip_slot_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """The candidates for one slot, plus a way to strip whatever's in it."""
    query = update.callback_query
    _, creature_id_raw, slot = query.data.split(":")
    creature_id = int(creature_id_raw)
    try:
        user, creature, slots = await run_db(_equip_panel_sync, update.effective_user, creature_id)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    row = next((r for r in slots if r["slot"] == slot), None)
    if row is None:
        await query.answer("این جایگاه وجود نداره.", show_alert=True)
        return

    await query.answer()
    lines = [f"{row['label']} — <b>{creature.name}</b>", ""]
    if row["item"] is not None:
        lines.append(f"الان: <b>{row['item'].name} +{row['item'].level}</b>")
        bonus = bonus_text(row["item"])
        if bonus:
            lines.append(f"<i>{bonus}</i>")
        lines.append("")
    if row["candidates"]:
        lines.append("یکی از این‌ها رو بذار توش:")
    else:
        lines.append(
            "<blockquote>هیچ تجهیزاتی برای این جایگاه نداری. از باکس ژنتیکی و جعبه‌های الماسی "
            "می‌تونی تجهیزات به دست بیاری.</blockquote>"
        )

    rows = []
    for item in row["candidates"]:
        # a candidate worn by another creature moves rather than duplicates, so say so
        worn = f" (روی {item.equipped_on.name})" if item.equipped_on_id else ""
        rows.append(
            [
                btn(
                    f"{item.name} +{item.level} · {constants.RARITY_LABELS[item.rarity]}{worn}",
                    style=CONFIRM,
                    callback_data=f"upg_equip:{creature_id}:{item.id}",
                )
            ]
        )
    if row["item"] is not None:
        rows.append(
            [btn("خالی کردن این جایگاه", emoji_key="btn_cancel", style=DANGER,
                 callback_data=f"upg_unequip:{creature_id}:{row['item'].id}")]
        )
    rows.append([back_btn(f"upg_eq:{creature_id}", "بازگشت به تجهیزات")])
    await safe_edit_message_text(
        query, "\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows)
    )


def _equip_do_sync(tg_user, creature_id, item_id):
    user, _ = get_or_create_user(tg_user)
    try:
        creature = Creature.objects.get(id=creature_id, owner=user)
    except Creature.DoesNotExist:
        raise GameError("این موجود توی کلکسیون تو نیست.")
    item = equip_item(user, creature, item_id)
    return user, creature, slot_loadout(user, creature), item


def _unequip_do_sync(tg_user, creature_id, item_id):
    user, _ = get_or_create_user(tg_user)
    try:
        creature = Creature.objects.get(id=creature_id, owner=user)
    except Creature.DoesNotExist:
        raise GameError("این موجود توی کلکسیون تو نیست.")
    item = unequip_item(user, item_id)
    return user, creature, slot_loadout(user, creature), item


async def equip_do_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    action, creature_id_raw, item_id_raw = query.data.split(":")
    handler = _equip_do_sync if action == "upg_equip" else _unequip_do_sync
    try:
        user, creature, slots, item = await run_db(
            handler, update.effective_user, int(creature_id_raw), int(item_id_raw)
        )
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    verb = "تجهیز شد" if action == "upg_equip" else "خارج شد"
    await query.answer(f"{item.name} {verb}")
    await safe_edit_message_text(
        query,
        equip_panel_text(user, creature, slots),
        parse_mode="HTML",
        reply_markup=equip_panel_keyboard(creature.id, slots),
    )


async def upgrade_set_default_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Make this creature the active one without leaving the upgrade screen."""
    query = update.callback_query
    creature_id = int(query.data.split(":")[1])
    try:
        await run_db(_select_sync, update.effective_user, creature_id)
        user, creature, equipped_items, slots = await run_db(
            _upgrade_pick_sync, update.effective_user, creature_id
        )
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer("🟢 پیش‌فرض شد!")
    await safe_edit_message_text(
        query,
        upgrade_panel_text(user, creature, equipped_items, slots),
        parse_mode="HTML",
        reply_markup=upgrade_panel_keyboard(creature.id, creature.is_active),
    )


# ── the in-DM guide ─────────────────────────────────────────────────────────
#
# Same two-part split as the group help, and the *same* concept pages behind it
# (game/guide.py): how the game works is identical in both places, and a rule
# written twice is a rule that eventually disagrees with itself. Only the
# "where is it" half differs, because the DM is button-driven and the group is
# word-driven.

_GUIDE_RULE = "━━━━━━━━━━━━━━"


def guide_home_text_and_keyboard(is_first_run: bool = False) -> tuple[str, InlineKeyboardMarkup]:
    lines = [f"{get_emoji('book')} <b>راهنمای Kaiju Bio-Lab</b>", ""]
    if is_first_run:
        lines.append("<b>خوش اومدی! این پنج قدم اولته:</b>")
        lines.append("<blockquote>" + "\n".join(guide.first_run_lines()) + "</blockquote>")
    else:
        lines.append(
            "یه بازی پرورش هیولاست: یه هیولا داری، قوی‌ترش می‌کنی، باهاش می‌جنگی "
            "و آزمایشگاهت رو بزرگ می‌کنی."
        )
    lines += [
        "",
        _GUIDE_RULE,
        f"{get_emoji('lab')} <b>چطور بازی می‌کنم؟</b>",
        "<i>قانون‌های بازی — اگه تازه‌واردی از اینجا شروع کن.</i>",
        "",
    ]
    concept_buttons = []
    for key, (emoji_key, title, _blurb, _rows) in guide.CONCEPTS.items():
        lines.append(f"{get_emoji(emoji_key)} {title}")
        concept_buttons.append(btn(title, style=CONFIRM, callback_data=f"guide:c_{key}"))

    lines += ["", _GUIDE_RULE, f"{get_emoji('settings')} <b>کجا چیکار کنم؟</b>",
              "<i>هر بخش منو چیه و چه‌کار می‌کنه.</i>", ""]
    area_buttons = []
    for key, (emoji_key, title, blurb, _rows) in guide.DM_SECTIONS.items():
        lines.append(f"{get_emoji(emoji_key)} {title} — <i>{blurb}</i>")
        area_buttons.append(btn(title, style=NAV, callback_data=f"guide:a_{key}"))

    keyboard = [concept_buttons[i : i + 2] for i in range(0, len(concept_buttons), 2)]
    keyboard += [area_buttons[i : i + 2] for i in range(0, len(area_buttons), 2)]
    keyboard.append([back_btn("menu:me", "بازگشت به بازی")])
    return "\n".join(lines), InlineKeyboardMarkup(keyboard)


def _guide_page(source: dict, key: str) -> tuple[str, InlineKeyboardMarkup] | None:
    found = source.get(key)
    if found is None:
        return None
    emoji_key, title, blurb, rows = found
    lines = [f"{get_emoji(emoji_key)} <b>{title}</b>", "", f"<i>{blurb}</i>", ""]
    for heading, body in rows:
        lines += [_GUIDE_RULE, f"<b>{heading}</b>", body, ""]
    keyboard = InlineKeyboardMarkup(
        [[btn("راهنمای اصلی", emoji_key="btn_back", style=BACK, callback_data="guide:home")],
         [back_btn("menu:me", "بازگشت به بازی")]]
    )
    return "\n".join(lines).rstrip(), keyboard


async def send_first_run_guide(message) -> None:
    """The welcome guide, as a SECOND message after the creature card.

    Appended to the card it would bury the thing the player actually came for,
    and the first screen of a game shouldn't be something you scroll past. The
    guide also lives permanently on the menu, so this is a nudge rather than the
    only chance to see it."""
    text, keyboard = guide_home_text_and_keyboard(is_first_run=True)
    await message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def guide_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text, keyboard = guide_home_text_and_keyboard()
    await send_screen(update, text, reply_markup=keyboard)


async def guide_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    key = query.data.split(":", 1)[1]
    await query.answer()
    if key == "home":
        text, keyboard = guide_home_text_and_keyboard()
    elif key.startswith("c_"):
        rendered = _guide_page(guide.CONCEPTS, key[2:])
        text, keyboard = rendered or guide_home_text_and_keyboard()
    else:
        rendered = _guide_page(guide.DM_SECTIONS, key[2:])
        text, keyboard = rendered or guide_home_text_and_keyboard()
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


def creature_keyboard(is_owner: bool = False) -> InlineKeyboardMarkup:
    """Navigation keyboard under the creature card. Body-part upgrades used to live
    here too, but they made both the card and this keyboard unreadable — they're on
    the «🔧 ارتقا» screen now (upgrade_panel)."""
    rows = [
        # The two things a player is most likely here to do get the only coloured
        # buttons on the screen; everything below is navigation and stays plain,
        # otherwise the whole menu is a wall of colour and none of it means anything.
        [btn("ارتقا و پرورش", emoji_key="btn_upgrade", style=PRIMARY, callback_data="menu:upgrade")],
        [
            btn("شکار انفرادی", emoji_key="btn_hunt", style=BATTLE, callback_data="menu:hunt"),
            btn("آرنا (کاپ)", emoji_key="btn_arena", style=BATTLE, callback_data="menu:arena"),
        ],
        [
            btn("🗺 کمپین", style=BATTLE, callback_data="menu:campaign"),
            btn("⚔️ تیم من", style=NAV, callback_data="menu:team"),
        ],
        [
            btn("کلکسیون", emoji_key="btn_collection", style=NAV, callback_data="menu:collection"),
            btn("ترکیب هیولا", emoji_key="btn_fusion", style=NAV, callback_data="menu:fusion"),
        ],
        [
            btn("غار هیولا", emoji_key="btn_breeding", style=NAV, callback_data="menu:breeding"),
            btn("ساختمون‌ها", emoji_key="btn_buildings", style=NAV, callback_data="menu:buildings"),
        ],
        [
            btn("تجهیزات", emoji_key="btn_inventory", style=NAV, callback_data="menu:inventory"),
            btn("آهنگری", emoji_key="btn_forge", style=NAV, callback_data="menu:blacksmith"),
        ],
        [
            btn("ماموریت‌ها", emoji_key="btn_missions", style=NAV, callback_data="menu:missions"),
            btn("🏅 دستاوردها", style=NAV, callback_data="menu:achievements"),
        ],
        [
            btn("📖 دانشنامه", style=NAV, callback_data="menu:codex"),
            btn("🎁 دعوت دوستان", style=NAV, callback_data="menu:referral"),
        ],
        [
            btn("🎟 پاس فصلی", style=SHOP, callback_data="menu:battlepass"),
            btn("⏳ رویداد", style=SHOP, callback_data="menu:events"),
        ],
        [
            btn("باکس ژنتیکی", emoji_key="btn_biocrate", style=SHOP, callback_data="menu:biocrate"),
            btn("جعبه‌های الماسی", emoji_key="btn_diamond_box", style=SHOP, callback_data="menu:diamond_box"),
        ],
        [btn("🎰 بنر ویژه", style=SHOP, callback_data="menu:banner")],
        [
            btn("گردونه‌ی شانس", emoji_key="btn_wheel", style=SHOP, callback_data="menu:wheel"),
            btn("💤 پاداش آفلاین", style=SHOP, callback_data="menu:idle"),
        ],
        [
            btn("اتحاد من", emoji_key="btn_alliance", style=NAV, callback_data="menu:alliance_info"),
            btn("رتبه‌بندی", emoji_key="btn_rank", style=NAV, callback_data="menu:rank"),
        ],
        [
            btn("پروفایل من", emoji_key="btn_profile", style=NAV, callback_data="menu:profile"),
            btn("راهنما", emoji_key="btn_report", style=CONFIRM, callback_data="menu:guide"),
        ],
    ]
    if is_owner:
        rows.append([btn("پنل ادمین", emoji_key="btn_admin", style=ADMIN, callback_data="menu:admin")])
    return InlineKeyboardMarkup(rows)


LAB_NAME_MAX_LEN = 32


def _start_sync(tg_user, referrer_id=None):
    user, was_created = get_or_create_user(tg_user)
    if referrer_id is not None:
        from game.referral import register_referral

        register_referral(user, was_created, referrer_id)
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
    # Collapse whitespace and strip control characters. The name is shown on every
    # leaderboard, so a name padded with newlines could push other rows off the
    # screen; lab_display() handles the HTML escaping separately.
    cleaned = " ".join(str(name).split())[:LAB_NAME_MAX_LEN]
    # Lab names must be unique — they're shown on every leaderboard AND used as a
    # moderation identifier (resolve_user), so a duplicate would be ambiguous.
    if lab_name_taken(cleaned, exclude_user_id=user.id):
        raise GameError("این اسم آزمایشگاه قبلاً گرفته شده")
    user.lab_name = cleaned
    user.save(update_fields=["lab_name"])
    creature = get_active_creature(user)
    return user, creature, get_equipped_items(creature) if creature else []


def _rename_lab_sync(tg_user, name):
    """Paid lab rename: charges diamonds (escalating each time) and enforces the
    same uniqueness as the free first-time name."""
    user, _ = get_or_create_user(tg_user)
    cleaned = " ".join(str(name).split())[:LAB_NAME_MAX_LEN]
    if not cleaned:
        raise GameError("اسم نمی‌تونه خالی باشه")
    if user.lab_name is None:
        raise GameError("اول با /start اسم آزمایشگاهت رو بذار")
    if cleaned.casefold() == user.lab_name.casefold():
        raise GameError("این همون اسم فعلیته")
    if lab_name_taken(cleaned, exclude_user_id=user.id):
        raise GameError("این اسم آزمایشگاه قبلاً گرفته شده")
    cost = constants.lab_rename_cost(user.lab_renames)
    if user.diamonds < cost:
        raise GameError(f"الماس کافی نداری! تغییر اسم {cost} الماس می‌خواد")
    user.diamonds -= cost
    user.lab_name = cleaned
    user.lab_renames += 1
    user.save(update_fields=["diamonds", "lab_name", "lab_renames"])
    return user, cost, cleaned


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    referrer_id = None
    if context.args:
        from game.referral import parse_payload

        referrer_id = parse_payload(context.args[0])
    user, creature, is_new, login_bonus, equipped_items = await run_db(
        _start_sync, update.effective_user, referrer_id
    )

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
            f"{get_emoji('egg')} <b>آزمایشگاه «{lab_display(user)}» فعال شد!</b>\n"
            "یه موجود تازه از کپسول زیستی بیرون اومد — بهش خوش‌آمد بگو 👇\n"
        )
    else:
        lines.append(f"👋 <b>به آزمایشگاه «{lab_display(user)}» خوش برگشتی!</b>\n")

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

    if is_new:
        await send_first_run_guide(update.message)


def _me_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    creature = get_active_creature(user)
    equipped_items = get_equipped_items(creature) if creature else []
    return user, creature, equipped_items


async def me(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user, creature, equipped_items = await run_db(_me_sync, update.effective_user)
    if creature is None:
        await send_screen(update, 
            "😅 هنوز موجودی نداری! دستور /start رو بزن تا از آزمایشگاه شروع کنی."
        )
        return
    is_owner = update.effective_user.id == OWNER_TELEGRAM_ID
    await send_screen(update, 
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
    # slots come back too: this re-renders the upgrade panel, and fetching them
    # here keeps the equipment section from vanishing after a feed/train/upgrade
    return user, creature, note + _mission_lines(completed_missions), equipped_items, slot_loadout(user, creature)


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

    user, creature, note, equipped_items, slots = result
    await query.answer()
    # every lab action is reachable only from the upgrade panel now, so re-render
    # that rather than bouncing the player back to the creature card
    await safe_edit_message_text(query,
        note + "\n\n" + upgrade_panel_text(user, creature, equipped_items, slots),
        parse_mode="HTML",
        reply_markup=upgrade_panel_keyboard(creature.id, creature.is_active),
    )


def _collection_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    return list_creatures(user)


def _collection_keyboard(creatures: list[Creature]) -> InlineKeyboardMarkup:
    """Each row is «name» + a one-tap activate button. Activating used to require
    opening the detail card first, which players read as "selecting doesn't work" —
    the shortcut is right here now, and the label itself still opens the card."""
    rows = []
    for c in creatures:
        stars = "⭐" * c.star_level
        label = f"{c.name} {stars} · Lv{c.level} · {constants.RARITY_LABELS[c.rarity]}"
        row = [btn(f"{'🟢 ' if c.is_active else ''}{label}", style=LIST, callback_data=f"coll_pick:{c.id}")]
        if not c.is_active:
            row.append(btn("فعال کن", style=CONFIRM, callback_data=f"coll_select:{c.id}"))
        rows.append(row)
    rows.append(
        [btn("ترکیب هیولا", emoji_key="btn_fusion", style=NAV, callback_data="menu:fusion")]
    )
    rows.append([back_btn("menu:me")])
    return InlineKeyboardMarkup(rows)


async def collection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    creatures = await run_db(_collection_sync, update.effective_user)
    if not creatures:
        await send_screen(update, f"📭 کلکسیونت خالیه! {get_emoji('egg')} با /start شروع کن.",
                          reply_markup=back_only_keyboard())
        return
    await send_screen(update, 
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
        rows.append([btn("انتخاب به‌عنوان موجود فعال", emoji_key="btn_confirm", style=CONFIRM, callback_data=f"coll_select:{creature_id}")])
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


def _fusion_candidates_sync(tg_user, creature_id):
    """Only genuinely fusable partners — same species, same star, lab built, below
    the star cap. Offering anything else would let the player pick a pair that
    fuse() then rejects, which reads as "fusion is broken"."""
    user, _ = get_or_create_user(tg_user)
    try:
        creature = Creature.objects.get(id=creature_id, owner=user)
    except Creature.DoesNotExist:
        raise GameError("این موجود توی کلکسیون تو نیست.")
    return creature, fusion_partners(user, creature), is_built(user, FUSION_BUILDING), star_cap(user)


async def fusion_pick_a_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    parent_a_id = int(query.data.split(":")[1])
    try:
        creature, candidates, lab_built, cap = await run_db(
            _fusion_candidates_sync, update.effective_user, parent_a_id
        )
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return

    if not candidates:
        # say *why* there's nothing to offer — a bare "no partners" alert sent
        # players hunting for a valid pair that could never exist
        if not lab_built:
            reason = "اول باید 🔮 تالار ادغام رو از «🏗 ساختمون‌ها» بسازی."
        elif creature.star_level >= cap:
            reason = f"سقف ستاره‌ی فعلی تو {cap}⭐ ـه — برای بالاتر رفتن تالار مِهر رو ارتقا بده."
        else:
            reason = (
                f"هیولای هم‌نوع دیگه‌ای با {creature.star_level}⭐ نداری.\n"
                f"برای ترکیب به دو تا «{creature.name}» با ستاره‌ی یکسان نیاز داری."
            )
        await query.answer(reason, show_alert=True)
        return

    await query.answer()
    rows = [
        [
            btn(
                f"{c.name} {'⭐' * c.star_level} · Lv{c.level}",
                style=PRIMARY,
                callback_data=f"fus_b:{parent_a_id}:{c.id}",
            )
        ]
        for c in candidates
    ]
    rows.append([back_btn(f"coll_pick:{parent_a_id}")])
    await safe_edit_message_text(query,
        f"{get_emoji('lab')} <b>ترکیب {creature.name}</b> {'⭐' * creature.star_level}\n"
        f"این‌ها هم‌نوع و هم‌ستاره‌ان، پس ترکیبشون <b>حتماً</b> جواب می‌ده:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(rows),
    )


def _fusion_panel_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    return user, ready_pairs(user), is_built(user, FUSION_BUILDING), star_cap(user)


async def fusion_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Lists the pairs the player can fuse *right now*. Reaching fusion used to mean
    going collection → pick a creature → hope it had a valid partner; this shows the
    valid combinations directly, so every offered button is guaranteed to work."""
    user, pairs, lab_built, cap = await run_db(_fusion_panel_sync, update.effective_user)

    lines = [f"{get_emoji('lab')} <b>تالار ادغام</b>"]
    if not lab_built:
        lines.append("\n🔒 اول باید 🔮 <b>تالار ادغام</b> رو از «🏗 ساختمون‌ها» بسازی.")
        rows = [
            [btn("رفتن به ساختمون‌ها", emoji_key="btn_buildings", style=PRIMARY, callback_data="menu:buildings")],
            [back_btn("menu:me")],
        ]
        await send_screen(update, 
            "\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows)
        )
        return

    lines.append(f"⭐ سقف ستاره‌ی فعلی تو: <b>{cap}</b>")
    lines.append("")
    if pairs:
        lines.append("این جفت‌ها آماده‌ی ترکیبن — <b>هر کدوم ۱۰۰٪ موفق می‌شه</b>:")
    else:
        lines.append(
            "الان هیچ جفت آماده‌ای نداری.\n\n"
            "<blockquote>برای ترکیب به <b>دو هیولای هم‌نام با ستاره‌ی یکسان</b> نیاز داری. "
            "از باکس ژنتیکی و جعبه‌های الماسی هیولای بیشتری بگیر تا جفت پیدا کنی.</blockquote>"
        )

    rows = [
        [
            btn(
                f"{p['name']} {'⭐' * p['star']} ×{p['count']} → {'⭐' * (p['star'] + 1)}",
                emoji_key="btn_fusion",
                style=PRIMARY,
                callback_data=f"fus_b:{p['parent_a'].id}:{p['parent_b'].id}",
            )
        ]
        for p in pairs
    ]
    rows.append([back_btn("menu:me")])
    lines.append(f"\n{wallet_line(user)}")
    await send_screen(update, 
        "\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows)
    )


async def fusion_pick_b_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, a_id, b_id = query.data.split(":")
    await query.answer()
    keyboard = InlineKeyboardMarkup(
        [
            [
                btn("تأیید فیوژن", emoji_key="btn_confirm", style=CONFIRM, callback_data=f"fus_confirm:{a_id}:{b_id}"),
                btn("لغو", emoji_key="btn_cancel", style=DANGER, callback_data=f"coll_pick:{a_id}"),
            ]
        ]
    )
    await safe_edit_message_text(query,
        f"{get_emoji('warning')} مطمئنی؟\n\n"
        f"<blockquote>این ترکیب <b>۱۰۰٪ موفق می‌شه</b> — هم‌نام و هم‌ستاره‌ان، پس شکست نداره.\n"
        f"هر دو موجود <b>برای همیشه سوزانده می‌شن</b> و "
        f"{constants.FUSION_GOLD_COST} {get_emoji('coin')} هزینه می‌شه. یه شانس هم هست درجه‌ی نایابی ارتقا پیدا کنه "
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


MISSIONS_PAGE_SIZE = 7


def _missions_render(status: list[dict], page: int) -> tuple[str, InlineKeyboardMarkup]:
    """Missions, in-progress first then completed, split across pages. A player's
    full mission list plus reward text overran Telegram's message limit and got
    rejected outright; paging it keeps every screen short and readable."""
    ordered = sorted(status, key=lambda m: (m["done"], m["label"]))  # unfinished first
    total_pages = max(1, (len(ordered) + MISSIONS_PAGE_SIZE - 1) // MISSIONS_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    chunk = ordered[page * MISSIONS_PAGE_SIZE : (page + 1) * MISSIONS_PAGE_SIZE]

    done_count = sum(1 for m in status if m["done"])
    page_note = f"  (صفحه {page + 1}/{total_pages})" if total_pages > 1 else ""
    lines = [f"{get_emoji('mission')} <b>ماموریت‌های امروز</b>  {done_count}/{len(status)}{page_note}", ""]
    for m in chunk:
        if m["done"]:
            lines.append(f"✅ <s>{m['label']}</s>")
        else:
            bar = constants.render_bar(m["progress"], m["target"], width=8)
            lines.append(f"⏳ <b>{m['label']}</b>")
            lines.append(f"    {bar} {m['progress']}/{m['target']}  🎁 <i>{mission_reward_text(m)}</i>")
    lines.append("\n<i>ماموریت‌ها هر روز (ساعت جهانی UTC) ریست می‌شن.</i>")

    rows = []
    nav = []
    if page > 0:
        nav.append(btn("◀️ قبلی", style=NAV, callback_data=f"mission_page:{page - 1}"))
    if page < total_pages - 1:
        nav.append(btn("بعدی ▶️", style=NAV, callback_data=f"mission_page:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([back_btn("menu:me")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def missions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    status = await run_db(_missions_sync, update.effective_user)
    text, keyboard = _missions_render(status, 0)
    await send_screen(update, text, parse_mode="HTML", reply_markup=keyboard)


async def missions_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    page = int(query.data.split(":")[1])
    status = await run_db(_missions_sync, update.effective_user)
    await query.answer()
    text, keyboard = _missions_render(status, page)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


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
            [btn("حمله!", emoji_key="btn_attack", style=BATTLE, callback_data=f"hunt_go:{target['tier']}:{target['seed']}")],
            [btn("🔍 بعدی", style=NAV, callback_data="hunt_next")],
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
        await send_screen(update, str(exc), parse_mode=None, reply_markup=back_only_keyboard())
        return
    await send_screen(update, 
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
            [btn("شکار دوباره", emoji_key="btn_hunt", style=BATTLE, callback_data="hunt_next")],
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
            [btn("واریز به خزانه", emoji_key="btn_deposit", style=BUILD, callback_data="ally_deposit")],
            [
                btn("🏰 پرک‌های اتحاد", style=PRIMARY, callback_data="ally_perks"),
                btn("⚔️ جنگ هفتگی", style=BATTLE, callback_data="ally_war"),
            ],
            [btn("شبیخون به اتحاد دیگه", emoji_key="btn_heist", style=BATTLE, callback_data="ally_heist_list")],
            [btn("برترین اتحادها", emoji_key="btn_rank", style=NAV, callback_data="ally_top")],
            [btn("خروج از اتحاد", emoji_key="btn_cancel", style=DANGER, callback_data="ally_leave")],
        ]
    else:
        rows = [
            [btn("ساخت اتحاد جدید", emoji_key="btn_alliance", style=BUILD, callback_data="ally_create")],
            [btn("پیوستن به اتحاد", emoji_key="btn_alliance", style=PRIMARY, callback_data="ally_join")],
            [btn("برترین اتحادها", emoji_key="btn_rank", style=NAV, callback_data="ally_top")],
        ]
    rows.append([back_btn("menu:me")])
    return InlineKeyboardMarkup(rows)


async def alliance_info_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    info = await run_db(_alliance_info_sync, update.effective_user)
    if info is None:
        await send_screen(update, 
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
    await send_screen(update, 
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
                btn("بی‌خیال", emoji_key="btn_cancel", style=DANGER, callback_data="menu:alliance_info"),
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
    rows = [[btn(a.name, emoji_key="btn_heist", style=BATTLE, callback_data=f"heist_pick:{a.id}")] for a in targets]
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
        try:
            user, creature, equipped_items = await run_db(_set_lab_name_sync, update.effective_user, text)
        except GameError as exc:
            context.user_data[AWAITING_PLAYER_KEY] = awaiting
            await message.reply_text(f"⚠️ {exc} — یه اسم دیگه بفرست:")
            return
        is_owner = update.effective_user.id == OWNER_TELEGRAM_ID
        await message.reply_text(
            f"{get_emoji('egg')} <b>آزمایشگاه «{lab_display(user)}» فعال شد!</b>\n"
            "یه موجود تازه از کپسول زیستی بیرون اومد — بهش خوش‌آمد بگو 👇\n\n"
            + creature_card_text(user, creature, equipped_items),
            parse_mode="HTML",
            reply_markup=creature_keyboard(is_owner),
        )
        # THIS is a player's real first screen, not /start: the first /start only
        # asks for a lab name and returns, and by the time they come back the
        # creature already exists so `is_new` is False. Sending the guide here is
        # what actually reaches a new player.
        await send_first_run_guide(message)
        return

    if action == "rename_lab":
        if not text or len(text) > LAB_NAME_MAX_LEN:
            context.user_data[AWAITING_PLAYER_KEY] = awaiting
            await message.reply_text(f"⚠️ اسم باید بین ۱ تا {LAB_NAME_MAX_LEN} کاراکتر باشه. دوباره بفرست:")
            return
        try:
            user, cost, _newname = await run_db(_rename_lab_sync, update.effective_user, text)
        except GameError as exc:
            await message.reply_text(f"⚠️ {exc}.")
            return
        await message.reply_text(
            f"✅ اسم آزمایشگاهت به «{lab_display(user)}» تغییر کرد. "
            f"({cost} {get_emoji('diamond')} کم شد؛ دفعه‌ی بعد {constants.lab_rename_cost(user.lab_renames)} می‌شه)",
            parse_mode="HTML",
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
    """The global table ranks *labs*, not creatures.

    Ranking creatures meant the board was really a rarity-luck board: one lucky
    crate could outrank weeks of play, and the player's own name never appeared
    on it. Lab XP accumulates from everything a player actually does, so the
    order reflects effort — and the row shows the lab that earned it."""
    from django.db.models import F, Max

    user, _ = get_or_create_user(tg_user)
    ranked = list(
        User.objects.filter(is_banned=False)
        .annotate(
            best_power=Max(
                F("creatures__base_hp")
                + F("creatures__base_atk")
                + F("creatures__base_def")
                + F("creatures__base_spd")
            )
        )
        .order_by("-lab_xp", "-cup", "id")
    )
    my_rank = next((i for i, u in enumerate(ranked, start=1) if u.id == user.id), None)
    return ranked[:10], my_rank, len(ranked), user


async def rank(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    top10, my_rank, total, me_user = await run_db(_rank_sync, update.effective_user)
    if not top10:
        await send_screen(update, "هنوز هیچ آزمایشگاهی ثبت نشده.", reply_markup=back_only_keyboard())
        return
    medals = [get_emoji("medal_gold"), get_emoji("medal_silver"), get_emoji("medal_bronze")]
    lines = [f"{get_emoji('trophy')} <b>رتبه‌بندی آزمایشگاه‌ها</b>", "<blockquote>بر اساس سطح کلی آزمایشگاه</blockquote>\n"]
    for i, u in enumerate(top10, start=1):
        rank_icon = medals[i - 1] if i <= 3 else f"<b>{i}.</b>"
        power = f" · 💪{u.best_power}" if u.best_power else ""
        lines.append(f"{rank_icon} {lab_display(u)} — 🔬 سطح {lab_level(u)}{power}")
    if my_rank is not None:
        lines.append(f"\n📍 رتبه‌ی تو: <b>{my_rank}</b> از {total} — 🔬 سطح {lab_level(me_user)}")
    await send_screen(update, "\n".join(lines), reply_markup=back_only_keyboard())


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


def _profile_text_and_keyboard(user, stats) -> tuple[str, InlineKeyboardMarkup]:
    rename_cost = constants.lab_rename_cost(user.lab_renames)
    lines = [
        f"{get_emoji('profile')} <b>آزمایشگاه {lab_display(user)}</b>",
        f"<blockquote>{lab_level_line(user)}</blockquote>\n",
        f"📅 عضو از: {timezone.localtime(user.created_at).strftime('%Y-%m-%d')}",
        f"🔥 روزهای ورود پشت‌سرهم: {user.login_streak}",
        f"{get_emoji('creature')} موجودات ساخته‌شده: {stats['creatures_owned']}\n",
        f"{get_emoji('battle')} دوئل‌های برده: {stats['duel_wins']}",
        f"{get_emoji('hunt')} شکارهای انجام‌شده: {stats['total_hunts']}",
        f"{get_emoji('raid_boss')} کل دمیج واردشده به رید باس‌ها: {stats['total_raid_damage']}\n",
        wallet_line(user),
    ]
    notif_label = "🔔 اعلان‌ها: روشن" if user.notifications_on else "🔕 اعلان‌ها: خاموش"
    rows = [
        [btn(f"✏️ تغییر اسم آزمایشگاه ({rename_cost} 💎)", style=SHOP, callback_data="lab_rename")],
        [btn(notif_label, style=NAV, callback_data="notif_toggle")],
        [back_btn("menu:me")],
    ]
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user, stats = await run_db(_profile_sync, update.effective_user)
    text, keyboard = _profile_text_and_keyboard(user, stats)
    await send_screen(update, text, parse_mode="HTML", reply_markup=keyboard)


def _notif_toggle_sync(tg_user):
    user, stats = _profile_sync(tg_user)
    user.notifications_on = not user.notifications_on
    user.save(update_fields=["notifications_on"])
    return user, stats


async def notif_toggle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user, stats = await run_db(_notif_toggle_sync, update.effective_user)
    await query.answer("🔔 اعلان‌ها روشن شد" if user.notifications_on else "🔕 اعلان‌ها خاموش شد")
    text, keyboard = _profile_text_and_keyboard(user, stats)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


async def lab_rename_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user, _ = await run_db(lambda tg: get_or_create_user(tg), update.effective_user)
    cost = constants.lab_rename_cost(user.lab_renames)
    if user.lab_name is None:
        await query.answer("اول با /start اسم آزمایشگاهت رو بذار.", show_alert=True)
        return
    if user.diamonds < cost:
        await query.answer(f"الماس کافی نداری! تغییر اسم {cost} الماس می‌خواد.", show_alert=True)
        return
    await query.answer()
    context.user_data[AWAITING_PLAYER_KEY] = {"action": "rename_lab"}
    await safe_edit_message_text(
        query,
        f"✏️ <b>تغییر اسم آزمایشگاه</b>\n"
        f"هزینه: <b>{cost}</b> {get_emoji('diamond')} (موجودی: {user.diamonds})\n"
        f"<i>هر بار که عوض کنی، دفعه‌ی بعد گرون‌تر می‌شه.</i>\n\n"
        f"اسم جدید رو بفرست (حداکثر {LAB_NAME_MAX_LEN} کاراکتر):",
        parse_mode="HTML",
        reply_markup=back_only_keyboard("menu:profile"),
    )


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            # same palette discipline as creature_keyboard: colour marks the doing,
            # not the going
            [
                btn("موجود فعال", emoji_key="btn_creature", style=PRIMARY, callback_data="menu:me"),
                btn("ارتقا و پرورش", emoji_key="btn_upgrade", style=PRIMARY, callback_data="menu:upgrade"),
            ],
            [
                btn("شکار انفرادی", emoji_key="btn_hunt", style=BATTLE, callback_data="menu:hunt"),
                btn("آرنا (کاپ)", emoji_key="btn_arena", style=BATTLE, callback_data="menu:arena"),
            ],
            [
                btn("کلکسیون", emoji_key="btn_collection", style=NAV, callback_data="menu:collection"),
                btn("ترکیب هیولا", emoji_key="btn_fusion", style=NAV, callback_data="menu:fusion"),
            ],
            [
                btn("غار هیولا", emoji_key="btn_breeding", style=NAV, callback_data="menu:breeding"),
                btn("ساختمون‌ها", emoji_key="btn_buildings", style=NAV, callback_data="menu:buildings"),
            ],
            [
                btn("تجهیزات", emoji_key="btn_inventory", style=NAV, callback_data="menu:inventory"),
                btn("آهنگری", emoji_key="btn_forge", style=NAV, callback_data="menu:blacksmith"),
            ],
            [
            btn("ماموریت‌ها", emoji_key="btn_missions", style=NAV, callback_data="menu:missions"),
            btn("🏅 دستاوردها", style=NAV, callback_data="menu:achievements"),
        ],
        [
            btn("📖 دانشنامه", style=NAV, callback_data="menu:codex"),
            btn("🎁 دعوت دوستان", style=NAV, callback_data="menu:referral"),
        ],
        [
            btn("🎟 پاس فصلی", style=SHOP, callback_data="menu:battlepass"),
            btn("⏳ رویداد", style=SHOP, callback_data="menu:events"),
        ],
            [
                btn("باکس ژنتیکی", emoji_key="btn_biocrate", style=SHOP, callback_data="menu:biocrate"),
                btn("جعبه‌های الماسی", emoji_key="btn_diamond_box", style=SHOP, callback_data="menu:diamond_box"),
            ],
            [
            btn("گردونه‌ی شانس", emoji_key="btn_wheel", style=SHOP, callback_data="menu:wheel"),
            btn("💤 پاداش آفلاین", style=SHOP, callback_data="menu:idle"),
        ],
            [
                btn("اتحاد من", emoji_key="btn_alliance", style=NAV, callback_data="menu:alliance_info"),
                btn("رتبه‌بندی", emoji_key="btn_rank", style=NAV, callback_data="menu:rank"),
            ],
            [
                btn("پروفایل من", emoji_key="btn_profile", style=NAV, callback_data="menu:profile"),
                btn("راهنما", emoji_key="btn_report", style=CONFIRM, callback_data="menu:guide"),
            ],
        ]
    )


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await send_screen(update, 
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
    "fusion": fusion_panel,
    "breeding": breeding_panel,
    "buildings": buildings_panel,
    "achievements": achievements_panel,
    "battlepass": battlepass_panel,
    "codex": codex_panel,
    "referral": referral_panel,
    "team": team_panel,
    "campaign": campaign_panel,
    "events": events_panel,
    "banner": banner_panel,
    "idle": idle_panel,
    "wheel": wheel_cmd,
    "alliance_info": alliance_info_cmd,
    "rank": rank,
    "admin": admin_cmd,
    "profile": profile,
    "guide": guide_panel,
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
    application.add_handler(CommandHandler("help", guide_panel, filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(menu_callback, pattern=r"^menu:"))
    application.add_handler(CallbackQueryHandler(guide_page_callback, pattern=r"^guide:"))
    application.add_handler(CallbackQueryHandler(upgrade_pick_callback, pattern=r"^upg_pick:"))
    application.add_handler(CallbackQueryHandler(upgrade_page_callback, pattern=r"^upg_page:"))
    application.add_handler(CallbackQueryHandler(missions_page_callback, pattern=r"^mission_page:"))
    application.add_handler(CallbackQueryHandler(lab_rename_start_callback, pattern=r"^lab_rename$"))
    application.add_handler(CallbackQueryHandler(notif_toggle_callback, pattern=r"^notif_toggle$"))
    application.add_handler(CallbackQueryHandler(equip_panel_callback, pattern=r"^upg_eq:"))
    application.add_handler(CallbackQueryHandler(equip_slot_callback, pattern=r"^upg_slot:"))
    application.add_handler(CallbackQueryHandler(equip_do_callback, pattern=r"^upg_(equip|unequip):"))
    application.add_handler(CallbackQueryHandler(upgrade_set_default_callback, pattern=r"^upg_default:"))
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

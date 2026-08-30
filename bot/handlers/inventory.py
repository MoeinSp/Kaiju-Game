from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

from bio_lab.models import Equipment
from bio_lab.repository import get_active_creature, get_or_create_user
from bot.buttons import BUILD, CONFIRM, DANGER, LIST, NAV, back_btn, back_only_keyboard, btn
from bot.utils import run_db, safe_edit_message_text, send_screen
from game import constants
from game.blacksmith import forge, forge_preview, forgeable_items
from game.creature import GameError
from game.emoji import get_emoji
from game.equipment import (
    equip_item,
    list_inventory,
    same_slot_candidates,
    unequip_item,
    upgrade_item,
)


def _item_line(item: Equipment) -> str:
    from game.equipment import equipment_power

    status = f" · روی #{item.equipped_on_id}" if item.equipped_on_id else ""
    return (
        f"<code>#{item.id}</code> {constants.EQUIPMENT_SLOT_LABELS[item.slot]} — {item.name} "
        f"{constants.RARITY_LABELS[item.rarity]} +{item.level} · 💪{equipment_power(item)}{status}"
    )


PAGE_SIZE = 10

_RARITY_RANK = {r: i for i, r in enumerate(constants.RARITY_ORDER)}


def _inv_home_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    items = list_inventory(user)
    counts = {slot: 0 for slot in constants.EQUIPMENT_SLOTS}
    for i in items:
        counts[i.slot] = counts.get(i.slot, 0) + 1
    return counts


def _inv_home_render(counts: dict) -> tuple[str, InlineKeyboardMarkup]:
    text = (
        f"{get_emoji('collection')} <b>کوله‌پشتی تجهیزات</b>\n"
        "بر اساس نوع دسته‌بندی شده — یه دسته انتخاب کن (رنگ کنار هر آیتم = نایابی):"
    )
    slots = constants.EQUIPMENT_SLOTS
    rows = []
    for i in range(0, len(slots), 2):
        row = [
            btn(f"{constants.EQUIPMENT_SLOT_LABELS[s]} ({counts.get(s, 0)})", style=NAV, callback_data=f"inv_cat:{s}:all:0")
            for s in slots[i : i + 2]
        ]
        rows.append(row)
    rows.append([back_btn("menu:me")])
    return text, InlineKeyboardMarkup(rows)


def _inv_cat_sync(tg_user, slot):
    user, _ = get_or_create_user(tg_user)
    items = [i for i in list_inventory(user) if i.slot == slot]
    # rarity (desc) then level (desc), so the best gear is on top
    items.sort(key=lambda i: (_RARITY_RANK.get(i.rarity, 0), i.level), reverse=True)
    return items


def _rarity_tab_rows(slot, items, filt):
    """Rarity filter tabs for one equipment slot — only rarities the player owns here,
    plus «همه», exactly like the collection picker so gear is split by نایابی."""
    counts = {}
    for i in items:
        counts[i.rarity] = counts.get(i.rarity, 0) + 1
    tabs = [btn(("• " if filt == "all" else "") + f"همه ({len(items)})",
                style=NAV, callback_data=f"inv_cat:{slot}:all:0")]
    for r in reversed(constants.RARITY_ORDER):
        if counts.get(r):
            mark = "• " if filt == r else ""
            tabs.append(btn(f"{mark}{constants.RARITY_LABELS[r]} ({counts[r]})",
                            style=NAV, callback_data=f"inv_cat:{slot}:{r}:0"))
    return [tabs[i:i + 3] for i in range(0, len(tabs), 3)]


def _inv_cat_render(slot, items: list[Equipment], filt: str, page: int) -> tuple[str, InlineKeyboardMarkup]:
    label = constants.EQUIPMENT_SLOT_LABELS[slot]
    if not items:
        return (
            f"{get_emoji('collection')} <b>کوله‌پشتی — {label}</b>\n\nتوی این دسته چیزی نداری.",
            InlineKeyboardMarkup([[back_btn("menu:inventory", "بازگشت به دسته‌ها")]]),
        )
    tab_rows = _rarity_tab_rows(slot, items, filt)
    shown = items if filt == "all" else [i for i in items if i.rarity == filt]
    total_pages = max(1, (len(shown) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    chunk = shown[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
    rows = list(tab_rows)
    for i in chunk:
        tag = "⚔️ " if i.equipped_on_id else ""
        rows.append([btn(
            f"{tag}{constants.RARITY_LABELS[i.rarity]} {i.name} +{i.level}",
            style=LIST, callback_data=f"inv_pick:{i.id}",
        )])
    nav = []
    if page > 0:
        nav.append(btn("قبلی", emoji_key="btn_prev", style=NAV, callback_data=f"inv_cat:{slot}:{filt}:{page - 1}"))
    if page < total_pages - 1:
        nav.append(btn("بعدی", emoji_key="btn_next", style=NAV, callback_data=f"inv_cat:{slot}:{filt}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([back_btn("menu:inventory", "بازگشت به دسته‌ها")])
    page_note = f"  (صفحه {page + 1}/{total_pages})" if total_pages > 1 else ""
    rarity_note = "" if filt == "all" else f" · {constants.RARITY_LABELS[filt]}"
    text = (f"{get_emoji('collection')} <b>کوله‌پشتی — {label}</b>{rarity_note}{page_note}\n"
            "نایابی رو انتخاب کن، بعد رو آیتم بزن:")
    return text, InlineKeyboardMarkup(rows)


async def inventory_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    counts = await run_db(_inv_home_sync, update.effective_user)
    if sum(counts.values()) == 0:
        await send_screen(update,
            f"{get_emoji('lab')} کوله‌پشتی‌ات خالیه! از باکس‌های ژنتیکی (📦 باکس ژنتیکی) تجهیزات به‌دست بیار.",
            parse_mode="HTML",
            reply_markup=back_only_keyboard(),
        )
        return
    text, keyboard = _inv_home_render(counts)
    await send_screen(update, text, parse_mode="HTML", reply_markup=keyboard)


async def inventory_cat_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    parts = query.data.split(":")
    # new form: inv_cat:<slot>:<filter>:<page> ; old form (stale keyboard): inv_cat:<slot>:<page>
    if len(parts) == 4:
        _, slot, filt, page = parts
    else:
        _, slot, page = parts
        filt = "all"
    items = await run_db(_inv_cat_sync, update.effective_user, slot)
    await query.answer()
    text, keyboard = _inv_cat_render(slot, items, filt, int(page))
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


def _item_detail_sync(tg_user, item_id):
    user, _ = get_or_create_user(tg_user)
    try:
        item = Equipment.objects.get(id=item_id, owner=user)
    except Equipment.DoesNotExist:
        raise GameError("این تجهیزات دیگه توی کوله‌پشتیت نیست.")
    dupes = list(
        Equipment.objects.filter(owner=user, slot=item.slot, template_key=item.template_key, rarity=item.rarity)
        .exclude(id=item.id)
    )
    return item, dupes


def _item_detail_keyboard(item: Equipment, dupe_count: int) -> InlineKeyboardMarkup:
    rows = []
    if item.equipped_on_id:
        rows.append([btn("خارج کردن از موجود", emoji_key="btn_inventory", style=DANGER, callback_data=f"inv_unequip:{item.id}")])
    else:
        rows.append([btn("تجهیز روی موجود فعال", emoji_key="btn_attack", style=BUILD, callback_data=f"inv_equip:{item.id}")])
    if item.level < constants.EQUIPMENT_MAX_LEVEL and dupe_count > 0:
        rows.append([btn("✨ ارتقا با یه نمونه‌ی مشابه", style=BUILD, callback_data=f"inv_upgrade:{item.id}")])
    rows.append([back_btn("menu:inventory", "بازگشت به کوله‌پشتی")])
    return InlineKeyboardMarkup(rows)


async def inventory_pick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    item_id = int(query.data.split(":")[1])
    try:
        item, dupes = await run_db(_item_detail_sync, update.effective_user, item_id)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer()
    await safe_edit_message_text(query,
        _item_line(item), parse_mode="HTML", reply_markup=_item_detail_keyboard(item, len(dupes))
    )


def _equip_sync(tg_user, item_id):
    user, _ = get_or_create_user(tg_user)
    creature = get_active_creature(user)
    if creature is None:
        raise GameError("اول یه موجود فعال انتخاب کن.")
    return equip_item(user, creature, item_id)


async def inventory_equip_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    item_id = int(query.data.split(":")[1])
    try:
        item = await run_db(_equip_sync, update.effective_user, item_id)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer("⚔️ تجهیز شد!")
    await safe_edit_message_text(query,
        f"⚔️ {constants.EQUIPMENT_SLOT_LABELS[item.slot]} <b>{item.name}</b> +{item.level} روی موجود فعالت تجهیز شد!\n\n"
        + _item_line(item),
        parse_mode="HTML",
        reply_markup=_item_detail_keyboard(item, 0),
    )


def _unequip_sync(tg_user, item_id):
    user, _ = get_or_create_user(tg_user)
    return unequip_item(user, item_id)


async def inventory_unequip_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    item_id = int(query.data.split(":")[1])
    try:
        item = await run_db(_unequip_sync, update.effective_user, item_id)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    _, dupes = await run_db(_item_detail_sync, update.effective_user, item_id)
    await query.answer("🎒 خارج شد.")
    await safe_edit_message_text(query,
        f"🎒 {item.name} به کوله‌پشتی برگشت.\n\n" + _item_line(item),
        parse_mode="HTML",
        reply_markup=_item_detail_keyboard(item, len(dupes)),
    )


async def inventory_upgrade_list_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    item_id = int(query.data.split(":")[1])
    try:
        item, dupes = await run_db(_item_detail_sync, update.effective_user, item_id)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    if not dupes:
        await query.answer("هیچ نمونه‌ی مشابهی نداری.", show_alert=True)
        return
    await query.answer()
    rows = [
        [btn(f"{d.name} +{d.level} (#{d.id})", style=CONFIRM, callback_data=f"inv_up_do:{item.id}:{d.id}")]
        for d in dupes
    ]
    rows.append([back_btn(f"inv_pick:{item.id}")])
    await safe_edit_message_text(query,
        f"✨ کدوم نمونه رو مصرف کنم تا <b>{item.name}</b> +{item.level} ارتقا پیدا کنه؟",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(rows),
    )


def _upgrade_item_sync(tg_user, item_id, dupe_id):
    user, _ = get_or_create_user(tg_user)
    return upgrade_item(user, item_id, dupe_id)


async def inventory_upgrade_do_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, item_id, dupe_id = query.data.split(":")
    try:
        item = await run_db(_upgrade_item_sync, update.effective_user, int(item_id), int(dupe_id))
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    _, dupes = await run_db(_item_detail_sync, update.effective_user, int(item_id))
    await query.answer("✨ ارتقا یافت!")
    await safe_edit_message_text(query,
        f"✨ {item.name} به <b>+{item.level}</b> ارتقا یافت!\n\n" + _item_line(item),
        parse_mode="HTML",
        reply_markup=_item_detail_keyboard(item, len(dupes)),
    )


async def equip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Power-user shortcut — the advertised path is /inventory's buttons."""
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(f"{get_emoji('collection')} برای تجهیز از /inventory استفاده کن.")
        return
    try:
        item = await run_db(_equip_sync, update.effective_user, int(context.args[0]))
    except GameError as exc:
        await update.message.reply_text(str(exc))
        return
    await update.message.reply_text(
        f"⚔️ {constants.EQUIPMENT_SLOT_LABELS[item.slot]} <b>{item.name}</b> +{item.level} روی موجود فعالت تجهیز شد!",
        parse_mode="HTML",
    )


async def unequip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Power-user shortcut — the advertised path is /inventory's buttons."""
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(f"{get_emoji('collection')} برای خارج کردن از /inventory استفاده کن.")
        return
    try:
        item = await run_db(_unequip_sync, update.effective_user, int(context.args[0]))
    except GameError as exc:
        await update.message.reply_text(str(exc))
        return
    await update.message.reply_text(f"🎒 {item.name} به کوله‌پشتی برگشت.", parse_mode="HTML")


async def upgrade_item_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Power-user shortcut — the advertised path is /inventory's buttons."""
    if len(context.args) != 2 or not all(a.isdigit() for a in context.args):
        await update.message.reply_text(f"{get_emoji('collection')} برای ارتقا از /inventory استفاده کن.")
        return
    try:
        item = await run_db(
            _upgrade_item_sync, update.effective_user, int(context.args[0]), int(context.args[1])
        )
    except GameError as exc:
        await update.message.reply_text(str(exc))
        return
    await update.message.reply_text(f"✨ {item.name} به <b>+{item.level}</b> ارتقا یافت!", parse_mode="HTML")


def _forge_home_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    items = forgeable_items(user)
    counts = {slot: 0 for slot in constants.EQUIPMENT_SLOTS}
    for i in items:
        counts[i.slot] = counts.get(i.slot, 0) + 1
    return user, counts


def _forge_home_render(user, counts: dict) -> tuple[str, InlineKeyboardMarkup]:
    text = (
        "⚒ <b>آهنگری</b>\n"
        f"با <b>طلا</b> سطح تجهیزات رو بالا ببر، یا با «🔗 ترکیب هم‌نوع» یه تجهیزات رو "
        "قربانیِ یکی دیگه کن (ریسک شکست داره).\n\n"
        f"{get_emoji('coin')} طلای تو: <b>{user.coins}</b>\n\n"
        "یه دسته انتخاب کن:"
    )
    # a 2×2 grid of the four equipment types, each showing how many are upgradeable
    slots = constants.EQUIPMENT_SLOTS
    rows = []
    for i in range(0, len(slots), 2):
        row = []
        for slot in slots[i : i + 2]:
            row.append(btn(
                f"{constants.EQUIPMENT_SLOT_LABELS[slot]} ({counts.get(slot, 0)})",
                style=NAV, callback_data=f"forge_cat:{slot}:all:0",
            ))
        rows.append(row)
    rows.append([back_btn("menu:me")])
    return text, InlineKeyboardMarkup(rows)


def _forge_cat_sync(tg_user, slot):
    user, _ = get_or_create_user(tg_user)
    items = [i for i in forgeable_items(user) if i.slot == slot]
    return user, items


def _forge_cat_render(user, slot, items, filt: str, page: int) -> tuple[str, InlineKeyboardMarkup]:
    label = constants.EQUIPMENT_SLOT_LABELS[slot]
    if not items:
        return (
            f"⚒ <b>آهنگری — {label}</b>\n\nهیچ موردی برای ارتقا توی این دسته نداری.",
            InlineKeyboardMarkup([[back_btn("menu:blacksmith", "بازگشت به آهنگری")]]),
        )
    # rarity tabs (only what the player owns in this slot) + «همه», like the collection
    counts = {}
    for i in items:
        counts[i.rarity] = counts.get(i.rarity, 0) + 1
    tabs = [btn(("• " if filt == "all" else "") + f"همه ({len(items)})",
                style=NAV, callback_data=f"forge_cat:{slot}:all:0")]
    for r in reversed(constants.RARITY_ORDER):
        if counts.get(r):
            mark = "• " if filt == r else ""
            tabs.append(btn(f"{mark}{constants.RARITY_LABELS[r]} ({counts[r]})",
                            style=NAV, callback_data=f"forge_cat:{slot}:{r}:0"))
    rows = [tabs[i:i + 3] for i in range(0, len(tabs), 3)]
    shown = items if filt == "all" else [i for i in items if i.rarity == filt]
    total_pages = max(1, (len(shown) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    chunk = shown[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
    for i in chunk:
        rows.append([btn(
            f"{constants.RARITY_LABELS[i.rarity]} {i.name} +{i.level}",
            style=LIST, callback_data=f"forge_pick:{i.id}",
        )])
    nav = []
    if page > 0:
        nav.append(btn("قبلی", emoji_key="btn_prev", style=NAV, callback_data=f"forge_cat:{slot}:{filt}:{page - 1}"))
    if page < total_pages - 1:
        nav.append(btn("بعدی", emoji_key="btn_next", style=NAV, callback_data=f"forge_cat:{slot}:{filt}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([back_btn("menu:blacksmith", "بازگشت به دسته‌ها")])
    page_note = f"  (صفحه {page + 1}/{total_pages})" if total_pages > 1 else ""
    rarity_note = "" if filt == "all" else f" · {constants.RARITY_LABELS[filt]}"
    text = f"⚒ <b>آهنگری — {label}</b>{rarity_note}{page_note}\nنایابی رو انتخاب کن، بعد رو آیتم بزن:"
    return text, InlineKeyboardMarkup(rows)


async def blacksmith_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user, counts = await run_db(_forge_home_sync, update.effective_user)
    text, keyboard = _forge_home_render(user, counts)
    await send_screen(update, text, parse_mode="HTML", reply_markup=keyboard)


async def forge_cat_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    parts = query.data.split(":")
    if len(parts) == 4:
        _, slot, filt, page = parts
    else:  # old form from a stale keyboard: forge_cat:<slot>:<page>
        _, slot, page = parts
        filt = "all"
    user, items = await run_db(_forge_cat_sync, update.effective_user, slot)
    await query.answer()
    text, keyboard = _forge_cat_render(user, slot, items, filt, int(page))
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


def _forge_detail_sync(tg_user, item_id):
    user, _ = get_or_create_user(tg_user)
    try:
        item = Equipment.objects.get(id=item_id, owner=user)
    except Equipment.DoesNotExist:
        raise GameError("این تجهیزات پیدا نشد.")
    return user, item, forge_preview(item)


def _forge_detail_text(user, item, preview) -> str:
    fail_pct = round(preview["fail_chance"] * 100)
    risk = "بدون ریسک ✅" if fail_pct == 0 else f"شانس شکست: <b>{fail_pct}٪</b> ⚠️"
    return (
        f"⚒ <b>آهنگری</b>\n\n"
        f"{_item_line(item)}\n\n"
        f"🎯 ارتقا به <b>+{preview['target_level']}</b>\n"
        f"{get_emoji('coin')} هزینه: <b>{preview['cost']}</b> (موجودی تو: {user.coins})\n"
        f"{risk}\n\n"
        "<blockquote>در صورت شکست، طلا خرج می‌شه ولی سطح بالا نمی‌ره. "
        "راه بی‌ریسک، ارتقا با نمونه‌ی تکراری از «🎒 تجهیزات» ـه.</blockquote>"
    )


def _forge_detail_keyboard(item_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [btn("بزن! (ارتقا با طلا)", emoji_key="btn_forge", style=BUILD, callback_data=f"forge_do:{item_id}")],
            [btn("🔗 ترکیب هم‌نوع (قربانی تجهیزات)", style=NAV, callback_data=f"efuse_start:{item_id}")],
            [back_btn("menu:blacksmith", "بازگشت به آهنگری")],
        ]
    )


# Multi-select equipment fusion: tick several same-slot sacrifices and feed them
# into the target one roll each. Selection is kept in user_data keyed by target id.
_EFUSE_SEL = "efuse_sel"


def _efuse_selection(context, target_id: int) -> set[int]:
    store = context.user_data.setdefault(_EFUSE_SEL, {})
    return store.setdefault(target_id, set())


def _efuse_scored_sync(tg_user, target_id):
    user, _ = get_or_create_user(tg_user)
    from game.equipment import _fuse_fail_chance

    try:
        target = Equipment.objects.get(id=target_id, owner=user)
    except Equipment.DoesNotExist:
        raise GameError("این تجهیزات پیدا نشد.")
    candidates = same_slot_candidates(user, target_id)
    return target, [(c, _fuse_fail_chance(target, c)) for c in candidates]


def _efuse_pick_render(target, scored, selected: set[int]) -> tuple[str, InlineKeyboardMarkup]:
    if not scored:
        return (
            f"🔗 <b>ترکیب هم‌نوع</b>\n\n{_item_line(target)}\n\n"
            "هیچ تجهیزات هم‌نوع دیگه‌ای برای قربانی کردن نداری.",
            InlineKeyboardMarkup([[back_btn(f"forge_pick:{target.id}", "بازگشت")]]),
        )
    shown = scored[:PAGE_SIZE]
    valid_ids = {c.id for c, _ in shown}
    selected = selected & valid_ids
    rows = []
    for c, fail in shown:
        mark = "✅" if c.id in selected else "⬜️"
        used = " · درحال‌استفاده" if c.equipped_on_id else ""
        rows.append([btn(
            f"{mark} {constants.RARITY_LABELS[c.rarity]} {c.name} +{c.level}  (شکست {round(fail * 100)}٪){used}",
            style=LIST, callback_data=f"efuse_tog:{target.id}:{c.id}",
        )])
    if len(selected) == len(shown):
        rows.append([btn("◻️ برداشتن همه", style=NAV, callback_data=f"efuse_none:{target.id}")])
    else:
        rows.append([btn("✅ انتخاب همه", style=NAV, callback_data=f"efuse_all:{target.id}")])
    if selected:
        rows.append([btn(
            f"🔗 ترکیب ({len(selected)} تا)",
            style=CONFIRM, callback_data=f"efuse_multi:{target.id}",
        )])
    rows.append([back_btn(f"forge_pick:{target.id}", "بازگشت")])
    text = (
        f"🔗 <b>ترکیب هم‌نوع</b>\n\n"
        f"هدف: {_item_line(target)}\n\n"
        "هرچند تا قربانی که می‌خوای رو <b>تیک بزن</b> — هر کدوم یه شانس جدا برای <b>+۱</b> سطحه. "
        "<i>قربانی هرچی نایاب‌تر و بالاتر، شانس موفقیت بیشتر. قربانی در هر صورت مصرف می‌شه.</i>"
    )
    return text, InlineKeyboardMarkup(rows)


async def _efuse_rerender(update, context, target_id: int) -> None:
    query = update.callback_query
    try:
        target, scored = await run_db(_efuse_scored_sync, update.effective_user, target_id)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    selected = _efuse_selection(context, target_id)
    text, keyboard = _efuse_pick_render(target, scored, selected)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


async def efuse_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    target_id = int(query.data.split(":")[1])
    context.user_data.setdefault(_EFUSE_SEL, {})[target_id] = set()
    await query.answer()
    await _efuse_rerender(update, context, target_id)


async def efuse_toggle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, target_id, sac_id = query.data.split(":")
    target_id, sac_id = int(target_id), int(sac_id)
    _efuse_selection(context, target_id).symmetric_difference_update({sac_id})
    await query.answer()
    await _efuse_rerender(update, context, target_id)


async def efuse_select_all_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    action, target_id = query.data.split(":")
    target_id = int(target_id)
    if action == "efuse_none":
        context.user_data.setdefault(_EFUSE_SEL, {})[target_id] = set()
    else:
        try:
            _target, scored = await run_db(_efuse_scored_sync, update.effective_user, target_id)
        except GameError as exc:
            await query.answer(str(exc), show_alert=True)
            return
        context.user_data.setdefault(_EFUSE_SEL, {})[target_id] = {c.id for c, _ in scored[:PAGE_SIZE]}
    await query.answer()
    await _efuse_rerender(update, context, target_id)


def _efuse_multi_sync(tg_user, target_id, sac_ids):
    user, _ = get_or_create_user(tg_user)
    from game.equipment import fuse_equipment_many

    return fuse_equipment_many(user, target_id, sac_ids)


async def efuse_multi_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    target_id = int(query.data.split(":")[1])
    selection = list(_efuse_selection(context, target_id))
    if not selection:
        await query.answer("اول حداقل یه تجهیزات رو تیک بزن.", show_alert=True)
        return
    try:
        result = await run_db(_efuse_multi_sync, update.effective_user, target_id, selection)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    context.user_data.get(_EFUSE_SEL, {}).pop(target_id, None)
    target = result["target"]
    await query.answer(f"🎉 {result['successes']} موفق / 💥 {result['fails']} شکست")
    head = (
        f"🔗 <b>ترکیب چندتایی انجام شد</b>\n"
        f"🎉 موفق: <b>{result['successes']}</b>  ·  💥 شکست: <b>{result['fails']}</b>  "
        f"(از {result['consumed']} قربانی)\n"
        f"سطح فعلی: <b>+{result['new_level']}</b>"
    )
    if result["capped"]:
        head += "\n<i>به سقف فعلی آهنگری رسید — برای بالاتر، ⚒ آهنگری رو ارتقا بده.</i>"
    await safe_edit_message_text(
        query,
        f"{head}\n\n{_item_line(target)}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [btn("🔗 ترکیب دوباره", style=NAV, callback_data=f"efuse_start:{target.id}")],
            [back_btn("menu:blacksmith", "بازگشت به آهنگری")],
        ]),
    )


async def forge_pick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    item_id = int(query.data.split(":")[1])
    try:
        user, item, preview = await run_db(_forge_detail_sync, update.effective_user, item_id)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer()
    await safe_edit_message_text(
        query,
        _forge_detail_text(user, item, preview),
        parse_mode="HTML",
        reply_markup=_forge_detail_keyboard(item_id),
    )


def _forge_do_sync(tg_user, item_id):
    user, _ = get_or_create_user(tg_user)
    result = forge(user, item_id)
    user.refresh_from_db()
    return user, result, forge_preview(result["item"])


async def forge_do_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    item_id = int(query.data.split(":")[1])
    try:
        user, result, preview = await run_db(_forge_do_sync, update.effective_user, item_id)
    except GameError as exc:
        from bot.handlers.shop import show_gold_error

        if await show_gold_error(query, exc):
            return
        await query.answer(str(exc), show_alert=True)
        return

    item = result["item"]
    if result["success"]:
        header = f"✨ <b>موفق!</b> {item.name} رسید به <b>+{item.level}</b>"
        await query.answer("✨ موفق!")
    else:
        header = f"💥 <b>شکست خورد!</b> {result['cost']} طلا سوخت و سطح تغییر نکرد."
        await query.answer("💥 شکست خورد.")

    await safe_edit_message_text(
        query,
        f"{header}\n\n" + _forge_detail_text(user, item, preview),
        parse_mode="HTML",
        reply_markup=_forge_detail_keyboard(item.id),
    )


def register(application) -> None:
    application.add_handler(CommandHandler("inventory", inventory_cmd, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("blacksmith", blacksmith_panel, filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(forge_pick_callback, pattern=r"^forge_pick:"))
    application.add_handler(CallbackQueryHandler(forge_cat_callback, pattern=r"^forge_cat:"))
    application.add_handler(CallbackQueryHandler(forge_do_callback, pattern=r"^forge_do:"))
    application.add_handler(CallbackQueryHandler(efuse_start_callback, pattern=r"^efuse_start:\d+$"))
    application.add_handler(CallbackQueryHandler(efuse_toggle_callback, pattern=r"^efuse_tog:\d+:\d+$"))
    application.add_handler(CallbackQueryHandler(efuse_select_all_callback, pattern=r"^efuse_(all|none):\d+$"))
    application.add_handler(CallbackQueryHandler(efuse_multi_callback, pattern=r"^efuse_multi:\d+$"))
    application.add_handler(CommandHandler("equip", equip_cmd, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("unequip", unequip_cmd, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("upgrade_item", upgrade_item_cmd, filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(inventory_pick_callback, pattern=r"^inv_pick:"))
    application.add_handler(CallbackQueryHandler(inventory_cat_callback, pattern=r"^inv_cat:"))
    application.add_handler(CallbackQueryHandler(inventory_equip_callback, pattern=r"^inv_equip:"))
    application.add_handler(CallbackQueryHandler(inventory_unequip_callback, pattern=r"^inv_unequip:"))
    application.add_handler(CallbackQueryHandler(inventory_upgrade_do_callback, pattern=r"^inv_up_do:"))
    application.add_handler(CallbackQueryHandler(inventory_upgrade_list_callback, pattern=r"^inv_upgrade:"))

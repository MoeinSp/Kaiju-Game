from django.db import transaction
from django.utils import timezone
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

from bio_lab.models import Alliance, Creature, User
from bio_lab.repository import (
    creature_has_nickname,
    creature_name,
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
from bot.handlers.league import league_panel
from bot.handlers.shop import gold_shop_panel, item_shop_panel, shield_shop_panel, shop_panel
from bot.handlers.exchange import exchange_panel
from bot.handlers.casino import casino_panel
from bot.handlers.titles import titles_panel
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
from game import botconfig, constants, keywords
from game.alliance import (
    alliance_info,
    approve_request,
    create_alliance,
    deposit_treasury,
    heist,
    leave_alliance,
    list_alliances_page,
    pending_requests,
    reject_request,
    request_or_join,
    request_or_join_by_id,
    search_alliances,
    set_join_settings,
    top_alliances,
)
from game.buildings import get_or_create_buildings, grant_speedup_card, is_built, star_cap
from game.creature import (
    GameError,
    create_starter_creature,
    devour_candidates,
    effective_stats,
    feed,
    list_creatures,
    set_active_creature,
    train,
    upgrade_part,
)
from game.daily import apply_daily_login, check_missions, consume_daily, mission_status, record_action
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
    # under energy: how long until the next point (or "full")
    from game.energy import minutes_until_next_point

    if energy >= constants.MAX_ENERGY:
        energy_note = "<i>پره ✅</i>"
    else:
        energy_note = f"<i>⏳ تا انرژی بعدی ~{minutes_until_next_point(user)} دقیقه</i>"
    return (
        f"{get_emoji('coin')} طلا: <b>{user.coins:,}</b>\n"
        f"{get_emoji('dna')} DNA: <b>{user.dna_fragments:,}</b>\n"
        f"{get_emoji('diamond')} الماس: <b>{user.diamonds:,}</b>\n"
        f"{get_emoji('energy')} انرژی: <b>{energy}</b>/{constants.MAX_ENERGY}   {energy_note}"
    )


def pct_bar(current: int, total: int, width: int = 10) -> str:
    """A `[■■□□□□□□□□] 42%` progress bar — the visual style used across the main
    dashboard cards (lab XP, creature XP, energy, win chance)."""
    total = max(int(total), 1)
    pct = max(0, min(100, round(current / total * 100)))
    filled = min(width, max(0, round(width * max(current, 0) / total)))
    return f"[{'■' * filled}{'□' * (width - filled)}] {pct}%"


_CARD_DIV = "──────────────"


def creature_picker_frame(creatures, filt, page, page_size, tab_cb, nav_cb):
    """The shared rarity-tab + pagination frame used by the collection, team and
    worker pickers so they all look identical. `tab_cb(filt)` and `nav_cb(filt, page)`
    return the callback_data for those buttons. Returns
    (tab_rows, chunk, nav_rows, total_pages, page, filtered_count)."""
    rank = {r: i for i, r in enumerate(constants.RARITY_ORDER)}
    counts = {}
    for c in creatures:
        counts[c.rarity] = counts.get(c.rarity, 0) + 1
    tabs = [btn(("• " if filt == "all" else "") + f"همه ({len(creatures)})", style=NAV, callback_data=tab_cb("all"))]
    for r in reversed(constants.RARITY_ORDER):
        if counts.get(r):
            lbl = constants.RARITY_LABELS[r].split()[0]
            tabs.append(btn(("• " if filt == r else "") + f"{lbl} ({counts[r]})", style=NAV, callback_data=tab_cb(r)))
    tab_rows = [tabs[i:i + 3] for i in range(0, len(tabs), 3)]
    filtered = creatures if filt == "all" else [c for c in creatures if c.rarity == filt]
    filtered = sorted(filtered, key=lambda c: (rank.get(c.rarity, 0), c.star_level, c.level), reverse=True)
    total_pages = max(1, (len(filtered) + page_size - 1) // page_size)
    page = max(0, min(page, total_pages - 1))
    chunk = filtered[page * page_size:(page + 1) * page_size]
    nav = []
    if page > 0:
        nav.append(btn("قبلی", emoji_key="btn_prev", style=NAV, callback_data=nav_cb(filt, page - 1)))
    if page < total_pages - 1:
        nav.append(btn("بعدی", emoji_key="btn_next", style=NAV, callback_data=nav_cb(filt, page + 1)))
    nav_rows = [nav] if nav else []
    return tab_rows, chunk, nav_rows, total_pages, page, len(filtered)


# win_chance is CALIBRATED to the actual combat: p = my^k / (my^k + opp^k) with k=9
# fits the measured win-rate from game.combat's per-fight simulation (parity ≈ 50%, a
# +25% power lead ≈ 85%, a 1.5× lead ≈ 97%). So the number a player sees genuinely
# predicts how often they'd win — it's a real probability they can analyse and act on,
# not a vague vibe. Bounded to [5, 95] so it's never a promised 0/100 (a fight is never
# a sure thing). Element advantage counts as a ~1.10× effective-power edge (measured:
# an element edge at equal power wins ~71% of the time).
_WIN_CHANCE_EXP = 14
ELEMENT_POWER_FACTOR = 1.15


def win_chance_pct(my_power: int, opp_power: int, my_elem=None, opp_elem=None) -> int:
    my = max(1, int(my_power))
    opp = max(1, int(opp_power))
    my_eff, opp_eff = float(my), float(opp)
    if my_elem and opp_elem:
        mult = constants.element_multiplier(my_elem, opp_elem)
        if mult > 1:
            my_eff *= ELEMENT_POWER_FACTOR
        elif mult < 1:
            opp_eff *= ELEMENT_POWER_FACTOR
    ratio = my_eff / opp_eff
    r_k = ratio ** _WIN_CHANCE_EXP
    p = r_k / (1 + r_k)
    return max(5, min(95, round(p * 100)))


def win_label(pct: int) -> str:
    if pct >= 80:
        return "🟢 (بسیار بالا)"
    if pct >= 60:
        return "🟢 (بالا)"
    if pct >= 45:
        return "🟡 (نزدیک)"
    if pct >= 25:
        return "🔴 (پایین)"
    return "🔴 (خطرناک)"


def element_advantage_line(my_elem, opp_elem) -> str:
    """One-line elemental read for a battle card, from the player's point of view."""
    if not my_elem or not opp_elem:
        return ""
    mult = constants.element_multiplier(my_elem, opp_elem)
    if mult > 1:
        return f"✅ برتری عنصری: {constants.element_label(my_elem)} بر {constants.element_label(opp_elem)} غلبه دارد!"
    if mult < 1:
        return f"⚠️ ضعف عنصری: {constants.element_label(opp_elem)} بر {constants.element_label(my_elem)} برتری دارد!"
    return "➖ بدون مزیت عنصری میان دو عنصر"


def creature_card_text(user, creature, equipped_items: list | None = None) -> str:
    """The main dashboard shown on /start and /me: base + resources, the active
    creature's identity/level/XP, its combat stats, the full gear loadout, and any
    active defensive shields — each in its own clearly divided block."""
    from game.arena import (group_shield_remaining_seconds, shield_remaining_seconds,
                            _fmt_shield_remaining)
    from game.energy import minutes_until_next_point
    from game.equipment import equipment_power

    stats = effective_stats(creature, equipped_items)
    energy = sync_energy(user)
    power = _creature_power(creature, equipped_items)

    lp = lab_progress(user)
    if lp["is_max"]:
        lab_line = f"🧪 سطح آزمایشگاه: <b>{lp['level']}</b> (بیشینه)"
    else:
        lab_line = (f"🧪 سطح آزمایشگاه: <b>{lp['level']}</b> {pct_bar(lp['into'], lp['span'])} "
                    f"({lp['into']:,}/{lp['span']:,} XP)")

    if energy >= constants.MAX_ENERGY:
        en_line = f"{get_emoji('energy')} انرژی: {pct_bar(energy, constants.MAX_ENERGY)} ({energy}/{constants.MAX_ENERGY}) ✅ پره"
    else:
        en_line = (f"{get_emoji('energy')} انرژی: {pct_bar(energy, constants.MAX_ENERGY)} "
                   f"({energy}/{constants.MAX_ENERGY}) ⏳ شارژ بعدی: ~{minutes_until_next_point(user)} دقیقه")

    max_level = constants.creature_max_level(creature.rarity, creature.star_level)
    xp_needed = constants.xp_for_creature_level(creature.level)
    is_maxed = creature.level >= max_level
    stars = get_emoji("star") * creature.star_level

    lines = [
        f"🏰 پایگاه و آزمایشگاه: <b>{lab_display(user)}</b>",
        "",
        lab_line,
        "💰 خزانه منابع:",
        f"{get_emoji('coin')} طلا: <b>{user.coins:,}</b> ┃ {get_emoji('dna')} DNA: <b>{user.dna_fragments:,}</b> "
        f"┃ {get_emoji('diamond')} الماس: <b>{user.diamonds:,}</b>",
        en_line,
        "",
        _CARD_DIV,
        "",
        f"{get_emoji('creature')} موجود فعال: <b>{creature_name(creature)}</b> <code>#{creature.id}</code>",
    ]
    if creature_has_nickname(creature):
        lines.append(f"🧬 نژاد: <b>{creature.name}</b>")
    lines += [
        f"{constants.RARITY_LABELS[creature.rarity]} {stars} ┃ {constants.element_label(creature.element)}",
        f"🎖 سطح موجود: <b>{creature.level}/{max_level}</b>" + ("  ✅ بیشینه" if is_maxed else ""),
    ]
    if not is_maxed:
        lines.append(f"📈 پیشرفت لول: {pct_bar(creature.xp, xp_needed)} ({creature.xp:,}/{xp_needed:,} XP)")
    lines += [
        "",
        _CARD_DIV,
        "",
        f"⚔️ آمار مبارزه | توان کل: <b>{power:,}</b> 💪",
        "",
        f"{get_emoji('hp')} سلامت (HP): <b>{stats['hp']}</b> ┃ {get_emoji('atk')} حمله (ATK): <b>{stats['atk']}</b>",
        f"{get_emoji('def')} دفاع (DEF): <b>{stats['def']}</b> ┃ {get_emoji('spd')} سرعت (SPD): <b>{stats['spd']}</b>",
        "",
        _CARD_DIV,
        "",
        "🎒 تجهیزات و لوداوت:",
    ]
    by_slot = {i.slot: i for i in (equipped_items or [])}
    for slot in constants.EQUIPMENT_SLOTS:
        label = constants.EQUIPMENT_SLOT_LABELS[slot]
        it = by_slot.get(slot)
        if it is not None:
            lines.append(f"{label}: {it.name} [+{it.level}] (+{equipment_power(it)} 💪)")
        else:
            lines.append(f"{label}: <i>خالی</i>")

    arena_secs = shield_remaining_seconds(user)
    group_secs = group_shield_remaining_seconds(user)
    if arena_secs > 0 or group_secs > 0:
        lines += ["", _CARD_DIV, "", "🛡 پوشش سپرهای دفاعی:"]
        if arena_secs > 0:
            lines.append(f"🏟 آرنا: {_fmt_shield_remaining(arena_secs)} باقی‌مانده")
        if group_secs > 0:
            lines.append(f"👥 گروهی: {_fmt_shield_remaining(group_secs)} باقی‌مانده")
    return "\n".join(lines)


def balance_text(user) -> str:
    """The «موجودی» / balance card — just the wallet + energy, cleanly laid out."""
    from game.energy import minutes_until_next_point

    energy = sync_energy(user)
    if energy >= constants.MAX_ENERGY:
        en_lines = [f"{get_emoji('energy')} انرژی: {pct_bar(energy, constants.MAX_ENERGY)} ({energy}/{constants.MAX_ENERGY}) ✅ پره"]
    else:
        en_lines = [
            f"{get_emoji('energy')} انرژی: {pct_bar(energy, constants.MAX_ENERGY)} ({energy}/{constants.MAX_ENERGY})",
            f"⏳ شارژ واحد بعدی: ~{minutes_until_next_point(user)} دقیقه",
        ]
    div = "──────────────"
    return "\n".join([
        "🏦 <b>خزانه دارایی | Balance</b>",
        "",
        f"👤 بازیکن: <b>{lab_display(user)}</b>",
        "",
        div,
        "",
        f"{get_emoji('coin')} طلا: <b>{user.coins:,}</b>",
        f"{get_emoji('dna')} دی‌ان‌ای (DNA): <b>{user.dna_fragments:,}</b>",
        f"{get_emoji('diamond')} الماس: <b>{user.diamonds:,}</b>",
        "",
        div,
        "",
        *en_lines,
    ])


def _balance_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    return user


async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await run_db(_balance_sync, update.effective_user)
    chat = update.effective_chat
    in_group = chat is not None and chat.type in ("group", "supergroup")
    # in a group the balance is a plain readout — no «بازگشت» button (it opened the
    # PV menu, which is broken in a group). Only the DM gets the back button.
    keyboard = None if in_group else back_only_keyboard("menu:me", "بازگشت به منو")
    await send_screen(update, balance_text(user), parse_mode="HTML", reply_markup=keyboard)


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
            from game.equipment import equipment_power

            item = row["item"]
            lines.append(f"{row['label']}: {item.name} <b>+{item.level}</b> · 💪{equipment_power(item)}")
    return lines


def _part_power_gain(creature, part: str, equipped_items: list | None, step: int = 1) -> int:
    """How much 💪 power upgrading `part` by `step` levels would add — computed
    exactly by re-scoring the creature with the part bumped (then restored), so the
    number a player sees before spending gold is the real gain, not a guess."""
    from game.creature import combat_rating

    attr = f"{part}_lvl"
    before = combat_rating(effective_stats(creature, equipped_items))
    current = getattr(creature, attr)
    setattr(creature, attr, current + max(1, step))
    after = combat_rating(effective_stats(creature, equipped_items))
    setattr(creature, attr, current)  # restore — the object may be reused/saved elsewhere
    return after - before


def upgrade_panel_text(user, creature, equipped_items: list | None = None, slots: list | None = None, step: int = 1) -> str:
    from game.creature import part_bulk_cost
    from game.energy import minutes_until_next_point
    from game.equipment import equipment_power

    stats = effective_stats(creature, equipped_items)
    stars = get_emoji("star") * creature.star_level
    mode = "🟢 حالت: پیش‌فرض" if creature.is_active else "⚪️ حالت: ذخیره"
    max_level = constants.creature_max_level(creature.rarity, creature.star_level)
    div = "──────────────"
    lines = [
        f"🦅 <b>{creature_name(creature)}</b> <code>#{creature.id}</code>",
    ]
    if creature_has_nickname(creature):
        lines.append(f"🧬 نژاد: <b>{creature.name}</b>")
    lines += [
        f"<b>مشخصات:</b> {constants.RARITY_LABELS[creature.rarity]} {stars}",
        f"🎖 <b>سطح موجود:</b> {creature.level}/{max_level} ┃ {mode}",
        f"⚡️ <b>نرخ ارتقا:</b> {step}× سطحی",
        "", div, "",
        "📊 <b>شاخص‌های مبارزه (Base Stats):</b>",
        "",
        f"{get_emoji('hp')} سلامت (HP): <b>{stats['hp']}</b> ┃ {get_emoji('atk')} حمله (ATK): <b>{stats['atk']}</b>",
        f"{get_emoji('def')} دفاع (DEF): <b>{stats['def']}</b> ┃ {get_emoji('spd')} سرعت (SPD): <b>{stats['spd']}</b>",
        f"{get_emoji('poison')} زهر (Poison): <b>{stats['poison']}</b>",
        "", div, "",
        "🧩 <b>وضعیت اعضای بدن (Body Parts):</b>",
        "",
    ]
    cap = constants.part_upgrade_cap(creature.star_level)
    any_capped = False
    for part, cfg in constants.BODY_PARTS.items():
        level = getattr(creature, f"{part}_lvl")
        if level >= cap:
            any_capped = True
            lines.append(f"{cfg['label']}: <b>{level}/{cap}</b> 🔒 (سقف {creature.star_level} ستاره)")
            continue
        buy = min(step, cap - level)
        cost = part_bulk_cost(level, buy)
        gain = _part_power_gain(creature, part, equipped_items, buy)
        lines.append(
            f"{cfg['label']}: <b>{level}/{cap}</b> → ارتقا: {cost:,} {get_emoji('coin')} (+{gain} 💪)"
        )
    if any_capped:
        lines.append("")
        if creature.star_level >= 5:
            lines.append(
                f"🔒 <b>قفل نهایی:</b> این هیولا 5⭐ است و اعضایش به سقف مطلق "
                f"<b>{constants.PART_UPGRADE_MAX}</b> رسیده‌اند."
            )
        else:
            lines.append(
                "⚠️ <b>قفل تکامل:</b> عضوهایی به سقف مجاز رسیده‌اند. جهت بازگشایی ارتقای بیشتر، "
                f"موجود را از طریق <b>فیوژن</b> به {creature.star_level + 1}⭐ ارتقا بده "
                f"(هر ستاره +{constants.PART_UPGRADE_CAP_PER_STAR}، تا {constants.PART_UPGRADE_MAX} در 5⭐)."
            )
    # gear
    lines += ["", div, "", "🎒 <b>تجهیزات فعال (Gear):</b>", ""]
    if slots is not None:
        any_gear = False
        for row in slots:
            if row["is_empty"]:
                lines.append(f"{row['label']}: <i>خالی</i>")
            else:
                any_gear = True
                item = row["item"]
                lines.append(f"{row['label']}: <b>{item.name} +{item.level}</b> (+{equipment_power(item)} 💪)")
        if not any_gear and all(r["is_empty"] for r in slots):
            pass  # all-empty already shown line by line
    else:
        lines.append("<i>—</i>")
    # resources
    energy = sync_energy(user)
    if energy >= constants.MAX_ENERGY:
        charge = "پره ✅"
    else:
        charge = f"⏳ شارژ بعدی: ~{minutes_until_next_point(user)} دقیقه"
    lines += [
        "", div, "",
        "🏦 <b>موجودی و منابع در دسترس:</b>",
        f"{get_emoji('coin')} طلا: <b>{user.coins:,}</b> ┃ {get_emoji('dna')} دی‌ان‌ای: <b>{user.dna_fragments:,}</b> ┃ "
        f"{get_emoji('diamond')} الماس: <b>{user.diamonds:,}</b>",
        f"{get_emoji('energy')} انرژی: {pct_bar(energy, constants.MAX_ENERGY)} ({energy}/{constants.MAX_ENERGY}) {charge}",
        "", div, "",
        "💡 <i>نکته: تغذیه و تمرین XP می‌دهند و ارتقای اعضا مستقیماً قدرت رزمی را بالا می‌برد.</i>",
    ]
    return "\n".join(lines)


def _fusion_button_label(star_level: int) -> str:
    """Label the fusion button with the ACTUAL next star this creature would reach
    (1★ → «ارتقا به ⭐۲», …), or a maxed note at 5★, so the button always tells the
    truth about what fusing does for this specific creature."""
    if star_level >= constants.STAR_MAX:
        return "🧬 فیوژن (به سقف ۵ ستاره رسیده)"
    return f"🧬 ورود به فیوژن (ارتقا به {'⭐' * (star_level + 1)})"


def upgrade_panel_keyboard(creature_id: int, is_active: bool = True, step: int = 1, star_level: int = 1) -> InlineKeyboardMarkup:
    """Every action carries the creature id, so upgrading a non-active creature
    doesn't silently swap which creature is active for hunting/arena."""
    sfx = f" ×{step}" if step > 1 else ""
    # ×1/×5/×10 selector: how many levels each part tap buys (paid in one go)
    step_row = [
        btn(("✅ " if s == step else "") + f"×{s}", style=(CONFIRM if s == step else NAV),
            callback_data=f"upg_step:{creature_id}:{s}")
        for s in _UPG_STEPS
    ]
    rows = [
        [
            btn("تغذیه", emoji_key="btn_feed", style=BUILD, callback_data=f"lab:feed:{creature_id}"),
            btn("تمرین", emoji_key="btn_train", style=BUILD, callback_data=f"lab:train:{creature_id}"),
        ],
        step_row,
        [
            btn(f"🦋 بال{sfx}", style=BUILD, callback_data=f"lab:up_wings:{creature_id}"),
            btn(f"🛡 زره{sfx}", style=BUILD, callback_data=f"lab:up_armor:{creature_id}"),
        ],
        [
            btn(f"🦷 نیش{sfx}", style=BUILD, callback_data=f"lab:up_fangs:{creature_id}"),
            btn(f"☠️ زهر{sfx}", style=BUILD, callback_data=f"lab:up_poison:{creature_id}"),
        ],
        [btn("مدیریت تجهیزات", emoji_key="btn_inventory", style=PRIMARY, callback_data=f"upg_eq:{creature_id}")],
        [btn("🍖 تقویت با خوردن هیولا", style=BUILD, callback_data=f"devour_start:{creature_id}")],
        [btn("✏️ نام‌گذاری", style=NAV, callback_data=f"kaiju_rename:{creature_id}:u")],
        [btn(_fusion_button_label(star_level), emoji_key="btn_fusion", style=PRIMARY, callback_data=f"upg_fusion:{creature_id}")],
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
        f"🎒 <b>تجهیزات {creature_name(creature)}</b>",
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
                    else " — 1 تجهیزات مناسب داری" if spare
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
    from game.creature import creature_power

    return creature_power(creature, equipped_items)


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


def _upgrade_render(user, ranked, filt: str, page: int) -> tuple[str, InlineKeyboardMarkup]:
    """One page of the strongest-first creature picker, split by rarity with tabs
    (like the collection) and paginated. `ranked` is [(creature, power)] desc."""
    # rarity tabs from the whole roster (only rarities the player owns) + «همه»
    counts = {}
    for c, _p in ranked:
        counts[c.rarity] = counts.get(c.rarity, 0) + 1
    tabs = [btn(("• " if filt == "all" else "") + f"همه ({len(ranked)})",
                style=NAV, callback_data="upg_page:all:0")]
    for r in reversed(constants.RARITY_ORDER):
        if counts.get(r):
            mark = "• " if filt == r else ""
            tabs.append(btn(f"{mark}{constants.RARITY_LABELS[r]} ({counts[r]})",
                            style=NAV, callback_data=f"upg_page:{r}:0"))
    rows = [tabs[i:i + 3] for i in range(0, len(tabs), 3)]

    shown = ranked if filt == "all" else [(c, p) for c, p in ranked if c.rarity == filt]
    total_pages = max(1, (len(shown) + UPGRADE_PAGE_SIZE - 1) // UPGRADE_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    chunk = shown[page * UPGRADE_PAGE_SIZE : (page + 1) * UPGRADE_PAGE_SIZE]

    for creature, power in chunk:
        active_tag = "🟢 " if creature.is_active else ""
        stars = "⭐" * creature.star_level
        rarity = constants.RARITY_LABELS[creature.rarity]
        rows.append([btn(
            f"{active_tag}{creature_name(creature)} {stars} · {rarity} · Lv{creature.level} · 💪{power}",
            style=LIST, callback_data=f"upg_pick:{creature.id}",
        )])
    nav = []
    if page > 0:
        nav.append(btn("قبلی", emoji_key="btn_prev", style=NAV, callback_data=f"upg_page:{filt}:{page - 1}"))
    if page < total_pages - 1:
        nav.append(btn("بعدی", emoji_key="btn_next", style=NAV, callback_data=f"upg_page:{filt}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([back_btn("menu:me")])

    page_note = f"  (صفحه {page + 1}/{total_pages})" if total_pages > 1 else ""
    rarity_note = "" if filt == "all" else f" · {constants.RARITY_LABELS[filt]}"
    text = (
        f"🔧 <b>ارتقا و پرورش</b>{rarity_note}{page_note}\n"
        f"نایابی رو انتخاب کن، بعد هیولا رو بزن (به ترتیب قدرت):\n\n{wallet_line(user)}"
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
    text, keyboard = _upgrade_render(user, ranked, "all", 0)
    await send_screen(update, text, parse_mode="HTML", reply_markup=keyboard)


async def upgrade_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    parts = query.data.split(":")
    if len(parts) == 3:  # upg_page:<filter>:<page>
        filt, page = parts[1], int(parts[2])
    else:  # old form from a stale keyboard: upg_page:<page>
        filt, page = "all", int(parts[1])
    try:
        user, ranked = await run_db(_upgrade_list_sync, update.effective_user)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer()
    text, keyboard = _upgrade_render(user, ranked, filt, page)
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
    step = context.user_data.get("upg_step", 1)
    await safe_edit_message_text(
        query,
        upgrade_panel_text(user, creature, equipped_items, slots, step=step),
        parse_mode="HTML",
        reply_markup=upgrade_panel_keyboard(creature.id, creature.is_active, step=step, star_level=creature.star_level),
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


_EQUIP_SLOT_PAGE = 8


async def equip_slot_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """The candidates for one slot — split by rarity with tabs and paginated (like the
    blacksmith) — plus a way to strip whatever's in it."""
    query = update.callback_query
    parts = query.data.split(":")
    # upg_slot:<cid>:<slot>[:<filter>:<page>]
    creature_id = int(parts[1])
    slot = parts[2]
    filt = parts[3] if len(parts) >= 5 else "all"
    page = int(parts[4]) if len(parts) >= 5 else 0
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
    candidates = row["candidates"]
    lines = [f"{row['label']} — <b>{creature.name}</b>", ""]
    if row["item"] is not None:
        lines.append(f"الان: <b>{row['item'].name} +{row['item'].level}</b>")
        bonus = bonus_text(row["item"])
        if bonus:
            lines.append(f"<i>{bonus}</i>")
        lines.append("")

    rows = []
    if candidates:
        # rarity tabs (only rarities present among the candidates) + «همه»
        counts = {}
        for it in candidates:
            counts[it.rarity] = counts.get(it.rarity, 0) + 1
        tabs = [btn(("• " if filt == "all" else "") + f"همه ({len(candidates)})",
                    style=NAV, callback_data=f"upg_slot:{creature_id}:{slot}:all:0")]
        for r in reversed(constants.RARITY_ORDER):
            if counts.get(r):
                mark = "• " if filt == r else ""
                tabs.append(btn(f"{mark}{constants.RARITY_LABELS[r]} ({counts[r]})",
                                style=NAV, callback_data=f"upg_slot:{creature_id}:{slot}:{r}:0"))
        rows += [tabs[i:i + 3] for i in range(0, len(tabs), 3)]

        shown = candidates if filt == "all" else [it for it in candidates if it.rarity == filt]
        total_pages = max(1, (len(shown) + _EQUIP_SLOT_PAGE - 1) // _EQUIP_SLOT_PAGE)
        page = max(0, min(page, total_pages - 1))
        chunk = shown[page * _EQUIP_SLOT_PAGE : (page + 1) * _EQUIP_SLOT_PAGE]
        lines.append("نایابی رو انتخاب کن، بعد آیتم رو بذار توش:")
        for item in chunk:
            worn = f" (روی {item.equipped_on.name})" if item.equipped_on_id else ""
            rows.append([btn(
                f"{item.name} +{item.level} · {constants.RARITY_LABELS[item.rarity]}{worn}",
                style=CONFIRM, callback_data=f"upg_equip:{creature_id}:{item.id}",
            )])
        nav = []
        if page > 0:
            nav.append(btn("قبلی", emoji_key="btn_prev", style=NAV,
                           callback_data=f"upg_slot:{creature_id}:{slot}:{filt}:{page - 1}"))
        if page < total_pages - 1:
            nav.append(btn("بعدی", emoji_key="btn_next", style=NAV,
                           callback_data=f"upg_slot:{creature_id}:{slot}:{filt}:{page + 1}"))
        if nav:
            rows.append(nav)
    else:
        lines.append(
            "<blockquote>هیچ تجهیزاتی برای این جایگاه نداری. از باکس ژنتیکی و جعبه‌های الماسی "
            "می‌تونی تجهیزات به دست بیاری.</blockquote>"
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
    step = context.user_data.get("upg_step", 1)
    await safe_edit_message_text(
        query,
        upgrade_panel_text(user, creature, equipped_items, slots, step=step),
        parse_mode="HTML",
        reply_markup=upgrade_panel_keyboard(creature.id, creature.is_active, step=step, star_level=creature.star_level),
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
    lines = [f"{get_emoji('book')} <b>راهنمای Kaiju Legends</b>", ""]
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


# ── Categorised menu ──────────────────────────────────────────────────────────
# The full feature list is ~28 items — too many on one screen, but hiding it all
# behind six category buttons felt empty. Middle ground: the core gameplay loop
# is shown directly (a full, colourful grid), and the long tail is folded into
# three category buttons that open a submenu in place.
#
# Each button spec is (label, menu-action, style-key, emoji_key-or-None). IMPORTANT:
# when emoji_key is set, the label must NOT also contain a literal emoji — Telegram
# draws the emoji_key icon before the label, so a literal one shows the emoji
# twice. Features that have a registry key use it (for the owner's Premium emoji
# theming); newer features that don't carry a literal emoji instead.
_STYLE_MAP = {"p": PRIMARY, "b": BATTLE, "n": NAV, "s": SHOP, "c": CONFIRM}


def _mkbtn(spec):
    label, action, style, ekey = spec
    return btn(label, emoji_key=ekey, style=_STYLE_MAP[style], callback_data=f"menu:{action}")


# shown directly on the main menu — the core loop. Every button carries an
# emoji_key (never a literal emoji in the label), and both buttons in a row share
# one style so each row is colour-symmetric.
_MAIN_ROWS = [
    [("ارتقا و پرورش", "upgrade", "p", "btn_upgrade")],
    [("شکار انفرادی", "hunt", "b", "btn_hunt"), ("آرنا (کاپ)", "arena", "b", "btn_arena")],
    [("دانجن", "campaign", "b", "btn_campaign"), ("تیم من", "team", "b", "btn_team")],
    [("کلکسیون", "collection", "n", "btn_collection"), ("ترکیب هیولا", "fusion", "n", "btn_fusion")],
    [("غار هیولا", "breeding", "n", "btn_breeding"), ("ساختمون‌ها", "buildings", "n", "btn_buildings")],
    [("تجهیزات", "inventory", "n", "btn_inventory"), ("آهنگری", "blacksmith", "n", "btn_forge")],
]

# folded into category submenus — the long tail. Each category's buttons share one
# style, so every row is colour-symmetric.
_CATEGORIES = {
    "rewards": ("🎁 جایزه‌ها", [
        [("ماموریت‌ها", "missions", "s", "btn_missions"), ("دستاوردها", "achievements", "s", "btn_achievements")],
        [("پاس فصلی", "battlepass", "s", "btn_battlepass"), ("رویداد", "events", "s", "btn_events")],
        [("گردونه‌ی شانس", "wheel", "s", "btn_wheel"), ("پاداش آفلاین", "idle", "s", "btn_idle")],
        [("دانشنامه", "codex", "s", "btn_codex"), ("دعوت دوستان", "referral", "s", "btn_referral")],
    ]),
    "shop": ("🛒 فروشگاه", [
        [("باکس ژنتیکی", "biocrate", "s", "btn_biocrate"), ("جعبه‌های الماسی", "diamond_box", "s", "btn_diamond_box")],
        [("بنر ویژه", "banner", "s", "btn_banner"), ("شاپ روزانه", "shop", "s", "btn_shop")],
        [("خرید سپر", "shield_shop", "s", "btn_shield"), ("کازینو", "casino", "s", "btn_casino")],
        [("آیتم‌های ویژه", "item_shop", "s", "btn_items"), ("خرید طلا", "gold_shop", "s", "btn_gold_shop")],
        [("مبادله طلا و DNA", "exchange", "s", "btn_exchange")],
    ]),
    "social": ("👥 اجتماعی", [
        [("اتحاد من", "alliance_info", "n", "btn_alliance"), ("لیگ رتبه‌بندی", "league", "n", "btn_league")],
        [("🏰 لیگ اتحادها", "alliance_league", "n", "btn_alliance"), ("رتبه‌بندی", "rank", "n", "btn_rank")],
        [("پروفایل من", "profile", "n", "btn_profile")],
    ]),
}

# the three category buttons on the main menu, in a single colour-symmetric row
_CATEGORY_BUTTONS = [
    ("جایزه‌ها", "cat_rewards", "btn_cat_rewards"),
    ("فروشگاه", "cat_shop", "btn_cat_shop"),
    ("اجتماعی", "cat_social", "btn_cat_social"),
]


def _main_menu_rows() -> list:
    rows = [[_mkbtn(spec) for spec in row] for row in _MAIN_ROWS]
    rows.append(
        [btn(label, emoji_key=ekey, style=SHOP, callback_data=f"menu:{action}") for (label, action, ekey) in _CATEGORY_BUTTONS]
    )
    rows.append([btn("راهنما", emoji_key="btn_report", style=CONFIRM, callback_data="menu:guide")])
    return rows


def _category_keyboard(cat_key: str) -> tuple[str, InlineKeyboardMarkup]:
    title, rows_def = _CATEGORIES[cat_key]
    rows = [[_mkbtn(spec) for spec in row] for row in rows_def]
    rows.append([back_btn("menu:me", "بازگشت به منو")])
    return title, InlineKeyboardMarkup(rows)


def creature_keyboard(is_owner: bool = False) -> InlineKeyboardMarkup:
    """Categorised navigation under the creature card — core loop direct, the rest
    in three category submenus."""
    rows = _main_menu_rows()
    # the owner-configured "join the game group" button, always last (in-memory read)
    group_link = botconfig.get_group_link()
    if group_link is not None:
        url, title = group_link
        rows.append([btn(title, emoji_key="btn_join_group", style=PRIMARY, url=url)])
    # in-game purchase: prefer the built-in flow when the owner has set prices + a card;
    # otherwise fall back to the owner-configured external buy link (payment bot / site)
    if botconfig.inbot_purchase_ready():
        rows.append([btn(botconfig.DEFAULT_BUY_TITLE, emoji_key="btn_buy", style=SHOP, callback_data="buy_open")])
    else:
        buy_link = botconfig.get_buy_link()
        if buy_link is not None:
            burl, btitle = buy_link
            rows.append([btn(btitle, emoji_key="btn_buy", style=SHOP, url=burl)])
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
    # The force-join gate stops the very first `/start ref_<id>` from reaching this
    # handler, so the payload it carried is stashed in user_data by capture_referral
    # (bot/middleware.py). Fall back to it when this /start arrives without args.
    if referrer_id is None:
        referrer_id = context.user_data.pop("pending_referrer", None)
    else:
        context.user_data.pop("pending_referrer", None)
    user, creature, is_new, login_bonus, equipped_items = await run_db(
        _start_sync, update.effective_user, referrer_id
    )

    if user.lab_name is None:
        context.user_data[AWAITING_PLAYER_KEY] = {"action": "set_lab_name"}
        await update.message.reply_text(
            f"{get_emoji('egg')} <b>به Kaiju Legends خوش اومدی!</b>\n"
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


def _lab_action_sync(tg_user, action, creature_id, count=1):
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
        new_level, spent = upgrade_part(user, creature, part, count)
        step_note = f" (<b>{count}</b> سطح، {spent:,} طلا)" if count > 1 else ""
        note = f"{constants.BODY_PARTS[part]['label']} به سطح {new_level} رسید{step_note}! ✨"
    else:
        return None

    equipped_items = get_equipped_items(creature)
    # slots come back too: this re-renders the upgrade panel, and fetching them
    # here keeps the equipment section from vanishing after a feed/train/upgrade
    return user, creature, note + _mission_lines(completed_missions), equipped_items, slot_loadout(user, creature)


_UPG_STEPS = (1, 5, 10)


async def upgrade_step_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Set how many levels each body-part tap buys (×1/×5/×10), then re-render."""
    query = update.callback_query
    try:
        _, creature_id, step = query.data.split(":")
        step = int(step)
    except ValueError:
        await query.answer()
        return
    context.user_data["upg_step"] = step if step in _UPG_STEPS else 1
    try:
        user, creature, equipped_items, slots = await run_db(
            _upgrade_view_sync, update.effective_user, int(creature_id)
        )
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer(f"هر ارتقا حالا ×{context.user_data['upg_step']}")
    await safe_edit_message_text(query,
        upgrade_panel_text(user, creature, equipped_items, slots, step=context.user_data["upg_step"]),
        parse_mode="HTML",
        reply_markup=upgrade_panel_keyboard(creature.id, creature.is_active, step=context.user_data["upg_step"], star_level=creature.star_level),
    )


def _upgrade_view_sync(tg_user, creature_id):
    user, _ = get_or_create_user(tg_user)
    try:
        creature = Creature.objects.get(id=creature_id, owner=user)
    except Creature.DoesNotExist:
        raise GameError("این موجود توی کلکسیون تو نیست.")
    equipped_items = get_equipped_items(creature)
    return user, creature, equipped_items, slot_loadout(user, creature)


async def lab_action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        _, action, creature_id = query.data.split(":")
    except ValueError:
        await query.answer()
        return
    # body-part upgrades honour the chosen ×1/×5/×10 step; feed/train ignore it
    step = context.user_data.get("upg_step", 1) if action.startswith("up_") else 1
    try:
        result = await run_db(_lab_action_sync, update.effective_user, action, int(creature_id), step)
    except GameError as exc:
        from bot.handlers.shop import show_gold_error

        if await show_gold_error(query, exc):
            return
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
        note + "\n\n" + upgrade_panel_text(user, creature, equipped_items, slots, step=step),
        parse_mode="HTML",
        reply_markup=upgrade_panel_keyboard(creature.id, creature.is_active, step=step, star_level=creature.star_level),
    )


def _collection_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    return list_creatures(user)


COLLECTION_PAGE_SIZE = 8


def _collection_render(creatures: list[Creature], filt: str = "all", page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    """One page of the collection, filterable by rarity via tabs (like the fusion
    picker). Paginated because a big roster (each creature is two buttons) blew past
    Telegram's ~100-button keyboard limit — the whole keyboard was rejected then."""
    rarity_idx = {r: i for i, r in enumerate(constants.RARITY_ORDER)}
    counts: dict[str, int] = {}
    for c in creatures:
        counts[c.rarity] = counts.get(c.rarity, 0) + 1

    # rarity tabs — «همه» plus only rarities the player actually owns, rarest first
    tabs = [btn(("• " if filt == "all" else "") + f"همه ({len(creatures)})", style=NAV,
                callback_data="coll_page:all:0")]
    for r in reversed(constants.RARITY_ORDER):
        if counts.get(r):
            label = constants.RARITY_LABELS[r].split()[0]
            tabs.append(btn(("• " if filt == r else "") + f"{label} ({counts[r]})", style=NAV,
                            callback_data=f"coll_page:{r}:0"))
    rows = [tabs[i:i + 3] for i in range(0, len(tabs), 3)]

    filtered = creatures if filt == "all" else [c for c in creatures if c.rarity == filt]
    filtered = sorted(filtered, key=lambda c: (rarity_idx.get(c.rarity, 0), c.level, c.star_level), reverse=True)
    total_pages = max(1, (len(filtered) + COLLECTION_PAGE_SIZE - 1) // COLLECTION_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    chunk = filtered[page * COLLECTION_PAGE_SIZE : (page + 1) * COLLECTION_PAGE_SIZE]

    for c in chunk:
        stars = "⭐" * c.star_level
        label = f"{creature_name(c)} {stars} · Lv{c.level} · {constants.RARITY_LABELS[c.rarity]}"
        row = [btn(f"{'🟢 ' if c.is_active else ''}{label}", style=LIST, callback_data=f"coll_pick:{c.id}")]
        if not c.is_active:
            row.append(btn("فعال کن", emoji_key="btn_confirm", style=CONFIRM, callback_data=f"coll_select:{c.id}"))
        rows.append(row)
    nav = []
    if page > 0:
        nav.append(btn("قبلی", emoji_key="btn_prev", style=NAV, callback_data=f"coll_page:{filt}:{page - 1}"))
    if page < total_pages - 1:
        nav.append(btn("بعدی", emoji_key="btn_next", style=NAV, callback_data=f"coll_page:{filt}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([btn("ترکیب هیولا", emoji_key="btn_fusion", style=NAV, callback_data="menu:fusion")])
    rows.append([back_btn("menu:me")])

    page_note = f"  (صفحه {page + 1}/{total_pages})" if total_pages > 1 else ""
    text = (
        f"{get_emoji('collection')} <b>کلکسیون تو</b> — {len(creatures)} موجود{page_note}\n"
        "<i>با تب‌های بالا بر اساس نایابی جدا کن.</i> رو هرکدوم بزن تا جزئیاتش رو ببینی:"
    )
    return text, InlineKeyboardMarkup(rows)


async def collection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    creatures = await run_db(_collection_sync, update.effective_user)
    if not creatures:
        await send_screen(update, f"📭 کلکسیونت خالیه! {get_emoji('egg')} با /start شروع کن.",
                          reply_markup=back_only_keyboard())
        return
    text, keyboard = _collection_render(creatures, "all", 0)
    await send_screen(update, text, parse_mode="HTML", reply_markup=keyboard)


async def collection_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    parts = query.data.split(":")
    # new form is coll_page:<filt>:<page>; tolerate the old coll_page:<page> too
    if len(parts) == 3:
        filt, page = parts[1], int(parts[2])
    else:
        filt, page = "all", int(parts[1])
    creatures = await run_db(_collection_sync, update.effective_user)
    await query.answer()
    text, keyboard = _collection_render(creatures, filt, page)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


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
    rows.append([btn("🍖 تقویت با خوردن هیولا", style=BUILD, callback_data=f"devour_start:{creature_id}")])
    rows.append([btn("✏️ نام‌گذاری", style=NAV, callback_data=f"kaiju_rename:{creature_id}:c")])
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


def _rename_prompt_sync(tg_user, creature_id):
    user, _ = get_or_create_user(tg_user)
    creature = Creature.objects.filter(id=creature_id, owner=user).first()
    if creature is None:
        raise GameError("این کایجو توی کلکسیون تو نیست.")
    from game.naming import rename_cost

    return creature, rename_cost(creature)


def _rename_kaiju_sync(tg_user, creature_id, name):
    user, _ = get_or_create_user(tg_user)
    from game.naming import rename_creature

    return rename_creature(user, creature_id, name)


async def kaiju_rename_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """«✏️ نام‌گذاری» from the collection detail or the upgrade panel — ask the player
    for a nickname. Charged (rising price) on submit in capture_player_text_reply."""
    query = update.callback_query
    parts = query.data.split(":")
    creature_id = int(parts[1])
    origin = parts[2] if len(parts) > 2 else "c"
    try:
        creature, cost = await run_db(_rename_prompt_sync, update.effective_user, creature_id)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    context.user_data[AWAITING_PLAYER_KEY] = {
        "action": "rename_kaiju", "creature_id": creature_id, "origin": origin,
    }
    from game.naming import NAME_MAX_LEN

    price_line = (
        "🎁 اولین نام‌گذاری این کایجو <b>رایگان</b>ه."
        if cost == 0 else f"{get_emoji('diamond')} هزینه: <b>{cost}</b> الماس"
    )
    back_cb = f"upg_pick:{creature_id}" if origin == "u" else f"coll_pick:{creature_id}"
    await query.answer()
    await safe_edit_message_text(
        query,
        f"✏️ <b>نام‌گذاری کایجو</b>\n"
        f"🧬 نژاد: <b>{creature.name}</b>\n"
        f"نام فعلی: <b>{creature_name(creature)}</b>\n\n"
        f"{price_line}\n"
        f"<i>یه اسم دلخواه و یکتا بفرست (حداکثر {NAME_MAX_LEN} حرف). هر بار نام‌گذاری ۱۰۰ الماس گران‌تر می‌شه.</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[back_btn(back_cb, "انصراف / بازگشت")]]),
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
        f"🟢 <b>{creature_name(creature)}</b> حالا موجود فعالته!\n\n" + creature_card_text(user, creature, equipped_items),
        parse_mode="HTML",
        reply_markup=creature_keyboard(is_owner),
    )


# ── devour: feed one OR MANY creatures to another for XP (multi-select) ────────
# Selection lives in user_data keyed by target id, so a player can tick several
# sacrifices and eat them all in one go («انتخاب چندتایی») instead of one at a time.
_DEVOUR_SEL = "devour_sel"


def _devour_selection(context, target_id: int) -> set[int]:
    store = context.user_data.setdefault(_DEVOUR_SEL, {})
    return store.setdefault(target_id, set())


def _devour_list_sync(tg_user, target_id):
    user, _ = get_or_create_user(tg_user)
    from game.creature import _devour_xp, xp_to_max_level

    try:
        target = Creature.objects.get(id=target_id, owner=user)
    except Creature.DoesNotExist:
        raise GameError("این موجود توی کلکسیون تو نیست.")
    candidates = devour_candidates(user, target_id)
    return target, [(c, _devour_xp(c)) for c in candidates], xp_to_max_level(target)


def _devour_can_add_sync(tg_user, target_id, current_ids, sac_id):
    """Whether the player may add one more sacrifice: blocked when the target is
    already maxed, or when the picks already cover the XP needed to max (so the extra
    one would be wasted). A single pick that overshoots on its own is allowed."""
    from game.creature import _devour_xp, xp_to_max_level

    user, _ = get_or_create_user(tg_user)
    try:
        target = Creature.objects.get(id=target_id, owner=user)
    except Creature.DoesNotExist:
        raise GameError("این موجود توی کلکسیون تو نیست.")
    need = xp_to_max_level(target)
    if need <= 0:
        return False, "این موجود به سقف سطحش رسیده و دیگه نمی‌تونه هیولا بخوره."
    selected_xp = sum(_devour_xp(c) for c in Creature.objects.filter(id__in=list(current_ids), owner=user))
    if selected_xp >= need:
        return False, "همین‌ها برای رسیدن به سقف سطح کافیه — بیشتر از این، قربانی هدر می‌ره. اول همین‌ها رو بخورون."
    return True, ""


_DEVOUR_PAGE = 12


def _devour_list_render(target, scored, selected: set[int], page: int = 0, xp_to_max: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    if xp_to_max <= 0:
        return (
            f"🍖 <b>تقویت {target.name}</b> (Lv{target.level})\n\n"
            "🔒 این موجود به <b>سقف سطحش</b> رسیده و دیگه نمی‌تونه هیولا بخوره.\n"
            "<i>برای ادامه‌ی رشد باید با فیوژن ستاره‌ش رو بالا ببری (سقف سطح بالاتر می‌ره).</i>",
            InlineKeyboardMarkup([[back_btn(f"coll_pick:{target.id}", "بازگشت")]]),
        )
    if not scored:
        return (
            f"🍖 <b>تقویت {target.name}</b>\n\n"
            "هیچ موجود آزادی برای خوروندن نداری (موجود فعال و موجودهای مشغول قابل قربانی نیستن).",
            InlineKeyboardMarkup([[back_btn(f"coll_pick:{target.id}", "بازگشت")]]),
        )
    all_ids = {c.id for c, _ in scored}
    selected = selected & all_ids  # drop picks that are no longer valid candidates
    total_pages = max(1, (len(scored) + _DEVOUR_PAGE - 1) // _DEVOUR_PAGE)
    page = max(0, min(page, total_pages - 1))
    chunk = scored[page * _DEVOUR_PAGE:(page + 1) * _DEVOUR_PAGE]
    rows = []
    for c, xp in chunk:
        mark = "✅" if c.id in selected else "⬜️"
        rows.append([btn(
            f"{mark} [{constants.RARITY_LABELS[c.rarity]}] {c.name} {'⭐' * c.star_level} Lv{c.level}  ➕{xp}",
            style=LIST, callback_data=f"devour_tog:{target.id}:{c.id}",
        )])
    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(btn("قبلی", emoji_key="btn_prev", style=NAV, callback_data=f"devour_page:{target.id}:{page - 1}"))
        if page < total_pages - 1:
            nav.append(btn("بعدی", emoji_key="btn_next", style=NAV, callback_data=f"devour_page:{target.id}:{page + 1}"))
        if nav:
            rows.append(nav)
    total_xp = sum(xp for c, xp in scored if c.id in selected)
    if selected and len(selected) >= len(all_ids):
        rows.append([btn("◻️ برداشتن همه", style=NAV, callback_data=f"devour_none:{target.id}")])
    else:
        rows.append([btn("✅ انتخاب همه", style=NAV, callback_data=f"devour_all:{target.id}")])
    if selected:
        rows.append([btn(
            f"🍖 بخورون ({len(selected)} تا · ➕{total_xp} XP)",
            style=CONFIRM, callback_data=f"devour_multi:{target.id}",
        )])
    rows.append([back_btn(f"coll_pick:{target.id}", "بازگشت")])
    page_note = f"  <i>(صفحه {page + 1}/{total_pages})</i>" if total_pages > 1 else ""
    enough = total_xp >= xp_to_max
    cap_line = (
        f"🎯 XP تا سقف سطح: <b>{xp_to_max:,}</b> · انتخاب‌شده: <b>{total_xp:,}</b>"
        + ("  ✅ کافیه!" if enough else "")
    )
    text = (
        f"🍖 <b>تقویت {target.name}</b> (Lv{target.level}){page_note}\n"
        f"{cap_line}\n\n"
        "هرچند تا موجود که می‌خوای رو <b>تیک بزن</b> تا با هم خورده بشن و XP‌شون به این منتقل شه. "
        "<i>فقط تا جایی می‌تونی انتخاب کنی که به سقف سطح برسه — بیشترش هدر می‌ره. "
        "قربانی‌ها برای همیشه حذف می‌شن.</i>"
    )
    return text, InlineKeyboardMarkup(rows)


def _devour_page(context, target_id: int, page: int | None = None) -> int:
    store = context.user_data.setdefault("devour_page", {})
    if page is not None:
        store[target_id] = page
    return store.get(target_id, 0)


async def _devour_rerender(update, context, target_id: int) -> None:
    query = update.callback_query
    try:
        target, scored, xp_to_max = await run_db(_devour_list_sync, update.effective_user, target_id)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    selected = _devour_selection(context, target_id)
    text, keyboard = _devour_list_render(target, scored, selected, _devour_page(context, target_id), xp_to_max)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


async def devour_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, target_id, page = query.data.split(":")
    _devour_page(context, int(target_id), int(page))
    await query.answer()
    await _devour_rerender(update, context, int(target_id))


async def devour_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    target_id = int(query.data.split(":")[1])
    context.user_data.setdefault(_DEVOUR_SEL, {})[target_id] = set()  # fresh selection
    _devour_page(context, target_id, 0)  # start on the first page
    await query.answer()
    await _devour_rerender(update, context, target_id)


async def devour_toggle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, target_id, sac_id = query.data.split(":")
    target_id, sac_id = int(target_id), int(sac_id)
    selection = _devour_selection(context, target_id)
    if sac_id in selection:
        selection.discard(sac_id)  # unticking is always allowed
    else:
        ok, reason = await run_db(_devour_can_add_sync, update.effective_user, target_id, set(selection), sac_id)
        if not ok:
            await query.answer(reason, show_alert=True)
            return
        selection.add(sac_id)
    await query.answer()
    await _devour_rerender(update, context, target_id)


async def devour_select_all_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    action, target_id = query.data.split(":")
    target_id = int(target_id)
    if action == "devour_none":
        context.user_data.setdefault(_DEVOUR_SEL, {})[target_id] = set()
        await query.answer()
    else:
        try:
            _target, scored, xp_to_max = await run_db(_devour_list_sync, update.effective_user, target_id)
        except GameError as exc:
            await query.answer(str(exc), show_alert=True)
            return
        # «انتخاب همه» caps at the XP needed to max — pick strongest-first until the
        # target would fill up (the one that crosses the line is included), so it never
        # over-selects and wastes creatures.
        picked, running = set(), 0
        for c, xp in sorted(scored, key=lambda t: -t[1]):
            if running >= xp_to_max:
                break
            picked.add(c.id)
            running += xp
        context.user_data.setdefault(_DEVOUR_SEL, {})[target_id] = picked
        if len(picked) < len(scored):
            await query.answer("فقط تا سقف سطح انتخاب شد — بقیه هدر می‌رفت.", show_alert=True)
        else:
            await query.answer()
    await _devour_rerender(update, context, target_id)


def _devour_multi_sync(tg_user, target_id, sac_ids):
    user, _ = get_or_create_user(tg_user)
    from game.creature import devour_creatures

    result = devour_creatures(user, target_id, sac_ids)
    result["equipped"] = get_equipped_items(result["target"])
    result["user"] = user
    return result


async def devour_multi_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    target_id = int(query.data.split(":")[1])
    selection = list(_devour_selection(context, target_id))
    if not selection:
        await query.answer("اول حداقل یه موجود رو تیک بزن.", show_alert=True)
        return
    try:
        result = await run_db(_devour_multi_sync, update.effective_user, target_id, selection)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    context.user_data.get(_DEVOUR_SEL, {}).pop(target_id, None)  # consumed
    target = result["target"]
    level_note = (
        f" و {result['levels']} سطح بالا رفت (الان Lv{result['new_level']})!" if result["levels"] else "!"
    )
    await query.answer(f"🍖 +{result['xp']} XP")
    eaten = "، ".join(result["eaten"][:6]) + (" …" if result["count"] > 6 else "")
    await safe_edit_message_text(
        query,
        f"🍖 <b>{result['count']} موجود</b> خورده شد ({eaten}) — "
        f"<b>{target.name}</b> <b>{result['xp']}</b> XP گرفت{level_note}\n\n"
        + creature_card_text(result["user"], target, result["equipped"]),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [btn("🍖 تقویت بیشتر", style=BUILD, callback_data=f"devour_start:{target.id}")],
            [back_btn("menu:collection", "بازگشت به کلکسیون")],
        ]),
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
        f"🟢 <b>{creature_name(creature)}</b> حالا موجود فعالته!\n\n" + creature_card_text(user, creature, equipped_items),
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
            "استفاده درست: <code>/fusion 3 5</code> (دو شماره‌ی موجود از /collection — هزینه‌ی طلا بر اساس ستاره و نایابی حساب می‌شه و والدین سوزانده می‌شن)",
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
            reason = (f"سقف ستاره‌ی فعلی تو {cap}⭐ ـه — برای رسیدن به {creature.star_level + 1}⭐ "
                      f"تالار ادغام رو به سطح {creature.star_level + 1} ارتقا بده.")
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


def _fusion_gate_sync(tg_user, creature_id):
    """Everything the «ورود به فیوژن» guide needs to tell a player exactly which
    requirement for raising this creature's star they're missing."""
    from game.buildings import building_level, is_built, main_hall_level, star_cap
    from game.fusion import FUSION_BUILDING, fusion_partners

    user, _ = get_or_create_user(tg_user)
    creature = Creature.objects.filter(id=creature_id, owner=user).first()
    if creature is None:
        raise GameError("این هیولا پیدا نشد.")
    lab_built = is_built(user, FUSION_BUILDING)
    cap = star_cap(user)
    at_cap = creature.star_level >= cap
    at_max = creature.star_level >= constants.STAR_MAX
    partners = fusion_partners(user, creature)
    cost = constants.fusion_cost(creature.star_level, creature.rarity)
    return {
        "id": creature.id,
        "name": creature.name,
        "rarity": creature.rarity,
        "star": creature.star_level,
        "lab_built": lab_built,
        "lab_level": building_level(user, FUSION_BUILDING),
        "hall_level": main_hall_level(user),
        "cap": cap,
        "at_cap": at_cap,
        "at_max": at_max,
        "partner_count": len(partners),
        "cost": cost,
        "coins": user.coins,
    }


async def upgrade_fusion_gate_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """The «🧬 ورود به فیوژن» button on the upgrade card. Checks every requirement for
    raising THIS creature a star and either routes into the partner picker (ready) or
    shows a complete guide marking exactly what's missing and how to fix it."""
    query = update.callback_query
    cid = int(query.data.split(":")[1])
    try:
        g = await run_db(_fusion_gate_sync, update.effective_user, cid)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer()

    stars = get_emoji("star") * g["star"]
    next_star = g["star"] + 1
    div = "──────────────"
    check = lambda ok: "✅" if ok else "❌"  # noqa: E731

    if g["at_max"]:
        lines = [
            f"🧬 <b>فیوژن — {g['name']}</b> {stars}",
            "",
            "🏆 این هیولا به سقف <b>۵ ستاره</b> رسیده و دیگه نیازی به فیوژن نداره!",
            "قوی‌ترین فرم ممکنه — می‌تونی روی ارتقای اعضا و تجهیزاتش تمرکز کنی.",
        ]
        rows = [[back_btn(f"upg_pick:{cid}", "بازگشت به ارتقا")]]
        await safe_edit_message_text(query, "\n".join(lines), parse_mode="HTML",
                                     reply_markup=InlineKeyboardMarkup(rows))
        return

    cap_ok = not g["at_cap"]
    gold_ok = g["coins"] >= g["cost"]
    partner_ok = g["partner_count"] > 0
    ready = g["lab_built"] and cap_ok and partner_ok and gold_ok

    # the single most important thing to do next — shown big at the top so the player
    # doesn't have to parse the whole checklist to know what's blocking them
    if ready:
        next_step = "✅ <b>همه‌چیز آماده‌ست!</b> دکمه‌ی «انتخاب جفت و ترکیب» رو بزن."
    elif not g["lab_built"]:
        next_step = "👉 <b>قدم بعدی:</b> 🔮 تالار ادغام رو از «🏗 ساختمون‌ها» بساز."
    elif not cap_ok:
        next_step = (f"👉 <b>قدم بعدی:</b> 🔮 تالار ادغام رو به سطح <b>{next_star}</b> ارتقا بده "
                     f"(سطح فعلیش: {g['lab_level']}).")
    elif not partner_ok:
        next_step = (f"👉 <b>قدم بعدی:</b> یک «{g['name']}» دیگه با همین نایابی و همین ستاره پیدا کن "
                     f"(از باکس یا غار هیولا).")
    else:  # not gold_ok
        next_step = f"👉 <b>قدم بعدی:</b> {g['cost'] - g['coins']:,} طلای دیگه جمع کن."

    lines = [
        "🧬 <b>ورود به فیوژن — ارتقای ستاره</b>",
        f"🎯 هدف: <b>{g['name']}</b> {stars} → {get_emoji('star') * next_star}",
        "",
        next_step,
        "", div, "",
        "<b>شرایط لازم برای این ارتقا:</b>",
        f"{check(g['lab_built'])} 🔮 تالار ادغام ساخته شده",
        f"{check(cap_ok)} ⭐ تالار ادغام سطح ≥ {next_star} (الان: {g['lab_level']})",
        f"{check(partner_ok)} 👥 یک هیولای هم‌نوع، هم‌رده و هم‌ستاره داری "
        f"({'داری ✔' if partner_ok else 'نداری'})",
        f"{check(gold_ok)} {get_emoji('coin')} طلای کافی: {g['cost']:,} (داری: {g['coins']:,})",
        "", div, "",
        "<blockquote>فیوژن = دو هیولای <b>هم‌نام + هم‌نایابی + هم‌ستاره</b> → یکی یک ستاره بالاتر. "
        "استت‌ها و بهترین اعضای هر دو والد به فرزند می‌رسه و XP‌شون جمع می‌شه. "
        "سطح تالار ادغام سقف ستاره‌ست: سطح ۲ برای ۲⭐، سطح ۳ برای ۳⭐ …</blockquote>",
    ]

    rows = []
    if ready:
        rows.append([btn("🔮 انتخاب جفت و ترکیب", emoji_key="btn_fusion", style=CONFIRM, callback_data=f"fus_a:{cid}")])
    if not g["lab_built"] or not cap_ok:
        rows.append([btn("رفتن به ساختمون‌ها", emoji_key="btn_buildings", style=PRIMARY, callback_data="menu:buildings")])
    rows.append([back_btn(f"upg_pick:{cid}", "بازگشت به ارتقا")])
    await safe_edit_message_text(query, "\n".join(lines), parse_mode="HTML",
                                 reply_markup=InlineKeyboardMarkup(rows))


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

    text, keyboard = _fusion_body(user, pairs, cap, "all")
    await send_screen(update, text, parse_mode="HTML", reply_markup=keyboard)


def _fusion_body(user, pairs, cap, filt: str) -> tuple[str, InlineKeyboardMarkup]:
    """Fusion list, filterable by rarity via tabs so a big roster isn't one long
    scattered list. `filt` is a rarity key or "all"."""
    lines = [
        f"{get_emoji('lab')} <b>تالار ادغام</b>",
        f"⭐ سقف ستاره‌ی فعلی تو: <b>{cap}</b>",
        "<blockquote>🔗 دو هیولای <b>هم‌نام + هم‌نایابی + هم‌ستاره</b> → یکی یک ستاره بالاتر. "
        "اول 2تا 1★ کن 2★، بعد 2تا 2★ کن 3★ … (5★ = 16 تا 1★).</blockquote>",
    ]
    # rarity tabs — only rarities that actually have a ready pair, plus «همه»
    present = [r for r in constants.RARITY_ORDER if any(p["rarity"] == r for p in pairs)]
    rows = []
    if present:
        tab_row = [btn(("• " if filt == "all" else "") + "همه", style=NAV, callback_data="fus_rarity:all")]
        for r in present:
            label = constants.RARITY_LABELS[r].split()[0]
            tab_row.append(btn(("• " if filt == r else "") + label, style=NAV, callback_data=f"fus_rarity:{r}"))
        rows.append(tab_row)

    shown = pairs if filt == "all" else [p for p in pairs if p["rarity"] == filt]
    if not pairs:
        lines.append(
            "\n📭 الان هیچ جفت آماده‌ای نداری — برای هر ترکیب به <b>دو تای دقیقاً یکسان</b> "
            "(نام و نایابی و ستاره) نیاز داری. از باکس‌ها هیولای بیشتری بگیر."
        )
    elif not shown:
        lines.append("\n📭 توی این نایابی جفت آماده‌ای نیست — یه تبِ دیگه رو ببین.")
    else:
        lines.append("\n✅ <b>هیولاهای آماده‌ی فیوژن</b> — اول هیولای اصلی رو انتخاب کن، بعد جفتش:")
        last_star = None
        for p in sorted(shown, key=lambda x: (x["star"], x["name"])):
            if p["star"] != last_star:
                lines.append(f"\n{'⭐' * p['star']} <b>{p['star']} ستاره → {p['star'] + 1} ستاره</b>")
                last_star = p["star"]
            cost = constants.fusion_cost(p["star"], p["rarity"])
            rarity_dot = constants.RARITY_LABELS[p["rarity"]].split()[0]
            extra = f" (+{p['count'] - 1} جفت)" if p["count"] > 2 else ""
            # main-first: pick the primary creature → the partner picker (fus_a) opens next
            rows.append([btn(
                f"{rarity_dot} {p['name']} {'⭐' * p['star']} → {'⭐' * (p['star'] + 1)}  ({cost:,}💰){extra}",
                style=PRIMARY, callback_data=f"fus_a:{p['parent_a'].id}",
            )])
    rows.append([back_btn("menu:me")])
    lines.append(f"\n{wallet_line(user)}")
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def fusion_rarity_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    filt = update.callback_query.data.split(":")[1]
    user, pairs, lab_built, cap = await run_db(_fusion_panel_sync, update.effective_user)
    if not lab_built:
        await update.callback_query.answer()
        return
    text, keyboard = _fusion_body(user, pairs, cap, filt)
    await send_screen(update, text, parse_mode="HTML", reply_markup=keyboard)


def _fusion_cost_sync(tg_user, a_id, b_id):
    user, _ = get_or_create_user(tg_user)
    a = Creature.objects.filter(id=a_id, owner=user).first()
    b = Creature.objects.filter(id=b_id, owner=user).first()
    if a is None or b is None:
        raise GameError("این جفت دیگه پیدا نشد.")
    base_rarity = constants.higher_rarity(a.rarity, b.rarity)
    return constants.fusion_cost(a.star_level, base_rarity)


async def fusion_pick_b_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, a_id, b_id = query.data.split(":")
    try:
        cost = await run_db(_fusion_cost_sync, update.effective_user, int(a_id), int(b_id))
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
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
        f"{get_emoji('warning')} <b>ترکیب؟</b>\n"
        f"هر دو سوزانده می‌شن و یکی با یک ستاره بالاتر می‌سازی.\n"
        f"هزینه: <b>{cost:,}</b> {get_emoji('coin')}",
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
        from bot.handlers.shop import show_gold_error

        if await show_gold_error(query, exc):
            return
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


def _mission_panel_reward(m: dict) -> str:
    """One mission's payout in the panel style: «+N طلا 🪙 ┃ +N دی‌ان‌ای 🧬 ┃ ۱× کارت سرعت …»."""
    parts = []
    if m.get("coins"):
        parts.append(f"+{m['coins']:,} طلا {get_emoji('coin')}")
    if m.get("dna"):
        parts.append(f"+{m['dna']} دی‌ان‌ای {get_emoji('dna')}")
    if m.get("speedup"):
        parts.append(f"۱× کارت سرعت {constants.speedup_plain_label(m['speedup'])} ⏱")
    return " ┃ ".join(parts) if parts else "—"


def _missions_render(status: list[dict], page: int) -> tuple[str, InlineKeyboardMarkup]:
    """Missions, in-progress first then completed, split across pages. A player's
    full mission list plus reward text overran Telegram's message limit and got
    rejected outright; paging it keeps every screen short and readable."""
    ordered = sorted(status, key=lambda m: (m["done"], m["label"]))  # unfinished first
    total = len(ordered)
    total_pages = max(1, (total + MISSIONS_PAGE_SIZE - 1) // MISSIONS_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    chunk = ordered[page * MISSIONS_PAGE_SIZE : (page + 1) * MISSIONS_PAGE_SIZE]

    done_count = sum(1 for m in status if m["done"])
    overall_bar = constants.render_bar(done_count, total, width=10)
    overall_pct = round(100 * done_count / max(1, total))
    div = "──────────────"
    lines = [
        f"{get_emoji('mission')} <b>ماموریت‌های روزانه | Daily Quests</b>",
        "",
        f"📊 <b>پیشرفت کل:</b> [{overall_bar}] {overall_pct}% ({done_count}/{total} ماموریت)",
    ]
    if total_pages > 1:
        lines.append(f"📑 صفحه: {page + 1} از {total_pages}")
    lines += ["", div, ""]
    for m in chunk:
        if m["done"]:
            lines.append(f"✅ <b>{m['label']}:</b> <s>انجام شد</s>")
            lines.append(f"🎁 پاداش: {_mission_panel_reward(m)}")
        else:
            bar = constants.render_bar(m["progress"], m["target"], width=10)
            lines.append(f"▫️ <b>{m['label']}:</b>")
            lines.append(f"⏳ [{bar}] {m['progress']}/{m['target']}")
            lines.append(f"🎁 پاداش: {_mission_panel_reward(m)}")
        lines.append("")
    lines.append("<i>ماموریت‌ها نیمه‌شب به وقت تهران ریست می‌شن.</i>")

    rows = []
    nav = []
    if page > 0:
        nav.append(btn("قبلی", emoji_key="btn_prev", style=NAV, callback_data=f"mission_page:{page - 1}"))
    if page < total_pages - 1:
        nav.append(btn("بعدی", emoji_key="btn_next", style=NAV, callback_data=f"mission_page:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([back_btn("menu:cat_rewards", "بازگشت به جایزه‌ها")])
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


def _hunt_scout_sync(tg_user, charge=False):
    from game.hunt import scout_cost

    user, _ = get_or_create_user(tg_user)
    creature = get_active_creature(user)
    if creature is None:
        raise GameError("اول /start رو بزن تا موجودت رو بگیری.")
    cost = scout_cost(creature)
    if charge:  # «بعدی» costs a little gold, scaled by power
        if user.coins < cost:
            raise GameError(f"برای جستجوی دوباره {cost} طلا لازمه (الان {user.coins} داری).")
        user.coins -= cost
        user.save(update_fields=["coins"])
    # use the canonical power metric (same as profile/arena) — the old stat-sum showed
    # a smaller, inconsistent number ("قدرتم کمه و باگه").
    my_power = _creature_power(creature, get_equipped_items(creature))
    return creature, my_power, user.cup, scout_one(user, creature), sync_energy(user), cost


def _hunt_scout_text(creature, my_power, cup, target, energy, scout_price) -> str:
    from game.hunt import hunt_dna_range

    tier_label = HUNT_TIERS[target["tier"]]["label"]
    lo, hi = estimated_reward(target["tier"], my_power)
    dlo, dhi = hunt_dna_range(my_power, target["tier"])
    pct = win_chance_pct(my_power, target["power"], creature.element, target["element"])
    adv = element_advantage_line(creature.element, target["element"])
    lines = [
        f"{get_emoji('hunt')} <b>حریف آماده نبرد است!</b>",
        "",
        f"🏰 حریف وحشی: <b>{target['name']}</b>  <i>({tier_label})</i>",
        f"🎯 عنصر حریف: {constants.element_label(target['element'])}",
        "",
        _CARD_DIV,
        "",
        "📊 مقایسه وضعیت نبرد:",
        f"💪 قدرت: شما <b>{my_power:,}</b> 🆚 حریف <b>{target['power']:,}</b>",
        f"🎯 شانس پیروزی: {pct_bar(pct, 100)} {win_label(pct)}",
        "",
        _CARD_DIV,
        "",
        "🔮 مزیت تاکتیکی:",
        adv or "➖ بدون مزیت عنصری",
        "",
        "🎁 جوایز نبرد (در صورت برد):",
        f"{get_emoji('coin')} غنیمت طلا: <b>+{lo:,} تا +{hi:,}</b>",
        f"{get_emoji('dna')} غنیمت دی‌ان‌ای: <b>+{dlo:,} تا +{dhi:,}</b>",
        "",
        _CARD_DIV,
        f"{get_emoji('energy')} هزینه حمله: {constants.HUNT_ENERGY_COST} انرژی  ·  🔍 حریف بعدی: <b>{scout_price}</b> طلا",
    ]
    return "\n".join(lines)


def _hunt_scout_keyboard(target, scout_price=0) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [btn("حمله!", emoji_key="btn_attack", style=BATTLE, callback_data=f"hunt_go:{target['tier']}:{target['seed']}")],
            [btn("🔄 انتخاب موجود دیگر از تیم", style=NAV, callback_data=f"hunt_swap:{target['tier']}:{target['seed']}")],
            [btn(f"🔍 بعدی ({scout_price} طلا)", style=NAV, callback_data="hunt_next")],
            [btn("⚡️ شکار خودکار", style=BATTLE, callback_data="autohunt_start")],
            [back_btn("menu:me")],
        ]
    )


async def hunt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scouting step: shows ONE opponent at a time with its power and payout so the
    player can judge the risk *before* any energy is spent. Finding an opponent costs
    a little gold (scaled by power) — the FIRST find too, not just «بعدی»."""
    try:
        creature, my_power, cup, target, energy, cost = await run_db(_hunt_scout_sync, update.effective_user, True)
    except GameError as exc:
        await send_screen(update, str(exc), parse_mode=None, reply_markup=back_only_keyboard())
        return
    await send_screen(update,
        _hunt_scout_text(creature, my_power, cup, target, energy, cost),
        parse_mode="HTML",
        reply_markup=_hunt_scout_keyboard(target, cost),
    )


async def hunt_next_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        creature, my_power, cup, target, energy, cost = await run_db(_hunt_scout_sync, update.effective_user, True)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer("🔍 جستجوی دوباره…")
    await safe_edit_message_text(
        query,
        _hunt_scout_text(creature, my_power, cup, target, energy, cost),
        parse_mode="HTML",
        reply_markup=_hunt_scout_keyboard(target, cost),
    )


def _hunt_team_choices_sync(tg_user):
    from bio_lab.repository import team_choices
    from game.workers import creature_status

    user, _ = get_or_create_user(tg_user)
    out = []
    for c in team_choices(user):
        busy = (not c.is_active) and creature_status(user, c) is not None
        out.append((c.id, c.name, c.element, _creature_power(c, get_equipped_items(c)), c.is_active, busy))
    return out


async def hunt_swap_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Pick a different team creature to fight THIS same wild target (same tier+seed)."""
    query = update.callback_query
    _, tier, seed = query.data.split(":")
    choices = await run_db(_hunt_team_choices_sync, update.effective_user)
    await query.answer()
    rows = []
    for cid, name, element, power, is_active, busy in choices:
        tag = "🟢 " if is_active else ("⛔ " if busy else "")
        note = " (مشغول)" if busy else ""
        rows.append([btn(f"{tag}{name} [{constants.ELEMENT_LABELS[element]}] · 💪{power:,}{note}",
                         style=BATTLE, callback_data=f"hunt_swap_pick:{tier}:{seed}:{cid}")])
    rows.append([btn("↩️ بازگشت به حریف", style=NAV, callback_data=f"hunt_swap_pick:{tier}:{seed}:0")])
    await safe_edit_message_text(
        query,
        "🔄 <b>کدوم موجود با این حریف بجنگه؟</b>\n<blockquote>حریف عوض نمی‌شه؛ فقط موجودِ خودت. "
        "عنصر مناسب رو انتخاب کن تا شانس بردت بره بالا.</blockquote>",
        parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows),
    )


def _hunt_swap_pick_sync(tg_user, tier, seed, creature_id):
    from game.creature import set_active_creature
    from game.hunt import rebuild_target, scout_cost

    user, _ = get_or_create_user(tg_user)
    if creature_id:
        set_active_creature(user, creature_id)  # may raise if busy
    creature = get_active_creature(user)
    if creature is None:
        raise GameError("اول یه موجود فعال انتخاب کن.")
    my_power = _creature_power(creature, get_equipped_items(creature))
    target = rebuild_target(user, tier, int(seed))
    return creature, my_power, user.cup, target, sync_energy(user), scout_cost(creature)


async def hunt_swap_pick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, tier, seed, cid = query.data.split(":")
    try:
        creature, my_power, cup, target, energy, cost = await run_db(
            _hunt_swap_pick_sync, update.effective_user, tier, seed, int(cid)
        )
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer("موجودت عوض شد." if int(cid) else "")
    await safe_edit_message_text(
        query,
        _hunt_scout_text(creature, my_power, cup, target, energy, cost),
        parse_mode="HTML",
        reply_markup=_hunt_scout_keyboard(target, cost),
    )


def _hunt_go_sync(tg_user, tier, seed):
    user, _ = get_or_create_user(tg_user)
    # LOCK the user row for the whole hunt so a spammed «شکار دوباره» can't fire twice
    # off one energy point: the second tap blocks here, then re-reads the already-spent
    # energy and bounces, instead of both passing the check and resolving two hunts.
    with transaction.atomic():
        user = User.objects.select_for_update().get(id=user.id)
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
        from bot.handlers.energy import show_energy_error

        if not await show_energy_error(query, exc):
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


def _autohunt_info_sync(tg_user):
    """Current fieldable creature + live energy, for the auto-hunt prompt."""
    user, _ = get_or_create_user(tg_user)
    creature = get_active_creature(user)
    if creature is None:
        raise GameError("اول /start رو بزن تا موجودت رو بگیری.")
    energy = sync_energy(user)
    user.save(update_fields=["energy", "energy_updated_at"])
    return energy


def _autohunt_sync(tg_user, energy_amount):
    """Run `energy_amount` auto-hunts (1 energy each) against fresh targets, each paying
    HALF the gold/DNA of a manual hunt. Locks the user row so the whole batch spends
    real energy exactly once. Returns aggregated totals."""
    from game.hunt import AUTO_HUNT_LOOT_MULT, resolve_hunt

    user, _ = get_or_create_user(tg_user)
    with transaction.atomic():
        user = User.objects.select_for_update().get(id=user.id)
        creature = get_active_creature(user)
        if creature is None:
            raise GameError("اول /start رو بزن تا موجودت رو بگیری.")
        sync_energy(user)
        # cost is HUNT_ENERGY_COST per hunt; clamp the request to what's actually available
        per = max(1, constants.HUNT_ENERGY_COST)
        hunts = min(int(energy_amount), user.energy) // per
        if hunts <= 0:
            raise GameError(f"⚡ انرژی کافی نداری (الان {user.energy}/{constants.MAX_ENERGY}).")
        spend_energy(user, hunts * per, "شکار خودکار")
        user.save(update_fields=["energy", "energy_updated_at"])

        wins = coins = dna = xp = levels = 0
        lab_up = False
        for _ in range(hunts):
            r = resolve_hunt(user, creature, "normal", None, loot_mult=AUTO_HUNT_LOOT_MULT)
            wins += 1 if r["won"] else 0
            coins += r["coins"]
            dna += r["dna"]
            xp += r["xp"]
            levels += r.get("levels") or 0
            lab_up = lab_up or bool(r.get("lab_up"))
        record_action(user, "hunt")
        completed_missions = check_missions(user, "hunt")
    return creature, {
        "hunts": hunts, "wins": wins, "losses": hunts - wins,
        "coins": coins, "dna": dna, "xp": xp, "levels": levels, "lab_up": lab_up,
        "energy_left": user.energy,
    }, completed_missions


async def autohunt_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ask how much energy to pour into auto-hunting."""
    query = update.callback_query
    try:
        energy = await run_db(_autohunt_info_sync, update.effective_user)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    if energy < constants.HUNT_ENERGY_COST:
        await query.answer(f"⚡ انرژی کافی نداری ({energy}/{constants.MAX_ENERGY}).", show_alert=True)
        return
    context.user_data[AWAITING_PLAYER_KEY] = {"action": "autohunt_energy"}
    await query.answer()
    await safe_edit_message_text(
        query,
        f"⚡️ <b>شکار خودکار</b>\n"
        f"چند واحد انرژی می‌خوای صرف کنی؟ (الان <b>{energy}/{constants.MAX_ENERGY}</b> داری — "
        f"هر شکار {constants.HUNT_ENERGY_COST} انرژی).\n\n"
        f"<i>یه عدد بفرست. توجه: شکار خودکار نصف لوت شکار دستیه و طلا و دی‌ان‌ای کمتری می‌ده.</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[back_btn("menu:me", "انصراف")]]),
    )


async def autohunt_do_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Confirmed — run the batch and show the summary."""
    query = update.callback_query
    amount = int(query.data.split(":")[1])
    try:
        creature, res, completed_missions = await run_db(_autohunt_sync, update.effective_user, amount)
    except GameError as exc:
        from bot.handlers.energy import show_energy_error

        if not await show_energy_error(query, exc):
            await query.answer(str(exc), show_alert=True)
        return
    lines = [
        f"⚡️ <b>نتیجه شکار خودکار</b>",
        f"🗡 <b>{res['hunts']}</b> نبرد · 🟢 {res['wins']} برد · 🔴 {res['losses']} باخت",
        "",
        f"{get_emoji('coin')} طلا: <b>+{res['coins']:,}</b>",
        f"{get_emoji('dna')} دی‌ان‌ای: <b>+{res['dna']:,}</b>",
        f"✨ XP: <b>+{res['xp']:,}</b>" + (f" · رسید به سطح {creature.level}!" if res["levels"] else ""),
        f"{get_emoji('energy')} انرژی باقی‌مانده: <b>{res['energy_left']}/{constants.MAX_ENERGY}</b>",
        "",
        "<i>یادآوری: شکار خودکار نصف لوت شکار دستی رو می‌ده.</i>",
    ]
    text = "\n".join(lines) + _mission_lines(completed_missions)
    await query.answer("✅ انجام شد!")
    await safe_edit_message_text(
        query, text, parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [btn("⚡️ شکار خودکار دوباره", style=BATTLE, callback_data="autohunt_start")],
            [btn("🔍 شکار دستی", style=NAV, callback_data="hunt_next")],
            [back_btn("menu:me")],
        ]),
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


async def _pv_notify(context, user_id: int, text: str, keyboard=None) -> None:
    """Fire-and-forget PV DM (alliance requests/decisions). No-ops if the user has
    never opened the bot or blocked it."""
    if not user_id:
        return
    from telegram.error import TelegramError

    try:
        await context.bot.send_message(chat_id=user_id, text=text, parse_mode="HTML", reply_markup=keyboard)
    except TelegramError:
        pass


async def _handle_join_result(update, context, result, *, via_query=None) -> None:
    """Render the outcome of request_or_join (instant join vs request filed) and DM
    the alliance's leader/deputy when a new request needs their decision."""
    alliance = result["alliance"]
    if result["joined"]:
        msg = f"{get_emoji('alliance')} به اتحاد <b>{alliance.name}</b> پیوستی! 🎉"
    elif result.get("already"):
        msg = f"⏳ قبلاً به <b>{alliance.name}</b> درخواست دادی — منتظر تأیید رهبر/قائم‌مقام بمون."
    else:
        msg = (f"📨 درخواست عضویتت به <b>{alliance.name}</b> فرستاده شد.\n"
               "رهبر یا قائم‌مقامش باید تأیید کنه؛ همین‌جا بهت خبر می‌دم.")
        applicant = update.effective_user
        who = applicant.first_name or str(applicant.id)
        note = (f"📨 <b>درخواست عضویت جدید</b>\n«{who}» (قدرت {result['requester_power']}) "
                f"می‌خواد به <b>{alliance.name}</b> بپیونده.\nاز «👥 اعضا و مدیریت ← درخواست‌ها» تأیید/رد کن.")
        kb = InlineKeyboardMarkup([[btn("📨 درخواست‌های عضویت", style=CONFIRM, callback_data="ally_requests")]])
        for mid in result.get("notify_ids", []):
            await _pv_notify(context, mid, note, kb)
    if via_query is not None:
        await safe_edit_message_text(via_query, msg, parse_mode="HTML",
                                     reply_markup=InlineKeyboardMarkup([[back_btn("menu:alliance_info")]]))
    else:
        await update.message.reply_text(msg, parse_mode="HTML")


def _alliance_join_sync(tg_user, name):
    user, _ = get_or_create_user(tg_user)
    return request_or_join(user, name)


async def alliance_join(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text(
            "استفاده: <code>/alliance_join اسم اتحاد</code>", parse_mode="HTML"
        )
        return
    try:
        result = await run_db(_alliance_join_sync, update.effective_user, " ".join(context.args))
    except GameError as exc:
        await update.message.reply_text(str(exc), parse_mode="HTML")
        return
    await _handle_join_result(update, context, result)


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
            [btn("👥 اعضا و مدیریت", style=NAV, callback_data="ally_members")],
            [btn("واریز به خزانه", emoji_key="btn_deposit", style=BUILD, callback_data="ally_deposit")],
            [btn("🏰 ساختمون‌های اتحاد", style=PRIMARY, callback_data="ally_perks")],
            [
                btn("🔥 جنگ یک‌روزه", style=BATTLE, callback_data="ally_war1d"),
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
    rows.append([back_btn("menu:cat_social", "بازگشت به اجتماعی")])
    return InlineKeyboardMarkup(rows)


def _alliance_info_text(info: dict) -> str:
    lines = [
        f"{get_emoji('alliance')} <b>اتحاد {info['name']}</b>",
        f"{get_emoji('crown')} رهبر: {display_name(info['leader']) if info['leader'] else '—'}"
        + (f"  ·  🎖 قائم‌مقام: {display_name(info['deputy'])}" if info.get('deputy') else ""),
        f"{get_emoji('users')} اعضا: {info['member_count']}/{info.get('capacity', 50)}   "
        f"💪 قدرت کل: {info['power']}",
        f"{get_emoji('coin')} خزانه: {info['treasury_gold']} طلا",
        "",
    ]
    # cap the printed list so a big alliance never blows past Telegram's message
    # limit; the rest are summarised on one line
    MEMBER_LIST_CAP = 20
    for m in info["members"][:MEMBER_LIST_CAP]:
        lines.append(f"• {display_name(m)}")
    extra = info["member_count"] - MEMBER_LIST_CAP
    if extra > 0:
        lines.append(f"<i>… و {extra} عضو دیگه</i>")
    return "\n".join(lines)


async def alliance_info_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # In a GROUP the panel is a shared message, so its management buttons would let
    # anyone tap on the person's alliance. There we show a READ-ONLY card + a «برو پیوی»
    # link; all the interactive management stays in the DM (scoped to one person).
    chat = update.effective_chat
    in_group = chat is not None and chat.type in ("group", "supergroup")

    info = await run_db(_alliance_info_sync, update.effective_user)
    if in_group:
        from config import BOT_USERNAME

        pv = InlineKeyboardMarkup([[btn("🤝 مدیریت اتحاد توی پیوی", style=PRIMARY,
                                        url=f"https://t.me/{BOT_USERNAME}?start=alliance")]])
        text = (_alliance_info_text(info) if info is not None
                else f"{get_emoji('alliance')} توی هیچ اتحادی نیستی. برای ساخت/پیوستن برو پیوی ربات 👇")
        await update.effective_message.reply_text(text, parse_mode="HTML", reply_markup=pv)
        return

    if info is None:
        await send_screen(update,
            f"{get_emoji('alliance')} توی هیچ اتحادی نیستی.",
            parse_mode="HTML",
            reply_markup=_alliance_action_keyboard(in_alliance=False),
        )
        return
    await send_screen(update,
        _alliance_info_text(info), parse_mode="HTML", reply_markup=_alliance_action_keyboard(in_alliance=True)
    )


# ── alliance members roster + management (leader/deputy) ──────────────────────
def _members_sync(tg_user):
    from game import alliance as alliance_mod

    user, _ = get_or_create_user(tg_user)
    if user.alliance_id is None:
        return None
    al = user.alliance
    return {
        "name": al.name,
        "roster": alliance_mod.member_roster(al),
        "viewer_role": alliance_mod._role_of(al, user.id),
        "has_deputy": al.deputy_id is not None,
        "pending": alliance_mod.pending_request_count(al),
        "auto_accept": al.auto_accept,
        "min_join_power": al.min_join_power,
    }


_MEMBERS_PER_PAGE = 20


def _members_render(data: dict, page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    roster = data["roster"]
    total_pages = max(1, (len(roster) + _MEMBERS_PER_PAGE - 1) // _MEMBERS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    chunk = roster[page * _MEMBERS_PER_PAGE : (page + 1) * _MEMBERS_PER_PAGE]

    lines = [
        f"{get_emoji('users')} <b>اعضای اتحاد {data['name']}</b> — {len(roster)} نفر"
        + (f"  <i>(صفحه {page + 1}/{total_pages})</i>" if total_pages > 1 else "") + "\n"
    ]
    for r in chunk:
        badge = "👑" if r["is_leader"] else ("🎖" if r["is_deputy"] else "•")
        lines.append(f"{badge} {r['name']} — <code>{r['id']}</code>  💪{r['power']}")

    rows = []
    nav = []
    if page > 0:
        nav.append(btn("قبلی", emoji_key="btn_prev", style=NAV, callback_data=f"ally_members:{page - 1}"))
    if page < total_pages - 1:
        nav.append(btn("بعدی", emoji_key="btn_next", style=NAV, callback_data=f"ally_members:{page + 1}"))
    if nav:
        rows.append(nav)

    role = data["viewer_role"]
    if role in ("leader", "deputy"):
        mode = "خودکار ✅" if data.get("auto_accept") else "با تأیید 📨"
        lines.append(
            f"\n<blockquote>👑 رهبر · 🎖 قائم‌مقام. عضوگیری: <b>{mode}</b> · حداقل قدرت: "
            f"<b>{data.get('min_join_power', 0)}</b>. برای مدیریت، آیدیِ عضو رو بده.</blockquote>"
        )
        pend = data.get("pending", 0)
        rows.append([btn(f"📨 درخواست‌های عضویت ({pend})", style=CONFIRM, callback_data="ally_requests")])
        rows.append([btn("⚙️ تنظیمات عضوگیری", style=NAV, callback_data="ally_settings")])
        rows.append([btn("🥾 کیک با آیدی", style=DANGER, callback_data="ally_kick")])
    if role == "leader":
        rows.append([btn("🎖 تعیین قائم‌مقام با آیدی", style=PRIMARY, callback_data="ally_deputy")])
        if data["has_deputy"]:
            rows.append([btn("➖ برداشتن قائم‌مقام", style=NAV, callback_data="ally_deputy_off")])
    rows.append([back_btn("menu:alliance_info", "بازگشت به اتحاد")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def alliance_members_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    parts = query.data.split(":")
    page = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    data = await run_db(_members_sync, update.effective_user)
    await query.answer()
    if data is None:
        await safe_edit_message_text(query, "توی هیچ اتحادی نیستی.",
                                     reply_markup=InlineKeyboardMarkup([[back_btn("menu:alliance_info")]]))
        return
    text, keyboard = _members_render(data, page)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


# ── join requests (leader/deputy approve/reject) ─────────────────────────────
def _requests_sync(tg_user):
    from game import alliance as alliance_mod

    user, _ = get_or_create_user(tg_user)
    al = user.alliance
    if al is None or not alliance_mod._is_manager(user, al):
        return None
    return {"name": al.name, "requests": pending_requests(al)}


def _requests_render(data: dict) -> tuple[str, InlineKeyboardMarkup]:
    reqs = data["requests"]
    lines = [f"📨 <b>درخواست‌های عضویت — {data['name']}</b> ({len(reqs)})\n"]
    rows = []
    if not reqs:
        lines.append("<i>الان درخواستی نیست.</i>")
    for r in reqs:
        name = r["user"].first_name or str(r["user"].id)
        lines.append(f"• <b>{name}</b> — <code>{r['user'].id}</code> · 💪{r['power']}")
        rows.append([
            btn(f"✅ قبول {name[:12]}", style=CONFIRM, callback_data=f"ally_approve:{r['id']}"),
            btn("❌ رد", style=DANGER, callback_data=f"ally_reject:{r['id']}"),
        ])
    rows.append([back_btn("ally_members", "بازگشت به اعضا")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def alliance_requests_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    data = await run_db(_requests_sync, update.effective_user)
    await query.answer()
    if data is None:
        await query.answer("فقط رهبر یا قائم‌مقام می‌تونه درخواست‌ها رو ببینه.", show_alert=True)
        return
    text, keyboard = _requests_render(data)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


def _approve_sync(tg_user, req_id):
    user, _ = get_or_create_user(tg_user)
    result = approve_request(user, req_id)
    return result, _requests_sync(tg_user)


async def alliance_approve_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    req_id = int(query.data.split(":")[1])
    try:
        result, data = await run_db(_approve_sync, update.effective_user, req_id)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        # refresh the list (the request may have vanished)
        data = await run_db(_requests_sync, update.effective_user)
        if data is not None:
            text, keyboard = _requests_render(data)
            await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)
        return
    await query.answer("✅ عضو اضافه شد.")
    # DM the new member
    await _pv_notify(
        context, result["applicant"].id,
        f"{get_emoji('alliance')} درخواستت قبول شد! حالا عضو اتحاد <b>{result['alliance'].name}</b> هستی. 🎉",
    )
    # DM the managers of every OTHER alliance this player had requested — their request is void
    for inv in result.get("invalidated", []):
        note = f"ℹ️ «{inv['applicant_name']}» به اتحاد دیگری («{result['alliance'].name}») پیوست، پس درخواستش به شما لغو شد."
        for mid in inv["manager_ids"]:
            await _pv_notify(context, mid, note)
    if data is not None:
        text, keyboard = _requests_render(data)
        await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


def _reject_sync(tg_user, req_id):
    user, _ = get_or_create_user(tg_user)
    result = reject_request(user, req_id)
    return result, _requests_sync(tg_user)


async def alliance_reject_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    req_id = int(query.data.split(":")[1])
    try:
        result, data = await run_db(_reject_sync, update.effective_user, req_id)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer("❌ رد شد.")
    await _pv_notify(
        context, result["applicant"].id,
        f"متأسفانه درخواست عضویتت به اتحاد <b>{result['alliance_name']}</b> رد شد.",
    )
    if data is not None:
        text, keyboard = _requests_render(data)
        await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


# ── alliance join settings (auto/request + min power) ─────────────────────────
def _settings_sync(tg_user):
    from game import alliance as alliance_mod

    user, _ = get_or_create_user(tg_user)
    al = user.alliance
    if al is None or not alliance_mod._is_manager(user, al):
        return None
    return {"name": al.name, "auto_accept": al.auto_accept, "min_join_power": al.min_join_power}


def _settings_render(data: dict) -> tuple[str, InlineKeyboardMarkup]:
    mode = "خودکار (هرکی شرایط داشته باشه فوری عضو می‌شه)" if data["auto_accept"] else "با تأیید (درخواست می‌دن، تو تأیید می‌کنی)"
    text = (
        f"⚙️ <b>تنظیمات عضوگیری — {data['name']}</b>\n\n"
        f"حالت فعلی: <b>{mode}</b>\n"
        f"حداقل قدرت برای عضویت: <b>{data['min_join_power']}</b>\n"
    )
    toggle_label = "🔁 تغییر به «با تأیید»" if data["auto_accept"] else "🔁 تغییر به «خودکار»"
    rows = [
        [btn(toggle_label, style=CONFIRM, callback_data="ally_toggle_auto")],
        [btn("🎯 تنظیم حداقل قدرت", style=NAV, callback_data="ally_set_minpow")],
        [back_btn("ally_members", "بازگشت به اعضا")],
    ]
    return text, InlineKeyboardMarkup(rows)


async def alliance_settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    data = await run_db(_settings_sync, update.effective_user)
    await query.answer()
    if data is None:
        await query.answer("فقط رهبر یا قائم‌مقام می‌تونه تنظیمات رو ببینه.", show_alert=True)
        return
    text, keyboard = _settings_render(data)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


def _toggle_auto_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    cur = _settings_sync(tg_user)
    if cur is None:
        raise GameError("فقط رهبر یا قائم‌مقام می‌تونه تنظیمات رو عوض کنه.")
    set_join_settings(user, auto_accept=not cur["auto_accept"])
    return _settings_sync(tg_user)


async def alliance_toggle_auto_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        data = await run_db(_toggle_auto_sync, update.effective_user)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer("✅ ذخیره شد.")
    text, keyboard = _settings_render(data)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


async def alliance_set_minpow_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    context.user_data[AWAITING_PLAYER_KEY] = {"action": "ally_minpow"}
    await query.answer()
    await safe_edit_message_text(
        query, "🎯 حداقل قدرت لازم برای عضویت رو به عدد بفرست (مثلاً <code>500</code>؛ برای برداشتن محدودیت 0):",
        parse_mode="HTML",
    )


async def alliance_kick_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    context.user_data[AWAITING_PLAYER_KEY] = {"action": "ally_kick"}
    await query.answer()
    await safe_edit_message_text(query, f"🥾 آیدیِ عضوی که می‌خوای کیک بشه رو بفرست:{_reply_hint(update)}",
                                 parse_mode="HTML")


async def alliance_deputy_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    context.user_data[AWAITING_PLAYER_KEY] = {"action": "ally_deputy"}
    await query.answer()
    await safe_edit_message_text(query, f"🎖 آیدیِ عضوی که قائم‌مقام بشه رو بفرست:{_reply_hint(update)}",
                                 parse_mode="HTML")


def _deputy_off_sync(tg_user):
    from game import alliance as alliance_mod

    user, _ = get_or_create_user(tg_user)
    alliance_mod.remove_deputy(user)
    return _members_sync(tg_user)


async def alliance_deputy_off_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        data = await run_db(_deputy_off_sync, update.effective_user)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer("قائم‌مقام برداشته شد.")
    text, keyboard = _members_render(data)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


AWAITING_PLAYER_KEY = "awaiting_player_input"


def _reply_hint(update: Update) -> str:
    """In a group, awaiting-text flows are fed by replying to the bot's prompt, so
    tell the user to do that. Empty in private chats, where a plain message works."""
    chat = update.effective_chat
    if chat is not None and chat.type in ("group", "supergroup"):
        return "\n\n<i>👈 توی گروه حتماً به همین پیام <b>ریپلای</b> کن و جوابت رو بنویس.</i>"
    return ""


async def alliance_create_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    context.user_data[AWAITING_PLAYER_KEY] = {"action": "alliance_create"}
    await query.answer()
    await safe_edit_message_text(
        query, f"🟢 {get_emoji('alliance')} اسم اتحاد جدیدت رو بفرست:{_reply_hint(update)}", parse_mode="HTML"
    )


def _alliance_browse_render(data: dict, search_results=None, search_query: str = "") -> tuple[str, InlineKeyboardMarkup]:
    rows = []
    if search_results is not None:
        # search results mode
        if search_results:
            lines = [f"🔍 <b>نتایج جستجو برای «{search_query}»:</b>\n"]
            for r in search_results:
                a = r["alliance"]
                lines.append(f"• <b>{a.name}</b> — {r['member_count']} عضو  💪{r['power']}")
                rows.append([btn(f"➕ پیوستن به {a.name}", style=PRIMARY, callback_data=f"ally_browse_join:{a.id}")])
        else:
            lines = [f"🔍 هیچ اتحادی با «{search_query}» پیدا نشد."]
        rows.append([btn("🔍 جستجوی دیگه", style=NAV, callback_data="ally_search")])
        rows.append([btn("📋 همه‌ی اتحادها", style=NAV, callback_data="ally_browse:0")])
        rows.append([back_btn("menu:alliance_info", "بازگشت")])
        return "\n".join(lines), InlineKeyboardMarkup(rows)

    # browse mode
    page = data["page"]
    total = data["total"]
    alliances = data["alliances"]
    lines = [f"🏰 <b>اتحادهای موجود</b> (صفحه {page + 1}، جمعاً {total} اتحاد)\n"]
    if not alliances:
        lines.append("<i>هنوز هیچ اتحادی ساخته نشده.</i>")
    for r in alliances:
        a = r["alliance"]
        lines.append(f"• <b>{a.name}</b> — {r['member_count']} عضو  💪{r['power']}")
        rows.append([btn(f"➕ پیوستن به {a.name}", style=PRIMARY, callback_data=f"ally_browse_join:{a.id}")])

    nav_row = []
    if data["has_prev"]:
        nav_row.append(btn("قبلی", emoji_key="btn_prev", style=NAV, callback_data=f"ally_browse:{page - 1}"))
    if data["has_next"]:
        nav_row.append(btn("▶️ بعدی", style=NAV, callback_data=f"ally_browse:{page + 1}"))
    if nav_row:
        rows.append(nav_row)
    rows.append([btn("🔍 جستجو با اسم", style=NAV, callback_data="ally_search")])
    rows.append([back_btn("menu:alliance_info", "بازگشت")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def alliance_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = await run_db(list_alliances_page, 0)
    text, keyboard = _alliance_browse_render(data)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


async def alliance_browse_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    page = int(query.data.split(":")[1])
    data = await run_db(list_alliances_page, page)
    text, keyboard = _alliance_browse_render(data)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


def _join_by_id_sync(tg_user, alliance_id: int):
    user, _ = get_or_create_user(tg_user)
    return request_or_join_by_id(user, alliance_id)


async def alliance_browse_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    alliance_id = int(query.data.split(":")[1])
    try:
        result = await run_db(_join_by_id_sync, update.effective_user, alliance_id)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await _handle_join_result(update, context, result, via_query=query)


async def alliance_search_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    context.user_data[AWAITING_PLAYER_KEY] = {"action": "alliance_search"}
    await query.answer()
    await safe_edit_message_text(
        query,
        f"🔍 {get_emoji('alliance')} اسم (یا بخشی از اسم) اتحاد رو بفرست:{_reply_hint(update)}",
        parse_mode="HTML",
    )


async def alliance_deposit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    context.user_data[AWAITING_PLAYER_KEY] = {"action": "alliance_deposit"}
    await query.answer()
    await safe_edit_message_text(query,
        f"💰 چند {get_emoji('coin')} طلا می‌خوای به خزانه واریز کنی؟ یه عدد بفرست:{_reply_hint(update)}",
        parse_mode="HTML",
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
    with transaction.atomic():
        consume_daily(user, "heist")  # atomic: a rapid double-tap can't heist twice
        result = heist(user, creature, target)
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

    if action == "exchange_custom":
        from bot.handlers.exchange import handle_custom_amount

        await handle_custom_amount(update, context, awaiting)
        return

    if action == "buy_custom":
        from bot.handlers.purchase import handle_custom_amount as _buy_custom

        await _buy_custom(update, context, awaiting)
        return

    if action == "set_lab_name":
        if not text or len(text) > LAB_NAME_MAX_LEN:
            context.user_data[AWAITING_PLAYER_KEY] = awaiting
            await message.reply_text(f"⚠️ اسم باید بین 1 تا {LAB_NAME_MAX_LEN} کاراکتر باشه. دوباره بفرست:")
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
            await message.reply_text(f"⚠️ اسم باید بین 1 تا {LAB_NAME_MAX_LEN} کاراکتر باشه. دوباره بفرست:")
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

    if action == "autohunt_energy":
        raw = (text or "").strip().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
        if not raw.isdigit() or int(raw) <= 0:
            context.user_data[AWAITING_PLAYER_KEY] = awaiting
            await message.reply_text("فقط یه عدد مثبت بفرست (مثلاً 10) — یا «انصراف».")
            return
        try:
            energy = await run_db(_autohunt_info_sync, update.effective_user)
        except GameError as exc:
            await message.reply_text(str(exc))
            return
        amount = min(int(raw), energy)
        if amount < constants.HUNT_ENERGY_COST:
            await message.reply_text(f"⚡ انرژی کافی نداری ({energy}/{constants.MAX_ENERGY}).")
            return
        hunts = amount // max(1, constants.HUNT_ENERGY_COST)
        await message.reply_text(
            f"⚡️ <b>تأیید شکار خودکار</b>\n"
            f"می‌خوای <b>{amount}</b> انرژی صرف <b>{hunts}</b> نبرد خودکار کنی؟\n\n"
            f"⚠️ <i>شکار خودکار نسبت به شکار دستی <b>طلا و دی‌ان‌ای کمتری</b> می‌ده "
            f"(نصف لوت). XP کامل می‌مونه.</i>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [btn("✅ تأیید و شروع", style=CONFIRM, callback_data=f"autohunt_do:{amount}")],
                [back_btn("menu:me", "انصراف")],
            ]),
        )
        return

    if action == "rename_kaiju":
        creature_id = awaiting["creature_id"]
        origin = awaiting.get("origin", "c")
        back_cb = f"upg_pick:{creature_id}" if origin == "u" else f"coll_pick:{creature_id}"
        try:
            res = await run_db(_rename_kaiju_sync, update.effective_user, creature_id, text)
        except GameError as exc:
            context.user_data[AWAITING_PLAYER_KEY] = awaiting
            await message.reply_text(
                f"⚠️ {exc}\n<i>یه اسم دیگه بفرست یا برگرد.</i>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[back_btn(back_cb, "انصراف / بازگشت")]]),
            )
            return
        cost_note = "رایگان بود ✅" if res["cost"] == 0 else f"{res['cost']} {get_emoji('diamond')} کم شد"
        await message.reply_text(
            f"✅ اسم کایجو روی «<b>{res['name']}</b>» تنظیم شد.\n"
            f"🧬 نژاد: <b>{res['breed']}</b>\n"
            f"<i>({cost_note} · نام‌گذاری بعدی: {res['next_cost']} {get_emoji('diamond')})</i>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[back_btn(back_cb, "بازگشت به کایجو")]]),
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
            result = await run_db(_alliance_join_sync, update.effective_user, text)
        except GameError as exc:
            context.user_data[AWAITING_PLAYER_KEY] = awaiting
            await message.reply_text(str(exc), parse_mode="HTML")
            return
        await _handle_join_result(update, context, result)
        return

    if action == "ally_minpow":
        raw = (text or "").strip().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
        if not raw.isdigit():
            context.user_data[AWAITING_PLAYER_KEY] = awaiting
            await message.reply_text("فقط یه عدد بفرست (مثلاً 500 یا 0).")
            return
        try:
            al = await run_db(lambda tg: set_join_settings(get_or_create_user(tg)[0], min_power=int(raw)),
                              update.effective_user)
        except GameError as exc:
            await message.reply_text(str(exc))
            return
        await message.reply_text(f"✅ حداقل قدرت عضویت روی <b>{al.min_join_power}</b> تنظیم شد.", parse_mode="HTML")
        return

    if action in ("ally_kick", "ally_deputy"):
        digits = text.strip().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
        if not digits.isdigit():
            context.user_data[AWAITING_PLAYER_KEY] = awaiting
            await message.reply_text("⚠️ آیدیِ عددی عضو رو بفرست (کد جلوی اسمش توی لیست اعضا).")
            return

        def _do(tg_user, target_id, act):
            from game import alliance as alliance_mod

            user, _ = get_or_create_user(tg_user)
            if act == "ally_kick":
                return alliance_mod.kick_member(user, target_id)
            return alliance_mod.set_deputy(user, target_id)

        try:
            result = await run_db(_do, update.effective_user, int(digits), action)
        except GameError as exc:
            await message.reply_text(str(exc))
            return
        if action == "ally_kick":
            await message.reply_text(f"🥾 <b>{result['name']}</b> از اتحاد حذف شد.", parse_mode="HTML")
        else:
            await message.reply_text(f"🎖 <b>{result['name']}</b> حالا قائم‌مقام اتحاده.", parse_mode="HTML")
        return

    if action == "alliance_search":
        if not text:
            context.user_data[AWAITING_PLAYER_KEY] = awaiting
            await message.reply_text("⚠️ یه اسم بفرست تا جستجو کنم.")
            return
        results = await run_db(search_alliances, text)
        rendered, keyboard = _alliance_browse_render({}, search_results=results, search_query=text)
        await message.reply_text(rendered, parse_mode="HTML", reply_markup=keyboard)
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


async def alliance_league_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """🏰 لیگ اتحادها — top alliances by power, with the weekly reward each rank pays
    its members. Rewards are handed out automatically at the weekly season reset."""
    from game.alliance import ALLIANCE_LEAGUE_REWARD_BY_RANK

    ranked = await run_db(_alliance_top_sync)
    medals = [get_emoji("medal_gold"), get_emoji("medal_silver"), get_emoji("medal_bronze")]
    lines = [
        "🏰 <b>لیگ اتحادها</b>",
        "<blockquote>اتحادها بر اساس <b>قدرت کل اعضا</b> رتبه‌بندی می‌شن. آخر هر هفته، "
        "10 اتحاد برتر به <b>همه‌ی اعضاشون</b> جایزه می‌دن — هرچی رتبه بالاتر، جایزه بیشتر.</blockquote>",
        "",
    ]
    if not ranked:
        lines.append("<i>هنوز هیچ اتحادی ساخته نشده.</i>")
    for i, r in enumerate(ranked, start=1):
        rank = medals[i - 1] if i <= 3 else f"{i}."
        rw = ALLIANCE_LEAGUE_REWARD_BY_RANK.get(i)
        rw_txt = f"  🎁 {rw['diamonds']}💎+{rw['coins']}🪙/نفر" if rw else ""
        lines.append(f"{rank} <b>{r['alliance'].name}</b> — 💪{r['power']} ({r['member_count']} عضو){rw_txt}")
    await send_screen(
        update, "\n".join(lines), parse_mode="HTML",
        reply_markup=back_only_keyboard("menu:cat_social", "بازگشت به اجتماعی"),
    )


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

    with transaction.atomic():
        consume_daily(user, "heist")  # atomic: a rapid double-tap can't heist twice
        result = heist(user, creature, target)
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
    await send_screen(update, "\n".join(lines), reply_markup=back_only_keyboard("menu:cat_social", "بازگشت به اجتماعی"))


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
    from game import titles

    rename_cost = constants.lab_rename_cost(user.lab_renames)
    lines = [
        f"{get_emoji('profile')} <b>آزمایشگاه {lab_display(user)}</b>{titles.label(user)}",
        f"<blockquote>{lab_level_line(user)}</blockquote>\n",
        f"📅 عضو از: {timezone.localtime(user.created_at).strftime('%Y-%m-%d')}",
        f"🔥 روزهای ورود پشت‌سرهم: {user.login_streak}",
        f"{get_emoji('creature')} موجودات ساخته‌شده: {stats['creatures_owned']}\n",
        f"{get_emoji('battle')} نبردهای گروهی برده: {stats['duel_wins']}",
        f"{get_emoji('hunt')} شکارهای انجام‌شده: {stats['total_hunts']}",
        f"{get_emoji('raid_boss')} کل دمیج واردشده به رید باس‌ها: {stats['total_raid_damage']}\n",
        wallet_line(user),
    ]
    notif_label = "🔔 اعلان‌ها: روشن" if user.notifications_on else "🔕 اعلان‌ها: خاموش"
    rows = [
        [btn("🏅 لقب‌ها", style=NAV, callback_data="menu:titles")],
        [btn(f"✏️ تغییر اسم آزمایشگاه ({rename_cost} 💎)", style=SHOP, callback_data="lab_rename")],
        [btn(notif_label, style=NAV, callback_data="notif_toggle")],
        [back_btn("menu:cat_social", "بازگشت به اجتماعی")],
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


def main_menu_keyboard(is_owner: bool = False) -> InlineKeyboardMarkup:
    """The /menu command's keyboard — same compact categorised layout as the
    creature-card menu, so the two can't drift."""
    return creature_keyboard(is_owner)


def _menu_lab_line_sync(tg_user) -> str:
    user, _ = get_or_create_user(tg_user)
    return lab_level_line(user)


async def _show_main_menu(update) -> None:
    """Main menu with the lab level + 'how far to the next level' line at the top."""
    lab_line = await run_db(_menu_lab_line_sync, update.effective_user)
    await send_screen(
        update, f"📋 <b>منوی اصلی</b>\n{lab_line}\n\nیکی رو انتخاب کن:",
        parse_mode="HTML", reply_markup=main_menu_keyboard(),
    )


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _show_main_menu(update)


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
    "league": league_panel,
    "alliance_league": alliance_league_panel,
    "shop": shop_panel,
    "shield_shop": shield_shop_panel,
    "item_shop": item_shop_panel,
    "gold_shop": gold_shop_panel,
    "exchange": exchange_panel,
    "casino": casino_panel,
    "titles": titles_panel,
    "wheel": wheel_cmd,
    "alliance_info": alliance_info_cmd,
    "rank": rank,
    "admin": admin_cmd,
    "profile": profile,
    "balance": balance,
    "guide": guide_panel,
}


# Typing a group trigger word in a *private* chat opens the matching menu panel,
# so the same simple words work in the DM as in a group. Group-only combat words
# (raid/attack/duel/guardian…) and anything unmapped fall back to the main menu.
_KEYWORD_TO_MENU = {
    "creature": "me", "equipment": "inventory", "collection": "collection",
    "upgrade": "upgrade", "lab": "profile", "hunt": "hunt", "arena": "arena",
    "mission": "missions", "fusion": "fusion", "breeding": "breeding",
    "reward": "idle", "alliance": "alliance_info", "leaderboard": "rank",
    "box": "biocrate", "mine": "buildings", "wheel": "wheel",
    "select": "collection", "help": "guide", "start": "guide",
    "casino": "casino", "exchange": "exchange", "balance": "balance",
}


async def route_private_keyword(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """If a plain private message is a recognised trigger word (incl. brand words
    like «کایجو»/«ربات»), open the matching panel. Returns True if it handled the
    message, False for ordinary text (which stays quiet, as before)."""
    message = update.effective_message
    if message is None or not message.text:
        return False
    action = keywords.match(message.text)
    if action is None:
        return False
    handler = _MENU_ACTIONS.get(_KEYWORD_TO_MENU.get(action, ""))
    if handler is None:
        # group-only combat word or brand fallback → just show the main menu
        await _show_main_menu(update)
        return True
    await handler(update, context)
    return True


async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    action = query.data.split(":", 1)[1]
    # a category button drills into its submenu (rendered in place)
    if action.startswith("cat_"):
        cat_key = action[4:]
        if cat_key in _CATEGORIES:
            title, keyboard = _category_keyboard(cat_key)
            await safe_edit_message_text(
                query, f"{title}\n<i>یکی رو انتخاب کن:</i>", parse_mode="HTML", reply_markup=keyboard
            )
        return
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
    application.add_handler(CommandHandler("balance", balance, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("menu", menu, filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(menu_callback, pattern=r"^menu:"))
    application.add_handler(CallbackQueryHandler(guide_page_callback, pattern=r"^guide:"))
    application.add_handler(CallbackQueryHandler(upgrade_pick_callback, pattern=r"^upg_pick:"))
    application.add_handler(CallbackQueryHandler(upgrade_fusion_gate_callback, pattern=r"^upg_fusion:"))
    application.add_handler(CallbackQueryHandler(upgrade_page_callback, pattern=r"^upg_page:"))
    application.add_handler(CallbackQueryHandler(missions_page_callback, pattern=r"^mission_page:"))
    application.add_handler(CallbackQueryHandler(lab_rename_start_callback, pattern=r"^lab_rename$"))
    application.add_handler(CallbackQueryHandler(notif_toggle_callback, pattern=r"^notif_toggle$"))
    application.add_handler(CallbackQueryHandler(equip_panel_callback, pattern=r"^upg_eq:"))
    application.add_handler(CallbackQueryHandler(equip_slot_callback, pattern=r"^upg_slot:"))
    application.add_handler(CallbackQueryHandler(equip_do_callback, pattern=r"^upg_(equip|unequip):"))
    application.add_handler(CallbackQueryHandler(upgrade_set_default_callback, pattern=r"^upg_default:"))
    application.add_handler(CallbackQueryHandler(upgrade_step_callback, pattern=r"^upg_step:\d+:\d+$"))
    application.add_handler(CallbackQueryHandler(hunt_go_callback, pattern=r"^hunt_go:"))
    application.add_handler(CallbackQueryHandler(autohunt_start_callback, pattern=r"^autohunt_start$"))
    application.add_handler(CallbackQueryHandler(autohunt_do_callback, pattern=r"^autohunt_do:\d+$"))
    application.add_handler(CallbackQueryHandler(hunt_next_callback, pattern=r"^hunt_next$"))
    application.add_handler(CallbackQueryHandler(hunt_swap_callback, pattern=r"^hunt_swap:"))
    application.add_handler(CallbackQueryHandler(hunt_swap_pick_callback, pattern=r"^hunt_swap_pick:"))
    application.add_handler(CallbackQueryHandler(collection_pick_callback, pattern=r"^coll_pick:"))
    application.add_handler(CallbackQueryHandler(collection_page_callback, pattern=r"^coll_page:"))
    application.add_handler(CallbackQueryHandler(collection_select_callback, pattern=r"^coll_select:"))
    application.add_handler(CallbackQueryHandler(kaiju_rename_callback, pattern=r"^kaiju_rename:\d+(:[cu])?$"))
    application.add_handler(CallbackQueryHandler(devour_start_callback, pattern=r"^devour_start:\d+$"))
    application.add_handler(CallbackQueryHandler(devour_toggle_callback, pattern=r"^devour_tog:\d+:\d+$"))
    application.add_handler(CallbackQueryHandler(devour_select_all_callback, pattern=r"^devour_(all|none):\d+$"))
    application.add_handler(CallbackQueryHandler(devour_page_callback, pattern=r"^devour_page:\d+:\d+$"))
    application.add_handler(CallbackQueryHandler(devour_multi_callback, pattern=r"^devour_multi:\d+$"))
    application.add_handler(CallbackQueryHandler(fusion_pick_a_callback, pattern=r"^fus_a:"))
    application.add_handler(CallbackQueryHandler(fusion_rarity_callback, pattern=r"^fus_rarity:"))
    application.add_handler(CallbackQueryHandler(fusion_pick_b_callback, pattern=r"^fus_b:"))
    application.add_handler(CallbackQueryHandler(fusion_confirm_callback, pattern=r"^fus_confirm:"))
    application.add_handler(CallbackQueryHandler(alliance_create_callback, pattern=r"^ally_create$"))
    application.add_handler(CallbackQueryHandler(alliance_join_callback, pattern=r"^ally_join$"))
    application.add_handler(CallbackQueryHandler(alliance_browse_callback, pattern=r"^ally_browse:\d+$"))
    application.add_handler(CallbackQueryHandler(alliance_browse_join_callback, pattern=r"^ally_browse_join:\d+$"))
    application.add_handler(CallbackQueryHandler(alliance_search_callback, pattern=r"^ally_search$"))
    application.add_handler(CallbackQueryHandler(alliance_deposit_callback, pattern=r"^ally_deposit$"))
    application.add_handler(CallbackQueryHandler(alliance_top_callback, pattern=r"^ally_top$"))
    application.add_handler(CallbackQueryHandler(alliance_members_callback, pattern=r"^ally_members(:\d+)?$"))
    application.add_handler(CallbackQueryHandler(alliance_requests_callback, pattern=r"^ally_requests$"))
    application.add_handler(CallbackQueryHandler(alliance_approve_callback, pattern=r"^ally_approve:\d+$"))
    application.add_handler(CallbackQueryHandler(alliance_reject_callback, pattern=r"^ally_reject:\d+$"))
    application.add_handler(CallbackQueryHandler(alliance_settings_callback, pattern=r"^ally_settings$"))
    application.add_handler(CallbackQueryHandler(alliance_toggle_auto_callback, pattern=r"^ally_toggle_auto$"))
    application.add_handler(CallbackQueryHandler(alliance_set_minpow_start, pattern=r"^ally_set_minpow$"))
    application.add_handler(CallbackQueryHandler(alliance_kick_start, pattern=r"^ally_kick$"))
    application.add_handler(CallbackQueryHandler(alliance_deputy_start, pattern=r"^ally_deputy$"))
    application.add_handler(CallbackQueryHandler(alliance_deputy_off_callback, pattern=r"^ally_deputy_off$"))
    application.add_handler(
        CallbackQueryHandler(alliance_leave_confirm_callback, pattern=r"^ally_leave_confirm$")
    )
    application.add_handler(CallbackQueryHandler(alliance_leave_callback, pattern=r"^ally_leave$"))
    application.add_handler(CallbackQueryHandler(alliance_heist_list_callback, pattern=r"^ally_heist_list$"))
    application.add_handler(CallbackQueryHandler(heist_pick_callback, pattern=r"^heist_pick:"))

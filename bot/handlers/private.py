import html
import logging
import re
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

from bot.gates import hall_gated
from bot.utils import alert_text
from bio_lab.models import Alliance, Creature, User
from bio_lab.repository import (
    creature_has_nickname,
    creature_name,
    display_name,
    get_active_creature,
    get_or_create_user,
    lab_display,
    lock_row,
    lab_name_taken,
)
from bot.handlers.achievements import achievements_panel
from bot.handlers.arena import arena_chests_panel, arena_panel
from bot.handlers.banner import banner_panel
from bot.handlers.battlepass import battlepass_panel
from bot.handlers.campaign import campaign_panel
from bot.handlers.dispatch import dispatch_panel
from bot.handlers.festival import festival_panel
from bot.handlers.today import today_panel
from bot.handlers.tournament import tournament_panel
from bot.handlers.worldboss import worldboss_panel
from bot.handlers.codex import codex_panel
from bot.handlers.events import events_panel
from bot.handlers.idle import idle_panel
from bot.handlers.league import league_panel
from bot.handlers.research import research_panel
from bot.handlers.equip_exchange import equip_exchange_panel
from bot.handlers.shop import gold_shop_panel, item_shop_panel, shield_shop_panel, shop_panel
from bot.handlers.subscription import subscription_panel
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
from bot.handlers.mugen_tower import mugen_panel
from bot.handlers.blackmarket import blackmarket_panel
from bot.handlers.purchase import buy_open_callback
from bot.handlers.wheel import wheel_cmd
from bot.buttons import (ADMIN, BACK, BATTLE, BUILD, CONFIRM, DANGER, LIST, NAV, PRIMARY,
                         SHOP, back_btn, back_only_keyboard, btn, enforce_keyboard_symmetry,
                         get_main_reply_keyboard, symmetric_markup)
from bot.utils import mission_reward_text, run_db, safe_edit_message_text, send_screen
from config import BOT_USERNAME, OWNER_TELEGRAM_ID
from game import botconfig, constants, keywords


def _is_admin_user(user_id: int | None) -> bool:
    if user_id is None:
        return False
    if user_id == OWNER_TELEGRAM_ID:
        return True
    from game import admins
    return admins.is_admin(user_id)

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
    capsule_counts,
    create_starter_creature,
    creature_is_maxed,
    devour_candidates,
    effective_stats,
    feed,
    feed_capsules,
    list_creatures,
    set_active_creature,
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
from game.hunt import HUNT_TIERS, resolve_hunt, scout_one


def _mission_lines(completed: list[dict]) -> str:
    if not completed:
        return ""
    lines = [
        f"{get_emoji('mission')} مأموریت{' هفتگی' if m.get('weekly') else ''} «{m['label']}» تکمیل شد! {mission_reward_text(m)}"
        for m in completed
    ]
    return "\n" + "\n".join(lines)


def lab_level_line(user) -> str:
    """The lab's overall level with a progress bar — the one number that answers
    "how far along is this player", independent of any single creature."""
    progress = lab_progress(user)
    if progress["is_max"]:
        return f"🔬 سطح آزمایشگاه: <code>{progress['level']}</code> (بیشینه) {lab_bar(user)}"
    return (
        f"🔬 سطح آزمایشگاه: <code>{progress['level']}</code> {lab_bar(user)} "
        f"<code>{progress['into']:,}/{progress['span']:,}</code>"
    )


def wallet_line(user, energy: int | None = None) -> str:
    """One compact resource strip, reused by every screen that needs it."""
    energy = sync_energy(user) if energy is None else energy
    from game.energy import get_max_energy, minutes_until_next_point

    max_energy = get_max_energy(user)
    if energy >= max_energy:
        energy_note = "<i>(کامل ✅)</i>"
    else:
        energy_note = f"<i>(⏳ تا انرژی بعدی ~<code>{minutes_until_next_point(user)}</code> دقیقه)</i>"
    return (
        f"{get_emoji('coin')} طلا: <code>{user.coins:,}</code>\n"
        f"{get_emoji('dna')} دی‌ان‌ای: <code>{user.dna_fragments:,}</code>\n"
        f"{get_emoji('diamond')} الماس: <code>{user.diamonds:,}</code>\n"
        f"{get_emoji('energy')} انرژی: <code>{energy}/{max_energy}</code> {energy_note}"
    )


def pct_bar(current: int, total: int, width: int = 10) -> str:
    """A `[■■□□□□□□□□] 42%` progress bar — the visual style used across the main
    dashboard cards (lab XP, creature XP, energy, win chance)."""
    total = max(int(total), 1)
    pct = max(0, min(100, round(current / total * 100)))
    filled = min(width, max(0, round(width * max(current, 0) / total)))
    return f"[{'■' * filled}{'□' * (width - filled)}] {pct}%"


_CARD_DIV = "━━━━━━━━━━━━━━━━━━━━"


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
    """The duel is deterministic now (constants.duel_attacker_wins — the very function
    combat uses), so this is an honest 100 (you win) or 0 (you lose), never a guess."""
    return 100 if constants.duel_attacker_wins(my_power, my_elem, opp_power, opp_elem) else 0


def _legacy_win_chance_pct(my_power: int, opp_power: int, my_elem=None, opp_elem=None) -> int:
    my = max(1, int(my_power))
    opp = max(1, int(opp_power))
    # Same-element fights are decided purely by power (see combat._simulate): the
    # stronger side wins for certain, so the shown chance is an honest 100 / 0 (50 only
    # on an exact power tie), never a misleading middle number.
    if my_elem and opp_elem and my_elem == opp_elem:
        if my > opp:
            return 100
        if my < opp:
            return 0
        return 50
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
    if pct >= 100:
        return "🟢 <b>می‌بری</b> (قطعی)"
    if pct <= 0:
        return "🔴 <b>می‌بازی</b> (قطعی)"
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
        return "✅ برتری با تو: +۲۰٪ قدرت"
    if mult < 1:
        return "⚠️ برتری با حریف: +۲۰٪ قدرت"
    return "➖ بدون مزیت عنصری"


def creature_card_text(user, creature, equipped_items: list | None = None, *, compact: bool = False) -> str:
    """The main dashboard shown on /start and /me: base + resources, the active
    creature's identity/level/XP, its combat stats, the full gear loadout, and any
    active defensive shields — each in its own clearly divided block."""
    from game.arena import (group_shield_remaining_seconds, shield_remaining_seconds,
                            _fmt_shield_remaining)
    from game.energy import get_max_energy, minutes_until_next_point
    from game.equipment import equipment_power

    stats = effective_stats(creature, equipped_items)
    energy = sync_energy(user)
    max_energy = get_max_energy(user)
    power = _creature_power(creature, equipped_items)

    lp = lab_progress(user)
    max_level = constants.creature_max_level(creature.rarity, creature.star_level)
    xp_needed = constants.xp_for_creature_level(creature.level)
    is_maxed = creature.level >= max_level
    stars = get_emoji("star") * creature.star_level

    if lp["is_max"]:
        lab_badge = f"{get_emoji('lab')} سطح آزمایشگاه: <code>{lp['level']}</code> (بیشینه)"
    else:
        lab_badge = f"{get_emoji('lab')} سطح آزمایشگاه: <code>{lp['level']}</code> (<code>{lp['into']}/{lp['span']}</code>)"

    arena_secs = shield_remaining_seconds(user)
    group_secs = group_shield_remaining_seconds(user)
    arena_status = _fmt_shield_remaining(arena_secs) if arena_secs > 0 else "غیرفعال"
    group_status = _fmt_shield_remaining(group_secs) if group_secs > 0 else "غیرفعال"

    if compact:
        lines = [
            f"🏰 <b>{lab_display(user)}</b>",
            lab_badge,
            f"{get_emoji('coin')} طلا: <code>{user.coins:,}</code>",
            f"{get_emoji('dna')} دی‌ان‌ای: <code>{user.dna_fragments:,}</code>",
            f"{get_emoji('diamond')} الماس: <code>{user.diamonds:,}</code>",
            f"{get_emoji('energy')} انرژی: <code>{energy}/{max_energy}</code>",
            f"🛡 سپر آرنا: <i>{arena_status}</i>",
            f"👥 سپر گروه: <i>{group_status}</i>",
            "",
            _CARD_DIV,
            "",
            f"{get_emoji('creature')} <b>{creature_name(creature)}</b> <code>#{creature.id}</code>",
        ]
        if creature_has_nickname(creature):
            lines.append(f"🧬 نژاد: <b>{creature.name}</b>")
        lines += [
            f"🏷 نایابی: <b>{constants.RARITY_LABELS[creature.rarity]}</b> {stars}",
            f"🔮 عنصر: <b>{constants.element_label(creature.element)}</b>",
            f"🎖 سطح: <code>{creature.level}/{max_level}</code>" + (" <i>(بیشینه ✅)</i>" if is_maxed else f" ({pct_bar(creature.xp, xp_needed, 6)})"),
            f"💪 قدرت کل: <code>{power:,}</code>",
        ]
        return "\n".join(lines)

    if energy >= max_energy:
        en_note = "<i>(کامل ✅)</i>"
    else:
        en_note = f"<i>(⏳ شارژ بعدی: ~<code>{minutes_until_next_point(user)}</code> دقیقه)</i>"

    lines = [
        f"🏰 پایگاه و آزمایشگاه: <b>{lab_display(user)}</b>",
        "",
        lab_badge,
        f"{get_emoji('biocrate')} <b>خزانه منابع:</b>",
        f"{get_emoji('coin')} طلا: <code>{user.coins:,}</code>",
        f"{get_emoji('dna')} دی‌ان‌ای: <code>{user.dna_fragments:,}</code>",
        f"{get_emoji('diamond')} الماس: <code>{user.diamonds:,}</code>",
        f"{get_emoji('energy')} انرژی: <code>{energy}/{max_energy}</code> {en_note}",
        "",
        _CARD_DIV,
        "",
        f"{get_emoji('creature')} موجود فعال: <b>{creature_name(creature)}</b> <code>#{creature.id}</code>",
    ]
    if creature_has_nickname(creature):
        lines.append(f"🧬 نژاد: <b>{creature.name}</b>")
    lines += [
        f"🏷 نایابی: <b>{constants.RARITY_LABELS[creature.rarity]}</b> {stars}",
        f"🔮 عنصر: <b>{constants.element_label(creature.element)}</b>",
        f"🎖 سطح موجود: <code>{creature.level}/{max_level}</code>" + (" <i>(بیشینه ✅)</i>" if is_maxed else ""),
    ]
    if not is_maxed:
        lines.append(f"📈 پیشرفت لول: {pct_bar(creature.xp, xp_needed)} (<code>{creature.xp:,}/{xp_needed:,}</code> تجربه)")
    lines += [
        "",
        _CARD_DIV,
        "",
        f"⚔️ <b>آمار مبارزه</b>",
        f"💪 توان کل: <code>{power:,}</code>",
        "",
        f"{get_emoji('hp')} سلامت: <code>{stats['hp']:,}</code>",
        f"{get_emoji('atk')} حمله: <code>{stats['atk']:,}</code>",
        f"{get_emoji('def')} دفاع: <code>{stats['def']:,}</code>",
        f"{get_emoji('spd')} سرعت: <code>{stats['spd']:,}</code>",
        "",
        _CARD_DIV,
        "",
        "🎒 <b>تجهیزات و لوداوت:</b>",
    ]
    by_slot = {i.slot: i for i in (equipped_items or [])}
    for slot in constants.EQUIPMENT_SLOTS:
        label = constants.EQUIPMENT_SLOT_LABELS[slot]
        it = by_slot.get(slot)
        if it is not None:
            lines.append(f"{label}: <b>{it.name}</b> <code>[+{it.level}]</code> (<code>+{equipment_power(it)}</code> 💪)")
        else:
            lines.append(f"{label}: <i>خالی</i>")

    lines += [
        "", _CARD_DIV, "",
        "🛡 <b>پوشش سپرهای دفاعی:</b>",
        f"🏟 آرنا: <i>{arena_status}</i>",
        f"👥 گروهی: <i>{group_status}</i>",
    ]
    return "\n".join(lines)


def _fine_bar(current: int, total: int, width: int = 10) -> str:
    """A smooth block bar using eighth-blocks for the partial cell, e.g. «█████████▊»."""
    frac = 0.0 if total <= 0 else max(0.0, min(1.0, current / total))
    filled = frac * width
    full = int(filled)
    eighths = " ▏▎▍▌▋▊▉█"
    partial = eighths[round((filled - full) * 8)] if full < width else ""
    bar = ("█" * full + partial)
    return bar + "░" * (width - len(bar))


def balance_text(user, kaiju_count: int = 0) -> str:
    """The «موجودی» / balance card — wallet + kaiju count + energy, in a compact
    English layout (per the owner's requested format)."""
    from game.energy import get_max_energy, seconds_until_next_point

    energy = sync_energy(user)
    max_energy = get_max_energy(user)
    div = "━━━━━━━━━━━━━━━━━━━━"
    lines = [
        f"👤 <b>{lab_display(user)}</b>",
        div,
        f"{get_emoji('coin')} طلا: <code>{user.coins:,}</code>",
        f"{get_emoji('dna')} دی‌ان‌ای: <code>{user.dna_fragments:,}</code>",
        f"{get_emoji('diamond')} الماس: <code>{user.diamonds:,}</code>",
        f"{get_emoji('creature')} هیولا: <code>{kaiju_count}</code>",
        div,
        f"{get_emoji('energy')} انرژی: <code>{energy}/{max_energy}</code>",
        f"{_fine_bar(energy, max_energy)} <code>{round(100 * energy / max(1, max_energy))}%</code>",
    ]
    if energy < max_energy:
        secs = seconds_until_next_point(user)
        lines += ["", f"<i>⏱ زمان شارژ: <code>{secs // 60:02d}:{secs % 60:02d}</code></i>"]
    else:
        lines += ["", "<i>(کامل ✅)</i>"]
    return "\n".join(lines)


def _balance_sync(tg_user):
    from bio_lab.models import Creature

    user, _ = get_or_create_user(tg_user)
    return user, Creature.objects.filter(owner=user).count()


async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user, kaiju_count = await run_db(_balance_sync, update.effective_user)
    chat = update.effective_chat
    in_group = chat is not None and chat.type in ("group", "supergroup")
    # in a group the balance is a plain readout — no «بازگشت» button (it opened the
    # PV menu, which is broken in a group). Only the DM gets the back button.
    keyboard = None if in_group else back_only_keyboard("menu:me", "بازگشت به منو")
    await send_screen(update, balance_text(user, kaiju_count), parse_mode="HTML", reply_markup=keyboard)


def _slot_summary_lines(slots: list[dict]) -> list[str]:
    """One line per equipment slot, empty slots included — an invisible empty slot
    is a feature a player never discovers."""
    lines = ["🎒 <b>تجهیزات</b>"]
    for row in slots:
        if row["is_empty"]:
            spare = len(row["candidates"])
            hint = f" — <i><code>{spare}</code> گزینه آماده</i>" if spare else ""  # 0 stays silent
            lines.append(f"{row['label']}: <i>خالی</i>{hint}")
        else:
            from game.equipment import equipment_power

            item = row["item"]
            lines.append(f"{row['label']}: <b>{item.name}</b> <code>[+{item.level}]</code> (💪 <code>{equipment_power(item)}</code>)")
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
    from game.energy import get_max_energy, sync_energy

    stats = effective_stats(creature, equipped_items)
    stars = get_emoji("star") * creature.star_level
    max_level = constants.creature_max_level(creature.rarity, creature.star_level)
    energy = sync_energy(user)
    max_energy = get_max_energy(user)

    lines = [
        f"🦅 <b>{creature_name(creature)}</b> <code>#{creature.id}</code>",
        f"🏷 <b>{constants.RARITY_LABELS[creature.rarity]}</b> {stars}",
        f"🎖 سطح: <code>{creature.level}/{max_level}</code>",
        f"⚡️ نرخ ارتقا: <code>{step}×</code>",
        "",
        _power_caption_line(creature, equipped_items),
        f"❤️ سلامت: <code>{stats['hp']:,}</code>",
        f"⚔️ حمله: <code>{stats['atk']:,}</code>",
        f"🛡 دفاع: <code>{stats['def']:,}</code>",
        f"⚡ سرعت: <code>{stats['spd']:,}</code>",
        f"☠️ زهر: <code>{stats['poison']:,}</code>",
        "",
        _CARD_DIV,
        "🧬 <b>ارتقای اعضای بدن:</b>",
    ]

    cap = constants.part_upgrade_cap(creature.rarity, creature.star_level)
    for part, cfg in constants.BODY_PARTS.items():
        level = getattr(creature, f"{part}_lvl")
        label = cfg["label"].split()[0]
        if level >= cap:
            lines.append(f"{label}: <code>{level}/{cap}</code> 🔒 <i>(سقف {creature.star_level}★)</i>")
            continue
        buy = min(step, cap - level)
        cost = part_bulk_cost(level, buy, creature.rarity)
        gain = _part_power_gain(creature, part, equipped_items, buy)
        lines.append(
            f"{label}: <code>{level}/{cap}</code> → <code>{cost:,}</code> 🪙 <i>(+<code>{gain:,}</code> 💪)</i>"
        )

    lines += [
        "",
        _CARD_DIV,
        f"{get_emoji('coin')} طلا: <code>{user.coins:,}</code>",
        f"{get_emoji('energy')} انرژی: <code>{energy}/{max_energy}</code>",
    ]
    return "\n".join(lines)


def _fusion_button_label(star_level: int) -> str:
    """Label the fusion button with the ACTUAL next star this creature would reach
    (1★ → «ارتقا به 2★», …), or a maxed note at 5★, so the button always tells the
    truth about what fusing does for this specific creature."""
    if star_level >= constants.STAR_MAX:
        return "فیوژن (سقف ۵★)"
    return f"ورود به فیوژن ({star_level + 1}★)"


def upgrade_panel_keyboard(creature_id: int, is_active: bool = True, step: int = 1, star_level: int = 1) -> InlineKeyboardMarkup:
    """Every action carries the creature id, so upgrading a non-active creature
    doesn't silently swap which creature is active for hunting/arena."""
    sfx = f" ×{step}" if step > 1 else ""
    # ×1/×5/×10 selector: how many levels each part tap buys (paid in one go)
    step_row = [
        btn(("• " if s == step else "") + f"×{s}", style=(CONFIRM if s == step else NAV),
            callback_data=f"upg_step:{creature_id}:{s}")
        for s in _UPG_STEPS
    ]
    rows = [
        [
            btn("تغذیه", emoji_key="btn_feed", style=BUILD, callback_data=f"feedcap:home:{creature_id}"),
        ],
        step_row,
        [
            btn(f"بال{sfx}", emoji_key="btn_wings", style=BUILD, callback_data=f"lab:up_wings:{creature_id}"),
            btn(f"زره{sfx}", emoji_key="btn_armor", style=BUILD, callback_data=f"lab:up_armor:{creature_id}"),
        ],
        [
            btn(f"نیش{sfx}", emoji_key="btn_fangs", style=BUILD, callback_data=f"lab:up_fangs:{creature_id}"),
            btn(f"زهر{sfx}", emoji_key="btn_poison", style=BUILD, callback_data=f"lab:up_poison:{creature_id}"),
        ],
        [btn("تجهیزات", emoji_key="btn_inventory", style=PRIMARY, callback_data=f"upg_eq:{creature_id}")],
        [btn("بلعیدن هیولا", emoji_key="btn_devour", style=BUILD, callback_data=f"devour_start:{creature_id}")],
        [btn("تغییر نام", emoji_key="btn_edit", style=NAV, callback_data=f"kaiju_rename:{creature_id}:u")],
        [btn(_fusion_button_label(star_level), emoji_key="btn_fusion", style=PRIMARY, callback_data=f"upg_fusion:{creature_id}")],
    ]
    if not is_active:
        # setting the default from here saves a trip through the collection screen,
        # which is where this used to be the only option
        rows.append(
            [btn("انتخاب به عنوان فعال", emoji_key="btn_confirm", style=CONFIRM, callback_data=f"upg_default:{creature_id}")]
        )
    rows.append([back_btn("upg_back", "لیست هیولاها")])
    return InlineKeyboardMarkup(rows)


def equip_panel_text(user, creature, slots: list[dict]) -> str:
    filled = sum(1 for row in slots if not row["is_empty"])
    lines = [
        f"🎒 <b>تجهیزات {creature_name(creature)}</b>",
        f"<blockquote><code>{filled}/{len(slots)}</code> جایگاه پر است — روی هر جایگاه بزن تا عوضش کنی.</blockquote>",
        "",
    ]
    for row in slots:
        if row["is_empty"]:
            spare = len(row["candidates"])
            lines.append(
                f"{row['label']}: <i>خالی</i>"
                + (
                    f" — <i><code>{spare}</code> گزینه برای تجهیز</i>" if spare
                    else " — <i>چیزی برای این جایگاه نداری</i>"
                )
            )
        else:
            item = row["item"]
            bonus = bonus_text(item)
            lines.append(f"{row['label']}: <b>{item.name}</b> <code>+{item.level}</code>" + (f"\n    <i>{bonus}</i>" if bonus else ""))
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


def _creature_base_power(creature, equipped_items: list | None = None) -> int:
    """Power WITHOUT the owner's research-lab buffs (the kaiju's own strength)."""
    from game.creature import creature_base_power

    return creature_base_power(creature, equipped_items)


def _power_caption_line(creature, equipped_items: list | None = None, label: str = "توان کل") -> str:
    """Detail-caption power line: the EFFECTIVE power (with the lab), plus — whenever the
    research lab actually changes it — the base power in parentheses so the player sees
    their kaiju's own strength separate from the lab boost."""
    full = _creature_power(creature, equipped_items)
    base = _creature_base_power(creature, equipped_items)
    if base != full:
        return (f"⚔️ <b>{label}:</b> <code>{full:,}</code> 💪\n"
                f"<i>(قدرت پایه: <code>{base:,}</code> — بدون احتساب تاثیر آزمایشگاه)</i>")
    return f"⚔️ <b>{label}:</b> <code>{full:,}</code> 💪"


def _upgrade_list_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    creatures = list_creatures(user)
    if not creatures:
        raise GameError("اول /start رو بزن تا موجودت رو بگیری.")
    from game import research

    research.attach_research(user, creatures)  # buttons always show WITH-lab power
    from game.equipment import equipped_items_map

    gear = equipped_items_map(creatures)  # ONE query, not one per kaiju
    ranked = sorted(
        ((c, _creature_power(c, gear[c.pk])) for c in creatures),
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
        tag = "🟢" if creature.is_active else "🧬"
        rarity = constants.RARITY_LABELS[creature.rarity].split()[0]
        rows.append([btn(
            f"{tag} {creature_name(creature)} ({rarity} - Lv.{creature.level} - {creature.star_level}★)",
            style=LIST, callback_data=f"upg_pick:{creature.id}",
        )])
    nav = []
    if page > 0:
        nav.append(btn("قبلی", emoji_key="btn_prev", style=NAV, callback_data=f"upg_page:{filt}:{page - 1}"))
    if page < total_pages - 1:
        nav.append(btn("بعدی", emoji_key="btn_next", style=NAV, callback_data=f"upg_page:{filt}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([back_btn("menu:hub_creature", "بازگشت به هیولا")])

    page_note = f" <i>(صفحه <code>{page + 1}/{total_pages}</code>)</i>" if total_pages > 1 else ""
    rarity_note = "" if filt == "all" else f" — <b>{constants.RARITY_LABELS[filt]}</b>"
    text = (
        f"🔧 <b>ارتقا و پرورش</b>{rarity_note}{page_note}\n\n"
        f"نایابی رو انتخاب کن، بعد هیولا رو بزن:\n\n{wallet_line(user)}"
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
    from game.media import get_feature_image_path
    photo = get_feature_image_path("upgrade")
    await send_screen(update, text, photo=photo, parse_mode="HTML", reply_markup=keyboard)


async def upgrade_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    parts = query.data.split(":")
    if parts[0] == "upg_back":  # back from a creature → the page/filter it was opened from
        filt, page = context.user_data.get("upg_loc", ("all", 0))
    elif len(parts) == 3:  # upg_page:<filter>:<page>
        filt, page = parts[1], int(parts[2])
    else:  # old form from a stale keyboard: upg_page:<page>
        filt, page = "all", int(parts[1])
    context.user_data["upg_loc"] = (filt, page)
    try:
        user, ranked = await run_db(_upgrade_list_sync, update.effective_user)
    except GameError as exc:
        await query.answer(alert_text(exc), show_alert=True)
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
    from game import research

    research.attach_research(user, creature)  # so the caption's effective power includes the lab
    return user, creature, get_equipped_items(creature), slot_loadout(user, creature)


async def creature_back_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """«بازگشت» from devour / fusion screens → the creature screen they were opened
    from (the upgrade panel or the collection detail), not always the collection."""
    if context.user_data.get("cr_origin") == "u":
        await upgrade_pick_callback(update, context)
    else:
        await collection_pick_callback(update, context)


async def upgrade_pick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    creature_id = int(query.data.split(":")[1])
    context.user_data["cr_origin"] = "u"
    try:
        user, creature, equipped_items, slots = await run_db(
            _upgrade_pick_sync, update.effective_user, creature_id
        )
    except GameError as exc:
        await query.answer(alert_text(exc), show_alert=True)
        return
    await query.answer()
    step = context.user_data.get("upg_step", 1)
    from game.media import get_creature_image_path
    photo = get_creature_image_path(creature)
    await safe_edit_message_text(
        query,
        upgrade_panel_text(user, creature, equipped_items, slots, step=step),
        photo=photo,
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
        await query.answer(alert_text(exc), show_alert=True)
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
        await query.answer(alert_text(exc), show_alert=True)
        return
    row = next((r for r in slots if r["slot"] == slot), None)
    if row is None:
        await query.answer("این جایگاه وجود نداره.", show_alert=True)
        return

    await query.answer()
    candidates = row["candidates"]
    lines = [f"{row['label']} — <b>{creature.name}</b>", ""]
    if row["item"] is not None:
        lines.append(f"الان: <b>{row['item'].name}</b> <code>+{row['item'].level}</code>")
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
                f"{item.name} +{item.level} ({constants.RARITY_LABELS[item.rarity]}){worn}",
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
            [btn("درآوردن از هیولا", emoji_key="btn_cancel", style=DANGER,
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
        await query.answer(alert_text(exc), show_alert=True)
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
        await query.answer(alert_text(exc), show_alert=True)
        return
    await query.answer("🟢 پیش‌فرض شد!")
    step = context.user_data.get("upg_step", 1)
    from game.media import get_creature_image_path
    photo = get_creature_image_path(creature)
    await safe_edit_message_text(
        query,
        upgrade_panel_text(user, creature, equipped_items, slots, step=step),
        photo=photo,
        parse_mode="HTML",
        reply_markup=upgrade_panel_keyboard(creature.id, creature.is_active, step=step, star_level=creature.star_level),
    )


# ── the in-DM guide ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━──────
#
# Same two-part split as the group help, and the *same* concept pages behind it
# (game/guide.py): how the game works is identical in both places, and a rule
# written twice is a rule that eventually disagrees with itself. Only the
# "where is it" half differs, because the DM is button-driven and the group is
# word-driven.


_CARD_DIV = "━━━━━━━━━━━━━━━━━━━━"


def guide_home_text_and_keyboard(is_first_run: bool = False) -> tuple[str, InlineKeyboardMarkup]:
    lines = [f"{get_emoji('book')} <b>راهنمای Kaiju Legends</b>", ""]
    if is_first_run:
        # a newcomer gets three lines and one button — not the whole table of contents
        lines.append("<b>خوش اومدی! فقط همین سه تا رو یادت باشه:</b>")
        lines.append("<blockquote>" + "\n".join(guide.first_run_lines()) + "</blockquote>")
        return "\n".join(lines), InlineKeyboardMarkup(
            [[btn("راهنمای کامل", emoji_key="btn_report", style=NAV, callback_data="guide:home")]]
        )
    else:
        lines.append(
            "<blockquote>یه بازی پرورش هیولاست: یه هیولا داری، قوی‌ترش می‌کنی، باهاش می‌جنگی "
            "و آزمایشگاهت رو بزرگ می‌کنی.</blockquote>"
        )
    lines += [
        "",
        _CARD_DIV,
        f"{get_emoji('lab')} <b>چطور بازی می‌کنم؟</b>",
        "<i>قانون‌های بازی — اگه تازه‌واردی از اینجا شروع کن.</i>",
        "",
    ]
    concept_buttons = []
    for key, (emoji_key, title, _blurb, _rows) in guide.CONCEPTS.items():
        lines.append(f"{get_emoji(emoji_key)} <b>{title}</b>")
        concept_buttons.append(btn(title, style=CONFIRM, callback_data=f"guide:c_{key}"))

    lines += ["", _CARD_DIV, f"{get_emoji('settings')} <b>کجا چیکار کنم؟</b>",
              "<i>هر بخش منو چیه و چه‌کار می‌کنه.</i>", ""]
    area_buttons = []
    for key, (emoji_key, title, blurb, _rows) in guide.DM_SECTIONS.items():
        lines.append(f"{get_emoji(emoji_key)} <b>{title}</b>\n    <i>{blurb}</i>")
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
        lines += [_CARD_DIV, f"<b>{heading}</b>", f"<blockquote>{body}</blockquote>", ""]
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
    await send_screen(update, text, parse_mode="HTML", reply_markup=keyboard)


async def guide_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    key = query.data.split(":", 1)[1]
    await query.answer()
    if key == "home":
        text, keyboard = guide_home_text_and_keyboard()
    elif key.startswith("c_"):
        rendered = _guide_page(guide.CONCEPTS, key[2:])
        text, keyboard = rendered or guide_home_text_and_keyboard()
    elif key.startswith("a_"):
        rendered = _guide_page(guide.DM_SECTIONS, key[2:])
        text, keyboard = rendered or guide_home_text_and_keyboard()
    else:
        rendered = _guide_page(guide.DM_SECTIONS, key)
        text, keyboard = rendered or guide_home_text_and_keyboard()
    await send_screen(update, text, parse_mode="HTML", reply_markup=keyboard)


# ── Categorised menu ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━───────
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
_STYLE_MAP = {"p": PRIMARY, "b": BATTLE, "n": NAV, "s": SHOP, "c": CONFIRM, "bu": BUILD}


def _mkbtn(spec, locked=frozenset()):
    label, action, style, ekey = spec
    if action in locked:
        return btn(label, emoji_key="btn_locked", style=_STYLE_MAP[style], callback_data=f"menu:{action}")
    return btn(label, emoji_key=ekey, style=_STYLE_MAP[style], callback_data=f"menu:{action}")


# Progressive unlocks: each major feature unlocks at its own specific, balanced level:
# Level 2: Fusion (ترکیب هیولا), Breeding (غار هیولا), Dispatch (مأموریت اعزامی), Battlepass, Exchange
# Level 3: Team (تیم من), Blacksmith (آهنگری), League, Events, Shield Shop, Equip Exchange
# Level 4: Dungeon Campaign (دانجن), Black Market, Casino, Titles, Treasury Rank
# Level 5: Mugen Tower (برج موگن), Research Lab, Special Banners, Item Shop, Alliance League, Raid Rank
SECTION_HALL_REQ = {
    # Level 2
    "fusion": 2,
    "breeding": 2,
    "dispatch": 2,
    "battlepass": 2,
    "exchange": 2,

    # Level 3
    "blacksmith": 3,
    "team": 3,
    "league": 3,
    "shield_shop": 3,
    "events": 3,
    "equip_exchange": 3,

    # Level 4
    "campaign": 4,
    "blackmarket": 4,
    "casino": 4,
    "titles": 4,
    "rank": 4,

    # Level 5
    "mugen_tower": 5,
    "research": 5,
    "banner": 5,
    "item_shop": 5,
    "alliance_league": 5,
    "raid_rank": 5,
}


def _locked_actions_for(hall_level) -> frozenset:
    """The menu actions the player can't open yet at this main-hall level."""
    if hall_level is None:
        return frozenset()
    return frozenset(a for a, req in SECTION_HALL_REQ.items() if hall_level < req)


_HUBS = {
    "hub_battle": ("⚔️ <b>نبرد</b>", [
        [("شکار", "hunt", "b", "btn_hunt"), ("آرنا", "arena", "b", "btn_arena")],
        [("جعبه‌ها", "arena_chests", "s", "btn_chests"), ("برج موگن", "mugen_tower", "b", "btn_mugen")],
        [("اعزام", "dispatch", "b", "btn_dispatch"), ("دانجن", "campaign", "b", "btn_campaign")],
        [("غول", "worldboss", "b", "btn_worldboss"), ("جام", "tournament", "b", "btn_tournament")],
    ]),
    "hub_battle_more": ("⚔️ <b>نبرد — بیشتر</b>", [
        [("کاروان", "expedition", "b", "btn_expedition"), ("جنگ اتحاد", "alliance_war", "b", "btn_war")],
    ]),
    "hub_creature": ("🦖 <b>هیولا</b>", [
        [("ارتقا", "upgrade", "p", "btn_upgrade"), ("تجهیزات", "inventory", "n", "btn_inventory")],
        [("آهنگری", "blacksmith", "bu", "btn_forge"), ("غار", "breeding", "bu", "btn_breeding")],
        [("ادغام", "fusion", "bu", "btn_fusion"), ("کلکسیون", "collection", "n", "btn_collection")],
        [("تیم", "team", "n", "btn_team")],
    ]),
    "hub_base": ("🏰 <b>پایگاه</b>", [
        [("ساختمان‌ها", "buildings", "bu", "btn_buildings"), ("پژوهش", "research", "bu", "btn_research")],
        [("خزانه", "vault", "bu", "btn_vault"), ("صرافی", "exchange", "s", "btn_exchange")],
        [("بازیافت", "equip_exchange", "s", "btn_ticket_exchange")],
    ]),
    "hub_shop": ("🛒 <b>فروشگاه</b>", [
        [("فروشگاه", "shop", "s", "btn_shop"), ("بازار سیاه", "blackmarket", "s", "btn_blackmarket")],
        [("باکس ژنتیکی", "biocrate", "s", "btn_biocrate"), ("باکس الماسی", "diamond_box", "s", "btn_diamond_box")],
        [("خرید الماس", "buy_open", "s", "btn_buy"), ("سپر", "shield_shop", "s", "btn_shield")],
        [("اشتراک VIP", "subscription", "s", "btn_vip")],
    ]),
    "hub_city": ("🌐 <b>شهر</b>", [
        [("مأموریت‌ها", "missions", "s", "btn_missions"), ("گردونه", "wheel", "s", "btn_wheel")],
        [("رویداد", "events", "s", "btn_events"), ("جشنواره", "festival", "s", "btn_festival")],
        [("لیگ", "league", "n", "btn_league"), ("پاس فصلی", "battlepass", "s", "btn_battlepass")],
        [("اتحاد", "alliance_info", "n", "btn_alliance"), ("پروفایل", "profile", "n", "btn_profile")],
    ]),
    "hub_city_more": ("🌐 <b>شهر — بیشتر</b>", [
        [("دستاوردها", "achievements", "s", "btn_achievements"), ("راهنما", "guide", "n", "btn_report")],
        [("کازینو", "casino", "s", "btn_casino"), ("بنر", "banner", "s", "btn_banner")],
        [("دعوت", "referral", "s", "btn_referral"), ("دانشنامه", "codex", "n", "btn_codex")],
        [("رتبه اتحادها", "rank", "n", "btn_league"), ("لیگ اتحادها", "alliance_league", "n", "btn_league")],
        [("رتبه رید", "raid_rank", "n", "btn_raid_rank")],
    ]),
}

# Legacy category aliases
_CATEGORIES = {
    "rewards": _HUBS["hub_city"],
    "shop": _HUBS["hub_shop"],
    "social": _HUBS["hub_city"],
}


_INVISIBLE_CHARS = dict.fromkeys(
    [0x200B, 0x200D, 0x200E, 0x200F, 0x2060, 0x2061, 0x2062, 0x2063, 0x2064, 0xFEFF, 0x00AD, 0x061C,
     0x202A, 0x202B, 0x202C, 0x202D, 0x202E, 0x2066, 0x2067, 0x2068, 0x2069, 0x3164, 0x115F, 0x1160]
)


def _clean_lab_name(name) -> str:
    """Whitespace collapsed, invisible/direction-control characters dropped, a ZWNJ kept
    only between letters (Persian half-space), then trimmed to the length limit."""
    text = " ".join(str(name).translate(_INVISIBLE_CHARS).split())
    text = re.sub(r"(?<![^\W\d_])\u200c|\u200c(?![^\W\d_])", "", text).strip()
    return text[:LAB_NAME_MAX_LEN]


def story_quest_card_text(quest: dict | None) -> str:
    if not quest:
        return ""
    # two lines, not five: what to do next and what it pays (the chapter name and the
    # flavour text made the main card scroll; the button under it says where to go)
    progress = f" <code>{quest['cur']}/{quest['target']}</code>" if quest["target"] > 1 else ""
    done = " ✅" if quest["is_done"] else ""
    return (
        f"🎯 <b>قدم بعدی ({quest['step_num']}/{quest['total_steps']}):</b> {quest['title']}{progress}{done}\n"
        f"🎁 {quest['reward_text']}\n"
    )


def _hub_keyboard(hub_key: str, locked=frozenset(), hide_locked: bool = False) -> tuple[str, InlineKeyboardMarkup]:
    """A hub's buttons (at most 8 — the rarely-used ones sit behind «بیشتر»). With
    `hide_locked` (a newcomer whose main hall is still level 1) the sections that haven't
    unlocked are left out instead of shown with a lock, so a new player sees a short menu
    of things that work; the title says how many more are waiting."""
    if not hub_key.startswith("hub_") and f"hub_{hub_key}" in _HUBS:
        hub_key = f"hub_{hub_key}"
    title, rows_def = _HUBS.get(hub_key, _CATEGORIES.get(hub_key, ("📋 <b>منوی بازی</b>", [])))
    more_key = f"{hub_key}_more"
    specs = [spec for row in rows_def for spec in row]
    hidden = 0
    if hide_locked:
        more_specs = [s for row in _HUBS.get(more_key, ("", []))[1] for s in row]
        hidden = sum(1 for s in specs + more_specs if s[1] in locked)
        specs = [s for s in specs if s[1] not in locked]
    rows = [[_mkbtn(s, locked) for s in specs[i:i + 2]] for i in range(0, len(specs), 2)]
    has_more = more_key in _HUBS and (
        not hide_locked or any(s[1] not in locked for row in _HUBS[more_key][1] for s in row)
    )
    if has_more:
        rows.append([btn("بیشتر", emoji_key="btn_next", style=NAV, callback_data=f"menu:{more_key}")])
    if hub_key.endswith("_more"):
        rows.append([back_btn(f"menu:{hub_key[:-5]}", "بازگشت")])
    else:
        rows.append([back_btn("menu:me", "منوی اصلی")])
    if hidden:
        title += f"\n🔒 <i>{hidden} بخش دیگه با ارتقای «تالار مِهر» باز می‌شه.</i>"
    return title, symmetric_markup(rows)


def _category_keyboard(cat_key: str, locked=frozenset()) -> tuple[str, InlineKeyboardMarkup]:
    return _hub_keyboard(cat_key, locked)


def creature_keyboard(quest: dict | None = None, is_owner: bool = False, locked=frozenset(), research_built=False,
                      today_state: dict | None = None) -> InlineKeyboardMarkup:
    """Clean 5-Hub navigation with Live Story Quest CTA banner. `today_state`
    (game.today.state) adds the count on «امروز» and a row for whatever is LIVE right now
    (world boss / tournament registration / festival) — those only show while they matter."""
    rows = []
    if quest:
        if quest["is_done"]:
            rows.append([btn(f"دریافت پاداش ({quest['title']})", emoji_key="btn_confirm", style=CONFIRM, callback_data="story_claim")])
        else:
            # the active quest's own section is exempt from the hall lock (menu_callback
            # lets it through), so the button always goes where the quest says
            cb = quest["cta_callback"]
            rows.append([btn(f"قدم بعدی: {quest['cta_label']}", emoji_key="btn_skill", style=PRIMARY, callback_data=cb)])

    from game import today as _today

    waiting = _today.waiting_count(today_state) if today_state else 0
    rows.append([btn(f"پاداش امروز ({waiting})" if waiting else "پاداش امروز", emoji_key="btn_today", style=CONFIRM,
                     callback_data="menu:today")])
    if today_state:
        live = []
        if today_state["boss"] and today_state["boss"]["hits_left"]:
            live.append(btn("غول سرگردان اینجاست", emoji_key="btn_worldboss", style=DANGER, callback_data="menu:worldboss"))
        if today_state["tournament_open"]:
            live.append(btn("ثبت‌نام جام", emoji_key="btn_tournament", style=DANGER, callback_data="menu:tournament"))
        if today_state["festival"]:
            live.append(btn("جشنواره", emoji_key="btn_festival", style=SHOP, callback_data="menu:festival"))
        if live:
            rows.append(live[:2])

    # Row 1: Battle & Creature
    rows.append([
        btn("نبرد", emoji_key="btn_hub_battle", style=DANGER, callback_data="menu:hub_battle"),
        btn("هیولا", emoji_key="btn_hub_creature", style=PRIMARY, callback_data="menu:hub_creature"),
    ])
    # Row 2: Base & Shop
    rows.append([
        btn("پایگاه", emoji_key="btn_hub_base", style=CONFIRM, callback_data="menu:hub_base"),
        btn("فروشگاه", emoji_key="btn_hub_shop", style=SHOP, callback_data="menu:hub_shop"),
    ])
    # Row 3: City
    rows.append([
        btn("شهر", emoji_key="btn_hub_city", style=PRIMARY, callback_data="menu:hub_city"),
    ])

    group_link = botconfig.get_group_link()
    if group_link is not None:
        url, title = group_link
        rows.append([btn(title, emoji_key="btn_join_group", style=PRIMARY, url=url)])
    add_group_url = f"https://t.me/{BOT_USERNAME}?startgroup=true"
    rows.append([
        btn("افزودن به گروه", emoji_key="btn_add_group", style=CONFIRM, url=add_group_url)
    ])
    if is_owner:
        rows.append([btn("ادمین", emoji_key="btn_admin", style=ADMIN, callback_data="menu:admin")])
    return symmetric_markup(rows)


logger = logging.getLogger(__name__)

LAB_NAME_MAX_LEN = 32


def _auto_lab_name(user, tg_user) -> None:
    """Give a brand-new player a lab name instead of asking for one. More than half of the
    people who pressed /start never got past «type a name» — the very first thing the bot
    asked. The name is unique like any other, and the first rename is free (lab_renames
    = -1 → constants.lab_rename_cost returns 0)."""
    first = _clean_lab_name(getattr(tg_user, "first_name", "") or "")
    first = re.sub(r"[<>&]", "", first).strip()[:18]
    base = f"آزمایشگاه {first}" if first else "آزمایشگاه"
    candidates = ([base] if first else []) + [f"{base} {n}" for n in range(2, 6)] + [f"{base} {user.id}"]
    for name in candidates:
        name = name[:LAB_NAME_MAX_LEN]
        if not lab_name_taken(name, exclude_user_id=user.id):
            user.lab_name = name
            user.lab_renames = -1
            user.save(update_fields=["lab_name", "lab_renames"])
            return


def _start_sync(tg_user, referrer_id=None):
    from game import story
    from game.buildings import main_hall_level
    from game import research

    user, was_created = get_or_create_user(tg_user)
    if not user.started_gate:
        user.started_gate = True
        user.save(update_fields=["started_gate"])
    if user.lab_name is None:
        _auto_lab_name(user, tg_user)
    if referrer_id is not None:
        from game.referral import register_referral

        register_referral(user, was_created, referrer_id)
    creature = get_active_creature(user)
    is_new = False
    if creature is None:
        if user.lab_name and user.onboarding_completed:
            creature = create_starter_creature(user)
            is_new = True
            for minutes, count in constants.STARTING_SPEEDUP_CARDS.items():
                grant_speedup_card(user, minutes, count=count)
    else:
        if not user.onboarding_completed:
            user.onboarding_completed = True
            user.save(update_fields=["onboarding_completed"])
    login_bonus = apply_daily_login(user) if creature else None
    equipped_items = get_equipped_items(creature) if creature else []
    get_or_create_buildings(user)
    user._story_note = _story_auto_claim(user) if creature else ""
    quest = story.get_active_quest(user)
    user._today = _today_state_safe(user) if creature else None
    return user, creature, is_new, login_bonus, equipped_items, main_hall_level(user), research.is_unlocked(user), quest


def _set_lab_name_sync(tg_user, name):
    from game import story
    from game.buildings import main_hall_level
    from game import research

    user, _ = get_or_create_user(tg_user)
    # Collapse whitespace and strip control characters. The name is shown on every
    # leaderboard, so a name padded with newlines could push other rows off the
    # screen; lab_display() handles the HTML escaping separately.
    cleaned = _clean_lab_name(name)
    if not cleaned:
        raise GameError("اسم نمی‌تونه خالی باشه")
    # Lab names must be unique — they're shown on every leaderboard AND used as a
    # moderation identifier (resolve_user), so a duplicate would be ambiguous.
    if lab_name_taken(cleaned, exclude_user_id=user.id):
        raise GameError("این اسم آزمایشگاه قبلاً گرفته شده")
    user.lab_name = cleaned
    user.save(update_fields=["lab_name"])
    creature = get_active_creature(user)
    equipped_items = get_equipped_items(creature) if creature else []
    quest = story.get_active_quest(user)
    return user, creature, equipped_items, main_hall_level(user), research.is_unlocked(user), quest


def _rename_lab_check_sync(tg_user, name):
    user, _ = get_or_create_user(tg_user)
    cleaned = _clean_lab_name(name)
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
    return user, cost, cleaned


def _rename_lab_sync(tg_user, name):
    """Paid lab rename: charges diamonds (escalating each time) and enforces the
    same uniqueness as the free first-time name."""
    user, _ = get_or_create_user(tg_user)
    cleaned = _clean_lab_name(name)
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


def _set_transfer_notify_sync(tg_user, on: bool):
    user, _ = get_or_create_user(tg_user)
    user.transfer_notify = on
    user.save(update_fields=["transfer_notify"])


async def transfer_notify_off_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/off — stop the DM you get when you RECEIVE a transfer."""
    await run_db(_set_transfer_notify_sync, update.effective_user, False)
    await update.effective_message.reply_text(
        "🔕 دیگه برای انتقال‌های دریافتی بهت پیام نمی‌دم.\n"
        "اگه بازم خواستی روشن شه، /on رو بزن.",
    )


async def transfer_notify_on_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/on — re-enable the received-transfer DM."""
    await run_db(_set_transfer_notify_sync, update.effective_user, True)
    await update.effective_message.reply_text(
        "🔔 باشه، از این به بعد برای هر انتقالِ دریافتی بهت خبر می‌دم.",
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.args:
        arg = context.args[0]
        if arg == "sub_silver":
            from bot.handlers.subscription import send_subscription_invoice
            await send_subscription_invoice(update.effective_message, update.effective_user, "silver", context)
            return
        elif arg == "sub_gold":
            from bot.handlers.subscription import send_subscription_invoice
            await send_subscription_invoice(update.effective_message, update.effective_user, "gold", context)
            return
        elif arg in ("subscription", "vip"):
            from bot.handlers.subscription import subscription_panel
            await subscription_panel(update, context)
            return

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
    user, creature, is_new, login_bonus, equipped_items, hall_level, research_built, quest = await run_db(
        _start_sync, update.effective_user, referrer_id
    )

    if user.lab_name is None:
        context.user_data[AWAITING_PLAYER_KEY] = {"action": "set_lab_name"}
        await update.message.reply_text(
            f"{get_emoji('egg')} <b>به Kaiju Legends خوش اومدی فرمانده!</b>\n\n"
            "برای تأسیس پایگاه ژنتیکی خودت، یک نام برای آزمایشگاه انتخاب کن (همینجا بفرست):",
            parse_mode="HTML",
        )
        return

    if not user.onboarding_completed and creature is None:
        text = (
            f"{get_emoji('egg')} <b>به Kaiju Legends خوش اومدی فرمانده!</b>\n\n"
            "<blockquote>یه تخم هیولا توی آزمایشگاهت منتظره. بشکنش تا اولین کایجوت به دنیا بیاد!</blockquote>\n"
            f"<i>اسم آزمایشگاهت فعلاً «{lab_display(user)}»ـه؛ هر وقت خواستی از «شهر ← پروفایل» رایگان عوضش کن.</i>"
        )
        keyboard = InlineKeyboardMarkup([
            [btn("شکستن اولین تخم کایجو", emoji_key="btn_hatch", style=CONFIRM, callback_data="onboarding:hatch")]
        ])
        await send_screen(update, text, parse_mode="HTML", reply_markup=keyboard)
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
        streak_line = f"🔥 <b><code>{login_bonus['streak']}</code> روز پشت‌سرهم</b> اومدی!\n"
        streak_line += f"{get_emoji('coin')} طلا: <code>+{login_bonus['coins']:,}</code>\n"
        if login_bonus.get("dna"):
            streak_line += f"{get_emoji('dna')} دی‌ان‌ای: <code>+{login_bonus['dna']:,}</code>\n"
        lines.append(f"<blockquote>{streak_line.strip()}</blockquote>\n")

    if getattr(user, "_story_note", ""):
        lines.append(user._story_note + "\n")
    quest_txt = story_quest_card_text(quest)
    if quest_txt:
        lines.append(f"<blockquote>{quest_txt.strip()}</blockquote>\n")

    from game.media import get_creature_image_path

    creature_photo = get_creature_image_path(creature) if creature else None
    if creature:
        lines.append(creature_card_text(user, creature, equipped_items, compact=bool(creature_photo)))
    is_owner = _is_admin_user(update.effective_user.id if update.effective_user else None)
    await send_screen(
        update,
        "\n".join(lines),
        photo=creature_photo,
        parse_mode="HTML",
        reply_markup=creature_keyboard(quest, is_owner, _locked_actions_for(hall_level), research_built,
                                       today_state=getattr(user, "_today", None)),
    )

    if is_new and update.message:
        await send_first_run_guide(update.message)


def _story_auto_claim(user) -> str:
    """A finished story quest is paid the moment the player is back on the main card —
    no «دریافت پاداش» tap. One quest per visit, so each still gets its own line."""
    from game import story

    try:
        res = story.claim_active_quest(user)
    except Exception:  # noqa: BLE001 — never let the story block the main menu
        logger.exception("story auto-claim failed")
        return ""
    if not res.get("success"):
        return ""
    user.refresh_from_db()
    return f"🎉 <b>مأموریت «{res['claimed_quest']['title']}» انجام شد!</b>\n🎁 {res['reward_text']}"


def _today_state_safe(user):
    from game import today

    try:
        return today.state(user, full=False)
    except Exception:  # noqa: BLE001 — the badge is a nicety; the menu must always open
        logger.exception("today.state failed")
        return None


def _me_sync(tg_user):
    from game.buildings import main_hall_level
    from game import research, story

    user, _ = get_or_create_user(tg_user)
    creature = get_active_creature(user)
    equipped_items = get_equipped_items(creature) if creature else []
    if creature:
        research.attach_research(user, creature)  # buffed power shows on the card
    user._story_note = _story_auto_claim(user) if creature else ""
    quest = story.get_active_quest(user)
    user._today = _today_state_safe(user) if creature else None
    return user, creature, equipped_items, main_hall_level(user), research.is_unlocked(user), quest


async def me(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("from_today", None)  # back on the main card → no «came from امروز»
    user, creature, equipped_items, hall_level, research_built, quest = await run_db(_me_sync, update.effective_user)
    if creature is None:
        await send_screen(update,
            "😅 هنوز هیولایی نداری! /start رو بزن تا اولین هیولات رو بگیری."
        )
        return
    is_owner = _is_admin_user(update.effective_user.id if update.effective_user else None)
    from game.media import get_creature_image_path
    photo_path = get_creature_image_path(creature)
    card_txt = creature_card_text(user, creature, equipped_items, compact=bool(photo_path))
    quest_txt = story_quest_card_text(quest)
    quest_block = f"<blockquote>{quest_txt.strip()}</blockquote>\n\n" if quest_txt.strip() else ""
    note = getattr(user, "_story_note", "")
    note_block = f"{note}\n\n" if note else ""
    text = f"👋 <b>آزمایشگاه «{lab_display(user)}»</b>\n\n{note_block}{quest_block}" + card_txt
    await send_screen(update,
        text,
        photo=photo_path,
        parse_mode="HTML",
        reply_markup=creature_keyboard(quest, is_owner, _locked_actions_for(hall_level), research_built,
                                       today_state=getattr(user, "_today", None)),
    )


def _lab_action_sync(tg_user, action, creature_id, count=1):
    user, _ = get_or_create_user(tg_user)
    try:
        creature = Creature.objects.get(id=creature_id, owner=user)
    except Creature.DoesNotExist:
        raise GameError("این موجود توی کلکسیون تو نیست.")

    completed_missions: list[dict] = []
    if action == "feed":
        with transaction.atomic():  # feed() re-reads the locked row → save energy first
            lock_row(user)
            spend_energy(user, constants.FEED_ENERGY_COST, "تغذیه")
            user.save(update_fields=["energy", "energy_updated_at"])
            levels = feed(user, creature)
        record_action(user, "feed")
        completed_missions = check_missions(user, "feed")
        note = "🍖 <b>تغذیه شد!</b>" + (
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
        await query.answer(alert_text(exc), show_alert=True)
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
        from bot.handlers.energy import show_energy_error
        from bot.handlers.shop import show_gold_error

        if await show_energy_error(query, exc):
            return
        if await show_gold_error(query, exc):
            return
        # Telegram caps callback-alert text at 200 chars — a longer message throws
        # BadRequest("Message_too_long"), which used to crash this handler
        await query.answer(alert_text(exc), show_alert=True)
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


# ── 🧪 capsule feeding panel ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━──
def _feedcap_view_sync(tg_user, creature_id):
    user, _ = get_or_create_user(tg_user)
    try:
        creature = Creature.objects.get(id=creature_id, owner=user)
    except Creature.DoesNotExist:
        raise GameError("این موجود توی کلکسیون تو نیست.")
    return user, creature, capsule_counts(user), creature_is_maxed(creature)


def feedcap_text(user, creature, caps: dict, maxed: bool) -> str:
    max_level = constants.creature_max_level(creature.rarity, creature.star_level)
    lines = [
        f"🍽 <b>تغذیهٔ {creature_name(creature)}</b>",
        f"🎖 سطح: <code>{creature.level}/{max_level}</code>",
        f"📈 تجربه: <code>{creature.xp:,}/{constants.xp_for_creature_level(creature.level):,}</code> XP",
        "",
        "چه غذایی بدی هیولات بخوره؟",
        _CARD_DIV,
    ]
    total = 0
    for tier in constants.XP_CAPSULE_ORDER:
        cfg = constants.XP_CAPSULES[tier]
        n = caps.get(tier, 0)
        total += n
        lines.append(f"{cfg['emoji']} <b>{cfg['label']}</b>: <code>{n}</code> عدد <i>(+<code>{cfg['xp']:,}</code> XP)</i>")
    lines.append("")
    if maxed:
        lines.append("<blockquote>🔒 این کایجو به سقف سطحش رسیده — تغذیه بی‌فایده‌ست.</blockquote>")
    elif total == 0:
        lines.append("<blockquote>هیچ حیوونی برای تغذیه نداری. از «🛒 فروشگاه روزانه» بخر یا از جایزه‌ها بگیر.</blockquote>")
    else:
        lines.append("<i>یکی رو بزن؛ «تغذیه با همه» بزرگ‌ها رو اول می‌ده و به سقف سطح که رسید متوقف می‌شه.</i>")
    return "\n".join(lines)


def feedcap_keyboard(creature, caps: dict, maxed: bool) -> InlineKeyboardMarkup:
    cid = creature.id
    rows = []
    if not maxed:
        for tier in constants.XP_CAPSULE_ORDER:
            cfg = constants.XP_CAPSULES[tier]
            n = caps.get(tier, 0)
            if n <= 0:
                continue
            rows.append([
                btn(f"{cfg['emoji']} +۱", style=BUILD, callback_data=f"feedcap:one:{cid}:{tier}"),
                btn(f"همه ({n})", style=BUILD, callback_data=f"feedcap:allt:{cid}:{tier}"),
            ])
        if sum(caps.values()) > 0:
            rows.append([btn("تغذیه با همه", emoji_key="btn_feed", style=CONFIRM,
                             callback_data=f"feedcap:all:{cid}")])
    rows.append([back_btn(f"upg_pick:{cid}")])
    return InlineKeyboardMarkup(rows)


def _feedcap_plan(kind: str, tier: str, caps: dict) -> list:
    if kind == "one":
        return [(tier, 1)]
    if kind == "allt":
        return [(tier, caps.get(tier, 0))]
    # all: biggest first so a nearly-maxed creature wastes the fewest
    return [(t, caps.get(t, 0)) for t in reversed(constants.XP_CAPSULE_ORDER)]


def _feedcap_do_sync(tg_user, creature_id, kind, tier):
    user, _ = get_or_create_user(tg_user)
    try:
        creature = Creature.objects.get(id=creature_id, owner=user)
    except Creature.DoesNotExist:
        raise GameError("این موجود توی کلکسیون تو نیست.")
    caps = capsule_counts(user)
    result = feed_capsules(user, creature, _feedcap_plan(kind, tier, caps))
    record_action(user, "feed")
    check_missions(user, "feed")
    return user, creature, result, capsule_counts(user), creature_is_maxed(creature)


async def feedcap_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    parts = query.data.split(":")
    sub, creature_id = parts[1], int(parts[2])
    if sub == "home":
        try:
            user, creature, caps, maxed = await run_db(_feedcap_view_sync, update.effective_user, creature_id)
        except GameError as exc:
            await query.answer(alert_text(exc), show_alert=True)
            return
        await query.answer()
        await safe_edit_message_text(query, feedcap_text(user, creature, caps, maxed),
                                     parse_mode="HTML", reply_markup=feedcap_keyboard(creature, caps, maxed))
        return
    tier = parts[3] if len(parts) > 3 else ""
    try:
        user, creature, result, caps, maxed = await run_db(
            _feedcap_do_sync, update.effective_user, creature_id, sub, tier
        )
    except GameError as exc:
        await query.answer(alert_text(exc), show_alert=True)
        return
    eaten = sum(result["consumed"].values())
    lvl = f"\n{get_emoji('celebrate')} <b>رسید به سطح <code>{result['new_level']}</code>!</b>" if result["levels"] else ""
    await query.answer(f"🍽 {eaten} تا غذا داده شد · +{result['xp']:,} XP")
    note = f"<blockquote>🍽 <b><code>{eaten}</code> تا غذا به هیولات دادی</b>\n✨ تجربه: <code>+{result['xp']:,}</code> XP{lvl}</blockquote>\n\n"
    await safe_edit_message_text(query, note + feedcap_text(user, creature, caps, maxed),
                                 parse_mode="HTML", reply_markup=feedcap_keyboard(creature, caps, maxed))


def _collection_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    creatures = list_creatures(user)
    from game import research

    research.attach_research(user, creatures)  # buttons always show WITH-lab power
    # pair each creature with its effective (with-lab) power for the button labels
    from game.equipment import equipped_items_map

    gear = equipped_items_map(creatures)  # ONE query, not one per kaiju
    return [(c, _creature_power(c, gear[c.pk])) for c in creatures]


COLLECTION_PAGE_SIZE = 8


_RARITY_CIRCLES = {
    "common": "⚪️",
    "rare": "🔵",
    "epic": "🟣",
    "legendary": "🟡",
    "mythic": "🔴",
}


def _collection_render(ranked, filt: str = "all", page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    """One page of the collection, filterable by rarity via tabs (like the fusion
    picker). Paginated because a big roster (each creature is two buttons) blew past
    Telegram's ~100-button keyboard limit — the whole keyboard was rejected then.
    `ranked` is [(creature, power)] where power is the WITH-lab effective power."""
    power_of = {c.id: p for c, p in ranked}
    creatures = [c for c, _p in ranked]
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
        rarity_short = constants.RARITY_LABELS[c.rarity].split()[-1]
        circle = _RARITY_CIRCLES.get(c.rarity, "⚪️")
        rows.append([btn(f"{circle} {creature_name(c)} ({rarity_short})", style=LIST, callback_data=f"coll_pick:{c.id}")])
        if c.is_active:
            rows.append([btn(f"⭐ {c.star_level} ستاره • سطح {c.level} (فعال)", style=CONFIRM, callback_data=f"coll_pick:{c.id}")])
        else:
            rows.append([
                btn(f"⭐ {c.star_level} ستاره • سطح {c.level}", style=NAV, callback_data=f"coll_pick:{c.id}"),
                btn("انتخاب", emoji_key="btn_confirm", style=PRIMARY, callback_data=f"coll_select:{c.id}"),
            ])
    nav = []
    if page > 0:
        nav.append(btn("قبلی", emoji_key="btn_prev", style=NAV, callback_data=f"coll_page:{filt}:{page - 1}"))
    if page < total_pages - 1:
        nav.append(btn("بعدی", emoji_key="btn_next", style=NAV, callback_data=f"coll_page:{filt}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([btn("ترکیب هیولا", emoji_key="btn_fusion", style=NAV, callback_data="menu:fusion")])
    rows.append([back_btn("menu:hub_creature", "بازگشت به هیولا")])

    page_note = f" <i>(صفحه <code>{page + 1}/{total_pages}</code>)</i>" if total_pages > 1 else ""
    text = (
        f"{get_emoji('collection')} <b>کلکسیون تو</b> — <code>{len(creatures)}</code> موجود{page_note}\n"
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
    from game.media import get_feature_image_path
    photo = get_feature_image_path("collection")
    await send_screen(update, text, photo=photo, parse_mode="HTML", reply_markup=keyboard)


async def collection_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    parts = query.data.split(":")
    # new form is coll_page:<filt>:<page>; tolerate the old coll_page:<page> too
    if parts[0] == "coll_back":  # back from a creature → the page/filter it was opened from
        filt, page = context.user_data.get("coll_loc", ("all", 0))
    elif len(parts) == 3:
        filt, page = parts[1], int(parts[2])
    else:
        filt, page = "all", int(parts[1])
    context.user_data["coll_loc"] = (filt, page)
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
    from game import research

    research.attach_research(user, creature)  # so the caption's effective power includes the lab
    return user, creature, get_equipped_items(creature)


def collection_creature_detail_text(creature, equipped_items: list | None = None) -> str:
    """Detailed profile card for a specific creature viewed from the collection.
    Exclusively displays creature-specific info (identity, element, rarity, level,
    stats, body parts, gear) without player/lab wallet clutter."""
    from game.equipment import equipment_power

    stats = effective_stats(creature, equipped_items)

    max_level = constants.creature_max_level(creature.rarity, creature.star_level)
    xp_needed = constants.xp_for_creature_level(creature.level)
    is_maxed = creature.level >= max_level
    stars = get_emoji("star") * creature.star_level

    status_badge = "🟢 <b>(موجود فعال شما)</b>" if creature.is_active else "⚪️ (در کلکسیون)"

    lines = [
        f"{get_emoji('creature')} <b>{creature_name(creature)}</b> <code>#{creature.id}</code>\n{status_badge}",
    ]
    if creature_has_nickname(creature):
        lines.append(f"🧬 نژاد: <b>{creature.name}</b>")
    lines += [
        f"🏷 نایابی: <b>{constants.RARITY_LABELS[creature.rarity]}</b> {stars}",
        f"🔮 عنصر: <b>{constants.element_label(creature.element)}</b>",
        f"🎖 سطح موجود: <code>{creature.level}/{max_level}</code>" + (" <i>(بیشینه ✅)</i>" if is_maxed else ""),
    ]
    if not is_maxed:
        lines.append(f"📈 پیشرفت لول: {pct_bar(creature.xp, xp_needed)} (<code>{creature.xp:,}/{xp_needed:,}</code> تجربه)")

    parts_info = []
    if getattr(creature, "wings_lvl", 0) > 0:
        parts_info.append(f"🦋 بال‌ها: لول <code>{creature.wings_lvl}</code>")
    if getattr(creature, "fangs_lvl", 0) > 0:
        parts_info.append(f"🦷 نیش: لول <code>{creature.fangs_lvl}</code>")
    if getattr(creature, "armor_lvl", 0) > 0:
        parts_info.append(f"🛡 زره: لول <code>{creature.armor_lvl}</code>")
    if getattr(creature, "poison_lvl", 0) > 0:
        parts_info.append(f"☠️ غده سمی: لول <code>{creature.poison_lvl}</code>")
    if parts_info:
        lines += ["", _CARD_DIV, "🧬 <b>اعضای تقویت‌شده:</b>"] + parts_info

    lines += [
        "",
        _CARD_DIV,
        "⚔️ <b>آمار مبارزه</b>",
        _power_caption_line(creature, equipped_items),
        "",
        f"{get_emoji('hp')} سلامت: <code>{stats['hp']:,}</code>",
        f"{get_emoji('atk')} حمله: <code>{stats['atk']:,}</code>",
        f"{get_emoji('def')} دفاع: <code>{stats['def']:,}</code>",
        f"{get_emoji('spd')} سرعت: <code>{stats['spd']:,}</code>",
        "",
        _CARD_DIV,
        "🎒 <b>تجهیزات مجهز شده:</b>",
    ]
    by_slot = {i.slot: i for i in (equipped_items or [])}
    for slot in constants.EQUIPMENT_SLOTS:
        label = constants.EQUIPMENT_SLOT_LABELS[slot]
        it = by_slot.get(slot)
        if it is not None:
            lines.append(f"{label}: <b>{it.name}</b> <code>[+{it.level}]</code> (<code>+{equipment_power(it)}</code> 💪)")
        else:
            lines.append(f"{label}: <i>خالی</i>")
    return "\n".join(lines)


def _creature_detail_keyboard(creature_id: int, is_active: bool) -> InlineKeyboardMarkup:
    rows = []
    if not is_active:
        rows.append([btn("انتخاب به عنوان فعال", emoji_key="btn_confirm", style=CONFIRM, callback_data=f"coll_select:{creature_id}")])
    rows.append([btn("ورود به فیوژن", emoji_key="btn_fusion", style=PRIMARY, callback_data=f"fus_a:{creature_id}")])
    rows.append([btn("بلعیدن هیولا", emoji_key="btn_devour", style=BUILD, callback_data=f"devour_start:{creature_id}")])
    rows.append([btn("تغییر نام", emoji_key="btn_edit", style=NAV, callback_data=f"kaiju_rename:{creature_id}:c")])
    rows.append([back_btn("coll_back")])
    return InlineKeyboardMarkup(rows)


async def collection_pick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    creature_id = int(query.data.split(":")[1])
    if not query.data.startswith("cr_back:"):
        context.user_data["cr_origin"] = "c"
    try:
        user, creature, equipped_items = await run_db(_creature_detail_sync, update.effective_user, creature_id)
    except GameError as exc:
        await query.answer(alert_text(exc), show_alert=True)
        return
    await query.answer()
    from game.media import get_creature_image_path
    photo_path = get_creature_image_path(creature)
    await safe_edit_message_text(query,
        collection_creature_detail_text(creature, equipped_items),
        photo=photo_path,
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
        await query.answer(alert_text(exc), show_alert=True)
        return
    context.user_data.pop("pending_kaiju_rename", None)
    context.user_data[AWAITING_PLAYER_KEY] = {
        "action": "rename_kaiju", "creature_id": creature_id, "origin": origin,
    }
    from game.naming import NAME_MAX_LEN

    price_line = (
        "🎁 اولین نام‌گذاری این کایجو <b>رایگان</b> است."
        if cost == 0 else f"{get_emoji('diamond')} هزینه: <code>{cost}</code> الماس"
    )
    await query.answer()
    await safe_edit_message_text(
        query,
        f"✏️ <b>نام‌گذاری کایجو</b>\n"
        f"🧬 نژاد: <b>{creature.name}</b>\n"
        f"نام فعلی: <b>{creature_name(creature)}</b>\n\n"
        f"{price_line}\n"
        f"<i>یه اسم دلخواه بفرست (حداکثر <code>{NAME_MAX_LEN}</code> حرف). بعد از فرستادن، تأیید می‌گیرم.</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[btn("لغو", emoji_key="btn_cancel", style=DANGER, callback_data=f"kaiju_rename_cancel:{creature_id}:{origin}")]]),
    )


async def kaiju_rename_ok_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Confirmed rename — apply the pending name (charged in rename_creature)."""
    query = update.callback_query
    creature_id = int(query.data.split(":")[1])
    pending = context.user_data.get("pending_kaiju_rename")
    if not pending or pending.get("creature_id") != creature_id:
        await query.answer("⌛ منقضی شد — دوباره از «تغییر نام» شروع کن.", show_alert=True)
        return
    origin = pending.get("origin", "c")
    try:
        res = await run_db(_rename_kaiju_sync, update.effective_user, creature_id, pending["name"])
    except GameError as exc:
        await query.answer(alert_text(exc), show_alert=True)
        return
    context.user_data.pop("pending_kaiju_rename", None)
    back_cb = f"upg_pick:{creature_id}" if origin == "u" else f"coll_pick:{creature_id}"
    cost_note = "رایگان بود ✅" if res["cost"] == 0 else f"<code>{res['cost']}</code> {get_emoji('diamond')} کم شد"
    await query.answer("✅ ثبت شد!")
    await safe_edit_message_text(
        query,
        f"✅ اسم کایجو روی «<b>{res['name']}</b>» تنظیم شد.\n"
        f"🧬 نژاد: <b>{res['breed']}</b>\n"
        f"<i>({cost_note} — نام‌گذاری بعدی: <code>{res['next_cost']}</code> {get_emoji('diamond')})</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[back_btn(back_cb, "بازگشت به کایجو")]]),
    )


async def kaiju_rename_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cancel naming from any step — clears the awaited text/pending name and goes back."""
    query = update.callback_query
    parts = query.data.split(":")
    creature_id = int(parts[1])
    origin = parts[2] if len(parts) > 2 else "c"
    context.user_data.pop("pending_kaiju_rename", None)
    awaiting = context.user_data.get(AWAITING_PLAYER_KEY)
    if awaiting and awaiting.get("action") == "rename_kaiju":
        context.user_data.pop(AWAITING_PLAYER_KEY, None)
    back_cb = f"upg_pick:{creature_id}" if origin == "u" else f"coll_pick:{creature_id}"
    await query.answer("لغو شد.")
    await safe_edit_message_text(
        query, "❌ نام‌گذاری لغو شد.",
        reply_markup=InlineKeyboardMarkup([[back_btn(back_cb, "بازگشت به کایجو")]]),
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
        await query.answer(alert_text(exc), show_alert=True)
        return
    is_owner = _is_admin_user(update.effective_user.id if update.effective_user else None)
    await query.answer("🟢 انتخاب شد!")
    from game.media import get_creature_image_path
    photo_path = get_creature_image_path(creature)
    await safe_edit_message_text(query,
        f"🟢 <b>{creature_name(creature)}</b> حالا موجود فعالته!\n\n" + creature_card_text(user, creature, equipped_items, compact=bool(photo_path)),
        photo=photo_path,
        parse_mode="HTML",
        reply_markup=creature_keyboard(None, is_owner),
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
            f"🍖 <b>تقویت {target.name}</b> (Lv<code>{target.level}</code>)\n\n"
            "<blockquote>🔒 این موجود به <b>سقف سطحش</b> رسیده و دیگه نمی‌تونه هیولا بخوره.\n"
            "<i>برای ادامه‌ی رشد باید با فیوژن ستاره‌ش رو بالا ببری (سقف سطح بالاتر می‌ره).</i></blockquote>",
            InlineKeyboardMarkup([[back_btn(f"coll_pick:{target.id}", "بازگشت")]]),
        )
    if not scored:
        return (
            f"🍖 <b>تقویت {target.name}</b>\n\n"
            "<blockquote>هیچ موجود آزادی برای خوروندن نداری (موجود فعال و موجودهای مشغول قابل قربانی نیستن).</blockquote>",
            InlineKeyboardMarkup([[back_btn(f"coll_pick:{target.id}", "بازگشت")]]),
        )
    all_ids = {c.id for c, _ in scored}
    selected = selected & all_ids  # drop picks that are no longer valid candidates
    total_pages = max(1, (len(scored) + _DEVOUR_PAGE - 1) // _DEVOUR_PAGE)
    page = max(0, min(page, total_pages - 1))
    chunk = scored[page * _DEVOUR_PAGE:(page + 1) * _DEVOUR_PAGE]
    rows = []
    for c, xp in chunk:
        mark = "✅" if c.id in selected else "▫️"
        rarity_short = constants.RARITY_LABELS[c.rarity].split()[0]
        rows.append([
            btn(f"{mark} {c.name} ({rarity_short})", style=LIST, callback_data=f"devour_tog:{target.id}:{c.id}"),
            btn(f"⭐ {c.star_level} | سطح {c.level} (+{xp:,} XP)", style=NAV, callback_data=f"devour_tog:{target.id}:{c.id}"),
        ])
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
        rows.append([btn("لغو انتخاب‌ها", emoji_key="btn_cancel", style=NAV, callback_data=f"devour_none:{target.id}")])
    else:
        rows.append([btn("انتخاب همه", emoji_key="btn_confirm", style=NAV, callback_data=f"devour_all:{target.id}")])
    if selected:
        rows.append([btn(
            f"بلعیدن ({len(selected)} موجود)", emoji_key="btn_devour",
            style=CONFIRM, callback_data=f"devour_multi:{target.id}",
        )])
    rows.append([back_btn(f"cr_back:{target.id}", "بازگشت")])
    page_note = f" <i>(صفحه <code>{page + 1}/{total_pages}</code>)</i>" if total_pages > 1 else ""
    enough = total_xp >= xp_to_max
    lines = [
        f"🍖 <b>تقویت {target.name}</b> (Lv<code>{target.level}</code>){page_note}",
        f"🎯 XP تا سقف سطح: <code>{xp_to_max:,}</code>",
        f"➕ XP انتخاب‌شده: <code>{total_xp:,}</code>" + (" <i>(کافیه! ✅)</i>" if enough else ""),
        "",
        "<blockquote>هرچند تا موجود که می‌خوای رو <b>تیک بزن</b> تا با هم خورده بشن و XP‌شون به این منتقل شه.\n"
        "فقط تا جایی می‌تونی انتخاب کنی که به سقف سطح برسه — بیشترش هدر می‌ره.\n"
        "قربانی‌ها برای همیشه حذف می‌شن.</blockquote>",
    ]
    return "\n".join(lines), InlineKeyboardMarkup(rows)


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
        await query.answer(alert_text(exc), show_alert=True)
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
            await query.answer(alert_text(exc), show_alert=True)
            return
        # «انتخاب همه» caps at the XP needed to max, and feeds the LEAST valuable first
        # (lowest rarity, then star, then XP) — it used to take the highest-XP ones, i.e.
        # the rarest/most-starred creatures, so two taps could burn the best of a roster.
        picked, running = set(), 0
        _rk = constants.RARITY_ORDER.index
        for c, xp in sorted(scored, key=lambda t: (_rk(t[0].rarity), t[0].star_level, t[1])):
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
    """«بلعیدن» → a confirmation screen first: devouring deletes creatures for good, so
    the player sees exactly who is about to be eaten before it happens."""
    query = update.callback_query
    target_id = int(query.data.split(":")[1])
    selection = _devour_selection(context, target_id)
    if not selection:
        await query.answer("اول حداقل یه موجود رو تیک بزن.", show_alert=True)
        return
    try:
        target, scored, _xp_to_max = await run_db(_devour_list_sync, update.effective_user, target_id)
    except GameError as exc:
        await query.answer(alert_text(exc), show_alert=True)
        return
    chosen = [(c, xp) for c, xp in scored if c.id in selection]
    if not chosen:
        await query.answer("انتخاب‌هات دیگه معتبر نیستن؛ دوباره تیک بزن.", show_alert=True)
        return
    await query.answer()
    chosen.sort(key=lambda t: (-constants.RARITY_ORDER.index(t[0].rarity), -t[0].star_level, -t[1]))
    lines = [
        f"⚠️ <b>بلعیدن {len(chosen)} هیولا — مطمئنی؟</b>",
        "",
        f"این هیولاها <b>برای همیشه حذف می‌شن</b> و تجربه‌شون به <b>{creature_name(target)}</b> می‌رسه:",
        "<blockquote>" + "\n".join(
            f"• {creature_name(c)} — {constants.RARITY_LABELS[c.rarity]} · {c.star_level}⭐ · سطح {c.level}"
            for c, _ in chosen[:15]
        ) + (f"\n… و {len(chosen) - 15} هیولای دیگه" if len(chosen) > 15 else "") + "</blockquote>",
        f"مجموع تجربه: <code>+{sum(xp for _, xp in chosen):,}</code>",
    ]
    await safe_edit_message_text(
        query, "\n".join(lines), parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [btn(f"بله، {len(chosen)} هیولا بلعیده بشه", emoji_key="btn_devour", style=DANGER,
                 callback_data=f"devour_ok:{target_id}")],
            [btn("نه، برگرد", emoji_key="btn_cancel", style=NAV, callback_data=f"devour_page:{target_id}:0")],
        ]),
    )


async def devour_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    target_id = int(query.data.split(":")[1])
    selection = list(_devour_selection(context, target_id))
    if not selection:
        await query.answer("انتخابی ثبت نشده؛ دوباره از «تقویت» شروع کن.", show_alert=True)
        return
    try:
        result = await run_db(_devour_multi_sync, update.effective_user, target_id, selection)
    except GameError as exc:
        await query.answer(alert_text(exc), show_alert=True)
        return
    context.user_data.get(_DEVOUR_SEL, {}).pop(target_id, None)  # consumed
    target = result["target"]
    level_note = (
        f" و <code>{result['levels']}</code> سطح بالا رفت (الان Lv<code>{result['new_level']}</code>)!" if result["levels"] else "!"
    )
    await query.answer(f"🍖 +{result['xp']} XP")
    eaten = "، ".join(result["eaten"][:6]) + (" …" if result["count"] > 6 else "")
    await safe_edit_message_text(
        query,
        f"🍖 <b><code>{result['count']}</code> موجود</b> خورده شد ({eaten})\n"
        f"<b>{target.name}</b> <code>{result['xp']}</code> XP گرفت{level_note}\n\n"
        + creature_card_text(result["user"], target, result["equipped"]),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [btn("تقویت بیشتر", emoji_key="btn_feed", style=BUILD, callback_data=f"devour_start:{target.id}")],
            [back_btn(f"cr_back:{target.id}", "بازگشت")],
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
        await update.message.reply_text(alert_text(exc, 3500))
        return
    is_owner = _is_admin_user(update.effective_user.id if update.effective_user else None)
    await update.message.reply_text(
        f"🟢 <b>{creature_name(creature)}</b> حالا موجود فعالته!\n\n" + creature_card_text(user, creature, equipped_items),
        parse_mode="HTML",
        reply_markup=creature_keyboard(None, is_owner),
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
        await update.message.reply_text(alert_text(exc, 3500))
        return
    is_owner = _is_admin_user(update.effective_user.id if update.effective_user else None)
    inherit_note = "\n🧬 یه تجهیزات از والدین به ارث رسید!" if inherited else ""
    await update.message.reply_text(
        f"{get_emoji('lab')} <b>فیوژن موفق بود!</b> والدین سوزانده شدن و یه موجود جدید متولد شد:{inherit_note}\n\n"
        + creature_card_text(user, child, equipped_items)
        + _mission_lines(completed_missions),
        parse_mode="HTML",
        reply_markup=creature_keyboard(None, is_owner),
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
    from game.fusion import fusion_partners_annotated

    return creature, fusion_partners_annotated(user, creature), is_built(user, FUSION_BUILDING), star_cap(user)


async def fusion_pick_a_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    parent_a_id = int(query.data.split(":")[1])
    try:
        creature, candidates, lab_built, cap = await run_db(
            _fusion_candidates_sync, update.effective_user, parent_a_id
        )
    except GameError as exc:
        await query.answer(alert_text(exc), show_alert=True)
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
    rows = []
    for c, busy in candidates:
        if busy:
            rows.append([btn(
                f"⛔ {creature_name(c)} ({c.star_level}★ - Lv.{c.level}) - مشغول",
                style=NAV, callback_data=f"fus_busy:{c.id}",
            )])
        else:
            rows.append([btn(
                f"🧬 {creature_name(c)} ({c.star_level}★ - Lv.{c.level})",
                style=PRIMARY, callback_data=f"fus_b:{parent_a_id}:{c.id}",
            )])
    rows.append([back_btn(f"cr_back:{parent_a_id}")])
    from game.media import get_creature_image_path
    photo = get_creature_image_path(creature)
    await safe_edit_message_text(query,
        f"{get_emoji('lab')} <b>ترکیب {creature.name}</b> {'⭐' * creature.star_level}\n\n"
        f"این‌ها هم‌نوع و هم‌ستاره‌ان، پس ترکیبشون <b>حتماً</b> جواب می‌ده:",
        photo=photo,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def fusion_busy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tapped a busy fusion partner — explain it can't be selected while busy."""
    query = update.callback_query
    await query.answer(
        "⛔ این کایجو الان مشغوله (توی معدن یا غار) — اول آزادش کن تا بتونی باهاش فیوژن کنی.",
        show_alert=True,
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
        await query.answer(alert_text(exc), show_alert=True)
        return
    await query.answer()

    stars = get_emoji("star") * g["star"]
    next_star = g["star"] + 1
    div = "━━━━━━━━━━━━━━━━━━━━"
    check = lambda ok: "✅" if ok else "❌"  # noqa: E731

    if g["at_max"]:
        lines = [
            f"🧬 <b>فیوژن — {g['name']}</b> {stars}",
            "",
            "🏆 این هیولا به سقف <b>۵ ستاره</b> رسیده و دیگه نیازی به فیوژن نداره!",
            "<i>قوی‌ترین فرم ممکنه — می‌تونی روی ارتقای اعضا و تجهیزاتش تمرکز کنی.</i>",
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
        next_step = (f"👉 <b>قدم بعدی:</b> 🔮 تالار ادغام رو به سطح <b><code>{next_star}</code></b> ارتقا بده "
                     f"(سطح فعلیش: <code>{g['lab_level']}</code>).")
    elif not partner_ok:
        next_step = (f"👉 <b>قدم بعدی:</b> یک «{g['name']}» دیگه با همین نایابی و همین ستاره پیدا کن "
                     f"(از باکس یا غار هیولا).")
    else:  # not gold_ok
        next_step = f"👉 <b>قدم بعدی:</b> <code>{g['cost'] - g['coins']:,}</code> طلای دیگه جمع کن."

    lines = [
        "🧬 <b>ورود به فیوژن — ارتقای ستاره</b>",
        f"🎯 هدف: <b>{g['name']}</b> {stars} ➔ {get_emoji('star') * next_star}",
        "",
        next_step,
        "", div, "",
        "<b>شرایط لازم برای این ارتقا:</b>",
        f"{check(g['lab_built'])} 🔮 تالار ادغام ساخته شده",
        f"{check(cap_ok)} ⭐ تالار ادغام سطح ≥ <code>{next_star}</code> (الان: <code>{g['lab_level']}</code>)",
        f"{check(partner_ok)} 👥 یک هیولای هم‌نوع، هم‌رده و هم‌ستاره داری "
        f"({'داری ✔' if partner_ok else 'نداری'})",
        f"{check(gold_ok)} {get_emoji('coin')} طلای کافی: <code>{g['cost']:,}</code> (داری: <code>{g['coins']:,}</code>)",
        "", div, "",
        "<blockquote>فیوژن = دو هیولای <b>هم‌نام + هم‌نایابی + هم‌ستاره</b> ➔ یکی یک ستاره بالاتر.\n"
        "فرزند سطحِ والدِ قوی‌تر و بهترین اعضای هر دو رو می‌گیره و XP‌شون جمع می‌شه.\n"
        "سطح تالار ادغام سقف ستاره‌ست: سطح ۲ برای ۲⭐، سطح ۳ برای ۳⭐ …</blockquote>",
    ]

    rows = []
    if ready:
        rows.append([btn("انتخاب جفت و ترکیب", emoji_key="btn_fusion", style=CONFIRM, callback_data=f"fus_a:{cid}")])
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
            [back_btn("menu:hub_creature", "بازگشت به هیولا")],
        ]
        await send_screen(update, 
            "\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows)
        )
        return

    text, keyboard = _fusion_body(user, pairs, cap, "all")
    from game.media import get_feature_image_path
    photo = get_feature_image_path("fusion")
    await send_screen(update, text, photo=photo, parse_mode="HTML", reply_markup=keyboard)


def _fusion_body(user, pairs, cap, filt: str) -> tuple[str, InlineKeyboardMarkup]:
    """Fusion list, filterable by rarity via tabs so a big roster isn't one long
    scattered list. `filt` is a rarity key or "all"."""
    lines = [
        f"{get_emoji('lab')} <b>تالار ادغام</b>",
        f"⭐ سقف ستاره‌ی فعلی تو: <code>{cap}</code>",
        "<blockquote>🔗 دو هیولای <b>هم‌نام + هم‌نایابی + هم‌ستاره</b> ➔ یکی یک ستاره بالاتر.\n"
        "اول ۲ تا ۱★ کن ۲★، بعد ۲ تا ۲★ کن ۳★ … (۵★ = ۱۶ تا ۱★).</blockquote>",
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
                lines.append(f"\n{'⭐' * p['star']} <b>{p['star']} ستاره ➔ {p['star'] + 1} ستاره</b>")
                last_star = p["star"]
            cost = constants.fusion_cost(p["star"], p["rarity"])
            rarity_dot = constants.RARITY_LABELS[p["rarity"]].split()[0]
            extra = f" (+{p['count'] - 1})" if p["count"] > 2 else ""
            # main-first: pick the primary creature → the partner picker (fus_a) opens next
            rows.append([btn(
                f"{rarity_dot} {p['name']} ({p['star']}★ ➔ {p['star'] + 1}★){extra}",
                style=PRIMARY, callback_data=f"fus_a:{p['parent_a'].id}",
            )])
    rows.append([back_btn("menu:hub_creature", "بازگشت به هیولا")])
    lines.append(f"\n{wallet_line(user)}")
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def fusion_rarity_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    filt = update.callback_query.data.split(":")[1]
    user, pairs, lab_built, cap = await run_db(_fusion_panel_sync, update.effective_user)
    if not lab_built:
        await update.callback_query.answer()
        return
    await update.callback_query.answer()
    text, keyboard = _fusion_body(user, pairs, cap, filt)
    await send_screen(update, text, parse_mode="HTML", reply_markup=keyboard)


def _fusion_cost_sync(tg_user, a_id, b_id):
    user, _ = get_or_create_user(tg_user)
    a = Creature.objects.filter(id=a_id, owner=user).first()
    b = Creature.objects.filter(id=b_id, owner=user).first()
    if a is None or b is None:
        raise GameError("این جفت دیگه پیدا نشد.")
    base_rarity = constants.higher_rarity(a.rarity, b.rarity)
    return constants.fusion_cost(a.star_level, base_rarity), a, b


async def fusion_pick_b_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, a_id, b_id = query.data.split(":")
    try:
        cost, par_a, par_b = await run_db(_fusion_cost_sync, update.effective_user, int(a_id), int(b_id))
    except GameError as exc:
        await query.answer(alert_text(exc), show_alert=True)
        return
    await query.answer()
    keyboard = InlineKeyboardMarkup(
        [
            [
                btn("تأیید فیوژن", emoji_key="btn_confirm", style=CONFIRM, callback_data=f"fus_confirm:{a_id}:{b_id}"),
                btn("لغو", emoji_key="btn_cancel", style=NAV, callback_data=f"cr_back:{a_id}"),
            ]
        ]
    )
    await safe_edit_message_text(query,
        f"{get_emoji('warning')} <b>تأیید ادغام</b>\n\n"
        "<blockquote>"
        f"• {creature_name(par_a)} — {constants.RARITY_LABELS[par_a.rarity]} · {par_a.star_level}⭐ · سطح {par_a.level}\n"
        f"• {creature_name(par_b)} — {constants.RARITY_LABELS[par_b.rarity]} · {par_b.star_level}⭐ · سطح {par_b.level}"
        "</blockquote>\n"
        f"این دو هیولا <b>برای همیشه حذف می‌شن</b> و یک «{par_a.name}» <b>{par_a.star_level + 1}⭐</b> ساخته می‌شه "
        "که هیولای فعالت می‌شه. تجهیزاتشون به کوله برمی‌گرده.\n"
        f"{get_emoji('coin')} هزینه: <code>{cost:,}</code> طلا",
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
        await query.answer(alert_text(exc), show_alert=True)
        return
    is_owner = _is_admin_user(update.effective_user.id if update.effective_user else None)
    inherit_note = "\n🧬 یه تجهیزات از والدین به ارث رسید!" if inherited else ""
    await query.answer("🟢 فیوژن موفق بود!")
    await safe_edit_message_text(query,
        f"{get_emoji('lab')} <b>فیوژن موفق بود!</b> والدین سوزانده شدن و یه موجود جدید متولد شد:\n\n"
        f"<tg-spoiler>{constants.RARITY_LABELS[child.rarity]}{inherit_note}</tg-spoiler>\n\n"
        + creature_card_text(user, child, equipped_items)
        + _mission_lines(completed_missions),
        parse_mode="HTML",
        reply_markup=creature_keyboard(None, is_owner),
    )


def _missions_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    return mission_status(user)


_BOX_LABEL = {"silver": "🥈 نقره‌ای", "golden": "🥇 طلایی", "magical": "🔮 جادویی", "mega": "👑 امگا"}


def _mission_panel_reward(m: dict) -> str:
    """One mission's payout in the panel style: «+N طلا 🪙 ، +N دی‌ان‌ای 🧬 ، ۱× کارت سرعت …»."""
    parts = []
    if m.get("coins"):
        parts.append(f"<code>+{m['coins']:,}</code> طلا {get_emoji('coin')}")
    if m.get("dna"):
        parts.append(f"<code>+{m['dna']:,}</code> دی‌ان‌ای {get_emoji('dna')}")
    if m.get("diamonds"):
        parts.append(f"<code>+{m['diamonds']}</code> الماس {get_emoji('diamond')}")
    if m.get("capsule"):
        tier, count = m["capsule"]
        cap = constants.XP_CAPSULES[tier]
        parts.append(f"<code>{count}×</code> {cap['label']} {cap['emoji']}")
    if m.get("speedup"):
        parts.append(f"<code>۱×</code> کارت سرعت {constants.speedup_plain_label(m['speedup'])} ⏱")
    return " ، ".join(parts) if parts else "—"


def _fmt_reset(seconds: int) -> str:
    d, rem = divmod(max(0, int(seconds)), 86400)
    h = rem // 3600
    return f"{d} روز و {h} ساعت" if d else f"{max(1, h)} ساعت"


def _mission_block(m: dict) -> list[str]:
    reward = f"🎁 {_mission_panel_reward(m)} · ⭐ <code>{m['points']}</code> امتیاز"
    if m["done"]:
        return [f"✅ <b>{m['label']}</b> — <s>انجام شد</s>", reward, ""]
    bar = constants.render_bar(m["progress"], m["target"], width=10)
    return [f"▫️ <b>{m['label']}</b>", f"⏳ [{bar}] <code>{m['progress']}/{m['target']}</code>", reward, ""]


def _open_missions(missions: list[dict]) -> list[str]:
    """The missions still to do, in full; the finished ones collapse into ONE line (they
    used to stay in the list with a tick, which made it twice as long by the evening)."""
    todo = [m for m in missions if not m["done"]]
    done = len(missions) - len(todo)
    lines: list[str] = []
    for m in todo:
        lines += _mission_block(m)
    if not todo:
        lines += ["🎉 <b>همه رو انجام دادی!</b>", ""]
    elif done:
        lines += [f"✅ <i>{done} مأموریت انجام‌شده از فهرست برداشته شد.</i>", ""]
    return lines


def _missions_render(status: dict, tab: str = "d", note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    """The missions screen, three tabs: today's missions, this week's missions, and the
    weekly box track (points → boxes, the last one is the Omega box)."""
    div = "━━━━━━━━━━━━━━━━━━━━"
    points, max_points = status["points"], status["max_points"]
    boxes = status["boxes"]
    next_box = next((b for b in boxes if not b["reached"]), None)
    ready = [b for b in boxes if b["reached"] and not b["opened"]]
    lines = [f"{get_emoji('mission')} <b>مأموریت‌ها</b>", ""]
    if note:
        lines = [note, ""] + lines
    target = next_box["need"] if next_box else boxes[-1]["need"]
    lines.append(
        f"⭐ امتیاز این هفته: <code>{points}</code> — [{constants.render_bar(min(points, target), target, width=10)}] "
        + (f"تا باکس {_BOX_LABEL[next_box['tier']]}: <code>{next_box['need'] - points}</code> امتیاز"
           if next_box else "همه‌ی باکس‌ها باز شدن 🎉")
    )
    if ready:
        lines.append(f"🎁 <b>{len(ready)} باکس آماده‌ی باز شدنه!</b>")
    lines += [div, ""]

    rows = []
    if tab == "w":
        lines.append("📅 <b>مأموریت‌های هفتگی</b> <i>(سخت‌تر، امتیاز و جایزه‌ی بیشتر)</i>")
        lines.append("")
        lines += _open_missions(status["weekly"])
        lines.append(f"<i>ریست هفتگی: {_fmt_reset(status['reset_in'])} دیگه (دوشنبه، نیمه‌شب تهران).</i>")
    elif tab == "b":
        lines.append("🎁 <b>مسیر باکس‌های هفته</b>")
        lines.append("<blockquote>با هر مأموریت امتیاز می‌گیری؛ به هر پله که برسی یه باکس باز می‌کنی. "
                     "آخرین پله باکس 👑 امگاست: برای کسی که تقریباً همه‌ی مأموریت‌های هفته رو انجام بده.</blockquote>")
        lines.append("")
        for b in boxes:
            if b["opened"]:
                state = "✅ باز شد"
            elif b["reached"]:
                state = "🎁 <b>آماده!</b>"
            else:
                state = f"🔒 <code>{b['need'] - points}</code> امتیاز مونده"
            lines.append(f"{b['index']}. <code>{b['need']}</code> امتیاز ← باکس {_BOX_LABEL[b['tier']]} — {state}")
            if b["reached"] and not b["opened"]:
                rows.append([btn(f"باز کردن باکس {b['index']}", emoji_key="btn_chests", style=CONFIRM,
                                 callback_data=f"mission_box:{b['index']}")])
        lines += ["", f"<i>سقف امتیاز هفته: {max_points} · ریست: {_fmt_reset(status['reset_in'])} دیگه.</i>"]
    else:
        tab = "d"
        lines.append(f"☀️ <b>مأموریت‌های امروز</b> — <code>{status['today_points']}/{status['today_max']}</code> امتیاز")
        lines.append("")
        lines += _open_missions(status["daily"])
        lines.append("<i>مأموریت‌های روزانه نیمه‌شب تهران ریست می‌شن.</i>")

    def _tab(label, key, emoji_key):
        return btn(("• " if tab == key else "") + label, emoji_key=emoji_key,
                   style=(CONFIRM if tab == key else NAV), callback_data=f"mission_tab:{key}")

    box_label = f"باکس‌ها ({len(ready)})" if ready else "باکس‌ها"
    rows.append([_tab("روزانه", "d", "btn_missions"), _tab("هفتگی", "w", "btn_missions"), _tab(box_label, "b", "btn_chests")])
    rows.append([back_btn("menu:hub_city", "بازگشت به شهر")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def missions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    status = await run_db(_missions_sync, update.effective_user)
    text, keyboard = _missions_render(status, "d")
    from game.media import get_feature_image_path
    photo = get_feature_image_path("missions")
    await send_screen(update, text, photo=photo, parse_mode="HTML", reply_markup=keyboard)


async def missions_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tab switch (mission_tab:d|w|b); also catches the old «mission_page:N» buttons."""
    query = update.callback_query
    tab = query.data.split(":")[1] if query.data.startswith("mission_tab:") else "d"
    status = await run_db(_missions_sync, update.effective_user)
    await query.answer()
    text, keyboard = _missions_render(status, tab)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


def _mission_box_sync(tg_user, index):
    from game.daily import claim_box

    user, _ = get_or_create_user(tg_user)
    c = claim_box(user, index)
    lines = [
        f"{c['emoji']} <b>{c['name']} باز شد!</b>",
        f"{get_emoji('coin')} طلا: <code>+{c['coins']:,}</code>",
        f"{get_emoji('dna')} DNA: <code>+{c['dna']:,}</code>",
    ]
    if c["diamonds"]:
        lines.append(f"{get_emoji('diamond')} الماس: <code>+{c['diamonds']}</code>")
    rarity = constants.RARITY_LABELS.get(c["rarity"], c["rarity"])
    if c["creature"] is not None:
        lines.append(f"{get_emoji('creature')} هیولای جدید: <b>{c['creature'].name}</b> [{rarity}] "
                     f"({constants.element_label(c['creature'].element)})")
    elif c["item"] is not None:
        lines.append(f"{get_emoji('gear')} تجهیزات جدید: <b>{c['item'].name}</b> [{rarity}]")
    return "\n".join(lines), mission_status(user)


async def mission_box_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    index = int(query.data.split(":")[1])
    try:
        note, status = await run_db(_mission_box_sync, update.effective_user, index)
    except GameError as exc:
        await query.answer(alert_text(exc), show_alert=True)
        return
    await query.answer("🎁 باز شد!")
    text, keyboard = _missions_render(status, "b", note=note)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


def _hunt_scout_sync(tg_user, charge=False):
    from game.hunt import scout_cost
    user, _ = get_or_create_user(tg_user)
    creature = get_active_creature(user)
    if creature is None:
        raise GameError("اول /start رو بزن تا موجودت رو بگیری.")
    equipped = get_equipped_items(creature)
    my_power = _creature_power(creature, equipped)
    cost = scout_cost(creature, power=my_power)
    if charge:  # «بعدی» costs a little gold, scaled by power
        paid = type(user).objects.filter(id=user.id, coins__gte=cost).update(coins=F("coins") - cost)
        if not paid:
            raise GameError(f"برای جستجوی دوباره {cost:,} طلا لازمه (الان {user.coins:,} داری).")
        user.coins -= cost
    return creature, my_power, user.cup, scout_one(user, creature), sync_energy(user), cost


def _hunt_scout_text(creature, my_power, cup, target, energy, scout_price) -> str:
    if target.get("is_encounter"):
        lines = [
            "✨ <b>رویداد غیرمنتظره در شکار!</b>",
            _CARD_DIV,
            f"📌 <b>{target['title']}</b>",
            f"<blockquote>{target['desc']}</blockquote>",
            f"{get_emoji('energy')} انرژی شما: <code>{energy}</code>",
            f"🔍 حریف بعدی: <code>{scout_price:,}</code> طلا",
        ]
        return "\n".join(lines)

    from game.hunt import hunt_reward_roll

    tier_label = HUNT_TIERS[target["tier"]]["label"]
    # a single random (seed-based) prize instead of a range — matches the actual payout
    from game import events as _events

    coin_reward, dna_reward = hunt_reward_roll(
        my_power, target["tier"], target.get("seed"), _events.hunt_loot_mult(creature.element)
    )
    adv = element_advantage_line(creature.element, target["element"])
    # same order on every battle card: you → opponent → element → reward → cost
    lines = [
        f"{get_emoji('hunt')} <b>شکار · حریف پیدا شد</b>",
        _CARD_DIV,
        f"💪 قدرت شما: <code>{my_power:,}</code>",
        f"🏰 حریف: <b>{target['name']}</b> <i>({tier_label})</i>",
        f"🎯 عنصر حریف: {constants.element_label(target['element'])}",
        f"⚔️ قدرت حریف: <code>{target['power']:,}</code>",
        adv or "➖ بدون مزیت عنصری",
        _CARD_DIV,
        f"🎁 جایزه برد: {get_emoji('coin')} <code>+{coin_reward:,}</code> · {get_emoji('dna')} <code>+{dna_reward:,}</code>",
        f"{get_emoji('energy')} هزینه حمله: <code>{constants.HUNT_ENERGY_COST}</code> انرژی (داری <code>{energy}</code>)",
        f"🔍 حریف بعدی: <code>{scout_price:,}</code> طلا",
    ]
    return "\n".join(lines)


def _hunt_scout_keyboard(target, scout_price=0) -> InlineKeyboardMarkup:
    if target.get("is_encounter"):
        enc_type = target["enc_type"]
        nxt = btn("حریف بعدی", emoji_key="btn_scout_next", style=NAV, callback_data="hunt_next")
        # the event's own action alone on top; skipping it sits beside «حریف بعدی»
        rows = []
        if enc_type == "chest":
            rows.append([btn("باز کردن صندوق", emoji_key="btn_confirm", style=CONFIRM, callback_data="henc:chest:open")])
            rows.append([btn("رها کردن", emoji_key="btn_cancel", style=NAV, callback_data="henc:chest:leave"), nxt])
        elif enc_type == "thief":
            rows.append([btn("حمله به دزد", emoji_key="btn_attack", style=BATTLE, callback_data="henc:thief:fight")])
            rows.append([btn("صرف‌نظر", emoji_key="btn_cancel", style=NAV, callback_data="henc:thief:leave"), nxt])
        elif enc_type == "fork":
            # two equal choices and no «leave» — side by side
            rows.append([
                btn("غار کریستالی", emoji_key="btn_diamond", style=PRIMARY, callback_data="henc:fork:crystal"),
                btn("دشت آتشفشانی", emoji_key="btn_charge", style=SHOP, callback_data="henc:fork:volcano"),
            ])
            rows.append([nxt])
        elif enc_type == "spring":
            rows.append([btn("نوشیدن از چشمه", emoji_key="btn_energy", style=CONFIRM, callback_data="henc:spring:drink")])
            rows.append([btn("عبور", emoji_key="btn_cancel", style=NAV, callback_data="henc:spring:leave"), nxt])
        else:
            rows.append([nxt])
        rows.append([back_btn("menu:hub_battle", "بازگشت به نبرد")])
        return InlineKeyboardMarkup(rows)

    # same shape as the arena opponent card: attack → next / swap → extra → back
    return InlineKeyboardMarkup(
        [
            [btn("حمله", emoji_key="btn_attack", style=BATTLE, callback_data=f"hunt_go:{target['tier']}:{target['seed']}")],
            [
                btn("حریف بعدی", emoji_key="btn_scout_next", style=NAV, callback_data="hunt_next"),
                btn("تعویض موجود", emoji_key="btn_swap", style=NAV, callback_data=f"hunt_swap:{target['tier']}:{target['seed']}"),
            ],
            [btn("شکار خودکار", emoji_key="btn_autohunt", style=BATTLE, callback_data="autohunt_start")],
            [back_btn("menu:hub_battle", "بازگشت به نبرد")],
        ]
    )


async def hunt_encounter_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    parts = query.data.split(":")
    if len(parts) != 3:
        await query.answer()
        return
    _, enc_type, action = parts

    def _do_enc(tg_user):
        user, _ = get_or_create_user(tg_user)
        creature = get_active_creature(user)
        from game.hunt import resolve_encounter_action
        return resolve_encounter_action(user, creature, enc_type, action)

    try:
        res = await run_db(_do_enc, update.effective_user)
    except GameError as exc:
        await query.answer(alert_text(exc), show_alert=True)
        return
    await query.answer()
    text = res["msg"]
    keyboard = InlineKeyboardMarkup([
        [btn("شکار دوباره", emoji_key="btn_hunt", style=BATTLE, callback_data="hunt_next")],
        [back_btn("menu:hub_battle", "بازگشت به نبرد")],
    ])
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


async def hunt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scouting step: shows ONE opponent at a time with its power and payout so the
    player can judge the risk *before* any energy is spent. Finding an opponent costs
    a little gold (scaled by power) — the FIRST find too, not just «بعدی»."""
    try:
        creature, my_power, cup, target, energy, cost = await run_db(_hunt_scout_sync, update.effective_user, True)
    except GameError as exc:
        await send_screen(update, str(exc), parse_mode=None, reply_markup=back_only_keyboard())
        return
    from game.media import get_feature_image_path
    if target.get("is_encounter"):
        enc = target.get("enc_type")
        photo = get_feature_image_path(f"encounter_{enc}" if enc != "fork" else "encounter_cave")
    else:
        photo = get_feature_image_path("hunt")
    await send_screen(update,
        _hunt_scout_text(creature, my_power, cup, target, energy, cost),
        photo=photo,
        parse_mode="HTML",
        reply_markup=_hunt_scout_keyboard(target, cost),
    )


async def hunt_next_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    try:
        creature, my_power, cup, target, energy, cost = await run_db(_hunt_scout_sync, update.effective_user, True)
    except GameError as exc:
        await query.answer(alert_text(exc), show_alert=True)
        return
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
        tag = "🟢" if is_active else ("⛔" if busy else "🧬")
        note = " (مشغول)" if busy else ""
        elem_lbl = constants.ELEMENT_LABELS.get(element, element)
        rows.append([btn(f"{tag} {name} ({elem_lbl}) · {power:,}{note}",
                         style=BATTLE, callback_data=f"hunt_swap_pick:{tier}:{seed}:{cid}")])
    rows.append([back_btn(f"hunt_swap_pick:{tier}:{seed}:0", "بازگشت به حریف")])
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
        await query.answer(alert_text(exc), show_alert=True)
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
    parts = query.data.split(":")
    if len(parts) != 3 or parts[1] not in HUNT_TIERS or not parts[2].isdigit():
        await query.answer("این کارت شکار دیگه معتبر نیست؛ دوباره «شکار» رو بزن.", show_alert=True)
        return
    await query.answer()
    _, tier, seed = parts
    try:
        creature, result, completed_missions = await run_db(
            _hunt_go_sync, update.effective_user, tier, int(seed)
        )
    except GameError as exc:
        from bot.handlers.energy import show_energy_error

        if not await show_energy_error(query, exc):
            await query.answer(alert_text(exc), show_alert=True)
        return

    div = "━━━━━━━━━━━━━━━━━━━━"
    if result["won"]:
        loot = f"{get_emoji('coin')} <code>+{result['coins']:,}</code>"
        if result["dna"]:
            loot += f" · {get_emoji('dna')} <code>+{result['dna']:,}</code>"
        reward_line = (
            f"{get_emoji('celebrate')} <b>بردی!</b>\n{div}\n"
            f"🎁 غنیمت: {loot}\n"
            f"✨ تجربه: <code>+{result['xp']:,}</code> XP"
        )
        if result["levels"]:
            reward_line += f"\n🎉 رسید به سطح <code>{creature.level}</code>!"
    else:
        reward_line = f"😔 <b>باختی...</b>\n{div}\n✨ تجربه تسلی‌بخش: <code>+{result['xp']:,}</code> XP"
    reward_line += _mission_lines(completed_missions)

    keyboard = InlineKeyboardMarkup(
        [
            [btn("شکار دوباره", emoji_key="btn_hunt", style=BATTLE, callback_data="hunt_next")],
            [back_btn("menu:hub_battle", "بازگشت به نبرد")],
        ]
    )
    body = f"{reward_line}\n{div}\n{result['log_text']}"
    await safe_edit_message_text(
        query,
        body,
        parse_mode="HTML",
        reply_markup=keyboard,
    )


def _autohunt_info_sync(tg_user):
    """Current fieldable creature + live energy, for the auto-hunt prompt."""
    from game.energy import get_max_energy, sync_energy

    user, _ = get_or_create_user(tg_user)
    creature = get_active_creature(user)
    if creature is None:
        raise GameError("اول /start رو بزن تا موجودت رو بگیری.")
    energy = sync_energy(user)
    user.save(update_fields=["energy", "energy_updated_at"])
    return energy, get_max_energy(user)


def _autohunt_sync(tg_user, energy_amount):
    """Run `energy_amount` auto-hunts (1 energy each) against fresh targets, each paying
    HALF the gold/DNA of a manual hunt. Resolved INSTANTLY via statistics (no per-hunt
    combat sim). Locks the user row so the whole batch spends energy exactly once, and
    counts every hunt toward the daily hunt missions."""
    from game.daily import record_action_bulk
    from game.energy import get_max_energy
    from game.hunt import resolve_auto_hunt

    user, _ = get_or_create_user(tg_user)
    with transaction.atomic():
        user = User.objects.select_for_update().get(id=user.id)
        creature = get_active_creature(user)
        if creature is None:
            raise GameError("اول /start رو بزن تا موجودت رو بگیری.")
        sync_energy(user)
        max_en = get_max_energy(user)
        # cost is HUNT_ENERGY_COST per hunt; clamp the request to what's actually available
        per = max(1, constants.HUNT_ENERGY_COST)
        hunts = min(int(energy_amount), user.energy) // per
        if hunts <= 0:
            raise GameError(f"⚡ انرژی کافی نداری (الان {user.energy}/{max_en}).")
        spend_energy(user, hunts * per, "شکار خودکار")
        user.save(update_fields=["energy", "energy_updated_at"])

        res = resolve_auto_hunt(user, creature, hunts)
        record_action_bulk(user, "hunt", hunts)  # each hunt counts toward hunt missions
        completed_missions = check_missions(user, "hunt")
    return creature, {**res, "energy_left": user.energy, "max_energy": max_en}, completed_missions


def _autohunt_confirm_kb(amount: int):
    """Confirmation text + keyboard for spending `amount` energy on auto-hunt."""
    hunts = amount // max(1, constants.HUNT_ENERGY_COST)
    text = (
        f"⚡️ <b>تأیید شکار خودکار</b>\n\n"
        f"می‌خوای <code>{amount}</code> انرژی صرف <code>{hunts}</code> نبرد خودکار کنی؟\n\n"
        f"<blockquote>⚠️ شکار خودکار نسبت به شکار دستی طلا و دی‌ان‌ای کمتری می‌ده (حدود ۶۰٪ لوت). XP کامل می‌مونه.</blockquote>"
    )
    kb = InlineKeyboardMarkup([
        [btn("تأیید و شروع", emoji_key="btn_confirm", style=CONFIRM, callback_data=f"autohunt_do:{amount}")],
        [back_btn("menu:hunt", "انصراف")],
    ])
    return text, kb


async def _autohunt_no_energy(query, energy: int, max_energy: int = 50) -> None:
    """Out-of-energy → show the diamond refill screen plus a way back to the hunt."""
    from bot.buttons import NAV, btn
    from bot.handlers.energy import _user_sub_info_sync
    from game import botconfig

    await query.answer()
    info = await run_db(_user_sub_info_sync, query.from_user)
    cost = botconfig.get_energy_refill_cost()

    if info["is_active"]:
        caption = (
            f"⚡ <b>انرژی کافی نداری</b> (<code>{energy}/{max_energy}</code>).\n\n"
            f"✨ <b>اشتراک {info['badge']} {info['tier_name']} برای شما فعال است</b> "
            f"(<code>{info['days_left']}</code> روز و <code>{info['hours_left']}</code> ساعت باقی‌مانده).\n\n"
            f"<i>💡 سقف انرژی شما ۶۰ است. می‌توانید با الماس آن را فوراً شارژ کامل کنید:</i>"
        )
        row1 = [btn("شارژ فوری انرژی", emoji_key="btn_charge", style=SHOP, callback_data=f"enr:ask:{query.from_user.id}")]
        row2 = [btn("بازگشت به شکار", emoji_key="btn_hunt", style=NAV, callback_data="menu:hunt")]
        markup = InlineKeyboardMarkup([row1, row2])
    else:
        caption = (
            f"⚡ <b>انرژی کافی نداری</b> (<code>{energy}/{max_energy}</code>).\n\n"
            f"👑 <b>با تهیه اشتراک نقره‌ای:</b>\n"
            f"  ⚡️ <b>سقف انرژیت ۲ برابر می‌شه (۶۰ به جای ۳۰)!</b>\n"
            f"  📋 جعبه‌های آرنا خودکار و پشت‌سرهم باز می‌شن\n"
            f"  🏹 درآمدت از شکار خودکار ۲۵٪ بیشتر می‌شه!\n"
            f"  🥈 نشان پرمیوم نقره‌ای کنار اسمت قرار می‌گیره\n\n"
            f"<i>💡 با اشتراک نقره‌ای ۳۰ روزه، سقف انرژیت دو برابر می‌شه و سریع‌تر شارژ می‌شی!</i>"
        )
        row1 = [btn("شارژ فوری انرژی", emoji_key="btn_charge", style=SHOP, callback_data=f"enr:ask:{query.from_user.id}")]
        row2 = [btn("خرید اشتراک نقره‌ای", emoji_key="btn_vip", style=SHOP, callback_data="sub_pick:silver:hunt")]
        row3 = [btn("بازگشت به شکار", emoji_key="btn_hunt", style=NAV, callback_data="menu:hunt")]
        markup = InlineKeyboardMarkup([row1, row2, row3])

    await safe_edit_message_text(
        query,
        caption,
        parse_mode="HTML",
        reply_markup=markup,
    )


async def autohunt_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ask how much energy to pour into auto-hunting — with all/half quick buttons."""
    query = update.callback_query
    try:
        energy, max_en = await run_db(_autohunt_info_sync, update.effective_user)
    except GameError as exc:
        await query.answer(alert_text(exc), show_alert=True)
        return
    if energy < constants.HUNT_ENERGY_COST:
        await _autohunt_no_energy(query, energy, max_en)
        return
    context.user_data.pop(AWAITING_PLAYER_KEY, None)  # buttons first; «دلخواه» arms text input
    half = max(constants.HUNT_ENERGY_COST, energy // 2)
    await query.answer()
    await safe_edit_message_text(
        query,
        f"⚡️ <b>شکار خودکار</b>\n\n"
        f"چقدر انرژی می‌خوای صرف کنی؟ (الان <code>{energy}/{max_en}</code> داری — "
        f"هر شکار <code>{constants.HUNT_ENERGY_COST}</code> انرژی).\n\n"
        f"<i>توجه: شکار خودکار حدود ۶۰٪ لوت شکار دستیه و طلا و دی‌ان‌ای کمتری می‌ده.</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [btn(f"همه ({energy})", emoji_key="btn_autohunt", style=BATTLE, callback_data="autohunt_amt:all"),
             btn(f"نصف ({half})", emoji_key="btn_autohunt", style=NAV, callback_data="autohunt_amt:half")],
            [btn("انرژی دلخواه", emoji_key="btn_custom_amt", style=NAV, callback_data="autohunt_amt:custom")],
            [back_btn("menu:hunt", "انصراف")],
        ]),
    )


async def autohunt_amt_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """«همه انرژی» / «نصف» → confirm screen; «انرژی دلخواه» → ask for a number."""
    query = update.callback_query
    which = query.data.split(":")[1]
    try:
        energy, max_en = await run_db(_autohunt_info_sync, update.effective_user)
    except GameError as exc:
        await query.answer(alert_text(exc), show_alert=True)
        return
    if energy < constants.HUNT_ENERGY_COST:
        await _autohunt_no_energy(query, energy, max_en)
        return
    if which == "custom":
        context.user_data[AWAITING_PLAYER_KEY] = {"action": "autohunt_energy"}
        await query.answer()
        await safe_edit_message_text(
            query,
            f"⌨️ <b>انرژی دلخواه</b>\n\n"
            f"یه عدد بفرست (بین <code>1</code> تا <code>{energy}</code>).\n\n"
            f"<i>شکار خودکار حدود ۶۰٪ لوت شکار دستیه.</i>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[back_btn("autohunt_start", "انصراف")]]),
        )
        return
    amount = energy if which == "all" else max(constants.HUNT_ENERGY_COST, energy // 2)
    context.user_data.pop(AWAITING_PLAYER_KEY, None)
    text, kb = _autohunt_confirm_kb(amount)
    await query.answer()
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=kb)


async def autohunt_do_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Confirmed — run the batch and show the summary."""
    query = update.callback_query
    amount = int(query.data.split(":")[1])
    try:
        creature, res, completed_missions = await run_db(_autohunt_sync, update.effective_user, amount)
    except GameError as exc:
        from bot.handlers.energy import show_energy_error

        if not await show_energy_error(query, exc):
            await query.answer(alert_text(exc), show_alert=True)
        return
    lines = [
        f"⚡️ <b>نتیجه شکار خودکار</b>",
        f"🗡 تعداد نبرد: <code>{res['hunts']}</code>",
        f"🟢 برد: <code>{res['wins']}</code>",
        f"🔴 باخت: <code>{res['losses']}</code>",
        "━━━━━━━━━━━━━━━━━━━━",
        "💰 <b>مجموع غارت دریافتی:</b>",
        f"{get_emoji('coin')} طلا: <code>+{res['coins']:,}</code>",
        f"{get_emoji('dna')} دی‌ان‌ای: <code>+{res['dna']:,}</code>",
        f"✨ تجربه: <code>+{res['xp']:,}</code> XP" + (f"\n🎉 رسید به سطح <code>{creature.level}</code>!" if res["levels"] else ""),
    ]
    if res.get("sub_bonus_pct"):
        sub_title = f"اشتراک {res.get('sub_name') or 'ویژه'}"
        pct = res['sub_bonus_pct']
        bonus_c = res.get('bonus_coins', 0)
        bonus_d = res.get('bonus_dna', 0)
        base_c = res.get('base_coins', res['coins'])
        base_d = res.get('base_dna', res['dna'])
        lines.extend([
            "━━━━━━━━━━━━━━━━━━━━",
            f"👑 <b>با احتساب <code>{pct}%</code> سود {sub_title}:</b>",
            f"🔹 طلا پایه: <code>{base_c:,}</code>",
            f"🔹 دی‌ان‌ای پایه: <code>{base_d:,}</code>",
            f"🎁 سود طلا: <code>+{bonus_c:,}</code>",
            f"🎁 سود دی‌ان‌ای: <code>+{bonus_d:,}</code>",
        ])
    else:
        lines.extend([
            "",
            "<i>💡 با اشتراک نقره‌ای ۲۵٪ و اشتراک طلایی ۵۰٪ لوت بیشتر از شکار خودکار دریافت می‌کنی!</i>",
        ])

    lines.extend([
        "",
        f"{get_emoji('energy')} انرژی باقی‌مانده: <code>{res['energy_left']}/{res.get('max_energy', constants.MAX_ENERGY)}</code>",
    ])
    text = "\n".join(lines) + _mission_lines(completed_missions)
    await query.answer("✅ انجام شد!")
    await safe_edit_message_text(
        query, text, parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [btn("شکار خودکار مجدد", emoji_key="btn_autohunt", style=BATTLE, callback_data="autohunt_start")],
            [btn("شکار دستی", emoji_key="btn_hunt", style=NAV, callback_data="menu:hunt")],
            [back_btn("menu:hub_battle", "بازگشت به نبرد")],
        ]),
    )


def _alliance_create_sync(tg_user, name):
    user, _ = get_or_create_user(tg_user)
    return create_alliance(user, name)


async def alliance_create(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from game.alliance import ALLIANCE_CREATE_COST

    if not context.args:
        await update.message.reply_text(
            "استفاده: <code>/alliance_create اسم اتحاد</code>\n"
            f"<i>هزینه‌ی ساخت اتحاد: <code>{ALLIANCE_CREATE_COST:,}</code> طلا</i>", parse_mode="HTML"
        )
        return
    try:
        alliance = await run_db(_alliance_create_sync, update.effective_user, " ".join(context.args))
    except GameError as exc:
        await update.message.reply_text(str(exc), parse_mode="HTML")
        return
    await update.message.reply_text(
        f"{get_emoji('alliance')} اتحاد <b>{alliance.name}</b> ساخته شد! تو رهبرشی {get_emoji('crown')}\n"
        f"<i>(<code>{ALLIANCE_CREATE_COST:,}</code> طلا کم شد)</i>",
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
        note = (f"📨 <b>درخواست عضویت جدید</b>\n"
                f"کاربر «{who}» (قدرت <code>{result['requester_power']:,}</code>) "
                f"می‌خواد به <b>{alliance.name}</b> بپیونده.\n"
                f"از «👥 اعضا و مدیریت ➔ درخواست‌ها» تأیید/رد کن.")
        kb = InlineKeyboardMarkup([[btn("درخواست‌های عضویت", emoji_key="btn_requests", style=CONFIRM, callback_data="ally_requests")]])
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


def _leave_preview_sync(tg_user) -> str:
    """What leaving will do, for the confirm screen (mirrors game.alliance.leave_alliance)."""
    user, _ = get_or_create_user(tg_user)
    alliance = Alliance.objects.filter(id=user.alliance_id).first() if user.alliance_id else None
    if alliance is None:
        return "توی هیچ اتحادی نیستی."
    others = User.objects.filter(alliance_id=alliance.id).exclude(id=user.id).count()
    if others == 0:
        return (
            "تو آخرین عضوی: با خروجت <b>اتحاد و خزانه‌ش "
            f"(<code>{alliance.treasury_gold:,}</code> طلا) برای همیشه حذف می‌شه</b>."
        )
    if alliance.leader_id == user.id:
        heir = "قائم‌مقام" if alliance.deputy_id and alliance.deputy_id != user.id else "یکی از اعضا"
        return f"تو رهبری: با خروجت <b>رهبری به {heir} می‌رسه</b> و طلایی که به خزانه دادی برنمی‌گرده."
    return "طلایی که به خزانه واریز کردی برنمی‌گرده."


async def alliance_leave(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    note = await run_db(_leave_preview_sync, update.effective_user)
    keyboard = InlineKeyboardMarkup([[
        btn("بله، خارج شو", emoji_key="btn_confirm", style=DANGER, callback_data="ally_leave_confirm"),
        btn("بی‌خیال", emoji_key="btn_cancel", style=NAV, callback_data="menu:alliance_info"),
    ]])
    await update.effective_message.reply_text(
        f"⚠️ <b>خروج از اتحاد</b>\n\n{note}\n\nمطمئنی می‌خوای خارج بشی؟",
        parse_mode="HTML", reply_markup=keyboard,
    )


def _alliance_info_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    if user.alliance_id is None:
        return None
    return alliance_info(user.alliance)


def _ally_raidtable_sync(tg_user):
    from bio_lab.models import Alliance
    from game.raid import alliance_raid_members, get_active_boss

    user, _ = get_or_create_user(tg_user)
    if user.alliance_id is None:
        return None
    al = Alliance.objects.get(id=user.alliance_id)
    boss = get_active_boss(user.alliance_id)
    return {
        "name": al.name, "raid_level": al.raid_level,
        "boss": ({"name": boss.name, "level": boss.level, "hp": max(0, boss.current_hp),
                  "max_hp": boss.max_hp} if boss else None),
        "members": alliance_raid_members(user.alliance_id, 10),
    }


async def ally_raidtable_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """🐲 جدول رید اتحاد — the alliance's raid level, its active boss, and its top-10 raiders."""
    query = update.callback_query
    data = await run_db(_ally_raidtable_sync, update.effective_user)
    await query.answer()
    if data is None:
        await safe_edit_message_text(query, "عضو هیچ اتحادی نیستی.",
                                     reply_markup=back_only_keyboard("menu:alliance_info", "بازگشت"))
        return
    lines = [
        f"🐲 <b>جدول رید اتحاد {data['name']}</b>",
        f"🐉 سطح رید اتحاد: <code>{data['raid_level']}</code>",
    ]
    if data["boss"]:
        b = data["boss"]
        lines.append(f"{get_emoji('raid_boss')} باس فعال: <b>{b['name']}</b> (سطح <code>{b['level']}</code>)\n"
                     f"❤️ سلامت: <code>{b['hp']:,}/{b['max_hp']:,}</code> HP")
    else:
        lines.append("<i>الان باس فعالی نیست — یکی از اعضا توی گروه «احضار» بزنه.</i>")
    lines += ["", "━━━━━━━━━━━━━━━━━━━━", "🏆 <b>۱۰ رِیدرِ برترِ این هفته:</b>"]
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    if not data["members"]:
        lines.append("<i>این هفته هنوز کسی اتک رید نزده.</i>")
    for m in data["members"]:
        badge = medals.get(m["rank"], f"{m['rank']}.")
        lines.append(f"{badge} <b>{m['name']}</b>\n   💥 دمیج: <code>{m['damage']:,}</code>")
    await safe_edit_message_text(query, "\n".join(lines), parse_mode="HTML",
                                 reply_markup=back_only_keyboard("menu:alliance_info", "بازگشت به اتحاد"))


def _alliance_action_keyboard(in_alliance: bool) -> InlineKeyboardMarkup:
    if in_alliance:
        rows = [
            [btn("اعضا و مدیریت", emoji_key="btn_members", style=NAV, callback_data="ally_members")],
            [btn("واریز به خزانه", emoji_key="btn_deposit", style=BUILD, callback_data="ally_deposit")],
            [btn("ساختمان‌های اتحاد", emoji_key="btn_buildings", style=PRIMARY, callback_data="ally_perks")],
            [btn("جدول رید اتحاد", emoji_key="btn_raid_table", style=NAV, callback_data="ally_raidtable")],
            [
                btn("جنگ یک‌روزه", emoji_key="btn_war", style=BATTLE, callback_data="ally_war1d"),
                btn("جنگ هفتگی", emoji_key="btn_attack", style=BATTLE, callback_data="ally_war"),
            ],
            [btn("شبیخون به اتحاد", emoji_key="btn_heist", style=BATTLE, callback_data="ally_heist_list")],
            [btn("برترین اتحادها", emoji_key="btn_rank", style=NAV, callback_data="ally_top")],
            [btn("خروج از اتحاد", emoji_key="btn_cancel", style=DANGER, callback_data="ally_leave")],
        ]
    else:
        rows = [
            [btn("ساخت اتحاد", emoji_key="btn_alliance", style=BUILD, callback_data="ally_create")],
            [btn("پیوستن به اتحاد", emoji_key="btn_alliance", style=PRIMARY, callback_data="ally_join")],
            [btn("برترین اتحادها", emoji_key="btn_rank", style=NAV, callback_data="ally_top")],
        ]
    rows.append([back_btn("menu:hub_city", "بازگشت به شهر")])
    return InlineKeyboardMarkup(rows)


def _alliance_info_text(info: dict) -> str:
    deputy_line = f"\n🎖 قائم‌مقام: {display_name(info['deputy'])}" if info.get('deputy') else ""
    lines = [
        f"{get_emoji('alliance')} <b>اتحاد {info['name']}</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"{get_emoji('crown')} رهبر: {display_name(info['leader']) if info['leader'] else '—'}{deputy_line}",
        f"{get_emoji('users')} اعضا: <code>{info['member_count']}/{info.get('capacity', 50)}</code>",
        f"💪 قدرت کل: <code>{info['power']:,}</code>",
        f"{get_emoji('coin')} خزانه: <code>{info['treasury_gold']:,}</code> طلا",
        "",
        "<b>لیست اعضا:</b>",
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


def _group_alliance_keyboard(in_alliance: bool) -> InlineKeyboardMarkup:
    """The trimmed alliance menu shown when «اتحاد» is typed in a GROUP. Only the
    actions that act on the presser's OWN alliance (war, heist, upgrades, treasury,
    ranking) — never roster/kick/settings/leave, which need per-person scoping."""
    if in_alliance:
        rows = [
            [btn("ساختمان‌های اتحاد", emoji_key="btn_buildings", style=PRIMARY, callback_data="ally_perks")],
            [
                btn("جنگ یک‌روزه", emoji_key="btn_war", style=BATTLE, callback_data="ally_war1d"),
                btn("جنگ هفتگی", emoji_key="btn_attack", style=BATTLE, callback_data="ally_war"),
            ],
            [btn("شبیخون به اتحاد", emoji_key="btn_heist", style=BATTLE, callback_data="ally_heist_list")],
            [btn("واریز به خزانه", emoji_key="btn_deposit", style=BUILD, callback_data="ally_deposit")],
            [btn("جدول رید اتحاد", emoji_key="btn_raid_table", style=NAV, callback_data="ally_raidtable")],
            [btn("برترین اتحادها", emoji_key="btn_rank", style=NAV, callback_data="ally_top")],
        ]
    else:
        rows = [
            [btn("ساخت اتحاد", emoji_key="btn_alliance", style=BUILD, callback_data="ally_create")],
            [btn("پیوستن به اتحاد", emoji_key="btn_alliance", style=PRIMARY, callback_data="ally_join")],
            [btn("برترین اتحادها", emoji_key="btn_rank", style=NAV, callback_data="ally_top")],
        ]
    return InlineKeyboardMarkup(rows)


async def _show_group_alliance(update: Update, *, edit: bool) -> None:
    """Render the trimmed in-group alliance menu — as a fresh reply (from the «اتحاد»
    word) or as an in-place edit (when a sub-panel's «بازگشت» returns here). This is the
    ONLY «menu:»-style destination that resolves inside a group; everything else in the
    private menu stays DM-only (see menu_callback's group guard)."""
    info = await run_db(_alliance_info_sync, update.effective_user)
    kb = _group_alliance_keyboard(in_alliance=info is not None)
    text = (_alliance_info_text(info) if info is not None
            else f"{get_emoji('alliance')} توی هیچ اتحادی نیستی — می‌تونی همین‌جا یکی بسازی یا عضو شی 👇")
    if edit and update.callback_query is not None:
        await safe_edit_message_text(update.callback_query, text, parse_mode="HTML", reply_markup=kb)
    else:
        sent = await update.effective_message.reply_text(text, parse_mode="HTML", reply_markup=kb)
        if update.effective_chat is not None and update.effective_user is not None:
            from bot.gates import remember_card_owner

            remember_card_owner(update.effective_chat.id, sent.message_id, update.effective_user.id)


async def alliance_info_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # In a GROUP the panel is a shared message, so its management buttons would let
    # anyone tap on the person's alliance. There we show the trimmed essential-actions
    # menu; the roster/kick/settings management stays in the DM (scoped to one person).
    chat = update.effective_chat
    in_group = chat is not None and chat.type in ("group", "supergroup")

    if in_group:
        await _show_group_alliance(update, edit=False)
        return
    info = await run_db(_alliance_info_sync, update.effective_user)

    from game.media import get_feature_image_path
    photo = get_feature_image_path("alliance")
    if info is None:
        await send_screen(update,
            f"{get_emoji('alliance')} توی هیچ اتحادی نیستی.",
            photo=photo,
            parse_mode="HTML",
            reply_markup=_alliance_action_keyboard(in_alliance=False),
        )
        return
    await send_screen(update,
        _alliance_info_text(info), photo=photo, parse_mode="HTML", reply_markup=_alliance_action_keyboard(in_alliance=True)
    )


# ── alliance members roster + management (leader/deputy) ━━━━━━━━━━━━━━━━━━━━─────
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
        f"{get_emoji('users')} <b>اعضای اتحاد {data['name']}</b> (<code>{len(roster)}</code> نفر)"
        + (f" <i>(صفحه <code>{page + 1}/{total_pages}</code>)</i>" if total_pages > 1 else ""),
        "━━━━━━━━━━━━━━━━━━━━",
    ]
    for r in chunk:
        badge = "👑" if r["is_leader"] else ("🎖" if r["is_deputy"] else "▫️")
        lines.append(f"{badge} <b>{r['name']}</b> (<code>{r['id']}</code>)\n   💪 قدرت: <code>{r['power']:,}</code>")

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
            f"\n<blockquote>👑 رهبر · 🎖 قائم‌مقام\n"
            f"عضوگیری: <b>{mode}</b>\n"
            f"حداقل قدرت: <code>{data.get('min_join_power', 0):,}</code>\n"
            f"برای مدیریت، آیدیِ عضو رو بده.</blockquote>"
        )
        pend = data.get("pending", 0)
        rows.append([btn(f"درخواست‌های عضویت ({pend})", emoji_key="btn_requests", style=CONFIRM, callback_data="ally_requests")])
        rows.append([btn("تنظیمات عضوگیری", emoji_key="btn_settings", style=NAV, callback_data="ally_settings")])
        rows.append([btn("اخراج عضو با آیدی", emoji_key="btn_kick", style=DANGER, callback_data="ally_kick")])
    if role == "leader":
        rows.append([btn("تعیین قائم‌مقام", emoji_key="btn_deputy", style=PRIMARY, callback_data="ally_deputy")])
        if data["has_deputy"]:
            rows.append([btn("عزل قائم‌مقام", emoji_key="btn_cancel", style=DANGER, callback_data="ally_deputy_off")])
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


# ── join requests (leader/deputy approve/reject) ━━━━━━━━━━━━━━━━━━━━────────────
def _requests_sync(tg_user):
    from game import alliance as alliance_mod

    user, _ = get_or_create_user(tg_user)
    al = user.alliance
    if al is None or not alliance_mod._is_manager(user, al):
        return None
    return {"name": al.name, "requests": pending_requests(al)}


def _requests_render(data: dict) -> tuple[str, InlineKeyboardMarkup]:
    reqs = data["requests"]
    lines = [
        f"📨 <b>درخواست‌های عضویت — {data['name']}</b> (<code>{len(reqs)}</code>)",
        "━━━━━━━━━━━━━━━━━━━━",
    ]
    rows = []
    if not reqs:
        lines.append("<i>الان درخواستی نیست.</i>")
    for r in reqs:
        name = r["user"].first_name or str(r["user"].id)
        lines.append(f"▫️ <b>{name}</b> (<code>{r['user'].id}</code>)\n   💪 قدرت: <code>{r['power']:,}</code>")
        rows.append([
            btn(f"قبول {name[:10]}", emoji_key="btn_confirm", style=CONFIRM, callback_data=f"ally_approve:{r['id']}"),
            btn("رد", emoji_key="btn_cancel", style=DANGER, callback_data=f"ally_reject:{r['id']}"),
        ])
    rows.append([back_btn("menu:alliance_info", "بازگشت به اتحاد")])
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
        await query.answer(alert_text(exc), show_alert=True)
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
        await query.answer(alert_text(exc), show_alert=True)
        return
    await query.answer("❌ رد شد.")
    await _pv_notify(
        context, result["applicant"].id,
        f"متأسفانه درخواست عضویتت به اتحاد <b>{result['alliance_name']}</b> رد شد.",
    )
    if data is not None:
        text, keyboard = _requests_render(data)
        await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


# ── alliance join settings (auto/request + min power) ━━━━━━━━━━━━━━━━━━━━────────
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
        f"حداقل قدرت برای عضویت: <code>{data['min_join_power']:,}</code>\n"
    )
    toggle_label = "عضوگیری: با تأیید" if data["auto_accept"] else "عضوگیری: خودکار"
    rows = [
        [btn(toggle_label, emoji_key="btn_settings", style=CONFIRM, callback_data="ally_toggle_auto")],
        [btn("حداقل قدرت ورود", emoji_key="btn_filter", style=NAV, callback_data="ally_set_minpow")],
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
        await query.answer(alert_text(exc), show_alert=True)
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
        reply_markup=InlineKeyboardMarkup([[back_btn("menu:alliance_info", "انصراف")]]),
    )


async def alliance_kick_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    context.user_data[AWAITING_PLAYER_KEY] = {"action": "ally_kick"}
    await query.answer()
    await safe_edit_message_text(
        query, f"🥾 آیدی عددیِ عضوی که می‌خوای اخراج بشه رو بفرست (همون عددِ کنار اسمش توی لیست اعضا):{_reply_hint(update)}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[back_btn("menu:alliance_info", "انصراف")]]),
    )


async def alliance_deputy_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    context.user_data[AWAITING_PLAYER_KEY] = {"action": "ally_deputy"}
    await query.answer()
    await safe_edit_message_text(
        query, f"🎖 آیدیِ عضوی که قائم‌مقام بشه رو بفرست:{_reply_hint(update)}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[back_btn("menu:alliance_info", "انصراف")]]),
    )


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
        await query.answer(alert_text(exc), show_alert=True)
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


def _alliance_precheck_sync(tg_user):
    from game.alliance import validate_new_alliance

    user, _ = get_or_create_user(tg_user)
    validate_new_alliance(user)  # no name yet — just membership + affordability
    return user.coins


def _alliance_validate_sync(tg_user, name):
    from game.alliance import validate_new_alliance

    user, _ = get_or_create_user(tg_user)
    validate_new_alliance(user, name)


async def alliance_create_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from game.alliance import ALLIANCE_CREATE_COST

    query = update.callback_query
    # gate on affordability BEFORE asking for a name — a broke player never even gets
    # to the naming step (per request). InsufficientGold gets the «خرید طلا» button.
    try:
        await run_db(_alliance_precheck_sync, update.effective_user)
    except GameError as exc:
        await query.answer()
        await safe_edit_message_text(
            query, str(exc), parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[back_btn("menu:alliance_info", "بازگشت")]]),
        )
        return
    context.user_data[AWAITING_PLAYER_KEY] = {"action": "alliance_create"}
    await query.answer()
    await safe_edit_message_text(
        query,
        f"🟢 {get_emoji('alliance')} اسم اتحاد جدیدت رو بفرست:\n\n"
        f"<i>ساختش <code>{ALLIANCE_CREATE_COST:,}</code> طلا هزینه داره — آخرش تأیید می‌گیریم.</i>{_reply_hint(update)}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[back_btn("menu:alliance_info", "انصراف")]]),
    )


async def alliance_create_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    name = context.user_data.pop("pending_alliance_name", None)
    if not name:
        await query.answer("اسمی ذخیره نشده — دوباره «ساخت اتحاد» رو بزن.", show_alert=True)
        return
    try:
        alliance = await run_db(_alliance_create_sync, update.effective_user, name)
    except GameError as exc:
        await query.answer()
        await safe_edit_message_text(
            query, str(exc), parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[back_btn("menu:alliance_info", "بازگشت")]]),
        )
        return
    from game.alliance import ALLIANCE_CREATE_COST

    await query.answer("✅ اتحاد ساخته شد!")
    await safe_edit_message_text(
        query,
        f"{get_emoji('alliance')} اتحاد <b>{alliance.name}</b> ساخته شد! تو رهبرشی {get_emoji('crown')}\n"
        f"<i>(<code>{ALLIANCE_CREATE_COST:,}</code> طلا کم شد)</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[back_btn("menu:alliance_info", "اتحاد من")]]),
    )


async def alliance_create_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    context.user_data.pop("pending_alliance_name", None)
    context.user_data.pop(AWAITING_PLAYER_KEY, None)
    await query.answer("لغو شد.")
    await safe_edit_message_text(
        query, "ساخت اتحاد لغو شد.", reply_markup=InlineKeyboardMarkup([[back_btn("menu:alliance_info", "بازگشت")]]),
    )


def _alliance_browse_render(data: dict, search_results=None, search_query: str = "") -> tuple[str, InlineKeyboardMarkup]:
    rows = []
    if search_results is not None:
        # search results mode
        if search_results:
            lines = [f"🔍 <b>نتایج جستجو برای «{search_query}»:</b>", "━━━━━━━━━━━━━━━━━━━━"]
            for r in search_results:
                a = r["alliance"]
                lines.append(f"🏰 <b>{a.name}</b>\n   👥 اعضا: <code>{r['member_count']}</code>\n   💪 قدرت: <code>{r['power']:,}</code>")
                rows.append([btn(f"عضویت در {a.name}", emoji_key="btn_ally_join", style=PRIMARY, callback_data=f"ally_browse_join:{a.id}")])
        else:
            lines = [f"🔍 هیچ اتحادی با «{search_query}» پیدا نشد."]
        rows.append([btn("جستجوی مجدد", emoji_key="btn_search", style=NAV, callback_data="ally_search")])
        rows.append([btn("مشاهده همه اتحادها", emoji_key="btn_list", style=NAV, callback_data="ally_browse:0")])
        rows.append([back_btn("menu:alliance_info", "بازگشت")])
        return "\n".join(lines), InlineKeyboardMarkup(rows)

    # browse mode
    page = data["page"]
    total = data["total"]
    alliances = data["alliances"]
    lines = [f"🏰 <b>اتحادهای موجود</b> (صفحه <code>{page + 1}</code>، جمعاً <code>{total}</code> اتحاد)", "━━━━━━━━━━━━━━━━━━━━"]
    if not alliances:
        lines.append("<i>هنوز هیچ اتحادی ساخته نشده.</i>")
    for r in alliances:
        a = r["alliance"]
        lines.append(f"🏰 <b>{a.name}</b>\n   👥 اعضا: <code>{r['member_count']}</code>\n   💪 قدرت: <code>{r['power']:,}</code>")
        rows.append([btn(f"عضویت در {a.name}", emoji_key="btn_ally_join", style=PRIMARY, callback_data=f"ally_browse_join:{a.id}")])

    nav_row = []
    if data["has_prev"]:
        nav_row.append(btn("قبلی", emoji_key="btn_prev", style=NAV, callback_data=f"ally_browse:{page - 1}"))
    if data["has_next"]:
        nav_row.append(btn("بعدی", emoji_key="btn_next", style=NAV, callback_data=f"ally_browse:{page + 1}"))
    if nav_row:
        rows.append(nav_row)
    rows.append([btn("جستجوی اتحاد", emoji_key="btn_search", style=NAV, callback_data="ally_search")])
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
        await query.answer(alert_text(exc), show_alert=True)
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
        reply_markup=InlineKeyboardMarkup([[back_btn("menu:alliance_info", "انصراف")]]),
    )


async def alliance_deposit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    context.user_data[AWAITING_PLAYER_KEY] = {"action": "alliance_deposit"}
    await query.answer()
    await safe_edit_message_text(
        query,
        f"💰 چند {get_emoji('coin')} طلا می‌خوای به خزانه واریز کنی؟ یه عدد بفرست:{_reply_hint(update)}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[back_btn("menu:alliance_info", "انصراف")]]),
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
    lines = [f"{get_emoji('trophy')} <b>برترین اتحادها</b>", "━━━━━━━━━━━━━━━━━━━━"]
    for i, r in enumerate(ranked, start=1):
        rank = medals[i - 1] if i <= 3 else f"<code>{i}.</code>"
        lines.append(f"{rank} <b>{r['alliance'].name}</b>\n   💪 قدرت: <code>{r['power']:,}</code>\n   👥 اعضا: <code>{r['member_count']}</code>")
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
    note = await run_db(_leave_preview_sync, update.effective_user)
    await query.answer()
    await safe_edit_message_text(
        query, f"⚠️ <b>خروج از اتحاد</b>\n\n{note}\n\nمطمئنی می‌خوای خارج بشی؟",
        parse_mode="HTML", reply_markup=keyboard,
    )


async def alliance_leave_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        await run_db(_alliance_leave_sync, update.effective_user)
    except GameError as exc:
        await query.answer(alert_text(exc), show_alert=True)
        return
    await query.answer("👋 خارج شدی.")
    await safe_edit_message_text(
        query, "👋 از اتحاد خارج شدی.",
        reply_markup=back_only_keyboard("menu:hub_city", "بازگشت به شهر"),
    )


def _heist_targets_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    if user.alliance_id is None:
        raise GameError("اول باید عضو یه اتحاد باشی.")
    return list(
        Alliance.objects.exclude(id=user.alliance_id).filter(treasury_gold__gt=0)
        .order_by("-treasury_gold", "id")[:40]
    )


async def alliance_heist_list_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        targets = await run_db(_heist_targets_sync, update.effective_user)
    except GameError as exc:
        await query.answer(alert_text(exc), show_alert=True)
        return
    if not targets:
        await query.answer("هیچ اتحاد دیگه‌ای برای شبیخون نیست.", show_alert=True)
        return
    await query.answer()
    rows = [
        [btn(f"{a.name} — {a.treasury_gold:,} طلا", emoji_key="btn_heist", style=BATTLE, callback_data=f"heist_pick:{a.id}")]
        for a in targets
    ]
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


def _kick_preview_sync(tg_user, target_id) -> str:
    user, _ = get_or_create_user(tg_user)
    if user.alliance_id is None:
        raise GameError("عضو هیچ اتحادی نیستی.")
    target = User.objects.filter(id=target_id, alliance_id=user.alliance_id).first()
    if target is None:
        raise GameError("همچین عضوی توی اتحادت نیست. آیدی عددی رو از لیست اعضا بردار.")
    return display_name(target)


async def alliance_kick_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    target_id = int(query.data.split(":")[1])

    def _do(tg_user):
        from game import alliance as alliance_mod

        user, _ = get_or_create_user(tg_user)
        return alliance_mod.kick_member(user, target_id)

    try:
        result = await run_db(_do, update.effective_user)
    except GameError as exc:
        await query.answer(alert_text(exc), show_alert=True)
        return
    await query.answer()
    await safe_edit_message_text(
        query, f"🥾 <b>{result['name']}</b> از اتحاد حذف شد.", parse_mode="HTML",
        reply_markup=back_only_keyboard("menu:alliance_info", "بازگشت به اتحاد"),
    )


async def alliance_deposit_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    amount = int(query.data.split(":")[1])
    try:
        alliance = await run_db(_alliance_deposit_sync, update.effective_user, amount)
    except GameError as exc:
        await query.answer(alert_text(exc), show_alert=True)
        return
    await query.answer()
    await safe_edit_message_text(
        query,
        f"{get_emoji('coin')} <code>{amount:,}</code> طلا به خزانه‌ی <b>{alliance.name}</b> واریز شد!\n"
        f"خزانه فعلی: <code>{alliance.treasury_gold:,}</code> طلا",
        parse_mode="HTML",
        reply_markup=back_only_keyboard("menu:alliance_info", "بازگشت به اتحاد"),
    )


def _heist_preview_sync(tg_user, target_alliance_id):
    from game.daily import get_daily_count

    user, _ = get_or_create_user(tg_user)
    target = Alliance.objects.filter(id=target_alliance_id).first()
    if target is None:
        raise GameError("این اتحاد دیگه پیدا نشد.")
    return target, get_daily_count(user, "heist"), constants.ENERGY_CAPS.get("heist")


async def heist_pick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Picking a target shows a confirmation first — the attempt is limited per day and
    used to fire on the very first tap."""
    query = update.callback_query
    target_id = int(query.data.split(":")[1])
    try:
        target, used, cap = await run_db(_heist_preview_sync, update.effective_user, target_id)
    except GameError as exc:
        await query.answer(alert_text(exc), show_alert=True)
        return
    await query.answer()
    attempts = f"\n🔁 شبیخون‌های امروز: <code>{used}/{cap}</code>" if cap else ""
    await safe_edit_message_text(
        query,
        f"🏴‍☠️ <b>شبیخون به «{target.name}»؟</b>\n\n"
        f"{get_emoji('coin')} خزانه‌ی هدف: <code>{target.treasury_gold:,}</code> طلا\n"
        f"در صورت برد، <b>{int(constants.HEIST_STEAL_PERCENT * 100)}٪</b> خزانه به خزانه‌ی اتحادت می‌رسه."
        f"{attempts}\n<i>چه ببری چه ببازی، یکی از شبیخون‌های امروزت مصرف می‌شه.</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            btn("حمله کن", emoji_key="btn_heist", style=BATTLE, callback_data=f"heist_ok:{target_id}"),
            btn("نه", emoji_key="btn_cancel", style=NAV, callback_data="ally_heist_list"),
        ]]),
    )


async def heist_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    target_id = int(query.data.split(":")[1])
    try:
        result, target = await run_db(_heist_by_id_sync, update.effective_user, target_id)
    except GameError as exc:
        await query.answer(alert_text(exc), show_alert=True)
        return
    await query.answer("🟢 انجام شد!" if result["success"] else "🔴 شکست خوردی.")
    lines = []
    if result["defender_creature"] is not None:
        lines.append(result["log_text"])
    if result["success"]:
        reveal = (
            f"{get_emoji('celebrate')} <b>شبیخون موفق بود!</b>\n"
            f"{get_emoji('coin')} غارت: <code>+{result['stolen']:,}</code> طلا از خزانه‌ی <b>{target.name}</b> غارت شد و کامل به <b>خزانه‌ی اتحادت</b> واریز شد!"
        )
    else:
        reveal = f"😔 نگهبان‌های <b>{target.name}</b> دفاع کردن و شبیخونت شکست خورد."
    lines.append(f"<tg-spoiler>{reveal}</tg-spoiler>")
    keyboard = InlineKeyboardMarkup([[back_btn("menu:alliance_info")]])
    await safe_edit_message_text(query, "\n\n".join(lines), parse_mode="HTML", reply_markup=keyboard)


def _lab_name_check_sync(tg_user, name) -> str:
    user, _ = get_or_create_user(tg_user)
    cleaned = _clean_lab_name(name)
    if not cleaned:
        raise GameError("اسم نمی‌تونه خالی باشه")
    if lab_name_taken(cleaned, exclude_user_id=user.id):
        raise GameError("این اسم آزمایشگاه قبلاً گرفته شده")
    return cleaned


def _needs_lab_name_sync(tg_user) -> bool:
    user, _ = get_or_create_user(tg_user)
    return user.lab_name is None


async def lab_name_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    name = context.user_data.pop("pending_lab_name", None)
    await query.answer()
    if query.data == "labname_no" or not name:
        context.user_data[AWAITING_PLAYER_KEY] = {"action": "set_lab_name"}
        await safe_edit_message_text(query, "باشه — اسم آزمایشگاهت رو بفرست:")
        return
    context.user_data[AWAITING_PLAYER_KEY] = {"action": "set_lab_name", "confirmed": True}
    await capture_player_text_reply(update, context, override_text=name)


async def capture_player_text_reply(update: Update, context: ContextTypes.DEFAULT_TYPE, override_text: str | None = None) -> None:
    """Single dispatcher for every 'awaiting a plain-text reply' player flow (alliance
    name, deposit amount) — PTB only runs the first handler that matches an update
    within a group, so this and owner.capture_owner_text_reply are combined into one
    MessageHandler registration in bot/main.py rather than each registering their own."""
    awaiting = context.user_data.pop(AWAITING_PLAYER_KEY, None)
    if awaiting is None:
        return
    message = update.effective_message
    action = awaiting["action"]
    text = (override_text if override_text is not None else (message.text or "")).strip()

    if action == "exchange_custom":
        from bot.handlers.exchange import handle_custom_amount

        await handle_custom_amount(update, context, awaiting)
        return

    if action == "blackmarket_custom_bid":
        from bot.handlers.blackmarket import handle_custom_bid_input

        await handle_custom_bid_input(update, context, awaiting)
        return

    if action == "buy_custom":
        from bot.handlers.purchase import handle_custom_amount as _buy_custom

        await _buy_custom(update, context, awaiting)
        return

    if action == "shop_buy_qty":
        from bot.handlers.shop import handle_custom_qty_buy

        raw = (text or "").strip().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
        if not raw.isdigit() or not (0 < int(raw) <= 9999):
            context.user_data[AWAITING_PLAYER_KEY] = awaiting
            await message.reply_text("⚠️ لطفاً فقط یه عدد بین ۱ تا ۹۹۹۹ بفرست (مثلاً 10) — یا دستور دیگری بزن:")
            return
        await handle_custom_qty_buy(
            update, context, awaiting["key"], int(raw), awaiting.get("shown_price"), awaiting.get("shown_currency")
        )
        return

    if action == "set_lab_name":
        if not text or len(text) > LAB_NAME_MAX_LEN:
            context.user_data[AWAITING_PLAYER_KEY] = awaiting
            await message.reply_text(f"⚠️ اسم باید بین 1 تا {LAB_NAME_MAX_LEN} کاراکتر باشه. دوباره بفرست:")
            return
        if not awaiting.get("confirmed"):
            # the first text a newcomer sends used to become their permanent name («سلام»)
            try:
                cleaned = await run_db(_lab_name_check_sync, update.effective_user, text)
            except GameError as exc:
                context.user_data[AWAITING_PLAYER_KEY] = awaiting
                await message.reply_text(f"⚠️ {exc} — یه اسم دیگه بفرست:")
                return
            context.user_data["pending_lab_name"] = cleaned
            await message.reply_text(
                f"🧪 اسم آزمایشگاهت «<b>{html.escape(cleaned)}</b>» باشه؟\n"
                "<i>این اسم توی همه‌ی جدول‌ها دیده می‌شه.</i>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[
                    btn("آره، همین", emoji_key="btn_confirm", style=CONFIRM, callback_data="labname_ok"),
                    btn("یه اسم دیگه", emoji_key="btn_cancel", style=NAV, callback_data="labname_no"),
                ]]),
            )
            return
        try:
            user, creature, equipped_items, hall_lvl, rsch_ok, quest = await run_db(_set_lab_name_sync, update.effective_user, text)
        except GameError as exc:
            context.user_data[AWAITING_PLAYER_KEY] = {"action": "set_lab_name"}
            await message.reply_text(f"⚠️ {exc} — یه اسم دیگه بفرست:")
            return

        if not user.onboarding_completed and creature is None:
            text_msg = (
                f"🧪 <b>آزمایشگاه «{lab_display(user)}» با موفقیت تأسیس شد!</b>\n\n"
                "<blockquote>فرمانده گرامی، یک کپسول زیستی حاوی تخم هیولای اولیه کشف شده است.\n"
                "با شکستن تخم، اولین کایجوی خودت رو متولد کن و ماجراجویی رو آغاز کن! 👇</blockquote>"
            )
            keyboard = InlineKeyboardMarkup([
                [btn("شکستن اولین تخم کایجو", emoji_key="btn_hatch", style=CONFIRM, callback_data="onboarding:hatch")]
            ])
            await message.reply_text(text_msg, parse_mode="HTML", reply_markup=keyboard)
            return

        is_owner = _is_admin_user(update.effective_user.id if update.effective_user else None)
        quest_txt = story_quest_card_text(quest)
        quest_block = f"<blockquote>{quest_txt.strip()}</blockquote>\n\n" if quest_txt.strip() else ""
        await message.reply_text(
            f"👋 <b>آزمایشگاه «{lab_display(user)}» فعال شد!</b>\n\n"
            f"{quest_block}"
            + creature_card_text(user, creature, equipped_items),
            parse_mode="HTML",
            reply_markup=creature_keyboard(quest, is_owner, _locked_actions_for(hall_lvl), rsch_ok),
        )
        return

    if action == "rename_lab":
        if not text or len(text) > LAB_NAME_MAX_LEN:
            context.user_data[AWAITING_PLAYER_KEY] = awaiting
            await message.reply_text(f"⚠️ اسم باید بین 1 تا {LAB_NAME_MAX_LEN} کاراکتر باشه. دوباره بفرست:")
            return
        try:
            user, cost, cleaned = await run_db(_rename_lab_check_sync, update.effective_user, text)
        except GameError as exc:
            await message.reply_text(f"⚠️ {exc}.")
            return
        if cost == 0:
            user, cost, _newname = await run_db(_rename_lab_sync, update.effective_user, text)
            await message.reply_text(
                f"✅ اسم آزمایشگاهت به «{lab_display(user)}» تغییر کرد.",
                parse_mode="HTML",
            )
            return
        context.user_data["pending_lab_rename"] = {
            "name": cleaned,
            "cost": cost,
        }
        await message.reply_text(
            f"✏️ تغییر نام آزمایشگاه به: <b>{cleaned}</b>\n"
            f"{get_emoji('diamond')} هزینه: <code>{cost}</code> الماس\n"
            f"💎 موجودی شما: <code>{user.diamonds}</code> الماس\n\n"
            "آیا از تغییر نام آزمایشگاه اطمینان دارید؟",
            reply_markup=InlineKeyboardMarkup([
                [btn("تأیید و ثبت", emoji_key="btn_confirm", style=CONFIRM, callback_data="lab_rename_ok")],
                [btn("لغو", emoji_key="btn_cancel", style=DANGER, callback_data="lab_rename_cancel")],
            ]),
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
            energy, max_en = await run_db(_autohunt_info_sync, update.effective_user)
        except GameError as exc:
            await message.reply_text(alert_text(exc, 3500))
            return
        amount = min(int(raw), energy)
        if amount < constants.HUNT_ENERGY_COST:
            await message.reply_text(f"⚡ انرژی کافی نداری ({energy}/{max_en}).")
            return
        text_confirm, kb_confirm = _autohunt_confirm_kb(amount)
        await message.reply_text(text_confirm, parse_mode="HTML", reply_markup=kb_confirm)
        return

    if action == "rename_kaiju":
        creature_id = awaiting["creature_id"]
        origin = awaiting.get("origin", "c")
        cancel_kb = InlineKeyboardMarkup([[btn("لغو", emoji_key="btn_cancel", style=DANGER, callback_data=f"kaiju_rename_cancel:{creature_id}:{origin}")]])
        # a slash-command or an empty message isn't a name — keep waiting, don't consume it
        if not text or text.startswith("/"):
            context.user_data[AWAITING_PLAYER_KEY] = awaiting
            await message.reply_text(
                "یه <b>اسم</b> برای کایجو بفرست (نه دستور)، یا «لغو» رو بزن.",
                parse_mode="HTML", reply_markup=cancel_kb,
            )
            return
        try:
            from game.naming import validate_name

            name = validate_name(text)
            creature, cost = await run_db(_rename_prompt_sync, update.effective_user, creature_id)
        except GameError as exc:
            context.user_data[AWAITING_PLAYER_KEY] = awaiting
            await message.reply_text(
                f"⚠️ {exc}\n<i>یه اسم دیگه بفرست یا «لغو» رو بزن.</i>",
                parse_mode="HTML", reply_markup=cancel_kb,
            )
            return
        # valid name → stop awaiting text and ask for confirmation (callback-based, so
        # no more stray messages get captured as the name)
        context.user_data.pop(AWAITING_PLAYER_KEY, None)
        context.user_data["pending_kaiju_rename"] = {
            "creature_id": creature_id, "name": name, "origin": origin,
        }
        cost_line = (
            "🎁 رایگان (اولین نام‌گذاری)" if cost == 0
            else f"{get_emoji('diamond')} هزینه: <b>{cost}</b> الماس"
        )
        await message.reply_text(
            f"✏️ <b>تأیید نام‌گذاری</b>\n"
            f"اسم جدید: «<b>{name}</b>»\n"
            f"🧬 نژاد: <b>{creature.name}</b>\n"
            f"{cost_line}\n\n"
            f"تأیید می‌کنی؟",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [btn("تأیید و ثبت", emoji_key="btn_confirm", style=CONFIRM, callback_data=f"kaiju_rename_ok:{creature_id}")],
                [btn("لغو", emoji_key="btn_cancel", style=DANGER, callback_data=f"kaiju_rename_cancel:{creature_id}:{origin}")],
            ]),
        )
        return

    if action == "alliance_create":
        from game.alliance import ALLIANCE_CREATE_COST

        name = text.strip()
        try:
            await run_db(_alliance_validate_sync, update.effective_user, name)
        except GameError as exc:
            context.user_data[AWAITING_PLAYER_KEY] = awaiting  # keep waiting for a valid name
            await message.reply_text(str(exc), parse_mode="HTML")
            return
        # valid + affordable — stash the name and take a FINAL confirmation before charging
        context.user_data["pending_alliance_name"] = name
        await message.reply_text(
            f"{get_emoji('alliance')} اتحاد <b>{name}</b>\n\n"
            f"ساخت این اتحاد <code>{ALLIANCE_CREATE_COST:,}</code> طلا از حسابت کم می‌کنه. تأیید می‌کنی؟",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                btn("تأیید و پرداخت", emoji_key="btn_confirm",
                    style=CONFIRM, callback_data="ally_create_confirm"),
                btn("لغو", emoji_key="btn_cancel", style=DANGER, callback_data="ally_create_cancel"),
            ]]),
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
            await message.reply_text(alert_text(exc, 3500))
            return
        await message.reply_text(
            f"✅ حداقل قدرت عضویت روی <code>{al.min_join_power:,}</code> تنظیم شد.", parse_mode="HTML",
            reply_markup=back_only_keyboard("menu:alliance_info", "بازگشت به اتحاد"),
        )
        return

    if action in ("ally_kick", "ally_deputy"):
        digits = text.strip().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
        if not digits.isdigit():
            context.user_data[AWAITING_PLAYER_KEY] = awaiting
            await message.reply_text("⚠️ آیدیِ عددی عضو رو بفرست (کد جلوی اسمش توی لیست اعضا).")
            return

        if action == "ally_kick":
            try:
                name = await run_db(_kick_preview_sync, update.effective_user, int(digits))
            except GameError as exc:
                await message.reply_text(
                    alert_text(exc, 3500), reply_markup=back_only_keyboard("menu:alliance_info", "بازگشت به اتحاد")
                )
                return
            await message.reply_text(
                f"🥾 <b>{name}</b> (<code>{digits}</code>) از اتحاد اخراج بشه؟",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[
                    btn("بله، اخراج کن", emoji_key="btn_confirm", style=DANGER, callback_data=f"ally_kick_ok:{digits}"),
                    btn("نه", emoji_key="btn_cancel", style=NAV, callback_data="menu:alliance_info"),
                ]]),
            )
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
            await message.reply_text(alert_text(exc, 3500))
            return
        if action == "ally_kick":
            await message.reply_text(f"🥾 <b>{result['name']}</b> از اتحاد حذف شد.", parse_mode="HTML")
        else:
            await message.reply_text(
                f"🎖 <b>{result['name']}</b> حالا قائم‌مقام اتحاده.", parse_mode="HTML",
                reply_markup=back_only_keyboard("menu:alliance_info", "بازگشت به اتحاد"),
            )
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
        await message.reply_text(
            f"💰 <code>{int(text):,}</code> {get_emoji('coin')} طلا به خزانه‌ی اتحاد واریز بشه؟\n"
            "<i>طلای واریزشده به خزانه دیگه قابل برداشت نیست.</i>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                btn("بله، واریز کن", emoji_key="btn_confirm", style=CONFIRM, callback_data=f"ally_dep_ok:{int(text)}"),
                btn("نه", emoji_key="btn_cancel", style=NAV, callback_data="menu:alliance_info"),
            ]]),
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
    lines = [f"{get_emoji('trophy')} <b>برترین اتحادها</b>", "━━━━━━━━━━━━━━━━━━━━"]
    for i, r in enumerate(ranked, start=1):
        rank = medals[i - 1] if i <= 3 else f"<code>{i}.</code>"
        lines.append(f"{rank} <b>{r['alliance'].name}</b>\n   💪 قدرت: <code>{r['power']:,}</code>\n   👥 اعضا: <code>{r['member_count']}</code>")
    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")


async def alliance_league_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """🏰 لیگ اتحادها — top alliances by power, with the weekly reward each rank pays
    its members. Rewards are handed out automatically at the weekly season reset."""
    from game.alliance import ALLIANCE_LEAGUE_REWARD_BY_RANK

    ranked = await run_db(_alliance_top_sync)
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}

    def _rw(rw):
        return f"<code>{rw['diamonds']}</code>💎 + <code>{rw['coins']:,}</code>🪙" if rw else "—"

    lines = [
        "🏰 <b>لیگ اتحادها</b>",
        "<blockquote>جوایز پایان هفته به تمام اعضای ۱۰ اتحاد برتر تعلق می‌گیرد.</blockquote>",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
    ]
    if not ranked:
        lines.append("<i>هنوز هیچ اتحادی ساخته نشده.</i>")
    for i, r in enumerate(ranked, start=1):
        rw = ALLIANCE_LEAGUE_REWARD_BY_RANK.get(i)
        name = r["alliance"].name
        power, members = r["power"], r["member_count"]
        if i <= 3:
            lines.append(f"{medals[i]} <b>{name}</b>")
            lines.append(f"   💪 قدرت: <code>{power:,}</code>")
            lines.append(f"   👥 اعضا: <code>{members}</code>")
            lines.append(f"   🎁 جایزه: {_rw(rw)}")
            lines.append("")
        else:
            if i == 4:
                lines.append("━━━━━━━━━━━━━━━━━━━━")
            lines.append(f"<code>{i}.</code> <b>{name}</b>")
            lines.append(f"   💪 قدرت: <code>{power:,}</code>")
            lines.append(f"   👥 اعضا: <code>{members}</code>")
            lines.append(f"   🎁 جایزه: {_rw(rw)}")
            lines.append("")
    from game.media import get_feature_image_path
    photo = get_feature_image_path("alliance_league")
    await send_screen(
        update, "\n".join(lines), photo=photo, parse_mode="HTML",
        reply_markup=back_only_keyboard("menu:hub_city", "بازگشت به شهر"),
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
        await update.message.reply_text(alert_text(exc, 3500))
        return
    await update.message.reply_text(
        f"{get_emoji('coin')} به خزانه‌ی <b>{alliance.name}</b> واریز شد!\n"
        f"خزانه فعلی: <code>{alliance.treasury_gold:,}</code> طلا",
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
            f"استفاده درست: <code>/heist اسم اتحاد</code> — روزی <code>{constants.HEIST_DAILY_ATTEMPTS}</code> بار مجازی، "
            f"<code>{int(constants.HEIST_STEAL_PERCENT * 100)}%</code> خزانه رو می‌بری اگه ببری.",
            parse_mode="HTML",
        )
        return
    try:
        result, target = await run_db(_heist_sync, update.effective_user, " ".join(context.args))
    except GameError as exc:
        await update.message.reply_text(alert_text(exc, 3500))
        return

    if result["defender_creature"] is not None:
        await update.message.reply_text(result["log_text"], parse_mode="HTML")

    if result["success"]:
        await update.message.reply_text(
            f"{get_emoji('celebrate')} <b>شبیخون موفق بود!</b>\n"
            f"{get_emoji('coin')} غارت: <code>+{result['stolen']:,}</code> طلا از خزانه‌ی <b>{target.name}</b> غارت شد و کامل به <b>خزانه‌ی اتحادت</b> واریز شد!",
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text(
            f"😔 نگهبان‌های <b>{target.name}</b> دفاع کردن و شبیخونت شکست خورد.", parse_mode="HTML"
        )


def _rank_sync(tg_user):
    """The global board ranks ALLIANCES by their treasury gold — the richest treasuries
    top the table, and the top 3 get a daily gold deposit (game.season.settle_daily_
    treasury). Replaces the old lab-level board."""
    from game.alliance import top_alliances_by_treasury

    user, _ = get_or_create_user(tg_user)
    ranked = top_alliances_by_treasury(limit=10)
    my_rank = None
    if user.alliance_id:
        all_ids = list(Alliance.objects.order_by("-treasury_gold", "id").values_list("id", flat=True))
        my_rank = next((i for i, aid in enumerate(all_ids, start=1) if aid == user.alliance_id), None)
        total = len(all_ids)
    else:
        total = Alliance.objects.count()
    return ranked, my_rank, total, user


async def rank(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from game.alliance import DAILY_TREASURY_REWARD_BY_RANK

    top10, my_rank, total, me_user = await run_db(_rank_sync, update.effective_user)
    if not top10:
        await send_screen(update, "هنوز هیچ اتحادی ساخته نشده.", reply_markup=back_only_keyboard())
        return
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    d1, d2, d3 = (DAILY_TREASURY_REWARD_BY_RANK[r] for r in (1, 2, 3))
    lines = [
        "🏦 <b>رتبه‌بندی خزانه اتحادها</b>",
        "",
        "<blockquote>⚠️ رتبه‌بندی بر اساس میانگین خزانه اتحاد در طول ۲۴ ساعت (از ساعت ۰۰:۰۰ تا ۲۴:۰۰) محاسبه می‌شود.</blockquote>",
        "",
        "🎁 <b>پاداش روزانه به خزانه:</b>",
        f"🥇 <code>{d1:,}</code>🪙",
        f"🥈 <code>{d2:,}</code>🪙",
        f"🥉 <code>{d3:,}</code>🪙",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
    ]
    for i, r in enumerate(top10, start=1):
        name = r["alliance"].name
        if i <= 3:
            reward = DAILY_TREASURY_REWARD_BY_RANK.get(i)
            lines.append(f"{medals[i]} <b>{name}</b>")
            lines.append(f"   🏦 خزانه: <code>{r['treasury']:,}</code> طلا")
            lines.append(f"   👥 اعضا: <code>{r['member_count']}</code>")
            lines.append(f"   🎁 پاداش: <code>{reward:,}</code>🪙")
            lines.append("")
        else:
            if i == 4:
                lines.append("━━━━━━━━━━━━━━━━━━━━")
            lines.append(f"<code>{i}.</code> <b>{name}</b>")
            lines.append(f"   🏦 خزانه: <code>{r['treasury']:,}</code>")
            lines.append(f"   👥 اعضا: <code>{r['member_count']}</code>")
            lines.append("")
    if my_rank is not None:
        lines.append(f"📍 رتبه‌ی اتحاد تو: <b><code>{my_rank}</code></b> از <code>{total}</code>")
    from game.media import get_feature_image_path
    photo = get_feature_image_path("rank")
    await send_screen(update, "\n".join(lines), photo=photo, parse_mode="HTML",
                      reply_markup=back_only_keyboard("menu:hub_city", "بازگشت به شهر"))


def _raid_rank_sync():
    from game.raid import RAID_WEEKLY_REWARD_BY_RANK, alliance_raid_ranking

    return alliance_raid_ranking(limit=10), RAID_WEEKLY_REWARD_BY_RANK


async def raid_rank_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """🐲 رتبه‌بندی رید — alliances ranked by raid level. At week's end the 10 best
    raiders of each of the top-3 alliances split that rank's reward equally."""
    rows, reward_by_rank = await run_db(_raid_rank_sync)
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    lines = [
        "🐲 <b>رتبه‌بندی رید اتحادها</b>",
        "",
        "<blockquote>رتبه‌بندی بر اساس <b>سطح رید اتحاد</b>. آخر هفته، جایزهٔ هر رتبه به‌طور مساوی بین <b>۱۰ رِیدرِ برترِ</b> اون اتحاد پخش می‌شه.</blockquote>",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
    ]
    if not rows:
        lines.append("<i>هنوز هیچ اتحادی رید نکرده.</i>")
    for r in rows:
        rank = r["rank"]
        name = r["alliance"].name
        reward = reward_by_rank.get(rank)
        rw = f"   🎁 جایزه: <code>{reward['diamonds']}</code>💎 + <code>{reward['coins']:,}</code>🪙 (بین ۱۰ نفر)" if reward else ""
        if rank <= 3:
            lines.append(f"{medals[rank]} <b>{name}</b>")
            lines.append(f"   🐉 سطح رید: <code>{r['raid_level']}</code>")
            lines.append(f"   👥 اعضا: <code>{r['member_count']}</code>")
            if rw:
                lines.append(rw)
            lines.append("")
        else:
            if rank == 4:
                lines.append("━━━━━━━━━━━━━━━━━━━━")
            lines.append(f"<code>{rank}.</code> <b>{name}</b>")
            lines.append(f"   🐉 سطح رید: <code>{r['raid_level']}</code>")
            lines.append(f"   👥 اعضا: <code>{r['member_count']}</code>")
            lines.append("")
    from game.media import get_feature_image_path

    raid_photo = get_feature_image_path("raid_rank")
    await send_screen(update, "\n".join(lines), photo=raid_photo, parse_mode="HTML",
                      reply_markup=back_only_keyboard("menu:hub_city", "بازگشت به شهر"))


def _profile_sync(tg_user):
    from django.db.models import Sum

    from bio_lab.models import DailyActionLog, DuelLog, RaidDamageLog

    user, _ = get_or_create_user(tg_user)
    duel_wins = DuelLog.objects.filter(winner=user).count()
    total_raid_damage = RaidDamageLog.objects.filter(user=user).aggregate(t=Sum("damage"))["t"] or 0
    total_hunts = DailyActionLog.objects.filter(user=user, action="hunt").aggregate(t=Sum("count"))["t"] or 0
    creatures_owned = Creature.objects.filter(owner=user).count()
    from game import titles

    return user, {
        "duel_wins": duel_wins,
        "total_raid_damage": total_raid_damage,
        "total_hunts": total_hunts,
        "creatures_owned": creatures_owned,
        # precompute the title label HERE (sync) — it hits the DB (codex count etc.), which
        # would raise SynchronousOnlyOperation if left to the async render path.
        "title_label": titles.label(user),
    }


def _profile_text_and_keyboard(user, stats) -> tuple[str, InlineKeyboardMarkup]:
    rename_cost = constants.lab_rename_cost(user.lab_renames)
    notif_label = "اعلان‌ها: فعال" if user.notifications_on else "اعلان‌ها: خاموش"
    lines = [
        f"{get_emoji('profile')} <b>آزمایشگاه {lab_display(user)}</b>{stats.get('title_label', '')}",
        "━━━━━━━━━━━━━━━━━━━━",
        f"<blockquote>{lab_level_line(user)}</blockquote>",
        "",
        f"📅 عضو از: <code>{timezone.localtime(user.created_at).strftime('%Y-%m-%d')}</code>",
        f"🔥 ورود پشت‌سرهم: <code>{user.login_streak}</code> روز",
        f"{get_emoji('creature')} موجودات ساخته‌شده: <code>{stats['creatures_owned']:,}</code>",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "📊 <b>آمار نبردها:</b>",
        f"{get_emoji('battle')} نبردهای گروهی برده: <code>{stats['duel_wins']:,}</code>",
        f"{get_emoji('hunt')} شکارهای انجام‌شده: <code>{stats['total_hunts']:,}</code>",
        f"{get_emoji('raid_boss')} کل دمیج رید باس‌ها: <code>{stats['total_raid_damage']:,}</code>",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        wallet_line(user),
        "",
        "<blockquote>✏️ تغییر نام آزمایشگاه: "
        + ("<b>رایگان</b> (بار اول)" if rename_cost == 0 else f"<code>{rename_cost}</code> الماس") + "</blockquote>",
    ]
    rows = [
        [btn("لقب‌ها", emoji_key="btn_titles", style=NAV, callback_data="menu:titles")],
        [btn("تغییر نام آزمایشگاه", emoji_key="btn_edit", style=SHOP, callback_data="lab_rename")],
        [btn(notif_label, emoji_key="btn_settings", style=NAV, callback_data="notif:menu")],
        [btn("راهنمای بازی", emoji_key="btn_report", style=NAV, callback_data="guide:home")],
        [back_btn("menu:me", "بازگشت به منوی اصلی")],
    ]
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def _notif_menu_sync(tg_user, toggle: str | None = None):
    """The «اعلان‌ها» screen: the master switch plus one switch per kind of DM
    (bot.handlers.notify.NOTIFY_CATEGORIES). `toggle` = "all" or a category key."""
    from bot.handlers.notify import NOTIFY_CATEGORIES

    user, _ = get_or_create_user(tg_user)
    off = {c for c in (user.notify_off or "").split(",") if c in NOTIFY_CATEGORIES}
    if toggle == "all":
        user.notifications_on = not user.notifications_on
        user.save(update_fields=["notifications_on"])
    elif toggle in NOTIFY_CATEGORIES:
        off.symmetric_difference_update({toggle})
        user.notify_off = ",".join(sorted(off))
        user.save(update_fields=["notify_off"])
    lines = [
        "🔔 <b>اعلان‌ها</b>",
        _CARD_DIV,
        "<blockquote>انتخاب کن ربات برای چی بهت پیام بده. اعلان‌های هم‌زمان توی یک پیام می‌آن "
        "و رویدادها و یادآوری‌ها روزی حداکثر ۴ تا هستن.</blockquote>",
    ]
    rows = [[btn("همه‌ی اعلان‌ها: " + ("روشن ✅" if user.notifications_on else "خاموش ❌"),
                 style=CONFIRM if user.notifications_on else DANGER, callback_data="notif:all")]]
    if user.notifications_on:
        for key, (title, desc) in NOTIFY_CATEGORIES.items():
            on = key not in off
            lines.append(f"{title} — <i>{desc}</i>")
            rows.append([btn(f"{title}: {'روشن ✅' if on else 'خاموش ❌'}", style=NAV, callback_data=f"notif:t:{key}")])
    else:
        lines.append("<i>همه‌ی اعلان‌ها خاموشه؛ هیچ پیامی از ربات نمی‌گیری.</i>")
    rows.append([back_btn("menu:profile", "بازگشت")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def notif_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    parts = query.data.split(":")
    toggle = "all" if parts[1] == "all" else (parts[2] if parts[1] == "t" and len(parts) > 2 else None)
    text, keyboard = await run_db(_notif_menu_sync, update.effective_user, toggle)
    await query.answer("ذخیره شد." if toggle else None)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


def _profile_render_sync(tg_user):
    """Whole profile render in SYNC context — titles.label / wallet_line hit the DB, so
    building the text+keyboard must not happen on the event loop."""
    user, stats = _profile_sync(tg_user)
    return _profile_text_and_keyboard(user, stats)


async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text, keyboard = await run_db(_profile_render_sync, update.effective_user)
    from game.media import get_feature_image_path
    photo = get_feature_image_path("profile")
    await send_screen(update, text, photo=photo, parse_mode="HTML", reply_markup=keyboard)


def _notif_toggle_sync(tg_user):
    user, stats = _profile_sync(tg_user)
    user.notifications_on = not user.notifications_on
    user.save(update_fields=["notifications_on"])
    return user, stats


def _notif_toggle_render_sync(tg_user):
    user, stats = _notif_toggle_sync(tg_user)
    text, keyboard = _profile_text_and_keyboard(user, stats)
    return user.notifications_on, text, keyboard


async def notif_toggle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    notif_on, text, keyboard = await run_db(_notif_toggle_render_sync, update.effective_user)
    await query.answer("🔔 اعلان‌ها روشن شد" if notif_on else "🔕 اعلان‌ها خاموش شد")
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
        f"✏️ <b>تغییر اسم آزمایشگاه</b>\n\n"
        f"{get_emoji('diamond')} هزینه: <code>{cost}</code> الماس\n"
        f"💎 موجودی: <code>{user.diamonds}</code> الماس\n\n"
        f"<i>💡 هر بار که عوض کنی، دفعه‌ی بعد گرون‌تر می‌شه.</i>\n\n"
        f"اسم جدید رو بفرست (حداکثر <code>{LAB_NAME_MAX_LEN}</code> کاراکتر):",
        parse_mode="HTML",
        reply_markup=back_only_keyboard("menu:profile"),
    )


async def lab_rename_ok_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    pending = context.user_data.get("pending_lab_rename")
    if not pending:
        await query.answer("درخواست تغییر نام منقضی شده است.", show_alert=True)
        return
    try:
        user, cost, _newname = await run_db(_rename_lab_sync, update.effective_user, pending["name"])
    except GameError as exc:
        await query.answer(alert_text(exc), show_alert=True)
        return
    context.user_data.pop("pending_lab_rename", None)
    await query.answer("✅ نام آزمایشگاه تغییر کرد!")
    await safe_edit_message_text(
        query,
        f"✅ اسم آزمایشگاهت به «{lab_display(user)}» تغییر کرد.\n\n"
        f"(<code>{cost}</code> {get_emoji('diamond')} کم شد؛ هزینه تغییر بعدی <code>{constants.lab_rename_cost(user.lab_renames)}</code> الماس است)",
        parse_mode="HTML",
        reply_markup=back_only_keyboard("menu:profile"),
    )


async def lab_rename_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    context.user_data.pop("pending_lab_rename", None)
    await query.answer("تغییر نام لغو شد.")
    await safe_edit_message_text(
        query,
        "❌ تغییر نام آزمایشگاه لغو شد.",
        parse_mode="HTML",
        reply_markup=back_only_keyboard("menu:profile"),
    )


def main_menu_keyboard(quest: dict | None = None, is_owner: bool = False, hall_level: int | None = None, research_built: bool = False) -> InlineKeyboardMarkup:
    """The /menu command's keyboard — clean 4-hub layout with live story quest banner."""
    return creature_keyboard(quest, is_owner, _locked_actions_for(hall_level), research_built)


def _menu_lab_line_sync(tg_user):
    """Returns (user, lab-level line, main-hall level, research-built, quest)."""
    from game.buildings import main_hall_level
    from game import research, story

    user, _ = get_or_create_user(tg_user)
    quest = story.get_active_quest(user)
    return user, lab_level_line(user), main_hall_level(user), research.is_unlocked(user), quest


async def _show_main_menu(update) -> None:
    """Main menu with the lab level + story quest banner + 4 main hubs."""
    user, lab_line, hall_level, research_built, quest = await run_db(_menu_lab_line_sync, update.effective_user)
    is_admin = _is_admin_user(update.effective_user.id if update.effective_user else None)
    quest_txt = story_quest_card_text(quest)
    quest_block = f"<blockquote>{quest_txt.strip()}</blockquote>\n\n" if quest_txt.strip() else ""
    text = (
        f"📋 <b>منوی اصلی</b>\n\n"
        f"{quest_block}"
        f"{lab_line}\n\n"
        f"<i>یکی از بخش‌های زیر را انتخاب کنید:</i>"
    )
    await send_screen(
        update,
        text,
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(quest=quest, is_owner=is_admin, hall_level=hall_level, research_built=research_built),
    )


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _show_main_menu(update)


def _expedition_sync(tg_user):
    from game.expedition import can_join_expedition, EXPEDITION_DESTINATIONS
    user, _ = get_or_create_user(tg_user)
    can_join, reason = can_join_expedition(user)
    return user, can_join, reason, EXPEDITION_DESTINATIONS


async def expedition_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """⛵ اعزام کاروان گروهی — info and status panel in private chat."""
    user, can_join, reason, destinations = await run_db(_expedition_sync, update.effective_user)
    status_text = "🟢 <b>آماده اعزام (سهمیه امروز شما فعال است)</b>" if can_join else f"🔴 {reason}"
    lines = [
        "⛵ <b>اعزام کاروان‌های گروهی</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "<blockquote>کاروان یک مأموریت ماجراجویی مشترک (۲ تا ۴ نفره) در گروه‌های بازی است. "
        "با ارسال کلمه «اعزام» در گروه، کاروان تشکیل داده و غنائم ارزشمند استخراج کنید!</blockquote>",
        "",
        "📊 <b>وضعیت سهمیه امروز شما:</b>",
        status_text,
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "🗺 <b>مقصدهای اکتشافی کاروان:</b>",
    ]
    for d in destinations:
        lines.append(
            f"📍 <b>{d['name']}</b>\n"
            f"   {get_emoji('coin')} طلا: <code>+{d['gold'][0]:,}</code> تا <code>+{d['gold'][1]:,}</code>\n"
            f"   {get_emoji('dna')} دی‌ان‌ای: <code>+{d['dna'][0]:,}</code> تا <code>+{d['dna'][1]:,}</code>\n"
            f"   {get_emoji('diamond')} الماس: <code>+{d['diamonds'][0]}</code> تا <code>+{d['diamonds'][1]}</code>"
        )
    rows = []
    group_link = botconfig.get_group_link()
    if group_link is not None:
        url, title = group_link
        rows.append([btn(f"رفتن به گروه برای اعزام ({title})", emoji_key="btn_exp_launch", style=PRIMARY, url=url)])
    rows.append([back_btn("menu:hub_battle", "بازگشت")])
    from game.media import get_feature_image_path
    photo = get_feature_image_path("expedition")
    await send_screen(update, "\n".join(lines), photo=photo, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))


async def alliance_war_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """⚔️ جنگ‌های اتحاد — Choose between 1-day war and weekly war."""
    info = await run_db(_alliance_info_sync, update.effective_user)
    if info is None:
        await send_screen(
            update,
            f"{get_emoji('alliance')} برای شرکت در جنگ اتحاد، ابتدا باید عضو یک اتحاد شوید.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [btn("پیوستن یا ساخت اتحاد", emoji_key="btn_alliance", style=PRIMARY, callback_data="menu:alliance_info")],
                [back_btn("menu:hub_battle", "بازگشت")],
            ]),
        )
        return
    lines = [
        f"{get_emoji('war')} <b>جنگ‌های اتحاد</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"{get_emoji('shield')} اتحاد شما: <b>{info['name']}</b>",
        f"{get_emoji('power')} قدرت کل: <code>{info['power']:,}</code>",
        "",
        "<blockquote>یکی از حالت‌های نبرد اتحاد را انتخاب کنید:</blockquote>",
    ]
    rows = [
        [btn("جنگ یک‌روزه اتحاد", emoji_key="btn_war", style=BATTLE, callback_data="ally_war1d")],
        [btn("لیگ و جنگ هفتگی اتحاد", emoji_key="btn_war", style=BATTLE, callback_data="ally_war")],
        [btn("ساختمان‌ها و پرک‌های اتحاد", emoji_key="btn_buildings", style=BUILD, callback_data="ally_perks")],
        [back_btn("menu:hub_battle", "بازگشت")],
    ]
    from game.media import get_feature_image_path
    photo = get_feature_image_path("war") or get_feature_image_path("alliance")
    await send_screen(update, "\n".join(lines), photo=photo, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))


def _workers_overview_sync(tg_user):
    from game.buildings import get_or_create_buildings
    from bot.handlers.buildings import (
        assigned_creatures,
        free_creatures,
        worker_bonus,
        worker_slots,
    )
    user, _ = get_or_create_user(tg_user)
    blds = get_or_create_buildings(user)
    producers = []
    for b in blds:
        slots = worker_slots(b)
        if slots > 0:
            workers = assigned_creatures(b)
            producers.append({
                "id": b.id,
                "type": b.building_type,
                "label": constants.BUILDING_LABELS[b.building_type],
                "level": b.level,
                "workers_count": len(workers),
                "slots": slots,
                "bonus_pct": worker_bonus(b) * 100,
            })
    free_cnt = len(free_creatures(user))
    return user, producers, free_cnt


async def workers_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """👷 مدیریت کارگران پایگاه — Overview of worker allocations across production buildings."""
    user, producers, free_cnt = await run_db(_workers_overview_sync, update.effective_user)
    lines = [
        f"{get_emoji('worker')} <b>مدیریت کارگران پایگاه</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"<blockquote>استقرار کایجوها در سازه‌های تولیدی، نرخ تولید طلا، دی‌ان‌ای و انرژی را تا <b>+۳۰۰٪</b> افزایش می‌دهد.</blockquote>",
        "",
        f"{get_emoji('paw')} کایجوهای آماده به کار (آزاد): <code>{free_cnt}</code> عدد",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        f"{get_emoji('building')} <b>وضعیت سازه‌های تولیدی:</b>",
    ]
    rows = []
    for p in producers:
        status = f"<code>{p['workers_count']}/{p['slots']}</code> کارگر (+<code>{p['bonus_pct']:.0f}%</code>)"
        lines.append(f"▫️ <b>{p['label']}</b> (سطح <code>{p['level']}</code>): {status}")
        rows.append([btn(f"تنظیم کارگران {p['label']}", emoji_key="btn_workers", style=BUILD, callback_data=f"bld_workers:{p['id']}")])
    rows.append([back_btn("menu:hub_base", "بازگشت")])
    from game.media import get_feature_image_path
    photo = get_feature_image_path("workers") or get_feature_image_path("buildings")
    await send_screen(update, "\n".join(lines), photo=photo, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))


def _vault_overview_sync(tg_user):
    from game.buildings import (
        get_or_create_buildings,
        pending_amount,
        produces,
        storage_cap,
    )
    user, _ = get_or_create_user(tg_user)
    blds = get_or_create_buildings(user)
    vault_info = []
    total_gold_pending = 0
    total_dna_pending = 0
    for b in blds:
        if b.level > 0 and produces(b.building_type):
            cfg = constants.BUILDING_PRODUCTION[b.building_type]
            res_type = cfg["resource"]
            amt = pending_amount(b)
            cap = storage_cap(b)
            if res_type == "coins":
                total_gold_pending += amt
            elif res_type == "dna":
                total_dna_pending += amt
            vault_info.append({
                "id": b.id,
                "label": constants.BUILDING_LABELS[b.building_type],
                "resource": res_type,
                "amount": amt,
                "cap": cap,
            })
    return user, vault_info, total_gold_pending, total_dna_pending


async def vault_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """🏦 خزانه و دخل پایگاه — Resource accumulation & collection dashboard."""
    user, vault_info, tot_gold, tot_dna = await run_db(_vault_overview_sync, update.effective_user)
    lines = [
        f"{get_emoji('vault')} <b>خزانه و دخل تولید پایگاه</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"{wallet_line(user)}",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        f"{get_emoji('biocrate')} <b>موجودی مخازن آماده برداشت:</b>",
        f"{get_emoji('coin')} طلای انباشته: <code>+{tot_gold:,}</code>",
        f"{get_emoji('dna')} دی‌ان‌ای انباشته: <code>+{tot_dna:,}</code>",
        "",
        "<blockquote>برای برداشت محصول، سازه مورد نظر را انتخاب کنید:</blockquote>",
    ]
    rows = []
    for v in vault_info:
        r_emoji = get_emoji("coin") if v["resource"] == "coins" else get_emoji("dna")
        rows.append([btn(f"{r_emoji} {v['label']} (+{v['amount']:,})", emoji_key="btn_vault", style=CONFIRM, callback_data=f"bld_pick:{v['id']}")])
    rows.append([btn("مبادله طلا و DNA (صرافی)", emoji_key="btn_exchange", style=SHOP, callback_data="menu:exchange")])
    rows.append([back_btn("menu:hub_base", "بازگشت")])
    from game.media import get_feature_image_path
    photo = get_feature_image_path("vault") or get_feature_image_path("buildings")
    await send_screen(update, "\n".join(lines), photo=photo, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))


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
    "research": research_panel,
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
    "subscription": subscription_panel,
    "arena_chests": arena_chests_panel,
    "shield_shop": shield_shop_panel,
    "item_shop": item_shop_panel,
    "gold_shop": gold_shop_panel,
    "exchange": exchange_panel,
    "equip_exchange": equip_exchange_panel,
    "casino": casino_panel,
    "titles": titles_panel,
    "wheel": wheel_cmd,
    "alliance_info": alliance_info_cmd,
    "rank": rank,
    "raid_rank": raid_rank_panel,
    "admin": admin_cmd,
    "profile": profile,
    "balance": balance,
    "guide": guide_panel,
    "mugen_tower": mugen_panel,
    "dispatch": dispatch_panel,
    "worldboss": worldboss_panel,
    "tournament": tournament_panel,
    "festival": festival_panel,
    "today": today_panel,
    "blackmarket": blackmarket_panel,
    "buy_open": buy_open_callback,
    "expedition": expedition_panel,
    "alliance_war": alliance_war_panel,
    "workers": workers_panel,
    "vault": vault_panel,
}


# Typing a group trigger word in a *private* chat opens the matching menu panel,
# so the same simple words work in the DM as in a group. Group-only combat words
# (raid/attack/duel/guardian…) and anything unmapped fall back to the main menu.
_KEYWORD_TO_MENU = {
    "creature": "me", "equipment": "inventory", "collection": "collection",
    "upgrade": "upgrade", "lab": "profile", "hunt": "hunt", "arena": "arena",
    "mission": "missions", "fusion": "fusion", "breeding": "breeding",
    "reward": "hub_city", "alliance": "alliance_info", "leaderboard": "rank",
    "box": "biocrate", "mine": "buildings", "wheel": "wheel",
    "select": "collection", "help": "guide", "start": "guide",
    "casino": "casino", "exchange": "exchange", "balance": "balance",
    "vip": "subscription", "subscription": "subscription", "chests": "arena_chests",
    "blackmarket": "blackmarket", "mugen": "mugen_tower", "tower": "mugen_tower",
    "blacksmith": "blacksmith", "forge": "blacksmith", "buy": "buy_open", "purchase": "buy_open",
    "expedition": "expedition", "caravan": "expedition", "war": "alliance_war",
    "workers": "workers", "vault": "vault",
}


_REPLY_BUTTON_MAP = {
    # Hub 1: Battle
    "نبرد و ماجراجویی": "hub_battle",
    "نبرد": "hub_battle",
    "ماجراجویی": "hub_battle",
    # Hub 2: Creature
    "هیولا و تجهیزات": "hub_creature",
    "هیولا": "hub_creature",
    "تجهیزات": "hub_creature",
    # Hub 3: Base
    "پایگاه و منابع": "hub_base",
    "پایگاه": "hub_base",
    "منابع": "hub_base",
    # Hub 4: Shop
    "فروشگاه و بازار": "hub_shop",
    "فروشگاه": "hub_shop",
    "بازار": "hub_shop",
    # Hub 5: City
    "شهر و جوایز": "hub_city",
    "شهر، جوایز و کلوپ": "hub_city",
    "شهر جوایز و کلوپ": "hub_city",
    "شهر و خدمات": "hub_city",
    "شهر": "hub_city",
    "جوایز": "hub_city",
    "خدمات": "hub_city",
    # Profile & Me
    "ازمایشگاه من": "me",
    "آزمایشگاه من": "me",
    "پروفایل": "profile",
    "منو": "me",
    "منوی اصلی": "me",
    # Guide
    "راهنما": "guide",
    "راهنمای بازی": "guide",
    # Keyboard toggle
    "کیبورد": "keyboard",
    "دکمه ها": "keyboard",
    "دکمه‌ها": "keyboard",
    "بستن کیبورد": "hide_keyboard",
    "حذف کیبورد": "hide_keyboard",
    "مخفی کردن کیبورد": "hide_keyboard",
    "مخفی کیبورد": "hide_keyboard",
}


async def keyboard_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends / refreshes the ReplyKeyboardMarkup for the user."""
    await update.effective_message.reply_text(
        "🎮 <b>کیبورد دکمه‌های سریع فعال شد:</b>",
        parse_mode="HTML",
        reply_markup=get_main_reply_keyboard(),
    )


async def hide_keyboard_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Hides/removes the ReplyKeyboardMarkup."""
    from telegram import ReplyKeyboardRemove
    await update.effective_message.reply_text(
        "⌨️ <b>کیبورد مخفی شد.</b>\n<i>(برای فعال‌سازی مجدد، دستور /keyboard یا کلمه «کیبورد» را بفرستید)</i>",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )


async def route_private_keyword(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """If a plain private message is a recognised trigger word (incl. brand words
    like «کایجو»/«ربات» or Reply Keyboard buttons), open the matching panel."""
    message = update.effective_message
    if message is None or not message.text:
        return False

    norm_text = keywords.normalize(message.text)
    menu_action = _REPLY_BUTTON_MAP.get(norm_text)

    if menu_action == "keyboard":
        await keyboard_cmd(update, context)
        return True

    if menu_action == "hide_keyboard":
        await hide_keyboard_cmd(update, context)
        return True

    if not menu_action:
        action = keywords.match(message.text)
        if action is None:
            # a newcomer whose «send your lab name» prompt was lost (bot restart): their
            # text used to be ignored with no hint — treat it as the name
            if update.effective_chat is not None and update.effective_chat.type == "private" \
                    and await run_db(_needs_lab_name_sync, update.effective_user):
                context.user_data[AWAITING_PLAYER_KEY] = {"action": "set_lab_name"}
                await capture_player_text_reply(update, context)
                return True
            return False
        menu_action = _KEYWORD_TO_MENU.get(action, "")

    if menu_action.startswith("hub_"):
        hall_level = await run_db(_hall_level_sync, update.effective_user)
        title, keyboard = _hub_keyboard(menu_action, _locked_actions_for(hall_level))
        from game.media import get_feature_image_path
        photo_path = get_feature_image_path(menu_action)
        await send_screen(
            update, f"{title}\n\n<i>یکی از بخش‌های زیر را انتخاب کنید:</i>", photo=photo_path, parse_mode="HTML", reply_markup=keyboard
        )
        return True

    handler = _MENU_ACTIONS.get(menu_action)
    if handler is None:
        # group-only combat word or brand fallback → just show the main menu
        await _show_main_menu(update)
        return True

    # same unlock gate as the menu buttons, so a keyword can't bypass it
    req = SECTION_HALL_REQ.get(menu_action)
    if req is not None:
        hall = await run_db(_hall_level_sync, update.effective_user)
        if hall < req:
            await update.effective_message.reply_text(
                f"🔒 این بخش از سطح {req} «تالار مِهر» باز می‌شه. اول تالار مِهرت رو ارتقا بده."
            )
            return True

    await handler(update, context)
    return True


def _hall_level_sync(tg_user) -> int:
    from game.buildings import main_hall_level

    user, _ = get_or_create_user(tg_user)
    return main_hall_level(user)


def _hall_gate_sync(tg_user) -> tuple[int, str | None]:
    """(main-hall level, the action the active story quest unlocks regardless of it)."""
    from game import story
    from game.buildings import main_hall_level

    user, _ = get_or_create_user(tg_user)
    return main_hall_level(user), story.active_cta_action(user)


FROM_TODAY_KEY = "from_today"
FROM_TODAY_SECONDS = 20 * 60  # how long «I came here from امروز» is remembered


async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    action = query.data.split(":", 1)[1]
    # «📋 امروز» opens sections with `tdy:<action>` instead of `menu:<action>`. Every
    # section's own «بازگشت» points at its hub (hunt → نبرد, missions → شهر, …), which
    # threw a player who came from «امروز» into a category they never opened. So we
    # remember the origin, and the next «back to a hub» lands on «امروز» instead.
    import time as _time

    if query.data.startswith("tdy:"):
        context.user_data[FROM_TODAY_KEY] = _time.time()
    elif action in ("me", "today"):
        context.user_data.pop(FROM_TODAY_KEY, None)
    elif action.startswith(("hub_", "cat_")):
        came = context.user_data.pop(FROM_TODAY_KEY, None)
        if came is not None and _time.time() - came < FROM_TODAY_SECONDS:
            action = "today"
    # Navigating away (incl. every «انصراف» that routes to a menu) cancels a pending
    # text prompt — otherwise the NEXT plain message was still consumed by it (a later
    # number got deposited into the treasury, or kicked a member by id).
    context.user_data.pop(AWAITING_PLAYER_KEY, None)
    if action in ("upgrade", "collection"):  # entering a list from the menu starts at page 1
        context.user_data.pop("upg_loc" if action == "upgrade" else "coll_loc", None)
    # The full DM menu must never open inside a group — walking up categories/root there
    # would expose the whole private menu. But group-reachable panels (alliance sub-panels,
    # the ticket exchange) legitimately have «بازگشت» buttons. So in a group we resolve
    # ONLY the alliance menu (the one safe in-group destination) and block everything else.
    chat = update.effective_chat
    if chat is not None and chat.type in ("group", "supergroup"):
        if action in ("alliance_info", "cat_social", "hub_battle"):
            from bot.gates import is_card_owner

            if not is_card_owner(update):
                await query.answer("این کارت مال یه بازیکن دیگه‌ست — خودت «اتحاد» رو بفرست.", show_alert=True)
                return
            await query.answer()
            await _show_group_alliance(update, edit=True)
            return
        await query.answer("منوی کامل ربات فقط توی پیوی ربات بازه — همون‌جا /start بزن.", show_alert=True)
        return
    # one cheap read drives BOTH the lock gate and the lock icons in submenus
    hall_level, story_action = await run_db(_hall_gate_sync, update.effective_user)
    # the authoritative gate: a section that hasn't unlocked yet never opens, even if
    # a stale keyboard still shows it — the player is told which hall level it needs.
    # (Exception: the section the active story quest sends the player to.)
    req = SECTION_HALL_REQ.get(action)
    if req is not None and hall_level < req and action != story_action:
        await query.answer(
            f"🔒 این بخش از سطح {req} «تالار مِهر» باز می‌شه (الان سطح {hall_level}). "
            "اول تالار مِهرت رو ارتقا بده.",
            show_alert=True,
        )
        return
    await query.answer()
    if action.startswith("hub_") or action.startswith("cat_"):
        hub_key = action[4:] if action.startswith("cat_") else action
        title, keyboard = _hub_keyboard(hub_key, _locked_actions_for(hall_level), hide_locked=hall_level <= 1)
        from game.media import get_feature_image_path
        photo_path = get_feature_image_path(hub_key[:-5] if hub_key.endswith("_more") else hub_key)
        await send_screen(update, title, photo=photo_path, parse_mode="HTML", reply_markup=keyboard)
        return
    handler = _MENU_ACTIONS.get(action)
    if handler is not None:
        await handler(update, context)


async def story_claim_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user, res = await run_db(_story_claim_sync, update.effective_user)
    if not res["success"]:
        await query.answer(str(res["msg"])[:200], show_alert=True)
        return
    await query.answer("🎉 پاداش مأموریت با موفقیت دریافت شد!")
    await me(update, context)


def _story_claim_sync(tg_user):
    from game import story
    user, _ = get_or_create_user(tg_user)
    res = story.claim_active_quest(user)
    return user, res


async def onboarding_hatch_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer("🥚 تخم کایجو در حال باز شدن...")
    user, creature, equipped_items = await run_db(_onboarding_hatch_sync, update.effective_user)
    from game.media import get_creature_image_path
    photo_path = get_creature_image_path(creature)
    stats = effective_stats(creature, equipped_items)
    text = (
        f"🎉 <b>کایجوی شما با موفقیت متولد شد!</b>\n\n"
        f"<blockquote>نام: <b>{creature.name}</b> | عنصر: {constants.element_label(creature.element)}\n"
        f"❤️ سلامت: <code>{stats['hp']:,}</code> | ⚔️ قدرت: <code>{stats['atk']:,}</code>\n"
        f"🛡 دفاع: <code>{stats['def']:,}</code> | ⚡ سرعت: <code>{stats['spd']:,}</code></blockquote>\n\n"
        f"<blockquote>این موجود برای رشد به تغذیه و تجربه نبرد نیاز دارد.\n"
        f"یک هیولای وحشی ضعیف در جنگل تاریک شناسایی شده است! برای اولین نبرد آماده‌ای؟ 👇</blockquote>"
    )
    keyboard = InlineKeyboardMarkup([
        [btn("اولین شکار در جنگل تاریک", emoji_key="btn_hunt", style=DANGER, callback_data="onboarding:hunt")]
    ])
    await send_screen(update, text, photo=photo_path, parse_mode="HTML", reply_markup=keyboard)


@transaction.atomic
def _onboarding_hatch_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    # lock the row: a double-tap used to hatch two starters (both active)
    user = type(user).objects.select_for_update().get(id=user.id)
    creature = get_active_creature(user)
    if creature is None:
        creature = create_starter_creature(user)
        for minutes, count in constants.STARTING_SPEEDUP_CARDS.items():
            grant_speedup_card(user, minutes, count=count)
    get_or_create_buildings(user)
    equipped_items = get_equipped_items(creature)
    return user, creature, equipped_items


async def onboarding_hunt_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer("⚔️ در حال نبرد در جنگل...")
    user, creature, loot = await run_db(_onboarding_hunt_sync, update.effective_user)
    if loot is None:  # already claimed (replayed button) → just go to the menu
        await me(update, context)
        return
    from game.media import get_feature_image_path
    photo_path = get_feature_image_path("hunt")
    text = (
        f"⚔️ <b>پیروزی در اولین شکار!</b>\n\n"
        f"<blockquote>غنائم به دست آمده از جنگل تاریک:\n"
        f"{get_emoji('coin')} طلا: <code>+{loot['coins']:,}</code>\n"
        f"{get_emoji('dna')} دی‌ان‌ای: <code>+{loot['dna']:,}</code>\n"
        f"{get_emoji('diamond')} الماس: <code>+{loot['diamonds']:,}</code></blockquote>\n\n"
        f"<blockquote>با این غنائم، کایجوی خودت رو ارتقا بده تا قدرت نبردش چند برابر بشه! 👇</blockquote>"
    )
    keyboard = InlineKeyboardMarkup([
        [btn("ارتقای سطح کایجو", emoji_key="btn_upgrade", style=CONFIRM, callback_data="onboarding:upgrade")]
    ])
    await send_screen(update, text, photo=photo_path, parse_mode="HTML", reply_markup=keyboard)


@transaction.atomic
def _onboarding_hunt_sync(tg_user):
    from bio_lab.models import DailyActionLog

    user, _ = get_or_create_user(tg_user)
    user = type(user).objects.select_for_update().get(id=user.id)
    creature = get_active_creature(user)
    loot = {"coins": 1000, "dna": 200, "diamonds": 20}
    # the tutorial loot is paid ONCE: only while onboarding is still open, and the
    # marker row (unique per user) stops replays of a kept/duplicated button
    _, first_time = DailyActionLog.objects.get_or_create(
        user=user, action="onboarding_loot", day="once", defaults={"count": 1}
    )
    if user.onboarding_completed or creature is None or not first_time:
        return user, creature, None
    user.coins += loot["coins"]
    user.dna_fragments += loot["dna"]
    user.diamonds += loot["diamonds"]
    user.save(update_fields=["coins", "dna_fragments", "diamonds"])
    DailyActionLog.objects.get_or_create(user=user, action="hunt", day="", defaults={"count": 1})
    return user, creature, loot


async def onboarding_upgrade_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer("✨ کایجو ارتقا یافت!")
    user, creature, equipped_items, hall_lvl, rsch_ok, quest = await run_db(_onboarding_upgrade_sync, update.effective_user)
    is_owner = _is_admin_user(update.effective_user.id if update.effective_user else None)
    from game.media import get_creature_image_path
    photo_path = get_creature_image_path(creature)
    
    card_txt = creature_card_text(user, creature, equipped_items, compact=bool(photo_path))
    quest_txt = story_quest_card_text(quest)
    quest_block = f"<blockquote>{quest_txt.strip()}</blockquote>\n\n" if quest_txt.strip() else ""
    text = (
        f"🎉 <b>تبریک! کایجوی شما به سطح ۲ رسید!</b>\n\n"
        f"<blockquote>آموزش اولیه با موفقیت تکمیل شد. اکنون کنترل کامل پایگاه و تمامی بخش‌های بازی در دستان شماست!</blockquote>\n\n"
        f"{quest_block}"
        f"{card_txt}"
    )
    await send_screen(
        update,
        text,
        photo=photo_path,
        parse_mode="HTML",
        reply_markup=creature_keyboard(quest, is_owner, _locked_actions_for(hall_lvl), rsch_ok),
    )


def _onboarding_upgrade_sync(tg_user):
    from game import story
    from game.buildings import main_hall_level
    from game import research

    user, _ = get_or_create_user(tg_user)
    creature = get_active_creature(user)
    # only during the tutorial (a kept button must not hand out free levels later), and
    # with the canonical level-2 stats instead of an ad-hoc ×1.2
    if creature and creature.level < 2 and not user.onboarding_completed:
        canon = constants.canonical_base_stats(creature.rarity, 2)
        creature.level = 2
        creature.base_hp, creature.base_atk = canon["base_hp"], canon["base_atk"]
        creature.base_def, creature.base_spd = canon["base_def"], canon["base_spd"]
        creature.save()
    user.onboarding_completed = True
    if user.story_step < 2:
        # the tutorial IS quests 0 and 1 (hatch + first hunt) — pay their rewards while
        # skipping them, so finishing the tutorial isn't worth less than abandoning it
        skipped = {"coins": 0, "dna": 0, "diamonds": 0}
        for q in story.STORY_QUESTS[user.story_step:2]:
            for k in skipped:
                skipped[k] += q["reward"].get(k, 0)
        type(user).objects.filter(pk=user.pk).update(
            coins=F("coins") + skipped["coins"],
            dna_fragments=F("dna_fragments") + skipped["dna"],
            diamonds=F("diamonds") + skipped["diamonds"],
        )
        user.coins += skipped["coins"]
        user.dna_fragments += skipped["dna"]
        user.diamonds += skipped["diamonds"]
        user.story_step = 2
    user.save(update_fields=["onboarding_completed", "story_step"])
    equipped_items = get_equipped_items(creature) if creature else []
    quest = story.get_active_quest(user)
    return user, creature, equipped_items, main_hall_level(user), research.is_unlocked(user), quest


async def _noop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Label-only buttons (page indicators) — just stop the loading spinner."""
    await update.callback_query.answer()


def register(application) -> None:
    application.add_handler(CallbackQueryHandler(_noop_callback, pattern=r"^noop$"))
    application.add_handler(CommandHandler("start", start, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("off", transfer_notify_off_cmd, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("on", transfer_notify_on_cmd, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("me", me, filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(story_claim_callback, pattern=r"^story_claim$"))
    application.add_handler(CallbackQueryHandler(onboarding_hatch_callback, pattern=r"^onboarding:hatch$"))
    application.add_handler(CallbackQueryHandler(onboarding_hunt_callback, pattern=r"^onboarding:hunt$"))
    application.add_handler(CallbackQueryHandler(onboarding_upgrade_callback, pattern=r"^onboarding:upgrade$"))
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
    application.add_handler(CommandHandler("rank", hall_gated("rank", rank), filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("profile", profile, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("balance", balance, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("menu", menu, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("keyboard", keyboard_cmd, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("buttons", keyboard_cmd, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("hide_keyboard", hide_keyboard_cmd, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("keyboard_off", hide_keyboard_cmd, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("nokeyboard", hide_keyboard_cmd, filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(menu_callback, pattern=r"^(menu|tdy):"))
    application.add_handler(CallbackQueryHandler(guide_page_callback, pattern=r"^guide:"))
    application.add_handler(CallbackQueryHandler(upgrade_pick_callback, pattern=r"^upg_pick:"))
    application.add_handler(CallbackQueryHandler(upgrade_fusion_gate_callback, pattern=r"^upg_fusion:"))
    application.add_handler(CallbackQueryHandler(upgrade_page_callback, pattern=r"^(upg_page:|upg_back$)"))
    application.add_handler(CallbackQueryHandler(creature_back_callback, pattern=r"^cr_back:\d+$"))
    application.add_handler(CallbackQueryHandler(lab_name_confirm_callback, pattern=r"^labname_(ok|no)$"))
    application.add_handler(CallbackQueryHandler(alliance_kick_confirm_callback, pattern=r"^ally_kick_ok:\d+$"))
    application.add_handler(CallbackQueryHandler(alliance_deposit_confirm_callback, pattern=r"^ally_dep_ok:\d+$"))
    application.add_handler(CallbackQueryHandler(heist_confirm_callback, pattern=r"^heist_ok:\d+$"))
    application.add_handler(CallbackQueryHandler(missions_page_callback, pattern=r"^mission_(page|tab):"))
    application.add_handler(CallbackQueryHandler(mission_box_callback, pattern=r"^mission_box:\d+$"))
    application.add_handler(CallbackQueryHandler(lab_rename_start_callback, pattern=r"^lab_rename$"))
    application.add_handler(CallbackQueryHandler(lab_rename_ok_callback, pattern=r"^lab_rename_ok$"))
    application.add_handler(CallbackQueryHandler(lab_rename_cancel_callback, pattern=r"^lab_rename_cancel$"))
    application.add_handler(CallbackQueryHandler(notif_toggle_callback, pattern=r"^notif_toggle$"))
    application.add_handler(CallbackQueryHandler(notif_menu_callback, pattern=r"^notif:(menu|all|t:[a-z]+)$"))
    application.add_handler(CallbackQueryHandler(equip_panel_callback, pattern=r"^upg_eq:"))
    application.add_handler(CallbackQueryHandler(equip_slot_callback, pattern=r"^upg_slot:"))
    application.add_handler(CallbackQueryHandler(equip_do_callback, pattern=r"^upg_(equip|unequip):"))
    application.add_handler(CallbackQueryHandler(upgrade_set_default_callback, pattern=r"^upg_default:"))
    application.add_handler(CallbackQueryHandler(upgrade_step_callback, pattern=r"^upg_step:\d+:\d+$"))
    application.add_handler(CallbackQueryHandler(feedcap_callback, pattern=r"^feedcap:"))
    application.add_handler(CallbackQueryHandler(hunt_go_callback, pattern=r"^hunt_go:"))
    application.add_handler(CallbackQueryHandler(hunt_encounter_callback, pattern=r"^henc:"))
    application.add_handler(CallbackQueryHandler(autohunt_start_callback, pattern=r"^autohunt_start$"))
    application.add_handler(CallbackQueryHandler(autohunt_amt_callback, pattern=r"^autohunt_amt:(all|half|custom)$"))
    application.add_handler(CallbackQueryHandler(autohunt_do_callback, pattern=r"^autohunt_do:\d+$"))
    application.add_handler(CallbackQueryHandler(hunt_next_callback, pattern=r"^hunt_next$"))
    application.add_handler(CallbackQueryHandler(hunt_swap_callback, pattern=r"^hunt_swap:"))
    application.add_handler(CallbackQueryHandler(hunt_swap_pick_callback, pattern=r"^hunt_swap_pick:"))
    application.add_handler(CallbackQueryHandler(collection_pick_callback, pattern=r"^coll_pick:"))
    application.add_handler(CallbackQueryHandler(collection_page_callback, pattern=r"^(coll_page:|coll_back$)"))
    application.add_handler(CallbackQueryHandler(collection_select_callback, pattern=r"^coll_select:"))
    application.add_handler(CallbackQueryHandler(kaiju_rename_callback, pattern=r"^kaiju_rename:\d+(:[cu])?$"))
    application.add_handler(CallbackQueryHandler(kaiju_rename_ok_callback, pattern=r"^kaiju_rename_ok:\d+$"))
    application.add_handler(CallbackQueryHandler(kaiju_rename_cancel_callback, pattern=r"^kaiju_rename_cancel:\d+:[cu]$"))
    application.add_handler(CallbackQueryHandler(devour_start_callback, pattern=r"^devour_start:\d+$"))
    application.add_handler(CallbackQueryHandler(devour_toggle_callback, pattern=r"^devour_tog:\d+:\d+$"))
    application.add_handler(CallbackQueryHandler(devour_select_all_callback, pattern=r"^devour_(all|none):\d+$"))
    application.add_handler(CallbackQueryHandler(devour_page_callback, pattern=r"^devour_page:\d+:\d+$"))
    application.add_handler(CallbackQueryHandler(devour_multi_callback, pattern=r"^devour_multi:\d+$"))
    application.add_handler(CallbackQueryHandler(devour_confirm_callback, pattern=r"^devour_ok:\d+$"))
    application.add_handler(CallbackQueryHandler(fusion_pick_a_callback, pattern=r"^fus_a:"))
    application.add_handler(CallbackQueryHandler(fusion_busy_callback, pattern=r"^fus_busy:\d+$"))
    application.add_handler(CallbackQueryHandler(fusion_rarity_callback, pattern=r"^fus_rarity:"))
    application.add_handler(CallbackQueryHandler(fusion_pick_b_callback, pattern=r"^fus_b:"))
    application.add_handler(CallbackQueryHandler(fusion_confirm_callback, pattern=r"^fus_confirm:"))
    application.add_handler(CallbackQueryHandler(alliance_create_callback, pattern=r"^ally_create$"))
    application.add_handler(CallbackQueryHandler(alliance_create_confirm_callback, pattern=r"^ally_create_confirm$"))
    application.add_handler(CallbackQueryHandler(alliance_create_cancel_callback, pattern=r"^ally_create_cancel$"))
    application.add_handler(CallbackQueryHandler(alliance_join_callback, pattern=r"^ally_join$"))
    application.add_handler(CallbackQueryHandler(alliance_browse_callback, pattern=r"^ally_browse:\d+$"))
    application.add_handler(CallbackQueryHandler(alliance_browse_join_callback, pattern=r"^ally_browse_join:\d+$"))
    application.add_handler(CallbackQueryHandler(alliance_search_callback, pattern=r"^ally_search$"))
    application.add_handler(CallbackQueryHandler(alliance_deposit_callback, pattern=r"^ally_deposit$"))
    application.add_handler(CallbackQueryHandler(alliance_top_callback, pattern=r"^ally_top$"))
    application.add_handler(CallbackQueryHandler(ally_raidtable_callback, pattern=r"^ally_raidtable$"))
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

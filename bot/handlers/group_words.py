"""Plain-word gameplay in group chats.

**This module owns the one and only text MessageHandler for groups.** Same rule
as bot/main.py's private-chat handler: PTB runs only the first matching handler
in a group, so a second text handler registered for GROUPS would silently never
fire. Anything that wants to react to ordinary group text dispatches from
`handle_group_text` below.

Cards rendered here carry buttons, and those buttons are **scoped to whoever
typed the word**. In a private chat a keyboard belongs to one person by
construction; in a group it's visible to everyone, so the presser's id is checked
against the id baked into the callback data. Without that, tapping «تجهیزات» on
someone else's card would quietly re-render it with your own gear.
"""

from telegram import InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import (CallbackQueryHandler, CommandHandler, ContextTypes,
                          MessageHandler, filters)

from bio_lab.repository import display_name, get_active_creature, get_or_create_group, get_or_create_user, lab_display, mention
from bot.buttons import BACK, BATTLE, BUILD, CONFIRM, NAV, PRIMARY, SHOP, btn
from bot.utils import run_db, safe_edit_message_text
from config import BOT_USERNAME
from game import constants, keywords, word_reward
from game.creature import GameError, combat_rating, effective_stats
from game.emoji import get_emoji
from game.energy import sync_energy
from game.equipment import bonus_text, get_equipped_items, slot_loadout
from game.lab import lab_bar, lab_progress

def _pm_button(label: str = "برو به پیوی ربات"):
    return btn(label, style=PRIMARY, url=f"https://t.me/{BOT_USERNAME}?start=group")


def _scoped(action: str, user_id: int) -> str:
    return f"grp:{action}:{user_id}"


def group_footer_keyboard(user_id: int, *, skip: str | None = None) -> InlineKeyboardMarkup:
    """The standard coloured keyboard hung under every group reply.

    Group answers used to be bare text, which made the group half of the game
    feel like a different, older product than the DM. These are the actions that
    are safe to run straight from a button in a shared chat: read-only cards plus
    the reward, all scoped to the presser. Anything that spends energy or picks a
    target still needs the word (or the DM), because a mis-tap in a group is
    public and irreversible.
    """
    row1 = [
        btn("هیولا", emoji_key="btn_creature", style=NAV, callback_data=_scoped("creature", user_id)),
        btn("جدول", emoji_key="btn_rank", style=NAV, callback_data=_scoped("leaderboard", user_id)),
    ]
    row2 = [
        btn("جایزه", emoji_key="btn_diamond_box", style=SHOP, callback_data=_scoped("reward", user_id)),
        btn("راهنما", emoji_key="btn_report", style=NAV, callback_data=_scoped("help", user_id)),
    ]
    rows = [
        [b for b in row1 if skip is None or not (b.callback_data or "").startswith(f"grp:{skip}:")],
        [b for b in row2 if skip is None or not (b.callback_data or "").startswith(f"grp:{skip}:")],
    ]
    rows = [r for r in rows if r]
    rows.append([_pm_button()])
    return InlineKeyboardMarkup(rows)


# ── card renderers ──────────────────────────────────────────────────────────


def _creature_card(user, creature, equipped, slots) -> tuple[str, InlineKeyboardMarkup]:
    stats = effective_stats(creature, equipped)
    stars = "⭐" * creature.star_level
    filled = sum(1 for row in slots if not row["is_empty"])
    lines = [
        f"{get_emoji('creature')} <b>{creature.name}</b> {stars}",
        f"{constants.RARITY_LABELS[creature.rarity]} · سطح {creature.level} · "
        f"آزمایشگاه {lab_display(user)}",
        "",
        f"{get_emoji('hp')} {stats['hp']}   {get_emoji('atk')} {stats['atk']}   "
        f"{get_emoji('def')} {stats['def']}   {get_emoji('spd')} {stats['spd']}",
        f"💪 قدرت: <b>{combat_rating(stats)}</b>   ·   🎒 تجهیزات: <b>{filled}/{len(slots)}</b> جایگاه پر",
    ]
    rows = [
        [
            btn("تجهیزات", emoji_key="btn_inventory", style=NAV, callback_data=_scoped("equipment", user.id)),
            btn("کلکسیون", emoji_key="btn_collection", style=NAV, callback_data=_scoped("collection", user.id)),
        ],
        [_pm_button("ارتقا و پرورش در پیوی")],
    ]
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def _equipment_card(user, creature, slots) -> tuple[str, InlineKeyboardMarkup]:
    lines = [f"🎒 <b>تجهیزات {creature.name}</b>", ""]
    for row in slots:
        if row["is_empty"]:
            lines.append(f"{row['label']}: <i>خالی</i>")
        else:
            item = row["item"]
            bonus = bonus_text(item)
            from game.equipment import equipment_power

            lines.append(
                f"{row['label']}: <b>{item.name} +{item.level}</b> · 💪{equipment_power(item)}"
                + (f" — <i>{bonus}</i>" if bonus else "")
            )
    rows = [
        [
            btn("هیولا", emoji_key="btn_creature", style=NAV, callback_data=_scoped("creature", user.id)),
            btn("کلکسیون", emoji_key="btn_collection", style=NAV, callback_data=_scoped("collection", user.id)),
        ],
        [_pm_button("مدیریت تجهیزات در پیوی")],
    ]
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def _collection_card(user, creatures) -> tuple[str, InlineKeyboardMarkup]:
    lines = [f"{get_emoji('collection')} <b>کلکسیون {lab_display(user)}</b> — {len(creatures)} هیولا", ""]
    for creature in creatures[:12]:
        tag = "🟢 " if creature.is_active else ""
        lines.append(
            f"{tag}{creature.name} {'⭐' * creature.star_level} · "
            f"{constants.RARITY_LABELS[creature.rarity]} · Lv{creature.level}"
        )
    if len(creatures) > 12:
        lines.append(f"<i>… و {len(creatures) - 12} تای دیگه</i>")
    rows = [
        [
            btn("هیولا", emoji_key="btn_creature", style=NAV, callback_data=_scoped("creature", user.id)),
            btn("تجهیزات", emoji_key="btn_inventory", style=NAV, callback_data=_scoped("equipment", user.id)),
        ],
        [btn("🔄 انتخاب هیولای فعال", style=NAV, callback_data=_scoped("select", user.id))],
        [_pm_button()],
    ]
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def _select_card(user, creatures, powers) -> tuple[str, InlineKeyboardMarkup]:
    """Group-side «انتخاب کایجو»: pick which creature is active, strongest first,
    right in the chat — no need to open the DM."""
    if not creatures:
        return (
            "هنوز هیولایی نداری. توی پیوی بات /start بزن تا اولین هیولات رو بگیری.",
            group_footer_keyboard(user.id),
        )
    lines = [f"{get_emoji('creature')} <b>انتخاب هیولای فعال</b> — قوی‌ترین‌ها اول:", ""]
    rows = []
    for c in creatures[:8]:
        active = c.is_active
        lines.append(
            f"{'🟢 ' if active else ''}{c.name} {'⭐' * c.star_level} · "
            f"{constants.RARITY_LABELS[c.rarity]} · Lv{c.level} · 💪{powers.get(c.id, 0)}"
        )
        if not active:
            rows.append([btn(
                f"فعال کن: {c.name} (💪{powers.get(c.id, 0)})",
                emoji_key="btn_confirm", style=CONFIRM,
                callback_data=_act("setactive", user.id, str(c.id)),
            )])
    lines.append("\n<i>هیولای فعال توی همه‌ی نبردها (اتک، شکار، نبرد) می‌جنگه.</i>")
    rows.append([_pm_button("مدیریت کامل در پیوی")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def _profile_card(user, creature_count, energy) -> tuple[str, InlineKeyboardMarkup]:
    progress = lab_progress(user)
    lines = [
        f"{get_emoji('profile')} <b>آزمایشگاه {lab_display(user)}</b>",
        f"🔬 سطح <b>{progress['level']}</b> {lab_bar(user)}",
        "",
        f"{get_emoji('trophy')} کاپ: <b>{user.cup}</b>",
        f"{get_emoji('creature')} هیولاها: <b>{creature_count}</b>",
        f"🔥 روزهای پشت‌سرهم: <b>{user.login_streak}</b>",
        "",
        f"{get_emoji('coin')} {user.coins:,}   {get_emoji('dna')} {user.dna_fragments:,}   "
        f"{get_emoji('diamond')} {user.diamonds:,}   {get_emoji('energy')} {energy}/{constants.MAX_ENERGY}",
    ]
    from game.arena import shield_status_lines

    shield_lines = shield_status_lines(user)
    if shield_lines:
        lines.append("")
        lines.extend(shield_lines)
    rows = [
        [
            btn("هیولا", emoji_key="btn_creature", style=NAV, callback_data=_scoped("creature", user.id)),
            btn("کلکسیون", emoji_key="btn_collection", style=NAV, callback_data=_scoped("collection", user.id)),
        ],
        [_pm_button()],
    ]
    return "\n".join(lines), InlineKeyboardMarkup(rows)


_RULE = "━━━━━━━━━━━━━━"


def _help_card(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """The help home: what the game is, then two kinds of page.

    Two earlier versions failed in opposite ways. The first listed all the words
    in one message — a wall nobody read. The second listed category names with a
    word count, which is a table of contents: it says how much there is without
    saying what any of it does. This one splits the job in two: «چطور بازی
    می‌کنم؟» pages teach the mechanics, and «چی بنویسم؟» pages list the words. A
    group member who has never opened the DM needs the first before the second.
    """
    lines = [
        f"{get_emoji('book')} <b>راهنمای Kaiju Legends</b>",
        "",
        "یه بازی پرورش هیولاست: یه هیولا داری، قوی‌ترش می‌کنی، باهاش می‌جنگی "
        "و آزمایشگاهت رو بزرگ می‌کنی.",
        "",
        f"<blockquote>همین‌جا توی گروه بازی کن — کافیه <b>خودِ کلمه</b> رو تنها "
        f"بفرستی، بدون اسلش.\nمثلاً بفرست <code>{keywords.word_for('creature')}</code> "
        f"(بزن روش تا کپی شه).</blockquote>",
        "",
        _RULE,
        f"{get_emoji('lab')} <b>چطور بازی می‌کنم؟</b>",
        "<i>مفهوم‌های بازی رو توضیح می‌ده — از اینجا شروع کن.</i>",
        "",
    ]
    topic_buttons = []
    for key, (emoji_key, title, _blurb, _rows) in keywords.HELP_TOPICS.items():
        lines.append(f"{get_emoji(emoji_key)} {title}")
        topic_buttons.append(btn(title, style=CONFIRM, callback_data=f"grph:t_{key}:{user_id}"))

    lines.append("")
    lines.append(_RULE)
    lines.append(f"{get_emoji('settings')} <b>چی بنویسم؟</b>")
    lines.append("<i>فهرست کلمه‌ها، دسته‌بندی‌شده.</i>")
    lines.append("")
    word_buttons = []
    for key, emoji_key, title, blurb, actions in keywords.KEYWORD_SECTIONS:
        lines.append(f"{get_emoji(emoji_key)} {title} — <i>{blurb}</i>")
        word_buttons.append(btn(title, style=NAV, callback_data=f"grph:{key}:{user_id}"))

    keyboard = [topic_buttons[i : i + 2] for i in range(0, len(topic_buttons), 2)]
    keyboard += [word_buttons[i : i + 2] for i in range(0, len(word_buttons), 2)]
    keyboard.append([_pm_button()])
    return "\n".join(lines), InlineKeyboardMarkup(keyboard)


def _help_back_rows(user_id: int):
    return [
        [btn("راهنمای اصلی", emoji_key="btn_back", style=BACK, callback_data=f"grph:__menu__:{user_id}")],
        [_pm_button()],
    ]


def _help_topic_card(topic_key: str, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """A concept page — how a mechanic works, with no word to type."""
    found = keywords.topic(topic_key)
    if found is None:
        return _help_card(user_id)
    emoji_key, title, blurb, rows = found

    lines = [f"{get_emoji(emoji_key)} <b>{title}</b>", "", f"<i>{blurb}</i>", ""]
    for heading, body in rows:
        lines.append(_RULE)
        lines.append(f"<b>{heading}</b>")
        lines.append(body)
        lines.append("")
    return "\n".join(lines).rstrip(), InlineKeyboardMarkup(_help_back_rows(user_id))


def _help_section_card(section_key: str, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """One category of words: each trigger, what it does, and its rules.

    Each entry is fenced by a rule and its rules sit in their own quote block —
    without that the descriptions and the bullet lists ran into each other and
    the page read as one undifferentiated paragraph.
    """
    if section_key.startswith("t_"):
        return _help_topic_card(section_key[2:], user_id)
    found = keywords.section(section_key)
    if found is None:
        return _help_card(user_id)
    _key, emoji_key, title, blurb, actions = found

    lines = [f"{get_emoji(emoji_key)} <b>{title}</b>", "", f"<i>{blurb}</i>", ""]
    for action in actions:
        lines.append(_RULE)
        # the word is wrapped in <code> so a tap copies it, ready to paste and send
        lines.append(
            f"{get_emoji(keywords.emoji_key_for(action))} <code>{keywords.word_for(action)}</code>"
            "  <i>(بزن روش تا کپی شه)</i>"
        )
        lines.append(keywords.describe(action))
        lines.append("<blockquote>" + "\n".join(keywords.how(action)) + "</blockquote>")
        lines.append("")
    return "\n".join(lines).rstrip(), InlineKeyboardMarkup(_help_back_rows(user_id))


# ── the sections promoted from DM-only into the group ───────────────────────
#
# These used to answer "go to the DM". They're here now because the group is
# where people actually are, and a game you can only play alone in a DM isn't a
# group game. Only the *interactive pickers* (choosing which creature to fuse,
# which slot to equip) stay in the DM — those need long scrollable lists that
# would bury a group chat.
#
# Every button below is owner-scoped: `grpa:` callbacks carry the id of whoever
# summoned the card and refuse anyone else, because in a shared chat a keyboard
# is visible to everybody.


def _act(action: str, user_id: int, arg: str = "") -> str:
    return f"grpa:{action}:{user_id}" + (f":{arg}" if arg else "")


def _upgrade_card(user, creature, energy) -> tuple[str, InlineKeyboardMarkup]:
    text = (
        f"{get_emoji('settings')} <b>ارتقای {creature.name}</b>\n"
        f"سطح <b>{creature.level}</b> · XP {creature.xp}/{constants.xp_for_creature_level(creature.level)}\n\n"
        f"{get_emoji('coin')} تغذیه: {constants.FEED_COST_COINS} طلا → "
        f"{constants.FEED_XP_GAIN} XP\n"
        f"🏋️ تمرین: رایگان → {constants.TRAIN_XP_GAIN} XP "
        f"(هر {constants.TRAIN_COOLDOWN_HOURS} ساعت)\n\n"
        f"{get_emoji('coin')} {user.coins:,}   {get_emoji('energy')} {energy}/{constants.MAX_ENERGY}"
    )
    rows = [
        [
            btn("تغذیه", emoji_key="btn_feed", style=BUILD, callback_data=_act("feed", user.id)),
            btn("تمرین", emoji_key="btn_train", style=BUILD, callback_data=_act("train", user.id)),
        ],
        [btn("هیولا", emoji_key="btn_creature", style=NAV, callback_data=_scoped("creature", user.id))],
        [_pm_button("ارتقای اعضای بدن در پیوی")],
    ]
    return text, InlineKeyboardMarkup(rows)


def _hunt_card(user, target, energy) -> tuple[str, InlineKeyboardMarkup]:
    if target is None:
        return (
            f"{get_emoji('hunt')} حریفی پیدا نشد — دوباره امتحان کن.",
            group_footer_keyboard(user.id),
        )
    lo, hi = target["reward"]
    text = (
        f"{get_emoji('hunt')} <b>حریف پیدا شد!</b>\n\n"
        f"<blockquote>{target['name']} — {target['tier_label']}\n"
        f"{constants.element_label(target['element'])} · 💪 قدرت {target['power']}</blockquote>\n"
        f"{get_emoji('coin')} جایزه‌ی تقریبی: {lo}–{hi}\n"
        f"{get_emoji('energy')} هزینه: ۱ (داری: {energy})"
    )
    rows = [
        [
            btn("حمله!", emoji_key="btn_attack", style=BATTLE,
                callback_data=_act("hunt_go", user.id, f"{target['tier']}:{target['seed']}")),
            btn("بعدی", emoji_key="btn_recheck", style=NAV, callback_data=_act("hunt_next", user.id)),
        ],
        [_pm_button()],
    ]
    return text, InlineKeyboardMarkup(rows)


def _arena_card(user, opponent, loot, shielded_for) -> tuple[str, InlineKeyboardMarkup]:
    lines = [
        f"{get_emoji('trophy')} <b>آرنا</b> — کاپ تو: <b>{user.cup}</b>",
    ]
    if shielded_for:
        lines.append(f"🛡 سپر داری: {shielded_for // 3600} ساعت — با حمله کردن می‌پره.")
    if opponent is None:
        lines.append("\nحریفی پیدا نشد، بعداً دوباره امتحان کن.")
        return "\n".join(lines), group_footer_keyboard(user.id)
    lines.append("")
    lines.append(f"<blockquote>🎯 {opponent['label']}\n💪 قدرت {opponent['power']} · 🏆 کاپ {opponent['cup']}</blockquote>")
    lines.append(f"{get_emoji('coin')} غنیمت تقریبی: <b>{loot:,}</b>")
    lines.append(f"{get_emoji('energy')} هزینه: ۱ انرژی")
    rows = [
        [
            btn("حمله!", emoji_key="btn_attack", style=BATTLE, callback_data=_act("arena_go", user.id)),
            btn("حریف بعدی", emoji_key="btn_recheck", style=NAV, callback_data=_act("arena_find", user.id)),
        ],
        [_pm_button()],
    ]
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def _mine_card(user, rows_data) -> tuple[str, InlineKeyboardMarkup]:
    lines = [f"{get_emoji('building')} <b>ساختمون‌های تو</b>", ""]
    total = 0
    for building, pending, label in rows_data:
        state = "🔒 ساخته‌نشده" if building.level == 0 else f"سطح {building.level}"
        extra = f" · آماده: <b>{pending}</b>" if pending else ""
        lines.append(f"{label} — {state}{extra}")
        total += pending
    lines.append("")
    lines.append(
        f"<blockquote>مجموع آماده‌ی جمع‌آوری: <b>{total}</b></blockquote>"
        if total
        else "<blockquote>فعلاً چیزی برای جمع‌آوری نیست.</blockquote>"
    )
    rows = []
    if total:
        rows.append([btn("جمع‌آوری همه", emoji_key="btn_collect", style=BUILD,
                         callback_data=_act("collect_all", user.id))])
    rows.append([_pm_button("ساخت و ارتقا در پیوی")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def _box_card(user) -> tuple[str, InlineKeyboardMarkup]:
    text = (
        f"{get_emoji('diamond')} <b>باکس ژنتیکی</b>\n\n"
        f"<blockquote>هزینه: <b>{constants.BIOCRATE_GOLD_COST}</b> {get_emoji('coin')} + "
        f"<b>{constants.BIOCRATE_DNA_COST}</b> {get_emoji('dna')}\n"
        "شانسی هیولا یا تجهیزات می‌ده، با درجه‌ی نایابی تصادفی.</blockquote>\n"
        f"موجودی تو: {user.coins:,} {get_emoji('coin')} · {user.dna_fragments} {get_emoji('dna')}"
    )
    rows = [
        [btn("باز کن", emoji_key="btn_biocrate", style=SHOP, callback_data=_act("box_open", user.id))],
        [_pm_button("جعبه‌های الماسی در پیوی")],
    ]
    return text, InlineKeyboardMarkup(rows)


def _wheel_card(user, spun_today) -> tuple[str, InlineKeyboardMarkup]:
    if spun_today:
        text = (
            f"{get_emoji('wheel')} <b>گردونه‌ی شانس</b>\n\n"
            "امروز چرخوندیش — فردا دوباره بیا."
        )
        return text, group_footer_keyboard(user.id)
    text = (
        f"{get_emoji('wheel')} <b>گردونه‌ی شانس</b>\n\n"
        "<blockquote>روزی یک‌بار رایگان. جایزه: طلا، DNA، الماس یا کارت سرعت.</blockquote>"
    )
    rows = [[btn("بچرخون!", emoji_key="btn_wheel", style=SHOP, callback_data=_act("wheel_spin", user.id))]]
    rows.append([_pm_button()])
    return text, InlineKeyboardMarkup(rows)


def _casino_card(user) -> tuple[str, InlineKeyboardMarkup]:
    """The casino home, playable right here in the group. One inline button per
    table (scoped to the summoner), tapping opens a confirm step."""
    from game import casino

    lines = [
        f"{get_emoji('wheel')} <b>کازینو</b>",
        f"<blockquote>{get_emoji('coin')} {user.coins:,} طلا · {get_emoji('diamond')} {user.diamonds} الماس\n"
        "یه میز رو انتخاب کن. قماره — ممکنه ببری یا ببازی.</blockquote>",
    ]
    rows = []
    for t in casino.tier_list():
        if t["daily"]:
            cost = "رایگان روزانه"
        else:
            cost = f"{t['cost']} " + ("💎" if t["currency"] == "diamonds" else "طلا")
        rows.append([btn(f"{t['label']} — {cost}", style=SHOP, callback_data=_act("casino_pick", user.id, t["key"]))])
    rows.append([_pm_button("🎰 در پیوی")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def _casino_home_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    return user


def _casino_play_sync(tg_user, tier):
    from game import casino

    user, _ = get_or_create_user(tg_user)
    prize = casino.play(user, tier)
    user.refresh_from_db()  # play() may have charged via a locked re-fetch
    return prize, user.coins, user.diamonds


def _casino_confirm(owner_id: int, tier: str) -> tuple[str, InlineKeyboardMarkup]:
    cfg = constants.CASINO_TIERS[tier]
    if cfg["daily"]:
        cost_line = "رایگان (روزی یک‌بار — همون قرعه‌کشی)"
    else:
        cur = "💎 الماس" if cfg["currency"] == "diamonds" else "طلا"
        cost_line = f"شرط: <b>{cfg['cost']}</b> {cur}"
    text = (
        f"{cfg['label']}\n<blockquote>{cfg['desc']}\n{cost_line}\n\n"
        "ممکنه جایزه‌ی بزرگ ببری یا هیچی گیرت نیاد. مطمئنی؟</blockquote>"
    )
    rows = [
        [btn("🎲 بچرخون!", style=CONFIRM, callback_data=_act("casino_play", owner_id, tier))],
        [btn("↩️ میزهای دیگه", style=NAV, callback_data=_act("casino_home", owner_id))],
    ]
    return text, InlineKeyboardMarkup(rows)


def _casino_result(owner_id: int, tier: str, prize: dict, coins: int, diamonds: int) -> tuple[str, InlineKeyboardMarkup]:
    if prize["kind"] == "nothing":
        reveal = "😔 <b>باختی!</b> این دور چیزی نصیبت نشد."
    else:
        reveal = f"🎉 <b>بردی!</b>\n<tg-spoiler>{prize['label']}</tg-spoiler>"
    text = (
        f"{constants.CASINO_TIERS[tier]['label']}\n\n{reveal}\n\n"
        f"<i>موجودی: {coins:,} طلا · {diamonds} الماس</i>"
    )
    rows = [
        [btn("🎲 دوباره همین میز", style=SHOP, callback_data=_act("casino_pick", owner_id, tier))],
        [btn("↩️ میزهای دیگه", style=NAV, callback_data=_act("casino_home", owner_id))],
    ]
    return text, InlineKeyboardMarkup(rows)


def _fusion_card(user, pairs, built, cap) -> tuple[str, InlineKeyboardMarkup]:
    lines = [f"{get_emoji('lab')} <b>ترکیب هیولا</b>"]
    if not built:
        lines.append("\n🔒 اول باید 🔮 تالار ادغام رو توی پیوی بسازی.")
    elif pairs:
        lines.append(f"⭐ سقف ستاره‌ی تو: <b>{cap}</b>\n")
        lines.append("این جفت‌ها آماده‌ان — <b>هر کدوم ۱۰۰٪ موفقه</b>:")
        for pair in pairs[:6]:
            lines.append(f"• {pair['name']} {'⭐' * pair['star']} ×{pair['count']} → {'⭐' * (pair['star'] + 1)}")
    else:
        lines.append(
            "\n<blockquote>الان جفت آماده‌ای نداری. برای ترکیب به <b>دو هیولای هم‌نام "
            "با ستاره‌ی یکسان</b> نیاز داری.</blockquote>"
        )
    return "\n".join(lines), InlineKeyboardMarkup([[_pm_button("انجام ترکیب در پیوی")]])


def _breeding_card(user, job, seconds_left, built, egg_count=0) -> tuple[str, InlineKeyboardMarkup]:
    lines = [f"🕳 <b>غار هیولا</b>"]
    if not built:
        lines.append("\n🔒 اول باید 🔮 تالار ادغام رو توی پیوی بسازی تا غار باز شه.")
    else:
        if job is None:
            lines.append(
                "\n<blockquote>یه جفت بفرست توی غار تا جفت‌گیری کنن و یه <b>تخم</b> بذارن. "
                "والدین بعد از تخم‌گذاری آزاد می‌شن. چی توی تخمه؟ تا سر باز نکنه معلوم نیست.</blockquote>"
            )
        elif seconds_left <= 0:
            lines.append(f"\n💞 <b>جفت‌گیری تموم شد!</b> {job.parent_a.name} + {job.parent_b.name}")
            lines.append("توی پیوی تخم رو بردار و والدها رو آزاد کن.")
        else:
            hours, rem = divmod(seconds_left, 3600)
            lines.append(f"\n💞 یه جفت توی غارن: {job.parent_a.name} + {job.parent_b.name}")
            lines.append(f"<b>{hours} ساعت و {rem // 60} دقیقه</b> مونده تا تخم‌گذاری")
        if egg_count:
            lines.append(f"\n{get_emoji('egg')} <b>{egg_count}</b> تخم در حال رشد داری.")
    return "\n".join(lines), InlineKeyboardMarkup([[_pm_button("مدیریت غار هیولا در پیوی")]])


def _start_card(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    lines = [f"{get_emoji('egg')} <b>چطور شروع کنم؟</b>", ""]
    for line in keywords.how("start"):
        lines.append(line)
    lines.append("")
    lines.append(f"<i>برای دیدن همه‌ی کارها «{keywords.word_for('help')}» رو بفرست.</i>")
    rows = [
        [btn("راهنمای کامل", emoji_key="btn_report", style=NAV, callback_data=_scoped("help", user_id))],
        [_pm_button("شروع در پیوی ربات")],
    ]
    return "\n".join(lines), InlineKeyboardMarkup(rows)


# ── sync data gathering ─────────────────────────────────────────────────────


def _require_creature(creature):
    if creature is None:
        raise GameError("هنوز هیولایی نداری! توی پیوی ربات /start رو بزن.")


def _card_sync(tg_user, chat, action):
    """One DB round-trip per card. Everything the renderers need has to be
    resolved here — they run on the event loop."""
    user, _ = get_or_create_user(tg_user)
    group = get_or_create_group(chat) if chat is not None else None
    creature = get_active_creature(user)
    data = {"user": user, "group": group, "creature": creature}

    if action in ("creature", "equipment"):
        _require_creature(creature)
        data["equipped"] = get_equipped_items(creature)
        data["slots"] = slot_loadout(user, creature)
    elif action == "collection":
        from game.creature import list_creatures

        data["creatures"] = list_creatures(user)
    elif action == "select":
        from bot.handlers.group import _creature_power
        from game.creature import list_creatures

        creatures = sorted(list_creatures(user), key=_creature_power, reverse=True)
        data["creatures"] = creatures
        data["powers"] = {c.id: _creature_power(c) for c in creatures}
    elif action == "lab":
        from bio_lab.models import Creature

        data["creature_count"] = Creature.objects.filter(owner=user).count()
        data["energy"] = sync_energy(user)
    elif action == "upgrade":
        _require_creature(creature)
        data["energy"] = sync_energy(user)
    elif action == "hunt":
        _require_creature(creature)
        from game.hunt import HUNT_TIERS, estimated_reward, scout_one

        data["energy"] = sync_energy(user)
        target = scout_one(creature)
        # scout_one returns the raw roll; the card needs it labelled and priced
        target["tier_label"] = HUNT_TIERS[target["tier"]]["label"]
        target["reward"] = estimated_reward(target["tier"], creature.level)
        data["target"] = target
    elif action == "arena":
        from game import arena

        _require_creature(creature)
        opponent = arena.find_opponent(user)
        data["opponent"] = opponent
        data["loot"] = arena.expected_loot(opponent, creature.level) if opponent else 0
        data["shielded_for"] = arena.shield_remaining_seconds(user)
    elif action == "mine":
        from game.buildings import get_or_create_buildings, pending_amount

        buildings = get_or_create_buildings(user)
        buildings.sort(key=lambda b: (b.building_type != constants.MAIN_BUILDING, b.building_type))
        data["rows"] = [
            (b, pending_amount(b), constants.BUILDING_LABELS[b.building_type]) for b in buildings
        ]
    elif action == "wheel":
        from game.daily import get_daily_count

        data["spun_today"] = get_daily_count(user, "wheel_spin") >= constants.WHEEL_DAILY_LIMIT
    elif action == "fusion":
        from game.buildings import is_built, star_cap
        from game.fusion import FUSION_BUILDING, ready_pairs

        data["built"] = is_built(user, FUSION_BUILDING)
        data["pairs"] = ready_pairs(user)
        data["cap"] = star_cap(user)
    elif action == "breeding":
        from game import breeding as breeding_mod
        from game.buildings import is_built

        job = breeding_mod.active_job(user)
        data["built"] = is_built(user, breeding_mod.BREEDING_BUILDING)
        data["job"] = job
        data["seconds_left"] = breeding_mod.seconds_left(job) if job else 0
        data["egg_count"] = len(breeding_mod.active_eggs(user))
    elif action == "leaderboard":
        from bot.handlers.group import _creature_power, group_member_creatures

        if group is None:
            data["ranked"] = []
        else:
            data["ranked"] = sorted(
                group_member_creatures(group), key=_creature_power, reverse=True
            )[:10]
            data["powers"] = {c.id: _creature_power(c) for c in data["ranked"]}
    return data


def _render(action: str, data: dict) -> tuple[str, InlineKeyboardMarkup]:
    user = data["user"]
    if action == "creature":
        return _creature_card(user, data["creature"], data["equipped"], data["slots"])
    if action == "equipment":
        return _equipment_card(user, data["creature"], data["slots"])
    if action == "collection":
        return _collection_card(user, data["creatures"])
    if action == "select":
        return _select_card(user, data["creatures"], data.get("powers", {}))
    if action == "lab":
        return _profile_card(user, data.get("creature_count", 0), data.get("energy", 0))
    if action == "upgrade":
        return _upgrade_card(user, data["creature"], data.get("energy", 0))
    if action == "hunt":
        return _hunt_card(user, data.get("target"), data.get("energy", 0))
    if action == "arena":
        return _arena_card(user, data.get("opponent"), data.get("loot", 0), data.get("shielded_for", 0))
    if action == "mine":
        return _mine_card(user, data.get("rows", []))
    if action == "box":
        return _box_card(user)
    if action == "wheel":
        return _wheel_card(user, data.get("spun_today", False))
    if action == "fusion":
        return _fusion_card(user, data.get("pairs", []), data.get("built", False), data.get("cap", 1))
    if action == "breeding":
        return _breeding_card(
            user, data.get("job"), data.get("seconds_left", 0), data.get("built", False), data.get("egg_count", 0)
        )
    if action == "start":
        return _start_card(user.id)
    if action == "leaderboard":
        return _leaderboard_card(user, data.get("ranked", []), data.get("powers", {}))
    if action == "casino":
        return _casino_card(user)
    return _help_card(user.id)


def _leaderboard_card(user, ranked, powers) -> tuple[str, InlineKeyboardMarkup]:
    if not ranked:
        return "هنوز هیچ موجودی توی این گروه ثبت نشده.", group_footer_keyboard(user.id, skip="leaderboard")
    medals = [get_emoji("medal_gold"), get_emoji("medal_silver"), get_emoji("medal_bronze")]
    lines = [f"{get_emoji('trophy')} <b>برترین بازیکن‌های این گروه</b>", ""]
    for i, c in enumerate(ranked, start=1):
        rank = medals[i - 1] if i <= 3 else f"<b>{i}.</b>"
        lines.append(f"{rank} {mention(c.owner)} — 💪{powers.get(c.id, 0)}  <i>(Lv{c.level})</i>")
    return "\n".join(lines), group_footer_keyboard(user.id, skip="leaderboard")


_CARD_ACTIONS = {
    "creature", "equipment", "collection", "lab", "leaderboard", "select",
    "upgrade", "hunt", "arena", "mine", "box", "wheel", "fusion", "breeding", "start",
    "casino",
}


# ── reward ──────────────────────────────────────────────────────────────────


def _reward_sync(tg_user, chat):
    user, _ = get_or_create_user(tg_user)
    group = get_or_create_group(chat)
    return user, word_reward.claim(user, group)


def _format_wait(seconds: int) -> str:
    minutes, secs = divmod(max(0, seconds), 60)
    if minutes and secs:
        return f"{minutes} دقیقه و {secs} ثانیه"
    if minutes:
        return f"{minutes} دقیقه"
    return f"{secs} ثانیه"


def _format_mmss(seconds: int) -> str:
    """MM:SS, e.g. 4:45 — the exact 'come back in' countdown the reward now shows."""
    minutes, secs = divmod(max(0, seconds), 60)
    return f"{minutes}:{secs:02d}"


def _reward_text(user, result: dict) -> str:
    if not result["ok"]:
        return (
            f"⏳ <b>{display_name(user)}</b> هنوز زوده!\n"
            f"تا جایزه‌ی بعدی <b>{_format_mmss(result['seconds_left'])}</b> مونده."
        )

    # off cooldown but a chance-based miss — still starts the fresh random cooldown
    if not result.get("won"):
        return (
            f"🎲 <b>{display_name(user)}</b>، این‌بار چیزی نبود!\n"
            f"<b>{_format_mmss(result['next_wait'])}</b> دیگه دوباره «جایزه» یا «کایجو» بفرست."
        )

    kind = result["kind"]
    if kind == "speedup":
        prize = f"<b>۱ کارت سرعت {constants.SPEEDUP_PLAIN_LABELS[result['minutes']]}</b> ⏱"
    elif kind == "jackpot":
        prize = f"🎰 <b>جکپات! {result['amount']:,}</b> {get_emoji('coin')}"
    elif kind == "coins":
        prize = f"<b>{result['amount']:,}</b> {get_emoji('coin')}"
    elif kind == "dna":
        prize = f"<b>{result['amount']}</b> {get_emoji('dna')}"
    else:
        prize = f"<b>{result['amount']}</b> {get_emoji('diamond')}"

    level_note = (
        f"\n{get_emoji('celebrate')} سطح آزمایشگاهت شد <b>{result['lab_up']['to']}</b>!"
        if result["lab_up"]
        else ""
    )
    return (
        f"{get_emoji('gift')} <b>تبریک {display_name(user)}!</b> جایزه گرفتی 🎉\n"
        f"🎁 {prize}\n"
        f"<i>این {result['count']}اُمین جایزه‌ی توئه</i>{level_note}\n\n"
        f"⏳ <b>{_format_mmss(result['next_wait'])}</b> دیگه دوباره «جایزه» یا «کایجو» بفرست."
    )


# ── dispatch ────────────────────────────────────────────────────────────────


_GROUP_AWAIT_KEYS = (
    "awaiting_player_input",
    "awaiting_emoji_key",
    "awaiting_force_join",
    "awaiting_admin_input",
    "awaiting_button_emoji_key",
)


async def _maybe_capture_group_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """In a group, an 'awaiting a name/number' flow (alliance name, admin search, …)
    is fed by REPLYING to the bot's prompt. Ordinary chatter is never captured — we
    only act when the message replies to the bot AND that user has a pending flow."""
    if not any(context.user_data.get(k) for k in _GROUP_AWAIT_KEYS):
        return False
    reply = update.effective_message.reply_to_message
    if reply is None or reply.from_user is None or reply.from_user.id != context.bot.id:
        return False
    from bot.handlers.owner import capture_owner_text_reply
    from bot.handlers.private import capture_player_text_reply

    await capture_player_text_reply(update, context)
    await capture_owner_text_reply(update, context)
    return True


# How long the bot's reply (and the triggering user message) survive in a group
# before being auto-deleted, per action. Keeps groups from filling with transient
# cards. The leaderboard lingers longer; everything else is short-lived.
_GROUP_TTL_DEFAULT = 60
_GROUP_TTL = {"leaderboard": 300, "casino": 600}  # casino is a multi-step play session


async def _delete_msgs_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id, ids = context.job.data
    for mid in ids:
        try:
            await context.bot.delete_message(chat_id, mid)
        except TelegramError:
            pass


def _schedule_cleanup(context, chat_id: int, message_ids, action: str) -> None:
    """Auto-delete the given messages (bot reply + the user's trigger) after the
    action's TTL, so recognised word-commands don't pile up in the group."""
    jq = getattr(context, "job_queue", None)
    ids = [m for m in message_ids if m]
    if jq is None or not ids:
        return
    jq.run_once(_delete_msgs_job, _GROUP_TTL.get(action, _GROUP_TTL_DEFAULT), data=(chat_id, ids))


async def handle_group_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None or not message.text:
        return
    # a reply to the bot's prompt feeds a pending text flow (alliance name, search…)
    if await _maybe_capture_group_reply(update, context):
        return

    # «انتقال طلا <عدد>» — a gold transfer to whoever you replied to
    norm = keywords.normalize(message.text)
    if norm.startswith("انتقال طلا") or norm.startswith("انتقال"):
        digits = "".join(ch for ch in norm if ch.isdigit())
        if norm.startswith("انتقال طلا") and digits:
            from bot.handlers import group as group_handlers

            await group_handlers.gold_transfer(update, context, int(digits))
            return

    action = keywords.match(message.text)
    if action is None:
        return  # ordinary conversation — stay quiet

    # delegate to the existing command handlers where one already does the job,
    # so a word and its slash command can never drift apart
    from bot.handlers import group as group_handlers

    delegates = {
        "attack": group_handlers.attack,
        "duel": group_handlers.duel,
        "raid": group_handlers.raid_spawn,
        "guardian": group_handlers.guardian,
        "guardian_challenge": group_handlers.guardian_challenge,
        "guardian_claim": group_handlers.guardian_claim,
    }
    if action in delegates:
        # combat / boss / guardian results are shared group events — leave them
        # (and their trigger word) in place; only transient info cards get cleaned.
        await delegates[action](update, context)
        return

    if action == "reward":
        user, result = await run_db(_reward_sync, update.effective_user, message.chat)
        sent = await message.reply_text(
            _reward_text(user, result),
            parse_mode="HTML",
            reply_markup=group_footer_keyboard(update.effective_user.id, skip="reward"),
        )
        _schedule_cleanup(context, message.chat_id, [message.message_id, sent.message_id], action)
        return

    if action == "mission":
        from bot.handlers import private as private_handlers

        await private_handlers.missions(update, context)
        _schedule_cleanup(context, message.chat_id, [message.message_id], action)
        return

    if action == "alliance":
        from bot.handlers import private as private_handlers

        await private_handlers.alliance_info_cmd(update, context)
        _schedule_cleanup(context, message.chat_id, [message.message_id], action)
        return

    if action in _CARD_ACTIONS or action == "help":
        try:
            data = await run_db(_card_sync, update.effective_user, message.chat, action)
        except GameError as exc:
            sent = await message.reply_text(str(exc))
            _schedule_cleanup(context, message.chat_id, [message.message_id, sent.message_id], action)
            return
        text, keyboard = _render(action, data)
        sent = await message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)
        _schedule_cleanup(context, message.chat_id, [message.message_id, sent.message_id], action)


async def group_card_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Re-render a card in place. Only the person who summoned it may press."""
    query = update.callback_query
    _, action, owner_id = query.data.split(":")
    if update.effective_user.id != int(owner_id):
        await query.answer("این کارت مال تو نیست — خودت کلمه‌ش رو بفرست.", show_alert=True)
        return
    if action == "reward":
        # edit in place rather than posting a new message — button presses shouldn't
        # pile up messages in the group (per the group-tidiness rule)
        user, result = await run_db(_reward_sync, update.effective_user, query.message.chat)
        won = result.get("ok") and result.get("won")
        await query.answer("🎁 گرفتی!" if won else ("🎲 این‌بار نشد" if result["ok"] else "هنوز زوده"))
        await safe_edit_message_text(
            query,
            _reward_text(user, result),
            parse_mode="HTML",
            reply_markup=group_footer_keyboard(update.effective_user.id, skip="reward"),
        )
        return

    try:
        data = await run_db(_card_sync, update.effective_user, query.message.chat, action)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer()
    text, keyboard = _render(action, data)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


PRIVACY_HELP = (
    "⚠️ <b>برای فعال‌سازی ربات، اون رو ادمین کنید</b>\n\n"
    "<blockquote>تا وقتی ربات توی این گروه ادمین نباشه، تلگرام پیام‌های معمولی گروه رو بهش "
    "نمی‌رسونه و نوشتن «هیولا» یا «اتک» هیچ جوابی نمی‌گیره.</blockquote>\n\n"
    "<b>چطور ادمینش کنم؟</b>\n"
    "تنظیمات گروه ← ادمین‌ها ← افزودن ادمین ← ربات رو انتخاب کن ← فقط دسترسی <b>«حذف پیام‌ها»</b> کافیه.\n\n"
    "🔒 <i>به دسترسی‌های حساس مثل بن/محدودکردن اعضا یا تغییر تنظیمات نیازی نداره — می‌تونی همه رو خاموش بذاری.</i>\n\n"
    "<i>بعدش دوباره «راهنما» رو بفرست تا مطمئن شی کار می‌کنه.</i>"
)


async def group_setup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/setup` — the one thing that still reaches the bot when privacy mode is on.

    Slash commands are delivered to a group bot regardless of the privacy
    setting, so this is the only channel through which the bot can explain why
    everything else it offers appears to be broken.
    """
    me = await context.bot.get_me()
    chat = update.effective_message.chat
    try:
        member = await context.bot.get_chat_member(chat.id, me.id)
        is_admin = member.status in ("administrator", "creator")
    except TelegramError:
        is_admin = False

    if me.can_read_all_group_messages or is_admin:
        reason = "چون ادمین گروهه" if is_admin and not me.can_read_all_group_messages else ""
        await update.effective_message.reply_text(
            f"✅ <b>همه‌چیز آماده‌ست!</b> {reason}\n\n"
            "کلمه‌ها رو مستقیم بفرست — مثلاً «هیولا»، «اتک»، «جایزه».\n"
            "برای دیدن همه‌ی کلمه‌ها «راهنما» رو بفرست.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                [[btn("همه‌ی کلمه‌ها", emoji_key="btn_report", style=PRIMARY, callback_data=_scoped("help", update.effective_user.id))],
                 [_pm_button()]]
            ),
        )
        return

    await update.effective_message.reply_text(PRIVACY_HELP, parse_mode="HTML")


async def announce_setup(bot, chat_id: int) -> None:
    """Posted when the bot joins a group. If privacy is on, the words the group
    is about to be told to use won't work, so say that up front rather than
    letting them discover it by being ignored."""
    me = await bot.get_me()
    if me.can_read_all_group_messages:
        return
    await bot.send_message(chat_id, PRIVACY_HELP, parse_mode="HTML")


async def group_help_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Move between the help menu and a category page, in place."""
    query = update.callback_query
    _, section_key, owner_id = query.data.split(":")
    if update.effective_user.id != int(owner_id):
        await query.answer("این راهنما مال تو نیست — خودت «راهنما» رو بفرست.", show_alert=True)
        return
    await query.answer()
    if section_key == "__menu__":
        text, keyboard = _help_card(update.effective_user.id)
    else:
        text, keyboard = _help_section_card(section_key, update.effective_user.id)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


# ── owner-scoped actions ────────────────────────────────────────────────────


def _do_sync(tg_user, chat, action, arg):
    """Every mutating group action, in one sync function.

    They share the shape "do the thing, then hand back whatever the follow-up
    card needs", so keeping them together means the transaction boundary and the
    error handling are identical for all of them instead of drifting per action.
    """
    user, _ = get_or_create_user(tg_user)
    creature = get_active_creature(user)

    if action in ("feed", "train"):
        from game.creature import feed, train
        from game.daily import check_missions, record_action

        _require_creature(creature)
        levels = feed(user, creature) if action == "feed" else train(creature)
        record_action(user, action)
        check_missions(user, action)
        return {"kind": action, "levels": levels, "creature": creature,
                "card": _card_sync(tg_user, chat, "upgrade")}

    if action == "setactive":
        from game.creature import set_active_creature

        chosen = set_active_creature(user, int(arg))
        return {"kind": "setactive", "creature": chosen,
                "card": _card_sync(tg_user, chat, "select")}

    if action == "hunt_next":
        return {"kind": "card", "card": _card_sync(tg_user, chat, "hunt")}

    if action == "hunt_go":
        from game.daily import check_missions, record_action
        from game.energy import spend_energy
        from game.hunt import resolve_hunt

        _require_creature(creature)
        tier, seed = arg.split(":")
        spend_energy(user, constants.HUNT_ENERGY_COST, "شکار")
        result = resolve_hunt(user, creature, tier, int(seed))
        record_action(user, "hunt")
        result["missions"] = check_missions(user, "hunt")
        return {"kind": "hunt", "result": result, "card": _card_sync(tg_user, chat, "hunt")}

    if action == "arena_find":
        return {"kind": "card", "card": _card_sync(tg_user, chat, "arena")}

    if action == "arena_go":
        from game import arena
        from game.daily import check_missions, record_action

        _require_creature(creature)
        opponent = arena.find_opponent(user)
        if opponent is None:
            raise GameError("حریفی پیدا نشد، دوباره امتحان کن.")
        result = arena.attack(user, opponent, award_cup=False)  # group arena is cup-neutral
        record_action(user, "arena_attack")
        result["missions"] = check_missions(user, "arena_attack")
        return {"kind": "arena", "result": result, "card": _card_sync(tg_user, chat, "arena")}

    if action == "collect_all":
        from game.buildings import collect, get_or_create_buildings, pending_amount
        from game.daily import check_missions, record_action

        collected = {}
        for building in get_or_create_buildings(user):
            if pending_amount(building) <= 0:
                continue
            amount, resource = collect(user, building)
            collected[resource] = collected.get(resource, 0) + amount
        if not collected:
            raise GameError("چیزی برای جمع‌آوری نیست.")
        record_action(user, "collect")
        check_missions(user, "collect")
        return {"kind": "collect", "collected": collected,
                "card": _card_sync(tg_user, chat, "mine")}

    if action == "box_open":
        from game.lootbox import open_biocrate

        result = open_biocrate(user)
        return {"kind": "box", "result": result, "card": _card_sync(tg_user, chat, "box")}

    if action == "wheel_spin":
        from game.wheel import spin

        result = spin(user)
        return {"kind": "wheel", "result": result, "card": _card_sync(tg_user, chat, "wheel")}

    raise GameError("این کار پیدا نشد.")


def _action_note(payload: dict) -> str:
    """The one-line "what just happened" banner above the refreshed card."""
    kind = payload["kind"]
    if kind == "setactive":
        note = f"🟢 <b>{payload['creature'].name}</b> شد هیولای فعالت!"
    elif kind == "feed":
        note = f"{get_emoji('coin')} <b>تغذیه شد!</b>"
    elif kind == "train":
        note = "🏋️ <b>تمرین کرد!</b>"
    elif kind == "hunt":
        r = payload["result"]
        if r["won"]:
            note = (f"{get_emoji('celebrate')} <b>بردی!</b> +{r['coins']} {get_emoji('coin')} "
                    f"+{r['dna']} {get_emoji('dna')}")
        else:
            note = "💀 <b>باختی!</b> دفعه‌ی بعد قوی‌تر برگرد."
    elif kind == "arena":
        r = payload["result"]
        arrow = "▲" if r["cup_delta"] >= 0 else "▼"
        note = (f"{get_emoji('celebrate')} <b>غارت موفق!</b> +{r['loot']:,} {get_emoji('coin')}"
                if r["won"] else "🛡 <b>حمله دفع شد!</b>")
        note += f"  {arrow} {abs(r['cup_delta'])} 🏆 (کاپ: {r['new_cup']})"
    elif kind == "collect":
        parts = [f"+{amount:,} {get_emoji(_RESOURCE_EMOJI[res])}" for res, amount in payload["collected"].items()]
        note = f"{get_emoji('coin')} <b>جمع‌آوری شد!</b> " + "  ".join(parts)
    elif kind == "box":
        r = payload["result"]
        note = f"{get_emoji('celebrate')} <b>باکس باز شد!</b> <tg-spoiler>{r.get('label', '')}</tg-spoiler>"
    elif kind == "wheel":
        r = payload["result"]
        note = f"{get_emoji('wheel')} <b>{r.get('label', 'جایزه گرفتی!')}</b>"
    else:
        return ""
    if payload.get("levels"):
        note += f" {get_emoji('celebrate')} سطح {payload['creature'].level}!"
    return note + "\n\n"


_RESOURCE_EMOJI = {"coins": "coin", "diamonds": "diamond", "dna_fragments": "dna"}


async def group_action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Perform a mutating action, then re-render its card with a result banner.

    Edits in place rather than posting: these are all "do it again" loops (hunt
    the next target, spin, collect) and a new message per tap would bury the
    group the way the DM used to be buried.
    """
    query = update.callback_query
    parts = query.data.split(":")
    action, owner_id = parts[1], parts[2]
    arg = ":".join(parts[3:]) if len(parts) > 3 else ""
    if update.effective_user.id != int(owner_id):
        await query.answer("این کارت مال تو نیست — خودت کلمه‌ش رو بفرست.", show_alert=True)
        return

    # ── casino: a self-contained pick → confirm → play loop, all in the group ──
    if action in ("casino_pick", "casino_home", "casino_play"):
        if action == "casino_home":
            user = await run_db(_casino_home_sync, update.effective_user)
            await query.answer()
            text, keyboard = _casino_card(user)
            await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)
            return
        if action == "casino_pick":
            if arg not in constants.CASINO_TIERS:
                await query.answer("این میز پیدا نشد.", show_alert=True)
                return
            await query.answer()
            text, keyboard = _casino_confirm(int(owner_id), arg)
            await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)
            return
        # casino_play
        try:
            prize, coins, diamonds = await run_db(_casino_play_sync, update.effective_user, arg)
        except GameError as exc:
            await query.answer(str(exc), show_alert=True)
            return
        await query.answer("🎉 بردی!" if prize["kind"] != "nothing" else "😔 نبردی.")
        text, keyboard = _casino_result(int(owner_id), arg, prize, coins, diamonds)
        await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)
        return

    try:
        payload = await run_db(_do_sync, update.effective_user, query.message.chat, action, arg)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer()
    card_action = {"feed": "upgrade", "train": "upgrade", "hunt_go": "hunt", "hunt_next": "hunt",
                   "arena_go": "arena", "arena_find": "arena", "collect_all": "mine",
                   "box_open": "box", "wheel_spin": "wheel", "setactive": "select"}[action]
    text, keyboard = _render(card_action, payload["card"])
    await safe_edit_message_text(
        query, _action_note(payload) + text, parse_mode="HTML", reply_markup=keyboard
    )


def register(application) -> None:
    application.add_handler(CallbackQueryHandler(group_card_callback, pattern=r"^grp:"))
    application.add_handler(CallbackQueryHandler(group_help_callback, pattern=r"^grph:"))
    application.add_handler(CallbackQueryHandler(group_action_callback, pattern=r"^grpa:"))
    application.add_handler(
        CommandHandler("setup", group_setup, filters.ChatType.GROUPS)
    )
    # THE group text handler — see the module docstring before adding another
    application.add_handler(
        MessageHandler(filters.ChatType.GROUPS & filters.TEXT & ~filters.COMMAND, handle_group_text)
    )

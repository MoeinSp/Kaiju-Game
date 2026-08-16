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

from bio_lab.repository import display_name, get_active_creature, get_or_create_group, get_or_create_user, lab_display
from bot.buttons import BACK, NAV, PRIMARY, SHOP, btn
from bot.utils import run_db, safe_edit_message_text
from config import BOT_USERNAME
from game import constants, keywords, word_reward
from game.creature import GameError, effective_stats
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
        f"🎒 تجهیزات: <b>{filled}/{len(slots)}</b> جایگاه پر",
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
            lines.append(
                f"{row['label']}: <b>{item.name} +{item.level}</b>" + (f" — <i>{bonus}</i>" if bonus else "")
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
        [_pm_button()],
    ]
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
    rows = [
        [
            btn("هیولا", emoji_key="btn_creature", style=NAV, callback_data=_scoped("creature", user.id)),
            btn("کلکسیون", emoji_key="btn_collection", style=NAV, callback_data=_scoped("collection", user.id)),
        ],
        [_pm_button()],
    ]
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def _help_card(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """The help *menu*: what the game is, then one button per category.

    Listing all sixteen words at once produced a wall nobody read. Splitting it
    into categories means the first screen fits on a phone and answers the only
    question a newcomer actually has — "what can I do here?" — with five choices
    instead of sixteen lines.
    """
    lines = [
        f"{get_emoji('book')} <b>راهنمای بازی توی گروه</b>",
        "",
        "<blockquote>کافیه <b>خودِ کلمه</b> رو تنها بفرستی — بدون اسلش، بدون آرگومان."
        "\nهر کار <b>دقیقاً یک کلمه</b> داره.</blockquote>",
        "",
        "<b>دسته‌ها:</b>",
    ]
    rows = []
    for key, emoji_key, title, actions in keywords.KEYWORD_SECTIONS:
        lines.append(f"{get_emoji(emoji_key)} {title} — {len(actions)} کلمه")
        rows.append(
            [btn(title, emoji_key=None, style=NAV, callback_data=f"grph:{key}:{user_id}")]
        )
    lines.append("")
    lines.append(f"<i>مثال: بنویس «{keywords.word_for('creature')}» تا کارت هیولات بیاد.</i>")

    # two per row keeps the menu compact on a phone
    paired = [rows[i][0:1] + (rows[i + 1][0:1] if i + 1 < len(rows) else []) for i in range(0, len(rows), 2)]
    paired = [[b for b in row] for row in paired]
    flat = [b for row in paired for b in row]
    keyboard = [flat[i : i + 2] for i in range(0, len(flat), 2)]
    keyboard.append([_pm_button()])
    return "\n".join(lines), InlineKeyboardMarkup(keyboard)


def _help_section_card(section_key: str, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """One category: its words, what each does, and what actually happens."""
    found = keywords.section(section_key)
    if found is None:
        return _help_card(user_id)
    _key, emoji_key, title, actions = found

    lines = [f"{get_emoji(emoji_key)} <b>{title}</b>", ""]
    for action in actions:
        word = keywords.word_for(action)
        lines.append(f"{get_emoji(keywords.emoji_key_for(action))} <b>{word}</b> — {keywords.describe(action)}")
        lines.append(f"<blockquote>{keywords.detail(action)}</blockquote>")
        lines.append("")  # entries run together without it
    rows = [
        [btn("همه‌ی دسته‌ها", emoji_key="btn_back", style=BACK, callback_data=f"grph:__menu__:{user_id}")],
        [_pm_button()],
    ]
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def _pm_card() -> tuple[str, InlineKeyboardMarkup]:
    """«پیوی» — one word covering everything that needs the full inline UI.

    These used to be six separate trigger words that all produced the same "go to
    the DM" message, which made the word list look twice as big as the group
    actually is."""
    text = (
        f"{get_emoji('lab')} <b>این بخش‌ها توی پیوی ربات‌ان</b>\n"
        "<blockquote>اینا دکمه‌های زیادی لازم دارن و توی گروه شلوغ می‌شن:</blockquote>\n"
        f"{get_emoji('hunt')} شکار انفرادی\n"
        f"{get_emoji('trophy')} آرنای کاپ\n"
        f"{get_emoji('building')} ساختمون‌ها و معدن‌ها\n"
        f"{get_emoji('lab')} ترکیب و تکثیر هیولا\n"
        f"{get_emoji('diamond')} باکس‌ها و گردونه‌ی شانس\n"
        f"{get_emoji('battle')} ارتقا و تجهیز هیولاها"
    )
    return text, InlineKeyboardMarkup([[_pm_button("باز کردن پیوی ربات")]])


# ── sync data gathering ─────────────────────────────────────────────────────


def _card_sync(tg_user, chat, action):
    """One DB round-trip per card. Everything the renderers need has to be
    resolved here — they run on the event loop."""
    user, _ = get_or_create_user(tg_user)
    group = get_or_create_group(chat) if chat is not None else None
    creature = get_active_creature(user)
    data = {"user": user, "group": group, "creature": creature}

    if action in ("creature", "equipment"):
        if creature is None:
            raise GameError("هنوز هیولایی نداری! توی پیوی ربات /start رو بزن.")
        data["equipped"] = get_equipped_items(creature)
        data["slots"] = slot_loadout(user, creature)
    elif action == "collection":
        from game.creature import list_creatures

        data["creatures"] = list_creatures(user)
    elif action == "profile":
        from bio_lab.models import Creature

        data["creature_count"] = Creature.objects.filter(owner=user).count()
        data["energy"] = sync_energy(user)
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
    if action == "profile":
        return _profile_card(user, data.get("creature_count", 0), data.get("energy", 0))
    if action == "leaderboard":
        return _leaderboard_card(user, data.get("ranked", []), data.get("powers", {}))
    if action == "pm":
        return _pm_card()
    return _help_card(user.id)


def _leaderboard_card(user, ranked, powers) -> tuple[str, InlineKeyboardMarkup]:
    if not ranked:
        return "هنوز هیچ موجودی توی این گروه ثبت نشده.", group_footer_keyboard(user.id, skip="leaderboard")
    medals = [get_emoji("medal_gold"), get_emoji("medal_silver"), get_emoji("medal_bronze")]
    lines = [f"{get_emoji('trophy')} <b>برترین موجودات این گروه</b>", ""]
    for i, c in enumerate(ranked, start=1):
        rank = medals[i - 1] if i <= 3 else f"<b>{i}.</b>"
        lines.append(f"{rank} {c.name} (Lv{c.level}) — 💪{powers.get(c.id, 0)}")
    return "\n".join(lines), group_footer_keyboard(user.id, skip="leaderboard")


_CARD_ACTIONS = {"creature", "equipment", "collection", "profile", "leaderboard", "pm"}


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


def _reward_text(user, result: dict) -> str:
    if not result["ok"]:
        return (
            f"⏳ {display_name(user)} هنوز زوده!\n"
            f"<b>{_format_wait(result['seconds_left'])}</b> دیگه دوباره مجازی.\n"
            f"<i>همه‌ی کلمه‌های جایزه یه تایمر مشترک دارن.</i>"
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
        f"{get_emoji('gift')} <b>تبریک {display_name(user)}!</b>\n"
        f"جایزه‌ت: {prize}\n"
        f"<i>جایزه‌ی شماره‌ی {result['count']} تو</i>{level_note}\n\n"
        f"⏳ <b>{word_reward.COOLDOWN_MINUTES} دقیقه</b> دیگه دوباره مجازی."
    )


# ── dispatch ────────────────────────────────────────────────────────────────


async def handle_group_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None or not message.text:
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
        await delegates[action](update, context)
        return

    if action == "reward":
        user, result = await run_db(_reward_sync, update.effective_user, message.chat)
        await message.reply_text(
            _reward_text(user, result),
            parse_mode="HTML",
            reply_markup=group_footer_keyboard(update.effective_user.id, skip="reward"),
        )
        return

    if action == "missions":
        from bot.handlers import private as private_handlers

        await private_handlers.missions(update, context)
        return

    if action == "alliance":
        from bot.handlers import private as private_handlers

        await private_handlers.alliance_info_cmd(update, context)
        return

    if action in _CARD_ACTIONS or action == "help":
        try:
            data = await run_db(_card_sync, update.effective_user, message.chat, action)
        except GameError as exc:
            await message.reply_text(str(exc))
            return
        text, keyboard = _render(action, data)
        await message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def group_card_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Re-render a card in place. Only the person who summoned it may press."""
    query = update.callback_query
    _, action, owner_id = query.data.split(":")
    if update.effective_user.id != int(owner_id):
        await query.answer("این کارت مال تو نیست — خودت کلمه‌ش رو بفرست.", show_alert=True)
        return
    if action == "reward":
        # answered as a NEW message, not an edit: a claim is a personal event and
        # overwriting the shared card with it would wipe whatever the group was
        # looking at
        user, result = await run_db(_reward_sync, update.effective_user, query.message.chat)
        await query.answer("🎁 گرفتی!" if result["ok"] else "هنوز زوده")
        await query.message.reply_text(
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
    "⚠️ <b>حالت حریم خصوصی بات روشنه</b>\n\n"
    "<blockquote>تلگرام تا وقتی این حالت روشنه، پیام‌های معمولی گروه رو <b>اصلاً به بات نمی‌رسونه</b> — "
    "فقط دستورهای اسلش‌دار و ریپلای‌ها می‌رسن. برای همین نوشتن «هیولا» یا «اتک» هیچ جوابی نمی‌گیره.</blockquote>\n\n"
    "<b>راه‌حل (یکی از این دو):</b>\n"
    "۱️⃣ بات رو توی گروه <b>ادمین</b> کن — ادمین‌ها همه‌ی پیام‌ها رو می‌گیرن. (سریع‌ترین راه)\n"
    "۲️⃣ یا توی @BotFather بزن <code>/setprivacy</code> ← بات رو انتخاب کن ← <b>Disable</b>، "
    "بعد بات رو از گروه حذف و دوباره اضافه کن.\n\n"
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


def register(application) -> None:
    application.add_handler(CallbackQueryHandler(group_card_callback, pattern=r"^grp:"))
    application.add_handler(CallbackQueryHandler(group_help_callback, pattern=r"^grph:"))
    application.add_handler(
        CommandHandler("setup", group_setup, filters.ChatType.GROUPS)
    )
    # THE group text handler — see the module docstring before adding another
    application.add_handler(
        MessageHandler(filters.ChatType.GROUPS & filters.TEXT & ~filters.COMMAND, handle_group_text)
    )

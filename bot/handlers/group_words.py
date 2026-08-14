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
from telegram.ext import CallbackQueryHandler, ContextTypes, MessageHandler, filters

from bio_lab.repository import display_name, get_active_creature, get_or_create_group, get_or_create_user, lab_display
from bot.buttons import NAV, PRIMARY, btn
from bot.utils import run_db, safe_edit_message_text
from config import BOT_USERNAME
from game import constants, keywords, word_reward
from game.creature import GameError, effective_stats
from game.emoji import get_emoji
from game.energy import sync_energy
from game.equipment import bonus_text, get_equipped_items, slot_loadout
from game.lab import lab_bar, lab_progress

# Actions that need the full inline UI and don't fit a group message. Typing the
# word still does something useful — it points at the private chat — rather than
# being silently ignored, which would read as "the bot didn't hear me".
_PRIVATE_ONLY = {
    "hunt": "شکار انفرادی",
    "arena": "آرنای کاپ",
    "buildings": "ساختمون‌ها",
    "wheel": "گردونه‌ی شانس",
    "breeding": "تکثیر زیستی",
    "shop": "باکس‌ها و جعبه‌ها",
    "start": "شروع بازی",
}


def _pm_button(label: str = "برو به پیوی ربات"):
    return btn(label, style=PRIMARY, url=f"https://t.me/{BOT_USERNAME}?start=group")


def _scoped(action: str, user_id: int) -> str:
    return f"grp:{action}:{user_id}"


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


def _help_card() -> tuple[str, InlineKeyboardMarkup]:
    lines = [
        "🎮 <b>با کلمه بازی کن</b>",
        "<blockquote>کافیه یکی از این کلمه‌ها رو تنها بفرستی — نیاز به دستور و اسلش نیست.</blockquote>",
    ]
    for title, actions in keywords.KEYWORD_SECTIONS:
        lines.append(f"\n<b>{title}</b>")
        for action in actions:
            words = " · ".join(keywords.words_for(action))
            lines.append(f"‹{words}› — {keywords.describe(action)}")
    return "\n".join(lines), InlineKeyboardMarkup([[_pm_button()]])


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
    elif action == "wallet":
        data["energy"] = sync_energy(user)
    return data


def _render(action: str, data: dict) -> tuple[str, InlineKeyboardMarkup]:
    user = data["user"]
    if action == "creature":
        return _creature_card(user, data["creature"], data["equipped"], data["slots"])
    if action == "equipment":
        return _equipment_card(user, data["creature"], data["slots"])
    if action == "collection":
        return _collection_card(user, data["creatures"])
    if action in ("profile", "lab"):
        return _profile_card(user, data.get("creature_count", 0), data.get("energy", 0))
    if action == "wallet":
        text = (
            f"{get_emoji('coin')} طلا: <b>{user.coins:,}</b>\n"
            f"{get_emoji('dna')} DNA: <b>{user.dna_fragments:,}</b>\n"
            f"{get_emoji('diamond')} الماس: <b>{user.diamonds:,}</b>\n"
            f"{get_emoji('energy')} انرژی: <b>{data['energy']}</b>/{constants.MAX_ENERGY}"
        )
        return text, InlineKeyboardMarkup(
            [[btn("پروفایل", emoji_key="btn_profile", style=NAV, callback_data=_scoped("profile", user.id))],
             [_pm_button()]]
        )
    return _help_card()


_CARD_ACTIONS = {"creature", "equipment", "collection", "profile", "lab", "wallet"}


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
        "leaderboard": group_handlers.leaderboard,
        "guardian": group_handlers.guardian,
        "guardian_challenge": group_handlers.guardian_challenge,
        "guardian_claim": group_handlers.guardian_claim,
    }
    if action in delegates:
        await delegates[action](update, context)
        return

    if action == "reward":
        user, result = await run_db(_reward_sync, update.effective_user, message.chat)
        await message.reply_text(_reward_text(user, result), parse_mode="HTML")
        return

    if action == "missions":
        from bot.handlers import private as private_handlers

        await private_handlers.missions(update, context)
        return

    if action == "alliance":
        from bot.handlers import private as private_handlers

        await private_handlers.alliance_info_cmd(update, context)
        return

    if action in _PRIVATE_ONLY:
        await message.reply_text(
            f"🔒 <b>{_PRIVATE_ONLY[action]}</b> توی پیوی ربات انجام می‌شه — اونجا دکمه‌های کاملش هست.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[_pm_button()]]),
        )
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
    try:
        data = await run_db(_card_sync, update.effective_user, query.message.chat, action)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer()
    text, keyboard = _render(action, data)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


def register(application) -> None:
    application.add_handler(CallbackQueryHandler(group_card_callback, pattern=r"^grp:"))
    # THE group text handler — see the module docstring before adding another
    application.add_handler(
        MessageHandler(filters.ChatType.GROUPS & filters.TEXT & ~filters.COMMAND, handle_group_text)
    )

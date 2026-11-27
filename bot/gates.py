"""Main-hall unlock gate for entry points that bypass the private menu.

`menu_callback` and the keyword router refuse a section whose main-hall requirement
(`SECTION_HALL_REQ`) isn't met yet, but a slash command (/casino, /mugen, /campaign, …)
called the panel directly and skipped that check. Wrap such handlers with `hall_gated`.

Also: owner-lock for personal panels posted in a group. Cards like «سیاه‌چال» /
«پژوهش» / «امروز» use callbacks with no user id (`dg:…`, `rsch:…`, …); without a
lock anyone in the group can tap them and spend the summoner's keys / edit their
message. `remember_card_owner` + the group_panels guard close that hole.
"""
from __future__ import annotations

import re

from bot.utils import run_db


def hall_gated(action: str, handler):
    async def wrapped(update, context):
        # imported lazily: bot.handlers.private imports the modules that use this wrapper
        from bot.handlers import private

        req = private.SECTION_HALL_REQ.get(action)
        if req is not None and update.effective_user is not None:
            level, story_action = await run_db(private._hall_gate_sync, update.effective_user)
            if level < req and action != story_action:
                text = (
                    f"🔒 این بخش از سطح {req} «تالار مِهر» باز می‌شه (الان سطح {level}). "
                    "اول تالار مِهرت رو ارتقا بده."
                )
                if update.callback_query is not None:
                    await update.callback_query.answer(text, show_alert=True)
                elif update.effective_message is not None:
                    await update.effective_message.reply_text(text)
                return
        await handler(update, context)

    return wrapped


# ── owner lock for cards shown in a group ─────────────────────────────────────
# A card posted in a group belongs to whoever summoned it. Callbacks that carry no user
# id (dungeon / research / today / forge / …) are locked through this map:
# (chat_id, message_id) → owner id.
_CARD_OWNERS: dict[tuple[int, int], int] = {}

# Callback prefixes that are a *player's own panel* — never safe for a stranger to tap
# in a group. Shared group events (raid / duel / expedition invite) are NOT listed here;
# those either embed a user id (`grpa:…`) or are intentionally multiplayer.
PERSONAL_CALLBACK_RE = re.compile(
    r"^(?:"
    r"dg:|dg_team|"
    r"rsch:|"
    r"forge_|efuse_|"
    r"inv_|"
    r"camp_|"
    r"tdy:|today:|nb:|"
    r"rcy:|"
    r"arena_|dteam:|"
    r"dsp:|"
    r"fest:|tour:|"
    r"ach_|"
    r"wb:|wbs:|"
    r"merch:|mcht:|"
    r"serum|ser_|"
    r"bld:|build:"
    r")"
)

_LOCK_ALERT = "این پنل مال یه بازیکن دیگه‌ست — خودت کلمه‌ش رو توی گروه بفرست."
_EXPIRED_ALERT = "این پنل منقضی شده — دوباره کلمه‌ش رو بفرست."


def remember_card_owner(chat_id: int, message_id: int, user_id: int) -> None:
    _CARD_OWNERS[(chat_id, message_id)] = user_id
    while len(_CARD_OWNERS) > 5000:
        _CARD_OWNERS.pop(next(iter(_CARD_OWNERS)))


def owner_of_message(chat_id: int, message_id: int) -> int | None:
    return _CARD_OWNERS.get((chat_id, message_id))


def is_personal_callback(data: str | None) -> bool:
    return bool(data and PERSONAL_CALLBACK_RE.match(data))


def keyboard_is_personal(reply_markup) -> bool:
    """True when any button on the keyboard is a personal-panel callback."""
    for row in getattr(reply_markup, "inline_keyboard", None) or ():
        for b in row:
            if is_personal_callback(getattr(b, "callback_data", None)):
                return True
    return False


def resolve_card_owner(update) -> int | None:
    """Owner id of the group card being tapped, or None if unknown."""
    query, chat = update.callback_query, update.effective_chat
    if query is None or chat is None or query.message is None:
        return None
    message = query.message
    owner = _CARD_OWNERS.get((chat.id, message.message_id))
    if owner is not None:
        return owner
    # group_panels keeps a parallel map for word→panel messages
    try:
        from bot.handlers.group_panels import _PANELS

        owner = _PANELS.get((chat.id, message.message_id))
        if owner is not None:
            return owner
    except Exception:  # noqa: BLE001
        pass
    replied = getattr(message, "reply_to_message", None)
    if replied is not None:
        owner = _CARD_OWNERS.get((chat.id, replied.message_id))
        if owner is not None:
            return owner
        try:
            from bot.handlers.group_panels import _PANELS

            owner = _PANELS.get((chat.id, replied.message_id))
            if owner is not None:
                return owner
        except Exception:  # noqa: BLE001
            pass
        # word message itself: the human who typed it (not the bot)
        from_user = getattr(replied, "from_user", None)
        if from_user is not None and not from_user.is_bot:
            return from_user.id
    return None


def is_card_owner(update) -> bool:
    """True in a private chat, or when the presser owns the group card.

    For *personal* callbacks (dungeon/research/…) an unknown owner is a NO — the
    panel must be re-opened by its player. For other callbacks (legacy alliance
    cards), unknown owner stays usable so a restart doesn't brick them.
    """
    query, chat, user = update.callback_query, update.effective_chat, update.effective_user
    if query is None or chat is None or user is None or chat.type not in ("group", "supergroup"):
        return True
    owner = resolve_card_owner(update)
    if owner is not None:
        return owner == user.id
    if is_personal_callback(getattr(query, "data", None)):
        return False
    return True


def card_owner_only(handler):
    async def wrapped(update, context):
        if not is_card_owner(update):
            await update.callback_query.answer(_LOCK_ALERT, show_alert=True)
            return
        await handler(update, context)

    return wrapped


def lock_sent_message(update, sent) -> None:
    """Record ownership of a message just posted/edited in a group (personal panels)."""
    if sent is None or update is None:
        return
    chat = update.effective_chat
    user = update.effective_user
    if chat is None or user is None or chat.type not in ("group", "supergroup"):
        return
    mid = getattr(sent, "message_id", None)
    if mid is None:
        # edit-in-place often returns True / the original Message
        q = update.callback_query
        if q is not None and q.message is not None:
            mid = q.message.message_id
            sent = q.message
    if mid is None:
        return
    remember_card_owner(chat.id, mid, user.id)
    try:
        from bot.handlers.group_panels import _remember

        _remember(chat.id, mid, user.id)
    except Exception:  # noqa: BLE001 — lock must never break the screen
        pass

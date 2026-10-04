"""Main-hall unlock gate for entry points that bypass the private menu.

`menu_callback` and the keyword router refuse a section whose main-hall requirement
(`SECTION_HALL_REQ`) isn't met yet, but a slash command (/casino, /mugen, /campaign, …)
called the panel directly and skipped that check. Wrap such handlers with `hall_gated`."""
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
# id (the alliance panels) are locked through this map instead: (chat_id, message_id) →
# owner id, with the «replied-to message» author as a fallback after a restart.
_CARD_OWNERS: dict[tuple[int, int], int] = {}


def remember_card_owner(chat_id: int, message_id: int, user_id: int) -> None:
    _CARD_OWNERS[(chat_id, message_id)] = user_id
    while len(_CARD_OWNERS) > 5000:
        _CARD_OWNERS.pop(next(iter(_CARD_OWNERS)))


def is_card_owner(update) -> bool:
    """True in a private chat, or when the presser owns the group card (or the owner
    can't be determined — then it stays usable rather than dead)."""
    query, chat, user = update.callback_query, update.effective_chat, update.effective_user
    if query is None or chat is None or user is None or chat.type not in ("group", "supergroup"):
        return True
    message = query.message
    if message is None:
        return True
    owner = _CARD_OWNERS.get((chat.id, message.message_id))
    if owner is None:
        replied = getattr(message, "reply_to_message", None)
        owner = getattr(getattr(replied, "from_user", None), "id", None)
    return owner is None or owner == user.id


def card_owner_only(handler):
    async def wrapped(update, context):
        if not is_card_owner(update):
            await update.callback_query.answer(
                "این کارت مال یه بازیکن دیگه‌ست — خودت «اتحاد» رو بفرست.", show_alert=True
            )
            return
        await handler(update, context)

    return wrapped

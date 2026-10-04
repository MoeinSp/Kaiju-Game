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

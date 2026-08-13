import logging
import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "telgame_site.settings")
django.setup()

from telegram import Update  # noqa: E402
from telegram.ext import Application, CallbackQueryHandler, ContextTypes, MessageHandler, filters  # noqa: E402

from bot import middleware  # noqa: E402
from bot.handlers import battle, group, inventory, lootbox, misc, owner, private, welcome  # noqa: E402
from config import BOT_TOKEN  # noqa: E402
from game.emoji import refresh_cache  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


async def _capture_private_text_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """The single MessageHandler for every 'awaiting a plain-text reply' button flow
    across the whole bot (player flows in private.py, owner flows in owner.py) — PTB
    only ever runs the first handler that matches an update within a group, so all
    such flows have to be dispatched from one registration rather than each module
    registering its own MessageHandler."""
    await private.capture_player_text_reply(update, context)
    await owner.capture_owner_text_reply(update, context)


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN تنظیم نشده. فایل .env رو بر اساس .env.example بساز.")

    refresh_cache()  # warm the custom-emoji cache once before the event loop starts

    application = Application.builder().token(BOT_TOKEN).build()

    middleware.register(application)
    private.register(application)
    inventory.register(application)
    lootbox.register(application)
    group.register(application)
    battle.register(application)
    misc.register(application)
    owner.register(application)
    welcome.register(application)
    # scoped to the lab's own callback_data values so it doesn't swallow battle.py's/private.py's callbacks
    application.add_handler(CallbackQueryHandler(private.lab_action_callback, pattern=r"^(feed|train|upgrade:)"))
    application.add_handler(
        MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, _capture_private_text_reply)
    )

    # my_chat_member (bot added/removed from a group) isn't in the default update set
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

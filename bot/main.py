import logging
import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "telgame_site.settings")
django.setup()

from telegram import Update  # noqa: E402
from telegram.ext import Application, CallbackQueryHandler  # noqa: E402

from bot import middleware  # noqa: E402
from bot.handlers import battle, group, inventory, lootbox, misc, owner, private, welcome  # noqa: E402
from config import BOT_TOKEN  # noqa: E402
from game.emoji import refresh_cache  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


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

    # my_chat_member (bot added/removed from a group) isn't in the default update set
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

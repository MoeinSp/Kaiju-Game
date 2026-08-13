import logging
import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "telgame_site.settings")
django.setup()

from telegram.ext import Application, CallbackQueryHandler  # noqa: E402

from bot.handlers import battle, group, misc, private  # noqa: E402
from config import BOT_TOKEN  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN تنظیم نشده. فایل .env رو بر اساس .env.example بساز.")

    application = Application.builder().token(BOT_TOKEN).build()

    private.register(application)
    group.register(application)
    battle.register(application)
    misc.register(application)
    # scoped to the lab's own callback_data values so it doesn't swallow battle.py's callbacks
    application.add_handler(CallbackQueryHandler(private.lab_action_callback, pattern=r"^(feed|train|upgrade:)"))

    application.run_polling()


if __name__ == "__main__":
    main()

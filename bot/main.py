import logging

from telegram.ext import Application, CallbackQueryHandler

from bot.handlers import battle, group, misc, private
from config import BOT_TOKEN
from db.session import init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN تنظیم نشده. فایل .env رو بر اساس .env.example بساز.")

    init_db()

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

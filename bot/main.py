import logging
import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "telgame_site.settings")
django.setup()

from telegram import Update  # noqa: E402
from telegram.ext import Application, CallbackQueryHandler, ContextTypes, MessageHandler, filters  # noqa: E402

from bot import middleware  # noqa: E402
from bot.handlers import (
    breeding,
    group_words,  # noqa: E402
    arena,
    battle,
    buildings,
    group,
    inventory,
    lootbox,
    misc,
    owner,
    private,
    welcome,
    wheel,
)
from config import BOT_TOKEN, WEBHOOK_PORT, WEBHOOK_SECRET, WEBHOOK_URL  # noqa: E402
from game.theme import refresh_theme_caches  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


async def _capture_private_text_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """The single MessageHandler for every 'awaiting a plain-text reply' button flow
    across the whole bot (player flows in private.py, owner flows in owner.py) — PTB
    only ever runs the first handler that matches an update within a group, so all
    such flows have to be dispatched from one registration rather than each module
    registering its own MessageHandler."""
    await private.capture_player_text_reply(update, context)
    await owner.capture_owner_text_reply(update, context)


async def _warn_if_group_privacy_on(application: Application) -> None:
    """Telegram's group privacy mode silently withholds ordinary messages from
    bots, which makes the entire word-driven group experience look broken while
    every log stays clean. get_me() reports it, so say it out loud at startup."""
    me = await application.bot.get_me()
    if me.can_read_all_group_messages:
        return
    logging.warning(
        "GROUP PRIVACY MODE IS ON for @%s - plain words like «هیولا» will NOT reach the bot "
        "in groups. Fix: make the bot an admin in the group, or @BotFather -> /setprivacy -> "
        "Disable, then remove and re-add the bot. Slash commands (/setup) still work either way.",
        me.username,
    )


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN تنظیم نشده. فایل .env رو بر اساس .env.example بساز.")

    # warm every theme cache (message emoji, button icons, button colours) before
    # the event loop starts — get_emoji()/btn() read them from async handler code,
    # so they must never hit the DB lazily
    refresh_theme_caches()

    application = Application.builder().token(BOT_TOKEN).build()

    middleware.register(application)
    private.register(application)
    inventory.register(application)
    lootbox.register(application)
    buildings.register(application)
    breeding.register(application)
    wheel.register(application)
    arena.register(application)
    group.register(application)
    group_words.register(application)  # owns THE group text handler
    battle.register(application)
    misc.register(application)
    owner.register(application)
    welcome.register(application)
    # scoped to the lab's own callback_data prefix so it doesn't swallow battle.py's/private.py's callbacks
    application.add_handler(CallbackQueryHandler(private.lab_action_callback, pattern=r"^lab:"))
    application.add_handler(
        MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, _capture_private_text_reply)
    )

    application.post_init = _warn_if_group_privacy_on

    # my_chat_member (bot added/removed from a group) isn't in the default update
    # set, so every mode below has to ask for ALL_TYPES explicitly.
    if WEBHOOK_URL:
        # Webhook mode: Telegram pushes updates to us instead of us polling it.
        # The URL path is the secret rather than a guessable "/webhook", and
        # secret_token makes PTB reject any request that doesn't carry the
        # matching X-Telegram-Bot-Api-Secret-Token header — without it, anyone
        # who learned the path could inject updates and act as any player.
        url_path = WEBHOOK_SECRET
        logging.info(
            "starting in WEBHOOK mode on port %s, public URL %s/<secret>",
            WEBHOOK_PORT,
            WEBHOOK_URL.rstrip("/"),
        )
        application.run_webhook(
            listen="0.0.0.0",  # inside the container; compose binds it to loopback
            port=WEBHOOK_PORT,
            url_path=url_path,
            secret_token=WEBHOOK_SECRET,
            webhook_url=f"{WEBHOOK_URL.rstrip('/')}/{url_path}",
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES,
        )
        return

    logging.info("starting in POLLING mode (set WEBHOOK_URL to switch)")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

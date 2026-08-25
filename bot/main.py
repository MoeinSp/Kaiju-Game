import logging
import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "telgame_site.settings")
django.setup()

from telegram import (  # noqa: E402
    BotCommand,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
    Update,
)
from telegram.ext import Application, CallbackQueryHandler, ContextTypes, MessageHandler, filters  # noqa: E402

from bot import middleware  # noqa: E402
from bot.handlers import (
    achievements,
    alliancewar,
    banner,
    battlepass,
    breeding,
    campaign,
    casino,
    codex,
    events,
    groupdrops,
    idle,
    league,
    referral,
    shop,
    team,
    titles,
    group_words,  # noqa: E402
    arena,
    battle,
    notify,
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
from game import admins, botconfig  # noqa: E402
from game.theme import refresh_theme_caches  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


# every "awaiting a plain-text reply" key across the bot — if any is pending, the
# message belongs to that flow, not to a keyword trigger
_PRIVATE_AWAIT_KEYS = (
    "awaiting_player_input",
    "awaiting_emoji_key",
    "awaiting_force_join",
    "awaiting_admin_input",
    "awaiting_button_emoji_key",
    "awaiting_restore_file",
)


async def _capture_private_text_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """The single MessageHandler for every 'awaiting a plain-text reply' button flow
    across the whole bot (player flows in private.py, owner flows in owner.py) — PTB
    only ever runs the first handler that matches an update within a group, so all
    such flows have to be dispatched from one registration rather than each module
    registering its own MessageHandler.

    When nothing is awaiting input, a plain private message is also given to the
    keyword router, so simple words («نبرد», «کایجو», …) work in the DM too."""
    pending = any(context.user_data.get(k) for k in _PRIVATE_AWAIT_KEYS)
    await private.capture_player_text_reply(update, context)
    await owner.capture_owner_text_reply(update, context)
    if not pending:
        await private.route_private_keyword(update, context)


# The minimal command set. Everything else is driven by buttons (in the DM) and
# plain words (in groups), which is what players actually use — a long slash-command
# list just clutters the input box, especially in groups.
_PRIVATE_COMMANDS = [
    BotCommand("start", "شروع بازی"),
    BotCommand("menu", "منوی اصلی"),
    BotCommand("help", "راهنما"),
]
_GROUP_COMMANDS = [
    BotCommand("setup", "فعال‌سازی و راهنمای گروه"),
    BotCommand("help", "راهنما و لیست کلمه‌ها"),
]


async def _configure_commands(application: Application) -> None:
    """Publish the minimal command set authoritatively, overriding whatever mess is
    left in BotFather. Scoped so groups see only /setup and /help."""
    bot = application.bot
    await bot.set_my_commands(_PRIVATE_COMMANDS, scope=BotCommandScopeAllPrivateChats())
    await bot.set_my_commands(_GROUP_COMMANDS, scope=BotCommandScopeAllGroupChats())


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


async def _post_init(application: Application) -> None:
    await _configure_commands(application)
    await _warn_if_group_privacy_on(application)


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN تنظیم نشده. فایل .env رو بر اساس .env.example بساز.")

    # warm every theme cache (message emoji, button icons, button colours) before
    # the event loop starts — get_emoji()/btn() read them from async handler code,
    # so they must never hit the DB lazily
    refresh_theme_caches()
    botconfig.refresh_cache()  # the "join the game group" button reads this in async code
    admins.refresh_cache()  # the panel's access check reads this in async code

    # NOTE: deliberately NO AIORateLimiter. Its default per-group throttle is
    # 20 messages/60s = one per 3 seconds, which for a busy game group queues
    # replies and — worse — delays answerCallbackQuery past its short validity
    # window, so buttons silently stop responding ("Query is too old"). That was a
    # real outage; the occasional Telegram 429 it was meant to prevent is rarer and
    # self-heals. If we revisit this, group_max_rate must be raised far above the
    # default and callback answers kept off the limiter.
    application = Application.builder().token(BOT_TOKEN).build()

    middleware.register(application)
    private.register(application)
    inventory.register(application)
    lootbox.register(application)
    buildings.register(application)
    breeding.register(application)
    achievements.register(application)
    battlepass.register(application)
    codex.register(application)
    referral.register(application)
    team.register(application)
    campaign.register(application)
    events.register(application)
    banner.register(application)
    alliancewar.register(application)
    idle.register(application)
    league.register(application)
    shop.register(application)
    casino.register(application)
    titles.register(application)
    groupdrops.register(application)  # flash reward drops in groups (JobQueue)
    notify.register(application)  # periodic re-engagement DMs (JobQueue)
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

    application.post_init = _post_init

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

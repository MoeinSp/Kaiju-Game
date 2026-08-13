from telegram import Update
from telegram.ext import CommandHandler, ContextTypes, filters

from bio_lab.repository import get_or_create_user
from bot.utils import run_db
from game.creature import GameError
from game.emoji import get_emoji
from game.wheel import spin

_KIND_EMOJI_KEY = {"coins": "coin", "dna": "dna", "diamonds": "diamond", "speedup": "speedup"}


def _wheel_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    return spin(user)


async def wheel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        prize = await run_db(_wheel_sync, update.effective_user)
    except GameError as exc:
        await update.effective_message.reply_text(str(exc))
        return

    emoji = get_emoji(_KIND_EMOJI_KEY[prize["kind"]])
    await update.effective_message.reply_text(
        f"{get_emoji('wheel')} <b>گردونه‌ی شانس روزانه</b>\n\n"
        f"<tg-spoiler>{emoji} {prize['label']}</tg-spoiler>\n\n"
        "<blockquote>فردا دوباره سر بزن، یه چرخش دیگه منتظرته.</blockquote>",
        parse_mode="HTML",
    )


def register(application) -> None:
    application.add_handler(CommandHandler("wheel", wheel_cmd, filters.ChatType.PRIVATE))

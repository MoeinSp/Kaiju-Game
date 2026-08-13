from telegram import Update
from telegram.ext import ApplicationHandlerStop, ContextTypes, TypeHandler

from bio_lab.models import User
from bot.utils import run_db
from config import OWNER_TELEGRAM_ID


def _is_banned_sync(user_id: int) -> bool:
    return User.objects.filter(id=user_id, is_banned=True).exists()


async def enforce_ban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Runs before every other handler (registered in group=-1). Blocked players
    never reach game logic — this is the single place a ban is enforced."""
    user = update.effective_user
    if user is None or user.id == OWNER_TELEGRAM_ID:
        return
    if await run_db(_is_banned_sync, user.id):
        if update.effective_message is not None:
            await update.effective_message.reply_text("🚫 دسترسیت به این بات مسدود شده.")
        raise ApplicationHandlerStop


def register(application) -> None:
    application.add_handler(TypeHandler(Update, enforce_ban), group=-1)

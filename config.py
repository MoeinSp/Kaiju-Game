import os

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

# Telegram user id allowed to run owner-only commands (/set_emoji, /list_emoji, /clear_emoji).
OWNER_TELEGRAM_ID = int(os.environ.get("OWNER_TELEGRAM_ID", "8810788620"))

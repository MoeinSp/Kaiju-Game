import os

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

# Telegram user id allowed to run owner-only commands (/set_emoji, /admin, /report, /broadcast, ...).
OWNER_TELEGRAM_ID = int(os.environ.get("OWNER_TELEGRAM_ID", "8810788620"))

# Only reachable from the same machine unless you run the admin server with a public
# host/port (or a tunnel) — see CLAUDE.md. Used by /admin's "open full panel" button.
ADMIN_PANEL_URL = os.environ.get("ADMIN_PANEL_URL", "http://127.0.0.1:8000/admin/")

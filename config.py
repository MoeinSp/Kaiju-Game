import os

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

# Telegram user id allowed to run owner-only commands (/set_emoji, /admin, /report, /broadcast, ...).
OWNER_TELEGRAM_ID = int(os.environ.get("OWNER_TELEGRAM_ID", "8810788620"))

# Target of /admin's "open full panel" button. Points at the operator panel
# (panel/), not Django's raw admin — that's still at /admin/ for the cases the
# panel deliberately doesn't cover. On a VPS set this to the real https domain,
# otherwise the button hands out a localhost link that only works on the server.
ADMIN_PANEL_URL = os.environ.get("ADMIN_PANEL_URL", "http://127.0.0.1:8000/panel/")

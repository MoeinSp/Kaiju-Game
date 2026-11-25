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

# Used to build the "open the bot in private" deep link on group cards. Without
# it the group can still play, but the buttons that hand off to DMs won't work.
# Prefer BOT_USERNAME in .env; if empty we resolve it from BOT_TOKEN via getMe
# so a local/test bot never deep-links into the production username by mistake.
_BOT_USERNAME_ENV = os.environ.get("BOT_USERNAME", "").lstrip("@")
_bot_username_cache: str | None = None


def get_bot_username() -> str:
    """Username of THIS bot (env override, else Telegram getMe for BOT_TOKEN)."""
    global _bot_username_cache
    if _BOT_USERNAME_ENV:
        return _BOT_USERNAME_ENV
    if _bot_username_cache:
        return _bot_username_cache
    if not BOT_TOKEN:
        return ""
    import json
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getMe", timeout=8
        ) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        uname = ((data.get("result") or {}).get("username") or "").lstrip("@")
        if uname:
            _bot_username_cache = uname
        return uname
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError):
        return ""


def __getattr__(name: str):
    # `from config import BOT_USERNAME` stays a live lookup of THIS token's bot.
    if name == "BOT_USERNAME":
        return get_bot_username()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

# Secret for the public "has this user started the bot?" API (telgame_site/api.py),
# handed to an advertiser to verify their referred users. Empty = the API is off.
AD_API_KEY = os.environ.get("AD_API_KEY", "")

# ── Webhook mode ──────────────────────────────────────────────────────────────
# Leave WEBHOOK_URL empty to run long-polling (the right choice on a laptop, and
# the only choice without a public HTTPS URL). Set it in production and the bot
# switches to receiving pushes from Telegram instead.
#
# WEBHOOK_SECRET does double duty: it's the URL path Telegram posts to *and* the
# value of the X-Telegram-Bot-Api-Secret-Token header PTB verifies. A guessable
# path alone would let anyone POST fabricated updates and act as any player.
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "").strip().rstrip("/")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "").strip()
WEBHOOK_PORT = int(os.environ.get("WEBHOOK_PORT", "8443"))

if WEBHOOK_URL and not WEBHOOK_SECRET:
    raise RuntimeError(
        "WEBHOOK_URL is set but WEBHOOK_SECRET is empty — refusing to expose an "
        "unauthenticated webhook. Generate one with: openssl rand -hex 32"
    )

PROXY_URL = os.environ.get("PROXY_URL", os.environ.get("HTTPS_PROXY", "")).strip()

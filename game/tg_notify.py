"""Fire-and-forget Telegram Bot API calls from the web process (no PTB Application).

Used when the Mini App needs a DM right now (e.g. the purchase card after an order)
without waiting for the bot's outbox job. Failures are logged and ignored — the
outbox remains the durable backup.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

log = logging.getLogger(__name__)


def send_html(chat_id: int, text: str, reply_markup: dict | None = None) -> bool:
    """Send an HTML message. Returns True on HTTP success."""
    from config import BOT_TOKEN

    if not BOT_TOKEN or not chat_id or not text:
        return False
    body: dict = {"chat_id": int(chat_id), "text": text, "parse_mode": "HTML"}
    if reply_markup is not None:
        body["reply_markup"] = reply_markup
    raw = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data=raw,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            return 200 <= getattr(resp, "status", 200) < 300
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        log.warning("tg_notify.send_html failed chat=%s: %s", chat_id, exc)
        return False

"""Send one broadcast message to every player (Telegram DM), rate-limited.

Used for game-wide announcements from an operator shell. The default message is the
official v1 relaunch note; override with --message.

    docker compose exec web python manage.py broadcast_all              # v1 relaunch note
    docker compose exec web python manage.py broadcast_all --message "…" --yes
"""

import json
import time
import urllib.request

from django.core.management.base import BaseCommand

from bio_lab.models import User

_DEFAULT_MESSAGE = (
    "🎉 <b>نسخه‌ی ۱ رسمی ربات منتشر شد!</b>\n\n"
    "به‌دلیل یه‌سری <b>تغییرات بالانس</b> و <b>سوءاستفاده‌ی</b> بعضی از دوستان، بازی برای همه "
    "<b>ریست</b> شد تا از این به بعد همه با <b>شرایط برابر و عادلانه</b> رقابت کنن.\n\n"
    "🦖 اسم آزمایشگاهت حفظ شده. برو /start و از نو قوی‌ترین آزمایشگاه رو بساز — موفق باشی!"
)


class Command(BaseCommand):
    help = "DM a broadcast message to every player."

    def add_arguments(self, parser):
        parser.add_argument("--message", default=_DEFAULT_MESSAGE, help="override the message (HTML)")
        parser.add_argument("--yes", action="store_true", help="actually send")

    def _send(self, chat_id, text):
        from config import BOT_TOKEN

        req = urllib.request.Request(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data=json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "HTML"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            return json.load(urllib.request.urlopen(req, timeout=20)).get("ok", False)
        except Exception as exc:  # noqa: BLE001 — blocked/deleted users must not stop the run
            return False

    def handle(self, *args, **opts):
        ids = list(User.objects.values_list("id", flat=True))
        self.stdout.write(f"recipients: {len(ids)}")
        if not opts["yes"]:
            self.stdout.write(self.style.WARNING("[dry-run] pass --yes to actually send."))
            self.stdout.write("--- message preview ---\n" + opts["message"])
            return
        sent = 0
        for uid in ids:
            if self._send(uid, opts["message"]):
                sent += 1
            time.sleep(0.05)  # ~20/s, safely under Telegram's broadcast limit
        self.stdout.write(self.style.SUCCESS(f"delivered {sent}/{len(ids)} messages."))

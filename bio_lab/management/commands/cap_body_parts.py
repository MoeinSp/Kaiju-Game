"""One-time (idempotent) fix: clamp every creature's body-part upgrade levels to
the new STAR-based cap (1★→20, 2★→40 … 5★→100), then DM each affected owner to
explain the reduction.

Body parts (نیش/زره/بال/سم) used to be upgradable without limit; a cap keyed to
star level was added so part power can't outrun a creature's prestige tier. This
command brings existing over-cap creatures down to the cap and notifies the owners.

Safe to re-run — once everything is at or under the cap it finds nothing to do and
sends no messages.

    docker compose exec web python manage.py cap_body_parts          # apply + notify
    docker compose exec web python manage.py cap_body_parts --dry-run # just report
"""

import json
import time
import urllib.request

from django.core.management.base import BaseCommand

from bio_lab.models import Creature
from game import constants

_PARTS = list(constants.BODY_PARTS)  # wings, armor, fangs, poison


class Command(BaseCommand):
    help = "Clamp over-cap body-part levels to the star-based cap and notify owners."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="report only, change nothing")

    def _send(self, chat_id, text):
        from config import BOT_TOKEN

        req = urllib.request.Request(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data=json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "HTML"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            return json.load(urllib.request.urlopen(req, timeout=20)).get("ok", False)
        except Exception as exc:  # noqa: BLE001 — a blocked/deleted user must not stop the run
            self.stderr.write(f"  ! DM to {chat_id} failed: {exc}")
            return False

    def handle(self, *args, **opts):
        dry = opts["dry_run"]
        # owner_id -> list of "«name» عضو از X به Y"
        affected: dict[int, list[str]] = {}

        for creature in Creature.objects.all().iterator():
            cap = constants.part_upgrade_cap(creature.star_level)
            changed_fields = []
            notes = []
            for part in _PARTS:
                attr = f"{part}_lvl"
                level = getattr(creature, attr)
                if level > cap:
                    setattr(creature, attr, cap)
                    changed_fields.append(attr)
                    label = constants.BODY_PARTS[part]["label"].split()[0]
                    notes.append(f"{label} {level}→{cap}")
            if changed_fields:
                if not dry:
                    creature.save(update_fields=changed_fields)
                affected.setdefault(creature.owner_id, []).append(
                    f"«{creature.name}» {creature.star_level}⭐: " + "، ".join(notes)
                )

        if not affected:
            self.stdout.write(self.style.SUCCESS("Nothing over the cap — no changes, no messages."))
            return

        self.stdout.write(f"{'[dry-run] ' if dry else ''}affected owners: {len(affected)}")
        sent = 0
        for owner_id, items in affected.items():
            body = (
                "⚠️ <b>به‌روزرسانی بازی</b>\n\n"
                "برای اعضای هیولا (نیش، زره، بال، غدد سمی) یه <b>سقف ارتقا</b> بر اساس ستاره اضافه شد:\n"
                "<blockquote>۱⭐ تا ۲۰ · ۲⭐ تا ۴۰ · ۳⭐ تا ۶۰ · ۴⭐ تا ۸۰ · ۵⭐ تا ۱۰۰</blockquote>\n"
                "بعضی از اعضای هیولاهات از سقف بالاتر بودن، برای همین به سقف کاهش پیدا کردن:\n\n"
                + "\n".join(f"• {line}" for line in items)
                + "\n\nبرای ارتقای بیشتر، با <b>فیوژن</b> ستاره‌ی هیولا رو زیاد کن — هر ستاره سقف رو "
                "۲۰ تا بالاتر می‌بره (تا ۱۰۰ در ۵⭐). ممنون که بازی می‌کنی 🙏"
            )
            self.stdout.write(f"  owner {owner_id}: {len(items)} creature(s)")
            if not dry:
                if self._send(owner_id, body):
                    sent += 1
                time.sleep(0.05)  # stay well under Telegram's broadcast rate limit

        self.stdout.write(self.style.SUCCESS(
            f"{'[dry-run] would notify' if dry else 'notified'} {len(affected)} owner(s)"
            + ("" if dry else f", {sent} DM(s) delivered")
        ))

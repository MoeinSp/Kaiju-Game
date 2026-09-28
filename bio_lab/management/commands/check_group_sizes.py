import json
import time
import urllib.error
import urllib.parse
import urllib.request

from django.core.management.base import BaseCommand

from bio_lab.models import Group
from config import BOT_TOKEN
from game.constants import MIN_GROUP_MEMBERS
from game.emoji import get_emoji


class Command(BaseCommand):
    help = "Check member count for all registered groups and leave those with < 10 members."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Check member counts without sending messages or leaving groups.",
        )

    def _api_call(self, method: str, params: dict | None = None) -> dict:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
        req = urllib.request.Request(
            url,
            data=json.dumps(params or {}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            try:
                body = json.loads(err.read().decode("utf-8"))
                return body
            except Exception:
                return {"ok": False, "description": str(err)}
        except Exception as exc:
            return {"ok": False, "description": str(exc)}

    def handle(self, *args, **opts):
        dry_run = opts.get("dry_run", False)
        groups = list(Group.objects.all().order_by("-created_at"))
        total = len(groups)
        self.stdout.write(f"Scanning {total} registered groups (dry_run={dry_run})...\n")

        warning_icon = get_emoji("warning", "⚠️")
        left_groups = []
        kept_groups = []
        inaccessible_groups = []

        for i, grp in enumerate(groups, start=1):
            chat_id = grp.id
            title = grp.title or f"Group {chat_id}"

            # 1. Get member count
            count_res = self._api_call("getChatMemberCount", {"chat_id": chat_id})
            if not count_res.get("ok"):
                desc = count_res.get("description", "Unknown error")
                inaccessible_groups.append({"id": chat_id, "title": title, "error": desc})
                self.stdout.write(f"[{i}/{total}] ❌ {title} ({chat_id}): Inaccessible ({desc})")
                time.sleep(0.1)
                continue

            member_count = count_res.get("result", 0)

            # 2. Check threshold
            if member_count < MIN_GROUP_MEMBERS:
                if not dry_run:
                    # Send warning
                    msg_text = (
                        f"{warning_icon} <b>عدم امکان فعالیت در گروه</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"⚠️ این ربات فقط در گروه‌های دارای <b>حداقل {MIN_GROUP_MEMBERS} عضو</b> فعال می‌شود.\n\n"
                        f"👥 تعداد اعضای فعلی این گروه: <code>{member_count}</code> نفر"
                    )
                    self._api_call("sendMessage", {"chat_id": chat_id, "text": msg_text, "parse_mode": "HTML"})
                    time.sleep(0.2)

                    # Leave chat
                    leave_res = self._api_call("leaveChat", {"chat_id": chat_id})
                    left_ok = leave_res.get("ok", False)
                else:
                    left_ok = True

                left_groups.append({"id": chat_id, "title": title, "members": member_count})
                self.stdout.write(
                    self.style.WARNING(
                        f"[{i}/{total}] 🚪 LEFT {title} ({chat_id}): {member_count} members (< {MIN_GROUP_MEMBERS})"
                    )
                )
            else:
                kept_groups.append({"id": chat_id, "title": title, "members": member_count})
                self.stdout.write(
                    self.style.SUCCESS(
                        f"[{i}/{total}] ✅ KEPT {title} ({chat_id}): {member_count} members (>= {MIN_GROUP_MEMBERS})"
                    )
                )

            time.sleep(0.15)

        self.stdout.write("\n" + "=" * 50)
        self.stdout.write("📊 SUMMARY REPORT:")
        self.stdout.write(f"Total groups in database: {total}")
        self.stdout.write(f"Kept (>= 10 members): {len(kept_groups)}")
        self.stdout.write(f"Left (< 10 members): {len(left_groups)}")
        self.stdout.write(f"Inaccessible (already kicked/deleted): {len(inaccessible_groups)}")
        self.stdout.write("=" * 50 + "\n")

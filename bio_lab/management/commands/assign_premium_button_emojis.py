"""Auto-assign Premium custom-emoji icons to bot buttons that don't have one yet.

The owner has Telegram Premium and has already themed some buttons by sending
Premium emojis to the bot. This command reuses THOSE emojis' sticker sets: it
discovers which Premium sets the owner draws from, pulls every emoji in them via
the Bot API, and fills in a fitting Premium icon for any button
(game.button_emoji.BUTTON_EMOJI_DEFS) that is still on its plain unicode fallback
— so a newly added button gets Premium theming automatically instead of needing
the owner to set each one by hand.

Idempotent: by default it only fills buttons that lack an override, never
overwriting an owner's existing choice. Pass --force to reassign every button.

Run it (then restart the bot so its in-memory cache reloads):

    docker compose exec web python manage.py assign_premium_button_emojis
    docker compose restart bot
"""

import json
import urllib.request

from django.core.management.base import BaseCommand

from bio_lab.models import ButtonEmojiOverride
from game.button_emoji import BUTTON_EMOJI_DEFS, set_button_emoji

# Curated nicer matches (a button's plain fallback isn't always the best premium
# pick — e.g. the cave button reads better as a hatching egg). Anything not listed
# falls back to the button's own unicode emoji from BUTTON_EMOJI_DEFS.
PREFERRED = {
    "btn_attack": "🗡", "btn_train": "💪", "btn_breeding": "🐣", "btn_collect": "💰",
    "btn_build": "🏭", "btn_speedup": "⚡", "btn_report": "📊", "btn_charge": "💵",
    "btn_campaign": "🏰", "btn_team": "🛡", "btn_league": "🎖", "btn_codex": "📚",
    "btn_referral": "🎁", "btn_battlepass": "🎟", "btn_events": "⏳", "btn_banner": "🎰",
    "btn_shop": "🛒", "btn_idle": "😴", "btn_achievements": "🏅", "btn_titles": "👑",
    "btn_cat_rewards": "🎁", "btn_cat_shop": "🏪", "btn_cat_social": "👥",
}

_VS16 = "️"  # emoji variation selector — strip it when matching


class Command(BaseCommand):
    help = "Fill in Premium button-emoji icons from the owner's own Premium sticker sets."

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="reassign every button, not just the missing ones")

    def _api(self, method, payload):
        from config import BOT_TOKEN

        req = urllib.request.Request(
            f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        return json.load(urllib.request.urlopen(req, timeout=20))

    def handle(self, *args, **opts):
        existing = {r.key: r for r in ButtonEmojiOverride.objects.all()}
        if not existing:
            self.stderr.write("No Premium button emojis set yet — the owner must theme at least one first "
                              "(the owner's sets are discovered from existing overrides).")
            return

        # 1) which Premium sets does the owner draw from?
        ids = [r.custom_emoji_id for r in existing.values()]
        stickers = self._api("getCustomEmojiStickers", {"custom_emoji_ids": ids}).get("result", [])
        set_names = sorted({s.get("set_name") for s in stickers if s.get("set_name")})

        # 2) every emoji available across those sets → base emoji : custom_emoji_id
        emap = {}
        for sn in set_names:
            r = self._api("getStickerSet", {"name": sn})
            if not r.get("ok"):
                continue
            for s in r["result"]["stickers"]:
                e, cid = s.get("emoji"), s.get("custom_emoji_id")
                if e and cid:
                    emap.setdefault(e, cid)
                    emap.setdefault(e.replace(_VS16, ""), cid)
        self.stdout.write(f"discovered {len(set_names)} Premium set(s), {len(emap)} emojis available")

        # 3) fill in every button that lacks an override (or all, with --force)
        done, skipped, unmatched = 0, 0, []
        for key, (label, fallback, _cat) in BUTTON_EMOJI_DEFS.items():
            if key in existing and not opts["force"]:
                skipped += 1
                continue
            want = PREFERRED.get(key, fallback)
            cid = emap.get(want) or emap.get(want.replace(_VS16, ""))
            if not cid:
                unmatched.append((key, want))
                continue
            set_button_emoji(key, cid, want)
            done += 1

        self.stdout.write(self.style.SUCCESS(
            f"set {done} button(s) | left {skipped} owner-set button(s) untouched | unmatched: {unmatched or 'none'}"))
        self.stdout.write("↻ now run:  docker compose restart bot   (to reload the bot's emoji cache)")

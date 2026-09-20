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
    "btn_sub_silver": "🥈", "btn_sub_gold": "👑", "btn_subscription": "⭐", "btn_vip": "⭐",
    "btn_mugen": "🏰", "btn_blackmarket": "🏛", "btn_expedition": "⛵",
    "btn_exp_join": "➕", "btn_exp_launch": "🚀", "btn_bm_bid": "🏷", "btn_bm_refresh": "🔄",
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
        from bio_lab.models import EmojiOverride
        from game.emoji import EMOJI_DEFS, set_emoji

        existing = {r.key: r for r in ButtonEmojiOverride.objects.all()}
        text_existing = {o.key: o for o in EmojiOverride.objects.all()}
        if not existing and not text_existing:
            self.stderr.write("No Premium emojis set yet — the owner must theme at least one first "
                              "(the owner's sets are discovered from existing overrides).")
            return

        # 1) which Premium sets does the owner draw from? (both button + text overrides)
        ids = list({r.custom_emoji_id for r in existing.values() if r.custom_emoji_id} | {o.custom_emoji_id for o in text_existing.values() if o.custom_emoji_id})
        set_names = set()
        for i in range(0, len(ids), 100):
            batch = ids[i:i+100]
            try:
                res = self._api("getCustomEmojiStickers", {"custom_emoji_ids": batch})
                for s in res.get("result", []):
                    if s.get("set_name"):
                        set_names.add(s["set_name"])
            except Exception as ex:
                self.stderr.write(f"Warning: batch {i} failed: {ex}")
                continue
        set_names = sorted(set_names)

        # 2) every emoji available across those sets → base emoji : custom_emoji_id
        emap = {}
        for sn in set_names:
            try:
                r = self._api("getStickerSet", {"name": sn})
                if not r.get("ok"):
                    continue
                for s in r["result"]["stickers"]:
                    e, cid = s.get("emoji"), s.get("custom_emoji_id")
                    if e and cid:
                        emap.setdefault(e, cid)
                        emap.setdefault(e.replace(_VS16, ""), cid)
            except Exception as ex:
                self.stderr.write(f"Warning: failed to load set {sn}: {ex}")
                continue
        self.stdout.write(f"discovered {len(set_names)} Premium set(s), {len(emap)} emojis available")

        # 3) fill in every button that lacks an override (or all, with --force)
        BUTTON_FALLBACKS = {
            "btn_creature": ["🧬", "🐉", "🦕", "👾"],
            "btn_upgrade": ["🔧", "⚙️", "🛠", "🔨"],
            "btn_collection": ["🗂", "📁", "🗃", "📚"],
            "btn_inventory": ["🎒", "📦", "👝", "💼"],
            "btn_missions": ["🎯", "🏹", "📜", "📋"],
            "btn_profile": ["👤", "🧑", "👑", "💎"],
            "btn_rank": ["🏆", "🥇", "👑", "🎖"],
            "btn_back": ["◀️", "⬅️", "🔙"],
            "btn_hunt": ["🏹", "🎯", "⚔️", "🗡"],
            "btn_attack": ["⚔️", "🗡", "🔪", "💥"],
            "btn_revenge": ["⚔️", "🗡", "🔥", "⚡"],
            "btn_raid_table": ["📊", "📈", "📋"],
            "btn_raid_rank": ["🐲", "🐉", "🏆", "👑"],
            "btn_atk_details": ["🔍", "🔎", "👁"],
            "btn_arena": ["🏆", "⚔️", "🏟", "🥇"],
            "btn_feed": ["🍖", "🥩", "🍗", "🍎"],
            "btn_train": ["🏋️", "💪", "🥋", "🥊"],
            "btn_wings": ["🦋", "🪽", "🕊", "🦅"],
            "btn_armor": ["🛡", "🔰", "🦾", "🦺"],
            "btn_fangs": ["🦷", "🦈", "🐊", "🗡"],
            "btn_poison": ["☠️", "💀", "🐍", "🧪"],
            "btn_devour": ["🍖", "🥩", "🤤", "🍽"],
            "btn_fusion": ["🧪", "⚗️", "🔮", "🧬"],
            "btn_research": ["🔬", "⚗️", "🧪", "🧫"],
            "btn_rsch_fire": ["🔥", "🌋", "💥"],
            "btn_rsch_water": ["💧", "🌊", "💦"],
            "btn_rsch_earth": ["🪨", "⛰", "🌍", "🗿"],
            "btn_rsch_electric": ["⚡", "🌩", "🔌"],
            "btn_rsch_might": ["⚔️", "🗡", "💪", "🥊"],
            "btn_rsch_vigor": ["❤️", "💖", "🩸", "🛡"],
            "btn_breeding": ["🐣", "🕳", "🥚", "🐤"],
            "btn_forge": ["⚒", "🔨", "🛠", "🔥"],
            "btn_collect": ["💰", "🪙", "💎", "📦"],
            "btn_build": ["🏗", "🏭", "🧱", "🏢"],
            "btn_speedup": ["⚡", "⏰", "⏳", "⏩"],
            "btn_bld_main_hall": ["🏛", "🏰", "👑", "🏢"],
            "btn_bld_gold_collector": ["🏭", "💰", "🪙", "🏢"],
            "btn_bld_diamond_collector": ["💎", "💠", "🏭", "🔹"],
            "btn_bld_dna_lab": ["🧬", "🔬", "⚗️", "🧫"],
            "btn_bld_blacksmith": ["⚒", "🔨", "⚔️", "🛡"],
            "btn_bld_fusion_lab": ["🔮", "🧪", "⚗️", "✨"],
            "btn_bld_trade_hall": ["🤝", "🏛", "⚖️", "🏪"],
            "btn_bld_research_lab": ["🔬", "⚗️", "🧪", "📚"],
            "btn_biocrate": ["📦", "🎁", "🧰", "🧬"],
            "btn_diamond_box": ["💠", "💎", "🎁", "🔷"],
            "btn_wheel": ["🎡", "🎰", "🎯", "🎲"],
            "btn_buildings": ["🏗", "🏢", "🏭", "🏛"],
            "btn_alliance": ["🤝", "👥", "🛡", "🏰"],
            "btn_deposit": ["💰", "🪙", "🏦", "📥"],
            "btn_heist": ["🏴‍☠️", "⚔️", "💰", "🦹"],
            "btn_locked": ["🔒", "🔐", "⛔"],
            "btn_confirm": ["✅", "✔️", "👍"],
            "btn_check": ["✔️", "✅", "☑️"],
            "btn_cancel": ["❌", "🚫", "✖️"],
            "btn_delete": ["🗑", "❌", "🗑️"],
            "btn_join": ["📡", "📢", "➕", "🔗"],
            "btn_recheck": ["🔄", "🔁", "🔃"],
            "btn_admin": ["🛠", "⚙️", "🔧", "👑"],
            "btn_broadcast": ["📢", "📣", "📡", "✉️"],
            "btn_report": ["📊", "📈", "📉", "📋"],
            "btn_charge": ["⚡", "💵", "💰", "💎"],
            "btn_lab": ["🔬", "⚗️", "🧪", "🧫"],
            "btn_campaign": ["🗺", "🏰", "⚔️", "🧭"],
            "btn_team": ["🛡", "👥", "🤝", "⚔️"],
            "btn_league": ["🎖", "🏆", "🥇", "⭐"],
            "btn_codex": ["📖", "📚", "📕", "📜"],
            "btn_referral": ["🎁", "👥", "🤝", "🎉"],
            "btn_battlepass": ["🎟", "🎫", "👑", "⭐"],
            "btn_events": ["⏳", "⏰", "🎉", "🔥"],
            "btn_banner": ["🎰", "✨", "🌟", "🎁"],
            "btn_shop": ["🛒", "🏪", "🏬", "🛍"],
            "btn_gold_shop": ["💰", "🪙", "💎", "🛒"],
            "btn_exchange": ["🔄", "💱", "🔁", "⚖️"],
            "btn_ticket_exchange": ["🎟", "🎫", "🔄", "🎁"],
            "btn_workers": ["👷", "🔨", "🛠", "👨‍🏭"],
            "btn_next": ["▶️", "➡️", "⏩"],
            "btn_prev": ["◀️", "⬅️", "⏪"],
            "btn_join_group": ["👥", "💬", "🤝", "🌐"],
            "btn_buy": ["🛒", "💳", "💰", "🛍"],
            "btn_idle": ["💤", "😴", "🌙", "⏳"],
            "btn_achievements": ["🏅", "🏆", "🎖", "⭐"],
            "btn_titles": ["👑", "🏷", "🎖", "⭐"],
            "btn_cat_rewards": ["🎁", "🎉", "📦", "🏆"],
            "btn_cat_shop": ["🛒", "🏪", "🏬", "🛍"],
            "btn_cat_social": ["👥", "💬", "🤝", "🌐"],
            "btn_shield": ["🛡", "🔰", "🏰", "⚡"],
            "btn_casino": ["🎰", "🎲", "🃏", "🎡"],
            "btn_items": ["🛍", "🎒", "📦", "✨"],
            "btn_skill": ["✨", "⚡", "🔮", "💥"],
            "btn_forfeit": ["🏳", "🚩", "🏃", "❌"],
            "btn_autohunt": ["⚡️", "🏹", "🤖", "⚡"],
            "btn_swap": ["🔄", "🔁", "🔃", "🔀"],
            "btn_custom_amt": ["🔢", "✍️", "✏️", "🔢"],
            "btn_reset": ["♻️", "🔄", "0️⃣", "🗑"],
            "btn_last_season": ["🗓", "📅", "📜", "🏆"],
            "btn_revenges": ["⚔️", "🗡", "🔥", "⚡"],
            "btn_scout_next": ["🔍", "🔎", "👁", "🔄"],
            "btn_chests": ["📦", "🎁", "🧰", "💎"],
            "btn_chest_silver": ["🥈", "📦", "🪙", "✨"],
            "btn_chest_golden": ["🥇", "👑", "📦", "⭐"],
            "btn_chest_magical": ["🔮", "✨", "📦", "🌌"],
            "btn_chest_mega": ["👑", "💎", "📦", "🌟"],
            "btn_chest_open": ["🎁", "📦", "✨", "🔓"],
            "btn_chest_speedup": ["⚡", "⏰", "⏳", "⏩"],
            "btn_chest_start": ["⏳", "⏰", "▶️", "⌛"],
            "btn_chest_queue": ["📋", "⏳", "📥", "📜"],
            "btn_chest_rewards": ["📊", "🎁", "ℹ️", "📜"],
            "btn_subscription": ["⭐", "👑", "💎", "✨"],
            "btn_vip": ["⭐", "👑", "💎", "✨"],
            "btn_sub_silver": ["🥈", "🪙", "⭐", "🥈"],
            "btn_sub_gold": ["👑", "🥇", "⭐", "💎"],
            "btn_sub_mgr": ["⭐", "👑", "⚙️", "🛠"],
            "btn_chest_grant": ["📦", "🎁", "🛠", "👑"],
            "btn_mugen": ["🏰", "🏯", "⚔️", "🛡"],
            "btn_blackmarket": ["🏛", "🏬", "💎", "💰"],
            "btn_expedition": ["⛵", "🚢", "🏹", "🧭"],
            "btn_exp_join": ["➕", "⛵", "🤝", "✅"],
            "btn_exp_launch": ["🚀", "⛵", "⚔️", "▶️"],
            "btn_bm_bid": ["🏷", "💰", "💎", "✍️"],
            "btn_bm_refresh": ["🔄", "🔁", "🏛", "🔃"],
            "btn_war": ["⚔️", "🛡", "🏹", "💥"],
            "btn_filter": ["🔍", "🔎", "🔽", "🗂"],
            "btn_settings": ["⚙️", "🔧", "🛠", "🎛"],
            "btn_diamond": ["💎", "💠", "🔹", "🔷"],
            "btn_kick": ["❌", "✖️", "🚫", "⛔", "🚪", "👟", "👞", "👢", "🏃", "👋", "🥾"],
            "btn_members": ["👥", "🧑‍🤝‍🧑", "👤", "📋"],
            "btn_deputy": ["🎖", "🏅", "⭐", "👑"],
            "btn_edit": ["✏️", "📝", "✍️", "🛠"],
            "btn_search": ["🔎", "🔍", "👁", "📋"],
            "btn_list": ["📜", "📃", "📄", "📑", "📝", "📊", "🗂", "📁", "📂", "📖", "📚", "📋"],
            "btn_energy": ["⚡", "🔋", "⚡️", "💥"],
            "btn_requests": ["📨", "✉️", "📬", "📩"],
            "btn_vault": ["🏦", "🏛", "💰", "🪙"],
            "btn_hatch": ["🐣", "🥚", "🐤", "🐣"],
            "btn_instant": ["⚡", "⏩", "⏱", "⚡️"],
            "btn_help": ["📖", "📚", "❓", "ℹ️"],
        }
        done, skipped, unmatched = 0, 0, []
        for key, (label, fallback, _cat) in BUTTON_EMOJI_DEFS.items():
            if key in existing and not opts["force"]:
                skipped += 1
                continue
            cid = None
            placeholder = PREFERRED.get(key, fallback)
            candidates = [placeholder] + BUTTON_FALLBACKS.get(key, [])
            for cand in candidates:
                cid = emap.get(cand) or emap.get(cand.replace(_VS16, ""))
                if cid:
                    placeholder = cand
                    break
            if not cid:
                unmatched.append((key, fallback))
                continue
            set_button_emoji(key, cid, placeholder)
            done += 1

        # 4) same for TEXT/message emojis (game.emoji.EMOJI_DEFS) — match each key's
        # default unicode glyph to a Premium custom emoji from the owner's own sets,
        # skipping any key the owner already themed (unless --force). For glyphs the
        # owner's sets don't carry, try a few semantically-close alternatives so the
        # key still gets a fitting Premium icon instead of staying plain.
        TEXT_FALLBACKS = {
            "scroll": ["📜", "📃", "📄", "📑", "📝", "✉️", "📖", "📚"],
            "poison": ["🐍", "💀", "🧪", "☠"], "def": ["🔰", "⛨"], "spd": ["🌪", "👟", "🏃", "⚡"],
            "wings": ["🪽", "🕊", "🦅"], "element_earth": ["⛰", "🌍", "🟫", "🗿"],
            "forfeit_action": ["🚩", "🏳"], "speedup": ["⏰", "⌛", "🕐", "⚡"],
            "shop_item": ["🛒", "🎒", "🏬"], "book": ["📚", "📕", "📗"], "collection": ["📁", "🗃", "📚"],
            "settings": ["⚙", "🔧", "🎛"], "creature": ["🐉", "🦕", "👾"], "raid_boss": ["🐉", "👹", "👾"],
            "attack_action": ["⚔", "🔪", "🗡"], "guardian": ["🛡", "🏰", "🔰"], "shield": ["🛡", "🔰"],
            "def_": [], "comet": ["🌠", "💫", "🪐"], "building": ["🏢", "🏭", "🧱"], "lab": ["⚗", "🔬", "🧫"],
            "element_electric": ["🔌", "🌩", "⚡"], "element_water": ["🌊", "💦"], "element_fire": ["🔥", "🌋"],
            "fangs": ["🦈", "🐊", "🗡"], "crit": ["💢", "🎯"], "lifesteal": ["🧛", "🩸", "❤"],
            "egg": ["🐣", "🐤", "🐥", "🍳"], "diamond_box": ["🔷", "🎁", "🔹", "💎", "📦"],
            "sub_silver": ["🥈", "🪙"], "sub_gold": ["👑", "🥇", "⭐"], "sub_vip": ["⭐", "👑", "✨"],
            "mugen": ["🏰", "🏯", "⚔", "🛡"], "blackmarket": ["🏛", "🏬", "💎", "💰"], "expedition": ["⛵", "🚢", "🏹", "🧭"],
        }
        tdone, tskipped, tunmatched = 0, 0, []
        for key, (_label, default, _cat) in EMOJI_DEFS.items():
            if key in text_existing and not opts["force"]:
                tskipped += 1
                continue
            cid = None
            placeholder = default
            for cand in [default] + TEXT_FALLBACKS.get(key, []):
                cid = emap.get(cand) or emap.get(cand.replace(_VS16, ""))
                if cid:
                    placeholder = cand
                    break
            if not cid:
                tunmatched.append((key, default))
                continue
            set_emoji(key, cid, placeholder)
            tdone += 1

        # 5) per-GLYPH themes: register EVERY base emoji the owner has a Premium for,
        # so any of them appearing LITERALLY in message text (not via get_emoji) is
        # auto-wrapped by game.emoji.premiumize_html. This is what themes the thousands
        # of hardcoded emojis across every screen without editing each string.
        from game.emoji import set_glyphs_bulk

        gset = set_glyphs_bulk(emap)

        self.stdout.write(self.style.SUCCESS(
            f"buttons: set {done}, left {skipped} untouched, unmatched {unmatched or 'none'}"))
        self.stdout.write(self.style.SUCCESS(
            f"text:    set {tdone}, left {tskipped} untouched, unmatched {tunmatched or 'none'}"))
        self.stdout.write(self.style.SUCCESS(
            f"glyphs:  set {gset} literal-emoji themes (auto-applied to all message text)"))
        self.stdout.write("↻ now run:  docker compose restart bot   (to reload the emoji caches)")

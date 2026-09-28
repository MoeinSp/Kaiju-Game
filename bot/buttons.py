"""One place that builds every inline button in the bot.

Telegram's Bot API supports two things on a button that plain text can't express:

* ``style`` — the button's colour (``primary`` blue / ``success`` green /
  ``danger`` red). Only rendered by Telegram clients released after 9 Feb 2026;
  older clients just show an unstyled button, so colour must never be the *only*
  way a player can tell two buttons apart.
* ``icon_custom_emoji_id`` — a Premium custom emoji shown before the label.
  Available because the bot owner has Telegram Premium (see game/button_emoji.py).

Both degrade gracefully. When no Premium icon is configured for a key, the plain
unicode glyph goes into the label instead, so a client that supports neither
field still shows a sensible, distinguishable button. The two are mutually
exclusive — Telegram draws the icon before the label, so showing both would
render the emoji twice. That's why ``btn()`` takes an ``emoji_key`` rather than
a pre-formatted label: it has to decide which of the two to use.

Call sites pass a **role**, not a colour (see game/button_style.py). ``btn()``
resolves the role through the configurable palette, so retuning the whole bot's
colour scheme — from the web panel or a loadout — never touches a handler.
"""

import re
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from game.button_emoji import BUTTON_EMOJI_DEFS, get_button_icon, get_button_label_emoji
from game.button_style import resolve_style

# A leading/trailing emoji cluster (broad unicode emoji ranges + geometric shapes like ◀ ▶
# + variation selectors / ZWJ joins), optionally followed by a space. Used to detect/strip
# an emoji a call site baked into the label so it never renders alongside the button's Premium icon.
_LEADING_EMOJI = re.compile(
    r"^\s*[\U0001F000-\U0001FAFF\u25A0-\u25FF☀-➿←-⇿⬀-⯿⌀-⏿]"
    r"[️‍\U0001F000-\U0001FAFF\u25A0-\u25FF☀-➿⬀-⯿]*\s*"
)
_TRAILING_EMOJI = re.compile(
    r"\s*[\U0001F000-\U0001FAFF\u25A0-\u25FF☀-➿←-⇿⬀-⯿⌀-⏿]"
    r"[️‍\U0001F000-\U0001FAFF\u25A0-\u25FF☀-➿⬀-⯿]*\s*$"
)

_INFER_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("لغو", "انصراف"), "btn_cancel"),
    (("تأیید", "تایید"), "btn_confirm"),
    (("قبلی",), "btn_prev"),
    (("بعدی",), "btn_next"),
    (("بازگشت",), "btn_back"),
    (("حذف",), "btn_delete"),
    (("تغذیه", "غذا"), "btn_feed"),
    (("تمرین",), "btn_train"),
    (("تسریع", "سریع"), "btn_speedup"),
    (("جستجو",), "btn_search"),
    (("تنظیمات",), "btn_settings"),
    (("بررسی مجدد", "تازه‌سازی", "بروزرسانی"), "btn_recheck"),
    (("آهنگری",), "btn_forge"),
    (("صرافی", "مبادله"), "btn_exchange"),
    (("دانجن",), "btn_campaign"),
    (("ماموریت", "مأموریت"), "btn_missions"),
    (("پروفایل",), "btn_profile"),
    (("رتبه‌بندی", "رتبه بندی"), "btn_rank"),
    (("آرنا",), "btn_arena"),
    (("کوله", "تجهیزات"), "btn_inventory"),
    (("کلکسیون",), "btn_collection"),
    (("پرورش", "غار"), "btn_breeding"),
    (("ترکیب", "فیوژن"), "btn_fusion"),
    (("پژوهش", "آزمایشگاه"), "btn_research"),
    (("کارگران", "کارگر"), "btn_workers"),
    (("ساختمان", "ساختمون"), "btn_buildings"),
    (("فروشگاه", "شاپ"), "btn_shop"),
    (("دستاورد",), "btn_achievements"),
    (("بتلپاس", "بتل‌پاس", "پاس فصلی", "پاس ماهانه"), "btn_battlepass"),
    (("رویداد",), "btn_events"),
    (("لیگ",), "btn_league"),
    (("دعوت", "زیرمجموعه"), "btn_referral"),
    (("دانشنامه",), "btn_codex"),
    (("القاب", "عناوین", "لقب"), "btn_titles"),
    (("گردونه",), "btn_wheel"),
    (("کازینو",), "btn_casino"),
    (("اتحاد",), "btn_alliance"),
]

_INFER_LEADING_GLYPHS: dict[str, str] = {
    "❌": "btn_cancel",
    "❎": "btn_cancel",
    "✅": "btn_confirm",
    "✔️": "btn_confirm",
    "✔": "btn_confirm",
    "☑️": "btn_confirm",
    "☑": "btn_confirm",
    "◀️": "btn_prev",
    "◀": "btn_prev",
    "▶️": "btn_next",
    "▶": "btn_next",
    "🔙": "btn_back",
    "⬅️": "btn_back",
    "⬅": "btn_back",
    "🗑️": "btn_delete",
    "🗑": "btn_delete",
    "🍖": "btn_feed",
    "🥩": "btn_feed",
    "🏋️": "btn_train",
    "🏋": "btn_train",
    "⚡️": "btn_speedup",
    "⚡": "btn_speedup",
    "🔍": "btn_search",
    "🔎": "btn_search",
    "⚙️": "btn_settings",
    "⚙": "btn_settings",
    "🍽️": "btn_feed",
    "🍽": "btn_feed",
    "🎒": "btn_inventory",
    "🗂️": "btn_collection",
    "🗂": "btn_collection",
    "🧬": "btn_creature",
    "🔧": "btn_upgrade",
    "🎯": "btn_missions",
    "👤": "btn_profile",
    "🏆": "btn_rank",
    "🏹": "btn_hunt",
    "⚔️": "btn_attack",
    "⚔": "btn_attack",
    "🐲": "btn_raid_rank",
    "📊": "btn_report",
    "🦋": "btn_wings",
    "🛡️": "btn_armor",
    "🛡": "btn_armor",
    "🦷": "btn_fangs",
    "☠️": "btn_poison",
    "🧪": "btn_fusion",
    "🔬": "btn_research",
    "🕳️": "btn_breeding",
    "🕳": "btn_breeding",
    "⚒️": "btn_forge",
    "⚒": "btn_forge",
    "💰": "btn_collect",
    "🏗️": "btn_buildings",
    "🏗": "btn_buildings",
    "📦": "btn_biocrate",
    "💠": "btn_diamond_box",
    "🎡": "btn_wheel",
    "🤝": "btn_alliance",
    "🔒": "btn_locked",
    "📡": "btn_join",
    "🔄": "btn_recheck",
    "🛠️": "btn_admin",
    "🛠": "btn_admin",
    "📢": "btn_broadcast",
    "🗺️": "btn_campaign",
    "🗺": "btn_campaign",
    "🎖️": "btn_league",
    "🎖": "btn_league",
    "📖": "btn_codex",
    "🎁": "btn_referral",
    "🎟️": "btn_battlepass",
    "🎟": "btn_battlepass",
    "⏳": "btn_events",
    "🎰": "btn_banner",
    "🛒": "btn_shop",
    "👷": "btn_workers",
    "👷‍♂️": "btn_workers",
    "💤": "btn_idle",
    "🏅": "btn_achievements",
    "👑": "btn_titles",
    "⭐": "btn_vip",
    "🌟": "btn_vip",
    "🛍️": "btn_items",
    "🛍": "btn_items",
    "✨": "btn_skill",
    "🏳️": "btn_forfeit",
    "🏳": "btn_forfeit",
    "🐣": "btn_hatch",
    "♻️": "btn_reset",
    "♻": "btn_reset",
    "🚀": "btn_exp_launch",
    "🏷": "btn_bm_bid",
    "💎": "btn_diamond",
    "🥾": "btn_kick",
    "👥": "btn_members",
    "✏️": "btn_edit",
    "✏": "btn_edit",
    "📋": "btn_list",
    "📨": "btn_requests",
    "🏦": "btn_vault",
    "💳": "btn_confirm_pay",
    "🦖": "btn_hub_creature",
    "🏰": "btn_hub_base",
    "🌐": "btn_hub_city",
}


def infer_emoji_key(label: str) -> str | None:
    """Infer a BUTTON_EMOJI_DEFS key from the label text or its leading emoji."""
    if not label:
        return None
    for keywords, key in _INFER_KEYWORDS:
        if any(kw in label for kw in keywords):
            return key
    m = _LEADING_EMOJI.match(label)
    if m:
        glyph = m.group().strip()
        if glyph in _INFER_LEADING_GLYPHS:
            return _INFER_LEADING_GLYPHS[glyph]
        norm = glyph.replace("\ufe0f", "").replace("\ufe0e", "").strip()
        for k, v in _INFER_LEADING_GLYPHS.items():
            if k.replace("\ufe0f", "").replace("\ufe0e", "").strip() == norm:
                return v
    return None

# Semantic roles. Call sites say what a button *means*; game.button_style decides
# what colour that currently is. Kept as module constants (rather than bare
# strings at the call site) so a typo is an ImportError instead of a silently
# uncoloured button.
NAV = "nav"  # moves between screens — the bulk of a menu
BACK = "back"  # the ubiquitous back button
LIST = "list"  # one row of a list of things to pick from
PRIMARY = "primary"  # the one main action of a screen
CONFIRM = "confirm"  # "yes, do it" in a two-step dialog
BUILD = "build"  # construct / upgrade / feed / collect — constructive actions
SHOP = "shop"  # spends a resource: crates, wheel, diamond finishes
BATTLE = "battle"  # hunt / arena / raid — can fail, costs something
DANGER = "danger"  # delete / cancel / leave / ban
ADMIN = "admin"  # owner-only panel buttons

# Back-compat alias: SUCCESS used to be a raw KeyboardButtonStyle. It now means
# "the confirm role", which is what every old call site actually intended.
SUCCESS = CONFIRM


_TG_EMOJI_HTML = re.compile(r"<tg-emoji\b[^>]*>(.*?)</tg-emoji>", re.DOTALL)
_ANY_HTML = re.compile(r"<[^>]+>")


def btn(
    label: str,
    *,
    emoji_key: str | None = None,
    style: str | None = None,
    **kwargs,
) -> InlineKeyboardButton:
    """Builds a button with an optional Premium icon and role-derived colour.

    ``emoji_key`` is a key from game.button_emoji.BUTTON_EMOJI_DEFS. Its unicode
    fallback is prefixed to the label, and if the owner has set a Premium emoji
    for that key it's used as the button's icon instead. ``style`` is a *role*
    from the constants above, not a Telegram colour. Pass the rest
    (``callback_data``, ``url``, …) through as usual.
    """
    if "<" in label and ">" in label:
        label = _TG_EMOJI_HTML.sub(r"\1", label)
        label = _ANY_HTML.sub("", label)

    if emoji_key is None:
        emoji_key = infer_emoji_key(label)
    elif emoji_key not in BUTTON_EMOJI_DEFS and f"btn_{emoji_key}" in BUTTON_EMOJI_DEFS:
        emoji_key = f"btn_{emoji_key}"

    if emoji_key is not None:
        icon = get_button_icon(emoji_key)
        has_leading = bool(_LEADING_EMOJI.match(label))
        has_trailing = bool(_TRAILING_EMOJI.search(label))

        stripped = label
        if has_leading:
            stripped = _LEADING_EMOJI.sub("", stripped, count=1)
        if has_trailing:
            stripped = _TRAILING_EMOJI.sub("", stripped, count=1)

        if icon:
            # Telegram draws the icon *before* the label, so any emoji baked into the
            # label would render a SECOND time next to the Premium icon. Strip it.
            kwargs["icon_custom_emoji_id"] = icon
            if stripped.strip():
                label = stripped.strip()
            elif emoji_key in ("btn_prev", "btn_back"):
                label = "قبلی"
            elif emoji_key == "btn_next":
                label = "بعدی"
            else:
                label = get_button_label_emoji(emoji_key) or label
        else:
            # icon is None: render text fallback. Strip hardcoded leading/trailing
            # emoji from label and prefix configured fallback.
            fallback = get_button_label_emoji(emoji_key)
            base_text = stripped.strip()
            if base_text:
                label = f"{fallback} {base_text}" if fallback else base_text
            else:
                label = fallback if fallback else label

    # emoji_key doubles as the per-button colour key, so a button that already
    # has its own identity in the registry can also have its own colour
    resolved = resolve_style(style, emoji_key)
    if resolved is not None:
        kwargs["style"] = resolved
    return InlineKeyboardButton(label, **kwargs)


def back_btn(callback_data: str, label: str = "بازگشت") -> InlineKeyboardButton:
    """The ubiquitous back button — always the same look and always the same role,
    so recolouring "back" anywhere recolours it everywhere."""
    return btn(label, emoji_key="btn_back", style=BACK, callback_data=callback_data)


def back_only_keyboard(callback_data: str = "menu:me", label: str = "بازگشت"):
    """A screen whose only control is "go back".

    Several screens (rank, missions, profile, …) used to end with no keyboard at
    all. That was survivable when every tap posted a new message, since the menu
    was still sitting above; now that screens edit in place, a keyboard-less
    screen is a dead end the player can only escape by retyping a command."""
    from telegram import InlineKeyboardMarkup

    return InlineKeyboardMarkup([[back_btn(callback_data, label)]])


def _clone_btn_with_style(b: InlineKeyboardButton, style: str | None) -> InlineKeyboardButton:
    kwargs = {}
    for attr in (
        "callback_data", "url", "web_app", "login_url",
        "switch_inline_query", "switch_inline_query_current_chat",
        "switch_inline_query_chosen_chat", "callback_game", "pay",
        "icon_custom_emoji_id",
    ):
        val = getattr(b, attr, None)
        if val is not None:
            kwargs[attr] = val
    if style is not None:
        kwargs["style"] = style
    return InlineKeyboardButton(b.text, **kwargs)


def enforce_keyboard_symmetry(rows: list[list[InlineKeyboardButton]]) -> list[list[InlineKeyboardButton]]:
    """Enforces absolute color and style symmetry across every row in a keyboard grid.
    
    If a row contains multiple buttons (e.g. 2 or 3 buttons), this function ensures
    every button in that row shares the same Telegram button style (primary/success/danger/none)
    and role, preventing color clashes or asymmetric button designs across all game levels
    and dynamic unlock states.
    """
    if not rows:
        return []
    new_rows = []
    for row in rows:
        if not row:
            continue
        if len(row) <= 1:
            new_rows.append(list(row))
            continue
        # Find dominant non-empty style in row
        dominant_style = None
        for b in row:
            st = getattr(b, "style", None)
            if st:
                dominant_style = st
                break
        if dominant_style:
            new_row = [_clone_btn_with_style(b, dominant_style) for b in row]
            new_rows.append(new_row)
        else:
            new_rows.append(list(row))
    return new_rows


def symmetric_markup(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    """Builds an InlineKeyboardMarkup after strictly enforcing color symmetry across all rows."""
    return InlineKeyboardMarkup(enforce_keyboard_symmetry(rows))


def get_main_reply_keyboard():
    """Reply keyboard for fast one-tap navigation in private chat.
    one_time_keyboard=True auto-hides the keyboard after a button is pressed,
    and is_persistent=False allows the user to collapse/toggle it anytime."""
    from telegram import KeyboardButton, ReplyKeyboardMarkup
    from game.button_emoji import get_button_label_emoji

    b_battle = f"{get_button_label_emoji('btn_hub_battle') or '⚔️'} نبرد و ماجراجویی"
    b_creature = f"{get_button_label_emoji('btn_hub_creature') or '🦖'} هیولا و تجهیزات"
    b_base = f"{get_button_label_emoji('btn_hub_base') or '🏰'} پایگاه و منابع"
    b_shop = f"{get_button_label_emoji('btn_hub_shop') or '🛒'} فروشگاه و بازار"
    b_city = f"{get_button_label_emoji('btn_hub_city') or '🌐'} شهر و جوایز"
    b_me = f"{get_button_label_emoji('btn_profile') or '👤'} آزمایشگاه من"

    keyboard = [
        [KeyboardButton(b_battle), KeyboardButton(b_creature)],
        [KeyboardButton(b_base), KeyboardButton(b_shop)],
        [KeyboardButton(b_city), KeyboardButton(b_me)],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True, is_persistent=False)



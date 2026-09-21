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

from game.button_emoji import get_button_icon, get_button_label_emoji
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

    if emoji_key is not None:
        icon = get_button_icon(emoji_key)
        has_leading = bool(_LEADING_EMOJI.match(label))
        has_trailing = bool(_TRAILING_EMOJI.search(label))
        if icon is not None:
            # Telegram draws the icon *before* the label, so any emoji baked into the
            # label would render a SECOND time next to the Premium icon. Strip it.
            stripped = label
            if has_leading:
                stripped = _LEADING_EMOJI.sub("", stripped, count=1)
            if has_trailing:
                stripped = _TRAILING_EMOJI.sub("", stripped, count=1)
            if stripped.strip():
                # there's real text left → show the Premium icon + that text
                kwargs["icon_custom_emoji_id"] = icon
                label = stripped.strip()
            elif emoji_key in ("btn_prev", "btn_back"):
                label = "قبلی"
                kwargs["icon_custom_emoji_id"] = icon
            elif emoji_key == "btn_next":
                label = "بعدی"
                kwargs["icon_custom_emoji_id"] = icon
            # else: the label was ONLY an emoji without known text fallback:
            # keep plain emoji and skip icon
        elif not has_leading and not has_trailing:
            # no Premium icon and the label has no emoji of its own → prefix the
            # key's unicode fallback so the button still has an identifying glyph
            fallback = get_button_label_emoji(emoji_key)
            if fallback:
                label = f"{fallback} {label}"
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


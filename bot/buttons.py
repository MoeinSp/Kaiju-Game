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

from telegram import InlineKeyboardButton

from game.button_emoji import get_button_icon, get_button_label_emoji
from game.button_style import resolve_style

# Semantic roles. Call sites say what a button *means*; game.button_style decides
# what colour that currently is. Kept as module constants (rather than bare
# strings at the call site) so a typo is an ImportError instead of a silently
# uncoloured button.
NAV = "nav"  # moves between screens — deliberately uncoloured by default
BACK = "back"  # the ubiquitous back button
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
    if emoji_key is not None:
        icon = get_button_icon(emoji_key)
        if icon is not None:
            # Telegram draws the icon *before* the label, so keeping the unicode
            # glyph in the label too would show the emoji twice. The icon replaces
            # the fallback rather than joining it.
            kwargs["icon_custom_emoji_id"] = icon
        else:
            fallback = get_button_label_emoji(emoji_key)
            if fallback and not label.startswith(fallback):
                label = f"{fallback} {label}"
    resolved = resolve_style(style)
    if resolved is not None:
        kwargs["style"] = resolved
    return InlineKeyboardButton(label, **kwargs)


def back_btn(callback_data: str, label: str = "بازگشت") -> InlineKeyboardButton:
    """The ubiquitous back button — always the same look. It's navigation, not an
    action, so it gets its own role and is uncoloured by default."""
    return btn(label, emoji_key="btn_back", style=BACK, callback_data=callback_data)

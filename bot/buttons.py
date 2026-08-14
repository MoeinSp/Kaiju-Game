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
"""

from telegram import InlineKeyboardButton
from telegram.constants import KeyboardButtonStyle

from game.button_emoji import get_button_icon, get_button_label_emoji

# semantic aliases — call sites say what a button *means*, not what colour it is,
# so the palette can be retuned in one place
PRIMARY = KeyboardButtonStyle.PRIMARY  # the main action of a screen
SUCCESS = KeyboardButtonStyle.SUCCESS  # confirm / build / acquire
DANGER = KeyboardButtonStyle.DANGER  # destructive / cancel / leave


def btn(
    label: str,
    *,
    emoji_key: str | None = None,
    style: str | None = None,
    **kwargs,
) -> InlineKeyboardButton:
    """Builds a button with an optional Premium icon and colour.

    ``emoji_key`` is a key from game.button_emoji.BUTTON_EMOJI_DEFS. Its unicode
    fallback is prefixed to the label, and if the owner has set a Premium emoji
    for that key it's additionally attached as the button's icon. Pass the rest
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
    if style is not None:
        kwargs["style"] = style
    return InlineKeyboardButton(label, **kwargs)


def back_btn(callback_data: str, label: str = "بازگشت") -> InlineKeyboardButton:
    """The ubiquitous back button — always the same look, never coloured (it's
    navigation, not an action, and colouring it would dilute the palette)."""
    return btn(label, emoji_key="btn_back", callback_data=callback_data)

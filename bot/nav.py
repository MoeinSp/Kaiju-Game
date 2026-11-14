"""Where a screen was opened from, so its «بازگشت» goes back THERE.

Several screens are reachable from more than one place: the serum lab from the main menu,
the «پایگاه» hub, «ساختمان‌ها» and «انبار»; «انبار» from «پایگاه», «ساختمان‌ها» and the
serum lab; the arena chests from the arena, the «نبرد» hub and «انبار». A fixed back
button sent a player who came from the main menu into «ساختمان‌ها» (owner, 2026-11-14).

The entry button carries a short origin code (``sr:home:m``, ``bp:home:b``, ``arena_chests:bp``);
the screen remembers it per player in ``context.user_data``, and its own inner screens
(which come back with the plain callback, e.g. ``sr:home``) keep it. In-memory only: after
a bot restart a screen falls back to its natural parent.
"""

from __future__ import annotations

_KEY = "nav_back"

# origin code → (callback, label)
ORIGINS: dict[str, tuple[str, str]] = {
    "m": ("menu:me", "منوی اصلی"),
    "hub": ("menu:hub_base", "پایگاه"),
    "b": ("menu:buildings", "ساختمان‌ها"),
    "bp": ("bp:home", "انبار"),
    "sr": ("sr:home", "آزمایشگاه سرم"),
    "ar": ("menu:arena", "آرنا"),
    "bt": ("menu:hub_battle", "نبرد"),
}


def remember(context, screen: str, origin: str | None) -> None:
    """Store `origin` (a key of ORIGINS) for `screen`; an unknown/None origin keeps the old one."""
    if origin in ORIGINS and context is not None:
        context.user_data.setdefault(_KEY, {})[screen] = origin


def back_of(context, screen: str, default: str) -> tuple[str, str]:
    """(callback, label) of the back button for `screen`."""
    origin = None
    if context is not None:
        origin = (context.user_data.get(_KEY) or {}).get(screen)
    return ORIGINS[origin if origin in ORIGINS else default]


def origin_of(data: str | None, plain: str, menu_key: str | None = None, menu_origin: str = "hub") -> str | None:
    """The origin a callback names: «<plain>:<code>» → code, «menu:<menu_key>» → menu_origin,
    no callback (a typed word / command in the DM) → "m", the plain callback → None (keep)."""
    if data is None:
        return "m"
    if menu_key and data == f"menu:{menu_key}":
        return menu_origin
    if data.startswith(plain + ":"):
        return data[len(plain) + 1:]
    return None

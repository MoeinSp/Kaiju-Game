"""Which colour each *kind* of button gets.

Telegram only offers three button colours (primary / success / danger) plus the
default uncoloured look. Four looks is a tiny palette, so the thing worth
configuring isn't "what colour is this one button" — it's **what colour means
what**. Call sites therefore name a semantic *role* ("this is a build action",
"this is destructive") and this module maps roles to colours.

Two consequences that matter:

* The palette can be retuned globally — from the web panel, or by editing
  ``ROLE_DEFS`` — without touching a single handler.
* Colour never carries information on its own. Older Telegram clients (before
  9 Feb 2026) ignore ``style`` entirely, so every button still has to be
  distinguishable by its label and icon alone.

The default palette deliberately leaves navigation **uncoloured**. An earlier
version painted every menu entry blue, which meant a fifteen-button menu was a
wall of blue with two green buttons floating in it — colour stopped meaning
anything. Now colour marks the things a player acts on, and the menu recedes.

Caching follows the same hard rule as game.button_emoji: the cache is never
lazily populated on read, because buttons are built inside async handlers and a
lazy Django query there raises SynchronousOnlyOperation. bot.main warms it at
startup; every write refreshes it.
"""

from bio_lab.models import ButtonStyleOverride

# The three colours Telegram actually renders, plus "no colour". Values are the
# raw Bot API strings (telegram.constants.KeyboardButtonStyle members equal them),
# kept as plain strings here so this module stays importable without the bot lib
# and so the web panel can store/serialise them directly.
STYLE_NONE = ""
STYLE_PRIMARY = "primary"
STYLE_SUCCESS = "success"
STYLE_DANGER = "danger"

STYLE_CHOICES: dict[str, tuple[str, str]] = {
    # value -> (Persian label, CSS colour used by the web panel preview)
    STYLE_NONE: ("بی‌رنگ (پیش‌فرض تلگرام)", "#3f4a56"),
    STYLE_PRIMARY: ("آبی — کنش اصلی", "#2f81f7"),
    STYLE_SUCCESS: ("سبز — مثبت/سازنده", "#2ea043"),
    STYLE_DANGER: ("قرمز — مخرب/پرریسک", "#da3633"),
}

# role -> (Persian label, default style, what the role is for)
ROLE_DEFS: dict[str, tuple[str, str, str]] = {
    "nav": (
        "ناوبری و منو",
        STYLE_NONE,
        "دکمه‌هایی که فقط بین صفحه‌ها جابه‌جا می‌کنن. بی‌رنگ‌ان تا رنگ‌ها برای کنش‌های واقعی بمونه.",
    ),
    "back": (
        "بازگشت",
        STYLE_NONE,
        "دکمه‌ی بازگشت که تقریباً زیر هر صفحه‌ای هست.",
    ),
    "primary": (
        "کنش اصلی صفحه",
        STYLE_PRIMARY,
        "مهم‌ترین کاری که توی اون صفحه می‌شه کرد — معمولاً یکی دو تا در هر صفحه.",
    ),
    "confirm": (
        "تأیید نهایی",
        STYLE_SUCCESS,
        "دکمه‌ی «آره انجامش بده» توی دیالوگ‌های دومرحله‌ای.",
    ),
    "build": (
        "ساخت، ارتقا و جمع‌آوری",
        STYLE_SUCCESS,
        "ساختمون‌ها، آهنگری، تغذیه/تمرین، جمع‌آوری منابع — کنش‌های سازنده.",
    ),
    "shop": (
        "خرید و شانس",
        STYLE_SUCCESS,
        "باکس ژنتیکی، جعبه‌های الماسی، گردونه، تموم‌کردن با الماس — هرجا منبع خرج می‌شه.",
    ),
    "battle": (
        "نبرد و حمله",
        STYLE_DANGER,
        "شکار، آرنا، حمله، شبیخون — کنش‌های پرریسک که می‌تونن شکست بخورن.",
    ),
    "danger": (
        "مخرب و لغو",
        STYLE_DANGER,
        "حذف، لغو، خروج از اتحاد، بن — کارهایی که برگردوندنشون سخته.",
    ),
    "admin": (
        "پنل مدیریت",
        STYLE_PRIMARY,
        "دکمه‌های مخصوص سازنده توی /admin.",
    ),
}

ROLE_LABELS: dict[str, str] = {key: label for key, (label, _s, _d) in ROLE_DEFS.items()}
ROLE_DEFAULTS: dict[str, str] = {key: style for key, (_l, style, _d) in ROLE_DEFS.items()}
ROLE_HELP: dict[str, str] = {key: help_ for key, (_l, _s, help_) in ROLE_DEFS.items()}

# Never lazily populated — see the module docstring.
_cache: dict[str, str] = {}


def refresh_cache() -> None:
    """Reload role overrides from the DB. Sync context only (startup or right
    after a write) — never from inside an async handler."""
    global _cache
    _cache = {
        o.role: o.style for o in ButtonStyleOverride.objects.all() if o.role in ROLE_DEFS
    }


def resolve_style(role: str | None) -> str | None:
    """The Bot API ``style`` value for a role, or None for "no colour".

    Pure in-memory read, safe from async handler code. An unknown role resolves
    to None rather than raising: a mis-typed role should make a button plain,
    not crash a handler mid-conversation.
    """
    if role is None:
        return None
    style = _cache.get(role, ROLE_DEFAULTS.get(role, STYLE_NONE))
    return style or None


def current_palette() -> dict[str, str]:
    """role -> effective style, for every known role. Used by the web panel and
    by loadout snapshots."""
    return {role: _cache.get(role, default) for role, default in ROLE_DEFAULTS.items()}


def set_role_style(role: str, style: str) -> None:
    if role not in ROLE_DEFS:
        raise ValueError(f"unknown button role: {role}")
    if style not in STYLE_CHOICES:
        raise ValueError(f"unknown button style: {style!r}")
    ButtonStyleOverride.objects.update_or_create(role=role, defaults={"style": style})
    refresh_cache()


def clear_role_style(role: str) -> bool:
    """Drop the override so the role falls back to its ROLE_DEFS default."""
    deleted, _ = ButtonStyleOverride.objects.filter(role=role).delete()
    refresh_cache()
    return deleted > 0


def reset_palette() -> None:
    ButtonStyleOverride.objects.all().delete()
    refresh_cache()


def apply_palette(palette: dict[str, str]) -> None:
    """Replace the whole palette at once — the operation a loadout switch needs.

    Roles the snapshot doesn't mention are reset to their defaults rather than
    left alone, so switching loadouts is a true swap and can't leave a stray
    override from the previous theme behind.
    """
    ButtonStyleOverride.objects.all().delete()
    rows = [
        ButtonStyleOverride(role=role, style=style)
        for role, style in palette.items()
        if role in ROLE_DEFS and style in STYLE_CHOICES and style != ROLE_DEFAULTS[role]
    ]
    ButtonStyleOverride.objects.bulk_create(rows)
    refresh_cache()

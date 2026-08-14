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

**Every role is coloured by default.** An earlier iteration left navigation
uncoloured on the theory that colour should be scarce to stay meaningful; in
practice a plain button sitting next to coloured ones just looks unfinished, and
Telegram's uncoloured style is visually weak rather than neutral. Meaning is
carried by *grouping* instead: blue moves you around, green gains you something,
red risks or destroys something. Three colours, consistently applied, still tell
the player what a button does.

On top of roles there's a per-button layer (``ButtonKeyStyle``, keyed by the same
registry as game/button_emoji.py) for the cases where one specific button should
break from its role. Resolution is per-button → role → role default.

Caching follows the same hard rule as game.button_emoji: neither cache is ever
lazily populated on read, because buttons are built inside async handlers and a
lazy Django query there raises SynchronousOnlyOperation. bot.main warms them at
startup; every write refreshes them.
"""

from bio_lab.models import ButtonKeyStyle, ButtonStyleOverride

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
#
# Every role has a colour: an uncoloured button next to coloured ones reads as
# unfinished rather than restrained. The palette keeps meaning by *grouping*
# instead — blue is "move around", green is "gain something", red is "risk or
# destroy" — so three colours still tell the player what a button will do.
ROLE_DEFS: dict[str, tuple[str, str, str]] = {
    "nav": (
        "ناوبری و منو",
        STYLE_PRIMARY,
        "دکمه‌هایی که بین صفحه‌ها جابه‌جا می‌کنن — بدنه‌ی اصلی منو.",
    ),
    "back": (
        "بازگشت",
        STYLE_PRIMARY,
        "دکمه‌ی بازگشت که تقریباً زیر هر صفحه‌ای هست.",
    ),
    "list": (
        "انتخاب از لیست",
        STYLE_PRIMARY,
        "ردیف‌های لیست: انتخاب هیولا، آیتم، ساختمون، کانال — هر جایی که از یه فهرست یکی رو برمی‌داری.",
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
        STYLE_DANGER,
        "دکمه‌های مخصوص سازنده توی /admin.",
    ),
}

# "follow the role" — what a per-button override holds when it isn't overriding.
# Distinct from STYLE_NONE (""), which is an override that says *explicitly*
# uncoloured. Collapsing the two would make "no opinion" and "no colour" the
# same value and the per-button page could never turn a colour off.
STYLE_INHERIT = "inherit"

KEY_STYLE_CHOICES: dict[str, tuple[str, str]] = {
    STYLE_INHERIT: ("پیروی از نقش", "#3f4a56"),
    **STYLE_CHOICES,
}

ROLE_LABELS: dict[str, str] = {key: label for key, (label, _s, _d) in ROLE_DEFS.items()}
ROLE_DEFAULTS: dict[str, str] = {key: style for key, (_l, style, _d) in ROLE_DEFS.items()}
ROLE_HELP: dict[str, str] = {key: help_ for key, (_l, _s, help_) in ROLE_DEFS.items()}

# Neither cache is ever lazily populated — see the module docstring.
_cache: dict[str, str] = {}
_key_cache: dict[str, str] = {}


def refresh_cache() -> None:
    """Reload role and per-button overrides from the DB. Sync context only
    (startup or right after a write) — never from inside an async handler."""
    global _cache, _key_cache
    _cache = {
        o.role: o.style for o in ButtonStyleOverride.objects.all() if o.role in ROLE_DEFS
    }
    _key_cache = {o.key: o.style for o in ButtonKeyStyle.objects.all()}


def resolve_style(role: str | None, key: str | None = None) -> str | None:
    """The Bot API ``style`` value for a button, or None for "no colour".

    Resolution order is per-button override → role → role default. The per-button
    layer exists because roles are broad by design: sooner or later one specific
    button wants to stand out (or blend in) without dragging every sibling in its
    role along with it.

    Pure in-memory read, safe from async handler code. An unknown role resolves
    to None rather than raising: a mis-typed role should make a button plain,
    not crash a handler mid-conversation.
    """
    if key is not None:
        override = _key_cache.get(key)
        if override is not None and override != STYLE_INHERIT:
            return override or None
    if role is None:
        return None
    style = _cache.get(role, ROLE_DEFAULTS.get(role, STYLE_NONE))
    return style or None


# --- per-button overrides --------------------------------------------------


def current_key_styles() -> dict[str, str]:
    """key -> stored override. Keys with no override are simply absent, which the
    panel renders as "پیروی از نقش"."""
    return dict(_key_cache)


def set_key_style(key: str, style: str) -> None:
    """`style` may be STYLE_INHERIT to drop the override, "" for explicitly
    uncoloured, or one of the three colours."""
    if style not in KEY_STYLE_CHOICES:
        raise ValueError(f"unknown button style: {style!r}")
    if style == STYLE_INHERIT:
        clear_key_style(key)
        return
    ButtonKeyStyle.objects.update_or_create(key=key, defaults={"style": style})
    refresh_cache()


def clear_key_style(key: str) -> bool:
    deleted, _ = ButtonKeyStyle.objects.filter(key=key).delete()
    refresh_cache()
    return deleted > 0


def apply_key_styles(styles: dict[str, str]) -> None:
    """Whole-set replace, matching apply_palette — a loadout switch must not
    leave a stray per-button colour from the previous theme behind."""
    ButtonKeyStyle.objects.all().delete()
    ButtonKeyStyle.objects.bulk_create(
        [
            ButtonKeyStyle(key=key, style=style)
            for key, style in styles.items()
            if style in STYLE_CHOICES  # STYLE_INHERIT is stored as "no row"
        ]
    )
    refresh_cache()


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
    """Back to stock: both the role palette and every per-button override."""
    ButtonStyleOverride.objects.all().delete()
    ButtonKeyStyle.objects.all().delete()
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

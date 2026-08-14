"""Premium custom-emoji icons for inline-keyboard buttons.

Deliberately separate from game.emoji (which handles message-body emoji):

* body emoji are rendered as `<tg-emoji emoji-id="...">x</tg-emoji>` inside
  parse_mode="HTML" text
* button icons are a bare `icon_custom_emoji_id` string on InlineKeyboardButton,
  with **at most one per button**, always shown before the label

Because a button can only carry one icon, these keys describe *buttons*, not
concepts — "the attack button" rather than "the battle emoji" — so the owner can
give the attack button its own icon without touching the battle emoji used in
message text.

Same caching rule as game.emoji: `refresh_cache()` after every write so lookups
never hit the DB from async handler code.
"""

from bio_lab.models import ButtonEmojiOverride

# key -> (label shown in the admin picker, fallback unicode emoji, category)
BUTTON_EMOJI_DEFS: dict[str, tuple[str, str, str]] = {
    # main navigation
    "btn_creature": ("موجود فعال", "🧬", "nav"),
    "btn_upgrade": ("ارتقا و پرورش", "🔧", "nav"),
    "btn_collection": ("کلکسیون", "🗂", "nav"),
    "btn_inventory": ("تجهیزات", "🎒", "nav"),
    "btn_missions": ("ماموریت‌ها", "🎯", "nav"),
    "btn_profile": ("پروفایل", "👤", "nav"),
    "btn_rank": ("رتبه‌بندی", "🏆", "nav"),
    "btn_back": ("بازگشت", "◀️", "nav"),
    # actions
    "btn_hunt": ("شکار انفرادی", "🏹", "action"),
    "btn_attack": ("حمله", "⚔️", "action"),
    "btn_arena": ("آرنا", "🏆", "action"),
    "btn_feed": ("تغذیه", "🍖", "action"),
    "btn_train": ("تمرین", "🏋️", "action"),
    "btn_fusion": ("فیوژن/ادغام", "🧪", "action"),
    "btn_breeding": ("تکثیر زیستی", "🧫", "action"),
    "btn_forge": ("آهنگری", "⚒", "action"),
    "btn_collect": ("جمع‌آوری", "💰", "action"),
    "btn_build": ("ساخت/ارتقای ساختمون", "🏗", "action"),
    "btn_speedup": ("سریع‌تر کردن", "⚡", "action"),
    # economy
    "btn_biocrate": ("باکس ژنتیکی", "📦", "economy"),
    "btn_diamond_box": ("جعبه‌ی الماسی", "💠", "economy"),
    "btn_wheel": ("گردونه‌ی شانس", "🎡", "economy"),
    "btn_buildings": ("ساختمون‌ها", "🏗", "economy"),
    "btn_alliance": ("اتحاد", "🤝", "economy"),
    "btn_deposit": ("واریز به خزانه", "💰", "economy"),
    "btn_heist": ("شبیخون", "🏴‍☠️", "economy"),
    # confirm / destructive
    "btn_confirm": ("تأیید", "✅", "confirm"),
    "btn_cancel": ("لغو", "❌", "confirm"),
    "btn_delete": ("حذف", "🗑", "confirm"),
    "btn_join": ("عضویت در کانال", "📡", "confirm"),
    "btn_recheck": ("بررسی مجدد", "🔄", "confirm"),
    # admin
    "btn_admin": ("پنل ادمین", "🛠", "admin"),
    "btn_broadcast": ("پیام همگانی", "📢", "admin"),
    "btn_report": ("گزارش", "📊", "admin"),
    "btn_charge": ("شارژ کاربر", "⚡", "admin"),
}

BUTTON_CATEGORY_LABELS: dict[str, str] = {
    "nav": "🧭 ناوبری",
    "action": "⚔️ اکشن‌ها",
    "economy": "💰 اقتصاد",
    "confirm": "✅ تأیید و حذف",
    "admin": "🛠 مدیریت",
}

BUTTON_EMOJI_KEYS: dict[str, str] = {
    key: f"{emoji} {label}" for key, (label, emoji, _cat) in BUTTON_EMOJI_DEFS.items()
}
BUTTON_DEFAULT_EMOJI: dict[str, str] = {
    key: emoji for key, (_label, emoji, _cat) in BUTTON_EMOJI_DEFS.items()
}
BUTTON_CATEGORY_OF: dict[str, str] = {
    key: cat for key, (_label, _emoji, cat) in BUTTON_EMOJI_DEFS.items()
}

# Starts empty rather than None, and is NEVER lazily populated on read. Buttons are
# built inside async handlers, so a lazy DB load here would raise Django's
# SynchronousOnlyOperation — and a purely decorative icon must never be able to
# crash a handler. bot.main warms this once at startup (and set/clear refresh it),
# so a cold cache simply means "no custom icons yet", which degrades to the plain
# unicode fallback instead of blowing up.
_cache: dict[str, ButtonEmojiOverride] = {}


def refresh_cache() -> None:
    """Reload from the DB. Must be called from sync context (startup, or right
    after a write) — never from inside an async handler."""
    global _cache
    _cache = {o.key: o for o in ButtonEmojiOverride.objects.all()}


def get_button_icon(key: str) -> str | None:
    """The custom_emoji_id for InlineKeyboardButton.icon_custom_emoji_id, or None
    when the owner hasn't set one (the button then just shows its unicode fallback).
    Pure in-memory read — safe to call from async handler code."""
    override = _cache.get(key)
    return override.custom_emoji_id if override is not None else None


def get_button_label_emoji(key: str) -> str:
    """Unicode fallback for the button label. Kept even when a custom icon is set:
    clients too old for `icon_custom_emoji_id` still show a sensible label."""
    return BUTTON_DEFAULT_EMOJI.get(key, "")


def set_button_emoji(key: str, custom_emoji_id: str, placeholder: str) -> None:
    ButtonEmojiOverride.objects.update_or_create(
        key=key, defaults={"custom_emoji_id": custom_emoji_id, "placeholder": placeholder}
    )
    refresh_cache()


def clear_button_emoji(key: str) -> bool:
    deleted, _ = ButtonEmojiOverride.objects.filter(key=key).delete()
    refresh_cache()
    return deleted > 0


def list_button_overrides() -> list[ButtonEmojiOverride]:
    return list(ButtonEmojiOverride.objects.order_by("key"))

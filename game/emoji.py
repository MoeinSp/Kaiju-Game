from bio_lab.models import EmojiOverride

# key -> (label, default unicode emoji, category). This is the single registry for
# every icon that's worth letting the owner re-skin with a Telegram Premium custom
# emoji. Deliberately excludes: rarity circles (⚪🔵🟣🟡🔴 — kept as a fixed color
# code so rarity stays instantly recognizable regardless of skin), and anything that
# only ever appears as text on an InlineKeyboardButton — Telegram buttons are plain
# text with no HTML support, so <tg-emoji> can never render there no matter what.
EMOJI_DEFS: dict[str, tuple[str, str, str]] = {
    # resources
    "coin": ("طلا", "💰", "resources"),
    "dna": ("DNA", "🧬", "resources"),
    "energy": ("انرژی", "⚡", "resources"),
    # stats
    "hp": ("HP", "❤️", "stats"),
    "atk": ("ATK", "⚔️", "stats"),
    "def": ("DEF", "🛡", "stats"),
    "spd": ("SPD", "💨", "stats"),
    "poison": ("زهر", "☠️", "stats"),
    "crit": ("کریتیکال", "💥", "stats"),
    "lifesteal": ("جون‌خواری", "🧛", "stats"),
    # elements
    "element_fire": ("عنصر آتش", "🔥", "elements"),
    "element_water": ("عنصر آب", "💧", "elements"),
    "element_earth": ("عنصر خاک", "🪨", "elements"),
    "element_electric": ("عنصر الکتریسیته", "⚡", "elements"),
    # body parts
    "wings": ("بال", "🦋", "body"),
    "fangs": ("نیش", "🦷", "body"),
    # battle
    "battle": ("نبرد/دوئل", "⚔️", "battle"),
    "attack_action": ("حمله", "🗡", "battle"),
    "skill_action": ("اسکیل", "✨", "battle"),
    "forfeit_action": ("تسلیم", "🏳", "battle"),
    "raid_boss": ("هیولای وحشی", "🐲", "battle"),
    "hunt": ("شکار", "🏹", "battle"),
    # social
    "alliance": ("اتحاد", "🤝", "social"),
    "gift": ("هدیه", "🎁", "social"),
    "profile": ("پروفایل", "👤", "social"),
    "users": ("کاربران", "👥", "social"),
    "crown": ("رهبر/محافظ", "👑", "social"),
    # progress / identity
    "creature": ("نماد موجود", "🧬", "progress"),
    "trophy": ("رتبه‌بندی", "🏆", "progress"),
    "celebrate": ("تبریک/لول‌آپ", "🎉", "progress"),
    "mission": ("ماموریت", "🎯", "progress"),
    "medal_gold": ("نشان طلا", "🥇", "progress"),
    "medal_silver": ("نشان نقره", "🥈", "progress"),
    "medal_bronze": ("نشان برنز", "🥉", "progress"),
    "guardian": ("محافظ گروه", "🛡", "progress"),
    "egg": ("موجود تازه", "🥚", "progress"),
    "lab": ("آزمایشگاه/ترکیب", "🧪", "progress"),
    "biocrate": ("باکس ژنتیکی", "📦", "progress"),
    "comet": ("رویداد جهش", "☄️", "progress"),
    # UI
    "confirm": ("تأیید", "✅", "ui"),
    "cancel": ("لغو", "❌", "ui"),
    "warning": ("هشدار", "⚠️", "ui"),
    "banned": ("مسدود", "🚫", "ui"),
    "stats": ("آمار", "📊", "ui"),
    "broadcast": ("پیام همگانی", "📢", "ui"),
    "collection": ("کلکسیون", "🗂", "ui"),
    "settings": ("تنظیمات", "🎨", "ui"),
}

CATEGORY_LABELS: dict[str, str] = {
    "resources": "💰 منابع",
    "stats": "📈 استت‌ها",
    "elements": "🌍 عناصر",
    "body": "🦴 اعضای بدن",
    "battle": "⚔️ نبرد",
    "social": "🤝 اجتماعی",
    "progress": "🏆 پیشرفت",
    "ui": "🖥 رابط کاربری",
}

# back-compat flat views used by owner.py's key pickers and help text
EMOJI_KEYS: dict[str, str] = {key: f"{emoji} {label}" for key, (label, emoji, _cat) in EMOJI_DEFS.items()}
DEFAULT_EMOJI: dict[str, str] = {key: emoji for key, (_label, emoji, _cat) in EMOJI_DEFS.items()}
CATEGORY_OF: dict[str, str] = {key: cat for key, (_label, _emoji, cat) in EMOJI_DEFS.items()}

_cache: dict[str, EmojiOverride] | None = None


def _load_cache() -> dict[str, EmojiOverride]:
    global _cache
    _cache = {o.key: o for o in EmojiOverride.objects.all()}
    return _cache


def refresh_cache() -> None:
    """Call after any EmojiOverride write so lookups reflect it without a bot restart."""
    _load_cache()


def get_emoji(key: str, fallback: str | None = None) -> str:
    """Returns HTML for `key`: a <tg-emoji> wrapper if the owner set a Premium custom
    emoji for it, otherwise the plain unicode default (from EMOJI_DEFS, or `fallback`
    if given). Safe to call from anywhere — reads an in-memory cache, not the
    database, after the first (eager-warmed) load. Only usable in message BODY text
    sent with parse_mode="HTML" — Telegram button labels are plain text and can
    never render <tg-emoji>, so never call this for InlineKeyboardButton text."""
    cache = _cache if _cache is not None else _load_cache()
    override = cache.get(key)
    if override is not None:
        return f'<tg-emoji emoji-id="{override.custom_emoji_id}">{override.placeholder}</tg-emoji>'
    return fallback if fallback is not None else DEFAULT_EMOJI.get(key, "❓")


def set_emoji(key: str, custom_emoji_id: str, placeholder: str) -> None:
    EmojiOverride.objects.update_or_create(
        key=key, defaults={"custom_emoji_id": custom_emoji_id, "placeholder": placeholder}
    )
    refresh_cache()


def clear_emoji(key: str) -> bool:
    deleted, _ = EmojiOverride.objects.filter(key=key).delete()
    refresh_cache()
    return deleted > 0


def list_overrides() -> list[EmojiOverride]:
    return list(EmojiOverride.objects.order_by("key"))

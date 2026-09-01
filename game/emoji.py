import re

from bio_lab.models import EmojiOverride

# Rarity circles stay fixed colour codes — NEVER premiumise them (rarity must read
# instantly regardless of theme). Same for the plain check/cross used as bullet marks
# where a themed icon would look odd mid-sentence.
GLYPH_SKIP = {"⚪", "🔵", "🟣", "🟡", "🔴", "▓", "░", "•", "·", "━"}
_GLYPH_PREFIX = "g:"  # EmojiOverride.key prefix for a per-GLYPH (not per-semantic-key) theme

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
    "diamond": ("الماس", "💎", "resources"),
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
    "battle": ("نبرد", "⚔️", "battle"),
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
    # deliberately NOT 🧬 — that's the DNA resource, and using the same glyph for
    # both made "🧬 وایو … 🧬 113" unreadable on the creature card
    "creature": ("نماد موجود", "🦖", "progress"),
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
    "diamond_box": ("جعبه الماسی", "💠", "progress"),
    "comet": ("رویداد جهش", "☄️", "progress"),
    "star": ("ستاره‌ی ارتقا", "⭐", "progress"),
    "building": ("ساختمون", "🏗", "progress"),
    "speedup": ("کارت سرعت", "⏱", "progress"),
    "wheel": ("گردونه‌ی شانس", "🎡", "progress"),
    "shield": ("سپر محافظ", "🛡", "progress"),
    "casino": ("کازینو", "🎰", "progress"),
    "shop_item": ("آیتم ویژه فروشگاه", "🛍", "progress"),
    # UI
    "confirm": ("تأیید", "✅", "ui"),
    "cancel": ("لغو", "❌", "ui"),
    "warning": ("هشدار", "⚠️", "ui"),
    "banned": ("مسدود", "🚫", "ui"),
    "stats": ("آمار", "📊", "ui"),
    "broadcast": ("پیام همگانی", "📢", "ui"),
    "collection": ("کلکسیون", "🗂", "ui"),
    "settings": ("تنظیمات", "🎨", "ui"),
    "book": ("راهنما", "📖", "ui"),
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


_glyph_map: dict[str, str] | None = None       # glyph -> custom_emoji_id
_glyph_re: "re.Pattern | None" = None
_TG_EMOJI_BLOCK = re.compile(r"<tg-emoji\b[^>]*>.*?</tg-emoji>", re.DOTALL)
_TAG = re.compile(r"<[^>]+>")  # any HTML tag — never premiumise a glyph inside one


def _norm_glyph(g: str) -> str:
    """Drop the emoji variation selector (U+FE0F) so `⚡️` and `⚡`, `🛡️` and `🛡`
    are treated as the same glyph. Without this a message that hard-codes the
    VS-16 form of an emoji would never match a themed glyph stored in the bare form
    (or vice-versa), so those emojis silently stayed un-skinnable — the exact
    'some emojis in the attack/hunt message aren't configurable' bug."""
    return g.replace("️", "")


def _load_glyph_map() -> dict[str, str]:
    """Build {normalized glyph: custom_emoji_id} from the g:-prefixed overrides, plus a
    regex that matches any themed glyph — variation-selector-insensitive, and longest
    first so multi-codepoint emoji win over their parts."""
    global _glyph_map, _glyph_re
    gm = {
        _norm_glyph(o.key[len(_GLYPH_PREFIX):]): o.custom_emoji_id
        for o in EmojiOverride.objects.filter(key__startswith=_GLYPH_PREFIX)
        if o.key[len(_GLYPH_PREFIX):] not in GLYPH_SKIP
    }
    _glyph_map = gm
    # each glyph may appear with an optional trailing VS-16 in the text; match either
    _glyph_re = (
        re.compile("|".join(re.escape(g) + "️?" for g in sorted(gm, key=len, reverse=True)))
        if gm else None
    )
    return gm


def refresh_cache() -> None:
    """Call after any EmojiOverride write so lookups reflect it without a bot restart."""
    _load_cache()
    _load_glyph_map()


def set_glyph(glyph: str, custom_emoji_id: str) -> None:
    EmojiOverride.objects.update_or_create(
        key=f"{_GLYPH_PREFIX}{glyph}", defaults={"custom_emoji_id": custom_emoji_id, "placeholder": glyph}
    )
    refresh_cache()


def set_glyphs_bulk(pairs: dict[str, str]) -> int:
    """Theme many glyphs at once (glyph -> custom_emoji_id), refreshing the cache just
    once. Skips the fixed rarity/bullet glyphs. Returns how many were set."""
    n = 0
    for glyph, cid in pairs.items():
        if not glyph or glyph in GLYPH_SKIP:
            continue
        EmojiOverride.objects.update_or_create(
            key=f"{_GLYPH_PREFIX}{glyph}", defaults={"custom_emoji_id": cid, "placeholder": glyph}
        )
        n += 1
    refresh_cache()
    return n


def clear_glyphs() -> int:
    deleted, _ = EmojiOverride.objects.filter(key__startswith=_GLYPH_PREFIX).delete()
    refresh_cache()
    return deleted


def premiumize_html(text: str) -> str:
    """Wrap every themed literal emoji in `text` with its Premium <tg-emoji>. Applied
    to ALL outgoing HTML messages, so plain emojis hardcoded in message strings render
    as the owner's Premium set without touching each f-string. Skips glyphs already
    inside a <tg-emoji> block or any HTML tag, and the fixed rarity/bullet glyphs."""
    if not text:
        return text
    gm = _glyph_map if _glyph_map is not None else _load_glyph_map()
    if not gm or _glyph_re is None:
        return text

    def _wrap_segment(seg: str) -> str:
        # don't touch glyphs that sit inside an HTML tag's angle brackets
        pieces = []
        last = 0
        for tag in _TAG.finditer(seg):
            pieces.append(_glyph_re.sub(lambda m: _wrap(m.group()), seg[last:tag.start()]))
            pieces.append(tag.group())  # tag text left as-is
            last = tag.end()
        pieces.append(_glyph_re.sub(lambda m: _wrap(m.group()), seg[last:]))
        return "".join(pieces)

    def _wrap(g: str) -> str:
        # look up variation-selector-insensitively, but display the exact matched glyph
        cid = gm.get(_norm_glyph(g))
        return f'<tg-emoji emoji-id="{cid}">{g}</tg-emoji>' if cid else g

    # leave existing <tg-emoji>…</tg-emoji> blocks untouched; premiumise only between them
    out, last = [], 0
    for block in _TG_EMOJI_BLOCK.finditer(text):
        out.append(_wrap_segment(text[last:block.start()]))
        out.append(block.group())
        last = block.end()
    out.append(_wrap_segment(text[last:]))
    result = "".join(out)
    # a Premium emoji at the very start of a <blockquote> sits flush against the text
    # and reads cramped — put a clear gap between it and what follows. Regular spaces
    # collapse to one in Telegram's renderer, so use NON-BREAKING spaces (U+00A0),
    # which it keeps.
    return _LEAD_EMOJI.sub(lambda m: m.group("pre") + m.group("emoji") + "  ", result)


# <blockquote> + optional whitespace + a leading <tg-emoji> block, then any trailing
# spaces (regular or NBSP) — normalised to exactly two NBSP.
_LEAD_EMOJI = re.compile(
    r"(?P<pre>(?:^|<blockquote>)[^\S\n]*)(?P<emoji><tg-emoji\b[^>]*>.*?</tg-emoji>)[^\S\n]*",
    re.DOTALL | re.MULTILINE,
)


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

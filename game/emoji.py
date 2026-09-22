import re

from bio_lab.models import EmojiOverride

# Rarity circles stay fixed colour codes — NEVER premiumise them (rarity must read
# instantly regardless of theme). Same for the plain check/cross used as bullet marks
# where a themed icon would look odd mid-sentence.
GLYPH_SKIP = {"▓", "░", "•", "·", "━", "─", "│", "┃", "—"}
_GLYPH_PREFIX = "g:"  # EmojiOverride.key prefix for a per-GLYPH (not per-semantic-key) theme

# key -> (label, default unicode emoji, category). This is the single registry for
# every icon that's worth letting the owner re-skin with a Telegram Premium custom
# emoji.
EMOJI_DEFS: dict[str, tuple[str, str, str]] = {
    # resources
    "coin": ("طلا", "💰", "resources"),
    "dna": ("DNA", "🧬", "resources"),
    "diamond": ("الماس", "💎", "resources"),
    "energy": ("انرژی", "⚡", "resources"),
    "bank": ("خزانه و بانک", "🏦", "resources"),
    "battery": ("شارژ و باتری", "🔋", "resources"),
    "meat": ("غذا و گوشت", "🍖", "resources"),
    "potion": ("کپسول تجربه/معجون", "🧪", "resources"),
    "wallet": ("کیف پول و موجودی", "💼", "resources"),
    "purse": ("کیف سکه", "👛", "resources"),
    "card_payment": ("کارت بانکی و پرداخت", "💳", "resources"),
    "cash_bill": ("اسکناس و پیشنهاد", "💵", "resources"),
    "invoice_receipt": ("فاکتور و صورتحساب", "🧾", "resources"),
    "collector": ("کارخانه و جمع‌کننده منابع", "🏭", "resources"),
    "mining": ("معدن‌کاری و استخراج", "⛏", "resources"),
    "clock_offline": ("صندوق آفلاین و ساعت", "🕰", "resources"),
    # stats
    "hp": ("HP", "❤️", "stats"),
    "atk": ("ATK", "⚔️", "stats"),
    "def": ("DEF", "🛡", "stats"),
    "spd": ("SPD", "💨", "stats"),
    "poison": ("زهر", "☠️", "stats"),
    "crit": ("کریتیکال", "💥", "stats"),
    "lifesteal": ("جون‌خواری", "🧛", "stats"),
    "power": ("قدرت و توان", "💪", "stats"),
    "level": ("سطح و لِوِل", "🎖", "stats"),
    "max_level": ("سطح بیشینه و ماکس", "👑", "stats"),
    "gear_power": ("قدرت تجهیزات", "⚙️", "stats"),
    "body_parts": ("ارتقای اعضای بدن", "🦴", "stats"),
    "trend_up": ("نرخ رشد و افزایش", "📈", "stats"),
    # elements
    "element_fire": ("عنصر آتش", "🔥", "elements"),
    "element_water": ("عنصر آب", "💧", "elements"),
    "element_earth": ("عنصر خاک", "🪨", "elements"),
    "element_electric": ("عنصر الکتریسیته", "⚡", "elements"),
    "element_advantage": ("مزیت عنصری", "🔮", "elements"),
    "no_advantage": ("بدون مزیت عنصری", "➖", "elements"),
    # rarity
    "rarity_common": ("نایابی معمولی", "⚪", "rarity"),
    "rarity_uncommon": ("نایابی غیرمعمول", "🟢", "rarity"),
    "rarity_rare": ("نایابی کمیاب", "🔵", "rarity"),
    "rarity_epic": ("نایابی حماسی", "🟣", "rarity"),
    "rarity_legendary": ("نایابی افسانه‌ای", "🟡", "rarity"),
    "rarity_mythic": ("نایابی اساطیری", "🔴", "rarity"),
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
    "raid_attacks_left": ("اتک رید باقی‌مانده", "🔁", "battle"),
    "mugen": ("برج موگن", "🏰", "battle"),
    "expedition": ("اعزام کاروان", "⛵", "battle"),
    "war": ("جنگ اتحاد", "⚔️", "battle"),
    "opponent_creature": ("موجود و کایجوی حریف", "👾", "battle"),
    "opponent_lab": ("آزمایشگاه و پایگاه حریف", "🏭", "battle"),
    "creature_active": ("هیولای مبارز شما", "🦅", "battle"),
    "log_win": ("پیروزی در حمله/دفاع", "🟢", "battle"),
    "log_loss": ("شکست در حمله/دفاع", "🔴", "battle"),
    "defense_log": ("گزارش دفاع و حمله", "🛡", "battle"),
    "skull_ko": ("ناک‌اوت و جمجمه", "💀", "battle"),
    "defeat": ("پیام شکست نبرد", "😔", "battle"),
    "arena_status": ("میدان و سپر آرنا", "🏟", "battle"),
    "war_fire": ("شعله و استارت جنگ", "🔥", "battle"),
    "heist": ("شبیخون و غارت اتحاد", "🏴‍☠️", "battle"),
    "raid_boss_spawn": ("ظهور باس رید", "👻", "battle"),
    "dragon": ("اژدها و سطح رید", "🐉", "battle"),
    # social
    "alliance": ("اتحاد", "🤝", "social"),
    "gift": ("هدیه", "🎁", "social"),
    "profile": ("پروفایل", "👤", "social"),
    "users": ("کاربران", "👥", "social"),
    "crown": ("رهبر/محافظ", "👑", "social"),
    "members": ("اعضای اتحاد", "👥", "social"),
    "deputy": ("معاون و ارشد", "🎖", "social"),
    "team": ("تیم کایجوها", "🛡", "social"),
    "ally_academy": ("آکادمی اتحاد", "🎓", "social"),
    "ally_shrine": ("معبد اتحاد", "⛩", "social"),
    "ally_fortress": ("دژ اتحاد", "🏯", "social"),
    "ally_barracks": ("پادگان اتحاد", "🪖", "social"),
    "door_resign": ("استعفا و کناره‌گیری", "🚪", "social"),
    "kick_boot": ("اخراج عضو", "🥾", "social"),
    # progress / identity
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
    "ticket": ("بلیط ژنتیکی", "🎟", "progress"),
    "dungeon": ("دانجن و دخمه", "🗺", "progress"),
    "chest_silver": ("جعبه نقره‌ای آرنا", "🥈", "progress"),
    "chest_golden": ("جعبه طلایی آرنا", "🥇", "progress"),
    "chest_magical": ("جعبه جادویی آرنا", "🔮", "progress"),
    "chest_mega": ("جعبه مگا / امگا آرنا", "👑", "progress"),
    "chest_arena": ("جعبه‌های آرنا", "📦", "progress"),
    "sub_silver": ("اشتراک نقره‌ای VIP", "🥈", "progress"),
    "sub_gold": ("اشتراک طلایی VIP", "👑", "progress"),
    "sub_vip": ("اشتراک ویژه VIP", "⭐", "progress"),
    "blackmarket": ("بازار سیاه", "🏛", "progress"),
    "dice": ("تاس و شانس", "🎲", "progress"),
    "hatch": ("جوجه‌کشی و تخم", "🐣", "progress"),
    "scroll": ("طومار و دستاورد", "📜", "progress"),
    "fitness": ("تمرین بدنی", "🏋️", "progress"),
    "gear": ("تجهیزات و ابزار", "⚙️", "progress"),
    "forge": ("آهنگری و چکش", "⚒", "progress"),
    "worker": ("کارگر ساخت‌وساز", "👷‍♂️", "progress"),
    "cave": ("غار هیولا و پرورش", "🕳", "progress"),
    "mating": ("جفت‌گیری در غار", "💞", "progress"),
    "fuse_chain": ("ترکیب و ادغام", "🔗", "progress"),
    "recycle": ("بازیافت و تبدیل", "♻️", "progress"),
    "rocket": ("اعزام و پرتاب کاروان", "🚀", "progress"),
    "lab_level": ("سطح آزمایشگاه", "🔬", "progress"),
    # UI
    "lock": ("قفل", "🔒", "ui"),
    "unlock": ("بازگشایی قفل", "🔓", "ui"),
    "timer": ("زمان‌سنج", "⏱", "ui"),
    "bell": ("اعلان فعال", "🔔", "ui"),
    "bell_off": ("بی‌صدا", "🔕", "ui"),
    "pin": ("سنجاق و نکته", "📌", "ui"),
    "idea": ("ایده و راهنمایی", "💡", "ui"),
    "key": ("کلید", "🗝", "ui"),
    "info": ("اطلاعات", "ℹ️", "ui"),
    "status_premium": ("نشان وضعیت پرمیوم", "✅", "ui"),
    "status_default": ("نشان وضعیت پیش‌فرض", "⬜️", "ui"),
    "confirm": ("تأیید و تیک سبز", "✅", "ui"),
    "check": ("تیک ساده و بررسی", "✔️", "ui"),
    "cancel": ("لغو و ضربدر", "❌", "ui"),
    "cross": ("رد و ضربدر قرمز", "✖️", "ui"),
    "warning": ("هشدار", "⚠️", "ui"),
    "banned": ("مسدود", "🚫", "ui"),
    "stats": ("آمار", "📊", "ui"),
    "broadcast": ("پیام همگانی", "📢", "ui"),
    "collection": ("کلکسیون", "🗂", "ui"),
    "settings": ("تنظیمات", "🎨", "ui"),
    "book": ("راهنما", "📖", "ui"),
    "search": ("جستجو و ذره‌بین", "🔍", "ui"),
    "edit": ("ویرایش و قلم", "✏️", "ui"),
    "delete": ("حذف و سطل زباله", "🗑", "ui"),
    "refresh": ("بروزرسانی و تازه‌سازی", "🔄", "ui"),
    "sparkles": ("درخشش و مهارت", "✨", "ui"),
    "cart": ("فروشگاه و سبد خرید", "🛒", "ui"),
    "discount": ("تخفیف ویژه روز", "🔻", "ui"),
    "sold_out": ("اتمام موجودی و سقف", "⛔", "ui"),
    "numeric": ("تعداد دلخواه", "🔢", "ui"),
    "photo_receipt": ("عکس رسید", "📸", "ui"),
    "siren": ("آژیر خطر و هشدار جنگ", "🚨", "ui"),
    "requests_inbox": ("درخواست‌های عضویت", "📨", "ui"),
    "admin_badge": ("مدیریت ادمین‌ها", "👮", "ui"),
    "backup_box": ("بکاپ و ذخیره‌سازی", "💾", "ui"),
    "search_inspect": ("چیت‌یاب و بازرسی", "🕵", "ui"),
    "writing_note": ("افزودن متن", "✍️", "ui"),
    "queue": ("صف جعبه‌ها", "📋", "ui"),
    "tag_name": ("برچسب و نام", "🏷", "ui"),
    "badge_id": ("شناسه و کد", "🆔", "ui"),
    "empty_inbox": ("صندوق خالی", "📭", "ui"),
    "greeting": ("خوش‌آمدگویی", "👋", "ui"),
    "feed": ("تغذیه هیولا", "🍽", "resources"),
    "idle_sleep": ("پاداش خواب و آفلاین", "💤", "resources"),
    "ring": ("زیورآلات و حلقه", "💍", "progress"),
    "paw": ("رد پای موجود", "🐾", "progress"),
    "archive": ("آرشیو و کلکسیون", "🗃", "progress"),
    "wood_league": ("لیگ چوب", "🪵", "progress"),
    "rookie_badge": ("نشان تازه‌کار", "🔰", "progress"),
    "play_card": ("کارت بازی", "🃏", "progress"),
    "element_balance": ("توازن و چرخه عناصر", "⚖️", "elements"),
    "volcano": ("دشت آتشفشانی", "🌋", "elements"),
    "body_part": ("قطعات بدن", "🧩", "body"),
    "no_duel": ("خطای نبرد", "🙅", "battle"),
    "flee": ("فرار و دویدن", "🏃", "battle"),
    "rest": ("استراحت هیولا", "😴", "battle"),
    "lion": ("شیر مبارز", "🦁", "battle"),
    "tiger": ("ببر مبارز", "🐯", "battle"),
    "shadow_thief": ("دزد سایه‌ها", "🦹", "battle"),
    "tower_ladder": ("نردبان برج", "🪜", "battle"),
    "admin_tools": ("ابزار و پنل مدیریت", "🛠", "ui"),
    "admin_dm": ("پیام ادمین", "✉️", "ui"),
    "btn_settings_icon": ("تنظیمات دکمه", "🎛", "ui"),
    "slider": ("اسلایدر و تنظیم مقدار", "🎚", "ui"),
    "help_mark": ("راهنما و علامت سوال", "❓", "ui"),
    "stop_warning": ("توقف و اخطار", "🛑", "ui"),
    "gallery": ("گالری و تصاویر", "🖼", "ui"),
    "channel_broadcast": ("کانال و ماهواره", "📡", "ui"),
    "game_group": ("گروه بازی", "🎮", "ui"),
    "calendar": ("تقویم و فصل", "🗓", "ui"),
    "trend_down": ("نرخ کاهش و کارمزد", "📉", "stats"),
    "upgrade_arrow": ("فلش ارتقا", "🔼", "stats"),
    "document": ("برگه و سند", "📄", "ui"),
    "broom_clear": ("پاکسازی و جارو", "🧹", "ui"),
    "inbox_receive": ("دریافت و صندوق ورودی", "📥", "ui"),
    "badge_warn": ("نشان ممنوع و اخطار", "📛", "ui"),
    "flag_finish": ("پایان و خط پایان", "🏁", "battle"),
    "rosette": ("مدال افسانه‌ای", "🏵", "progress"),
    "page_counter": ("نشانگر صفحه", "📑", "ui"),
}

CATEGORY_LABELS: dict[str, str] = {
    "resources": "💰 منابع",
    "stats": "📈 استت‌ها",
    "elements": "🌍 عناصر",
    "rarity": "💎 نایابی هیولاها",
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

_cache: dict[str, EmojiOverride] = {}


def _load_cache() -> dict[str, EmojiOverride]:
    global _cache
    try:
        _cache = {o.key: o for o in EmojiOverride.objects.all()}
    except Exception:
        pass
    return _cache


def list_overrides() -> dict[str, EmojiOverride]:
    """Returns the text emoji overrides dictionary (key -> EmojiOverride)."""
    return dict(_load_cache())


def text_category_stats(category: str | None = None) -> tuple[int, int]:
    """Returns (set_count, total) for text emojis. Pure in-memory cache lookup."""
    cache = _cache
    if category:
        keys = [k for k, c in CATEGORY_OF.items() if c == category]
    else:
        keys = list(EMOJI_DEFS.keys())
    set_cnt = sum(1 for k in keys if k in cache)
    return set_cnt, len(keys)


_glyph_map: dict[str, str] | None = None       # glyph -> custom_emoji_id
_glyph_re: "re.Pattern | None" = None
_TG_EMOJI_BLOCK = re.compile(r"<tg-emoji\b[^>]*>.*?</tg-emoji>", re.DOTALL)
_TAG = re.compile(r"<[^>]+>")  # any HTML tag — never premiumise a glyph inside one


def _norm_glyph(g: str) -> str:
    """Strip variation selectors (U+FE0F, U+FE0E) so '⚔️' and '⚔' match the same key."""
    return g.replace("\ufe0f", "").replace("\ufe0e", "").strip()


def _load_glyph_map() -> dict[str, str]:
    """Build a mapping of normalised Unicode glyph -> custom_emoji_id.
    Reads from:
      1) Per-glyph overrides (keys like 'g:⚔')
      2) Semantic-key overrides (e.g. key 'atk' mapped to custom_emoji_id -> sets '⚔')
    """
    global _glyph_map, _glyph_re
    try:
        cache = _load_cache()
        gm: dict[str, str] = {}
        for key, glyphs in CANONICAL_KEY_GLYPHS.items():
            override = cache.get(key)
            if override is not None:
                for g in glyphs:
                    gm[_norm_glyph(g)] = override.custom_emoji_id
        for k, o in cache.items():
            if k.startswith(_GLYPH_PREFIX):
                raw_glyph = k[len(_GLYPH_PREFIX):]
                if raw_glyph:
                    gm[_norm_glyph(raw_glyph)] = o.custom_emoji_id
        for skip in GLYPH_SKIP:
            gm.pop(_norm_glyph(skip), None)
        _glyph_map = gm
        # each glyph may appear with an optional trailing VS-16 in the text; match either
        _glyph_re = (
            re.compile("|".join(re.escape(g) + "️?" for g in sorted(gm, key=len, reverse=True)))
            if gm else None
        )
        return gm
    except Exception:
        if _glyph_map is not None:
            return _glyph_map
        return {}


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
    once. Returns how many were set."""
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
    inside a <tg-emoji> block or any HTML tag, and the fixed bullet glyphs."""
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
    return _LEAD_EMOJI.sub(lambda m: m.group("pre") + m.group("emoji") + "  ", result)


# <blockquote> + optional whitespace + a leading <tg-emoji> block, then any trailing
# spaces (regular or NBSP) — normalised to exactly two NBSP.
_LEAD_EMOJI = re.compile(
    r"(?P<pre>(?:^|<blockquote>)[^\S\n]*)(?P<emoji><tg-emoji\b[^>]*>.*?</tg-emoji>)[^\S\n]*",
    re.DOTALL | re.MULTILINE,
)


CANONICAL_KEY_GLYPHS: dict[str, set[str]] = {
    # Resources
    "coin": {"💰", "🪙"},
    "dna": {"🧬"},
    "diamond": {"💎"},
    "energy": {"⚡", "⚡️"},
    "potion": {"🧪"},
    "battery": {"🔋"},
    "bank": {"🏦"},
    "meat": {"🍖", "🥩"},
    "wallet": {"💼"},
    "purse": {"👛"},
    "card_payment": {"💳"},
    "cash_bill": {"💵"},
    "invoice_receipt": {"🧾"},
    "collector": {"🏭"},
    "mining": {"⛏", "⛏️"},
    "clock_offline": {"🕰"},
    # Stats
    "hp": {"❤️", "♥️"},
    "atk": {"⚔️", "⚔"},
    "def": {"🛡️", "🛡"},
    "spd": {"💨"},
    "poison": {"☠️", "☠"},
    "crit": {"💥"},
    "lifesteal": {"🧛", "🩸"},
    "power": {"💪"},
    "level": {"🎖️", "🎖"},
    "max_level": {"👑", "⭐"},
    "gear_power": {"⚙️", "⚙"},
    "body_parts": {"🦴"},
    "trend_up": {"📈", "💹"},
    # Elements
    "element_fire": {"🔥"},
    "element_water": {"💧", "🌊"},
    "element_earth": {"🪨", "🗿", "⛰️", "⛰"},
    "element_electric": {"⚡", "⚡️"},
    "element_advantage": {"🔮"},
    "no_advantage": {"➖"},
    # Rarity
    "rarity_common": {"⚪", "🔘"},
    "rarity_uncommon": {"🟢"},
    "rarity_rare": {"🔵", "🔷"},
    "rarity_epic": {"🟣", "🟪"},
    "rarity_legendary": {"🟡", "🟨"},
    "rarity_mythic": {"🔴", "🟥"},
    # Body parts
    "wings": {"🦋"},
    "fangs": {"🦷"},
    # Battle
    "battle": {"⚔️", "⚔"},
    "attack_action": {"🗡️", "🗡"},
    "skill_action": {"✨"},
    "forfeit_action": {"🏳️", "🏳"},
    "raid_boss": {"🐲", "👹"},
    "hunt": {"🏹"},
    "raid_attacks_left": {"🔁"},
    "mugen": {"🏰", "🏯"},
    "expedition": {"⛵", "🚢"},
    "war": {"⚔️", "⚔"},
    "opponent_creature": {"👾", "👹", "👿"},
    "opponent_lab": {"🏭", "🏰"},
    "creature_active": {"🦅"},
    "log_win": {"🟢"},
    "log_loss": {"🔴"},
    "defense_log": {"🛡️", "🛡"},
    "skull_ko": {"💀"},
    "defeat": {"😔", "💔"},
    "arena_status": {"🏟️", "🏟"},
    "war_fire": {"🔥"},
    "heist": {"🏴‍☠️", "🏴"},
    "raid_boss_spawn": {"👻"},
    "dragon": {"🐉"},
    # Social
    "alliance": {"🤝"},
    "gift": {"🎁"},
    "profile": {"👤"},
    "users": {"👥"},
    "crown": {"👑"},
    "members": {"👥"},
    "deputy": {"🎖️", "🎖"},
    "team": {"🛡️", "🛡"},
    "ally_academy": {"🎓"},
    "ally_shrine": {"⛩️", "⛩"},
    "ally_fortress": {"🏯"},
    "ally_barracks": {"🪖"},
    "door_resign": {"🚪"},
    "kick_boot": {"🥾"},
    # Progress & Identity
    "creature": {"🦖", "🦕"},
    "trophy": {"🏆"},
    "celebrate": {"🎉", "🎊"},
    "mission": {"🎯"},
    "medal_gold": {"🥇"},
    "medal_silver": {"🥈"},
    "medal_bronze": {"🥉"},
    "guardian": {"🛡️", "🛡"},
    "egg": {"🥚"},
    "hatch": {"🐣", "🐤", "🐥"},
    "lab": {"🧪"},
    "biocrate": {"📦"},
    "diamond_box": {"💠"},
    "comet": {"☄️", "☄"},
    "star": {"⭐", "🌟"},
    "building": {"🏗️", "🏗", "🔨", "⚒️", "⚒"},
    "speedup": {"⏱️", "⏱"},
    "wheel": {"🎡"},
    "shield": {"🛡️", "🛡"},
    "casino": {"🎰"},
    "shop_item": {"🛍️", "🛍", "🛒"},
    "ticket": {"🎟️", "🎟", "🎫"},
    "dungeon": {"🗺️", "🗺", "🧭"},
    "chest_silver": {"🥈"},
    "chest_golden": {"🥇"},
    "chest_magical": {"🔮"},
    "chest_mega": {"👑"},
    "chest_arena": {"📦"},
    "sub_silver": {"🥈"},
    "sub_gold": {"👑"},
    "sub_vip": {"⭐", "🌟"},
    "blackmarket": {"🏛️", "🏛"},
    "dice": {"🎲"},
    "scroll": {"📜"},
    "fitness": {"🏋️", "🏋"},
    "gear": {"⚙️", "⚙"},
    "forge": {"⚒️", "⚒", "🔨"},
    "worker": {"👷‍♂️", "👷"},
    "cave": {"🕳️", "🕳"},
    "mating": {"💞"},
    "fuse_chain": {"🔗"},
    "recycle": {"♻️", "♻"},
    "rocket": {"🚀"},
    "lab_level": {"🔬"},
    # UI & Moderation
    "lock": {"🔒"},
    "unlock": {"🔓"},
    "timer": {"⏱️", "⏱", "⏳", "⌛"},
    "bell": {"🔔"},
    "bell_off": {"🔕"},
    "pin": {"📌", "📍"},
    "idea": {"💡"},
    "key": {"🗝️", "🗝"},
    "info": {"ℹ️", "ℹ"},
    "status_premium": {"✅"},
    "status_default": {"⬜️", "⬜"},
    "confirm": {"✅", "☑️", "☑"},
    "check": {"✔️", "✔", "✓"},
    "cancel": {"❌", "❎"},
    "cross": {"✖️", "✖"},
    "warning": {"⚠️", "⚠", "❗"},
    "banned": {"🚫", "⛔"},
    "stats": {"📊"},
    "broadcast": {"📢", "📣"},
    "collection": {"🗂️", "🗂", "🎒"},
    "settings": {"🎨", "⚙️", "⚙"},
    "book": {"📖", "📚"},
    "search": {"🔍", "🔎"},
    "edit": {"✏️", "✏", "📝"},
    "delete": {"🗑️", "🗑"},
    "refresh": {"🔄", "🔃", "🔁"},
    "sparkles": {"✨"},
    "cart": {"🛒"},
    "discount": {"🔻"},
    "sold_out": {"⛔"},
    "numeric": {"🔢"},
    "photo_receipt": {"📸"},
    "siren": {"🚨"},
    "requests_inbox": {"📨"},
    "admin_badge": {"👮", "👮‍♂️"},
    "backup_box": {"💾", "📤"},
    "search_inspect": {"🕵️", "🕵"},
    "writing_note": {"✍️", "✍"},
    "queue": {"📋"},
    "tag_name": {"🏷️", "🏷"},
    "badge_id": {"🆔"},
    "empty_inbox": {"📭"},
    "greeting": {"👋"},
    "feed": {"🍽️", "🍽"},
    "idle_sleep": {"💤"},
    "ring": {"💍"},
    "paw": {"🐾"},
    "archive": {"🗃️", "🗃"},
    "wood_league": {"🪵"},
    "rookie_badge": {"🔰"},
    "play_card": {"🃏"},
    "element_balance": {"⚖️", "⚖"},
    "volcano": {"🌋"},
    "body_part": {"🧩"},
    "no_duel": {"🙅‍♂️", "🙅‍♀️", "🙅"},
    "flee": {"🏃‍♂️", "🏃‍♀️", "🏃"},
    "rest": {"😴"},
    "lion": {"🦁"},
    "tiger": {"🐯"},
    "shadow_thief": {"🦹‍♂️", "🦹‍♀️", "🦹"},
    "tower_ladder": {"🪜"},
    "admin_tools": {"🛠️", "🛠", "🔧"},
    "admin_dm": {"✉️", "✉"},
    "btn_settings_icon": {"🎛️", "🎛"},
    "slider": {"🎚️", "🎚"},
    "help_mark": {"❓"},
    "stop_warning": {"🛑"},
    "gallery": {"🖼️", "🖼"},
    "channel_broadcast": {"📡"},
    "game_group": {"🎮"},
    "calendar": {"🗓️", "🗓", "📅", "📆"},
    "trend_down": {"📉"},
    "upgrade_arrow": {"🔼"},
    "document": {"📄", "🗒️", "🗒"},
    "broom_clear": {"🧹"},
    "inbox_receive": {"📥"},
    "badge_warn": {"📛"},
    "flag_finish": {"🏁"},
    "rosette": {"🏵️", "🏵"},
    "page_counter": {"📑"},
}


KEY_ALIASES: dict[str, str] = {
    "gold": "coin",
    "coins": "coin",
    "diamonds": "diamond",
    "dia": "diamond",
    "rank": "trophy",
    "vip": "sub_vip",
    "speed_card": "speedup",
    "fire": "element_fire",
    "water": "element_water",
    "earth": "element_earth",
    "electric": "element_electric",
    "shop": "shop_item",
    "inventory": "collection",
    "team": "team",
    "campaign": "dungeon",
    "market": "blackmarket",
    "chest": "chest_arena",
    "crates": "biocrate",
    "common": "rarity_common",
    "uncommon": "rarity_uncommon",
    "rare": "rarity_rare",
    "epic": "rarity_epic",
    "legendary": "rarity_legendary",
    "mythic": "rarity_mythic",
    "advantage": "element_advantage",
}


def get_emoji(key: str, fallback: str | None = None) -> str:
    """Returns HTML for `key`: a <tg-emoji> wrapper if the owner set a Premium custom
    emoji for it, otherwise the plain unicode default (from EMOJI_DEFS, or `fallback`
    if given). Safe to call from anywhere — reads an in-memory cache, not the
    database, after the first (eager-warmed) load. Only usable in message BODY text
    sent with parse_mode="HTML" — Telegram button labels are plain text and can
    never render <tg-emoji>, so never call this for InlineKeyboardButton text."""
    resolved_key = KEY_ALIASES.get(key, key)
    cache = _cache if _cache is not None else _load_cache()
    override = cache.get(resolved_key)
    if override is not None:
        ph = override.placeholder
        # If the stored placeholder conflicts with another key (e.g. coin had 🧬),
        # fall back to the canonical emoji for the tag placeholder
        if resolved_key in CANONICAL_KEY_GLYPHS and any(
            _norm_glyph(ph) in {_norm_glyph(g) for g in glyphs}
            for other_k, glyphs in CANONICAL_KEY_GLYPHS.items()
            if other_k != resolved_key
        ):
            ph = DEFAULT_EMOJI.get(resolved_key, "💰")
        return f'<tg-emoji emoji-id="{override.custom_emoji_id}">{ph}</tg-emoji>'
    return fallback if fallback is not None else DEFAULT_EMOJI.get(resolved_key, "❓")


def get_plain_emoji(key: str, fallback: str | None = None) -> str:
    """Returns plain unicode glyph for `key` (never wraps in <tg-emoji> tags).
    Use this for Telegram button text, query.answer toasts, or anywhere raw HTML tags are forbidden."""
    resolved_key = KEY_ALIASES.get(key, key)
    cache = _cache if _cache is not None else _load_cache()
    override = cache.get(resolved_key)
    if override is not None and override.placeholder:
        return override.placeholder
    return fallback if fallback is not None else DEFAULT_EMOJI.get(resolved_key, "")



def _key_glyphs(key: str, placeholder: str) -> set[str]:
    """The literal glyph(s) that a semantic key should also theme: its default unicode
    emoji and the placeholder the owner chose (normalized, skipping fixed glyphs).
    Protects canonical glyphs so an arbitrary placeholder from a custom emoji pack
    (e.g. coin pack using 🧬) never hijacks another key's canonical glyph."""
    out = set()
    if key in CANONICAL_KEY_GLYPHS:
        for cg in CANONICAL_KEY_GLYPHS[key]:
            out.add(_norm_glyph(cg))
    else:
        def_glyph = DEFAULT_EMOJI.get(key, "")
        if def_glyph:
            out.add(_norm_glyph(def_glyph))

    if placeholder:
        norm_p = _norm_glyph(placeholder)
        if norm_p and norm_p not in GLYPH_SKIP:
            # Reject if norm_p belongs to another key's canonical set
            is_conflict = any(
                norm_p in {_norm_glyph(g) for g in glyphs}
                for other_k, glyphs in CANONICAL_KEY_GLYPHS.items()
                if other_k != key
            )
            if not is_conflict:
                out.add(norm_p)

    return {g for g in out if g and g not in GLYPH_SKIP}


def set_emoji(key: str, custom_emoji_id: str, placeholder: str) -> None:
    # If the placeholder provided conflicts with another key (e.g. 🧬 when setting coin),
    # use the canonical default instead of polluting the placeholder
    clean_placeholder = placeholder
    norm_p = _norm_glyph(placeholder)
    if key in CANONICAL_KEY_GLYPHS and any(
        norm_p in {_norm_glyph(g) for g in glyphs}
        for other_k, glyphs in CANONICAL_KEY_GLYPHS.items()
        if other_k != key
    ):
        clean_placeholder = DEFAULT_EMOJI.get(key, "💰")

    EmojiOverride.objects.update_or_create(
        key=key, defaults={"custom_emoji_id": custom_emoji_id, "placeholder": clean_placeholder}
    )
    # ALSO theme the literal glyph(s) for this key, so hard-coded emojis in message
    # bodies (💥, 💎, ⚔️ …) render as the owner's choice EVERYWHERE — not only where
    # get_emoji() is used. This is what keeps e.g. the diamond emoji consistent across
    # every screen instead of differing between get_emoji() and literal 💎.
    for g in _key_glyphs(key, clean_placeholder):
        EmojiOverride.objects.update_or_create(
            key=f"{_GLYPH_PREFIX}{g}", defaults={"custom_emoji_id": custom_emoji_id, "placeholder": g}
        )
    refresh_cache()


def clear_emoji(key: str) -> bool:
    # find the placeholder before deleting, so we can also drop the coupled glyph overrides
    existing = EmojiOverride.objects.filter(key=key).first()
    placeholder = existing.placeholder if existing else ""
    deleted, _ = EmojiOverride.objects.filter(key=key).delete()
    glyph_keys = [f"{_GLYPH_PREFIX}{g}" for g in _key_glyphs(key, placeholder)]
    if glyph_keys:
        EmojiOverride.objects.filter(key__in=glyph_keys).delete()
    refresh_cache()
    return deleted > 0


def couple_all_key_glyphs() -> int:
    """One-shot backfill: for every semantic emoji the owner has already set, make sure
    the matching literal glyph is themed too (so pre-existing settings for 💎/💥/… also
    apply to hard-coded emojis in messages). Idempotent; safe to run at every startup."""
    # First ensure all canonical glyphs strictly point to their own key's custom emoji
    for other_k, glyphs in CANONICAL_KEY_GLYPHS.items():
        override = EmojiOverride.objects.filter(key=other_k).first()
        if override:
            for g in glyphs:
                norm_g = _norm_glyph(g)
                EmojiOverride.objects.update_or_create(
                    key=f"{_GLYPH_PREFIX}{norm_g}",
                    defaults={"custom_emoji_id": override.custom_emoji_id, "placeholder": norm_g}
                )

    n = 0
    for o in EmojiOverride.objects.exclude(key__startswith=_GLYPH_PREFIX):
        for g in _key_glyphs(o.key, o.placeholder):
            EmojiOverride.objects.update_or_create(
                key=f"{_GLYPH_PREFIX}{g}", defaults={"custom_emoji_id": o.custom_emoji_id, "placeholder": g}
            )
            n += 1
    if n:
        refresh_cache()
    return n

"""Persian trigger words for group chat.

Groups are where people actually play together, and `/guardian_challenge` is not
something anyone types mid-conversation. This module maps ordinary words — «اتک»,
«هیولا», «جایزه» — onto the same actions the slash commands run.

**One word per action, no synonyms.** An earlier version registered three or four
aliases each and the result was 84 triggers nobody could hold in their head: with
«رتبه», «برترین», «جدول», «لیدربرد» and «تاپ» all doing the same thing, a player
can't tell whether they're five features or one, and the help screen becomes a
wall. One canonical word means the help is a short list, and every line in it is
something you can act on.

Two rules keep this from firing on normal chatter:

* **Whole-message match only.** A message is a trigger when the entire text, once
  normalised, equals a registered word. Substring matching would make «شانس» fire
  every time someone said «شانسی» or «خوش‌شانس», which in a busy group is
  indistinguishable from a broken bot.
* **Distinctive words.** Everything here is either a game noun or an imperative
  nobody types by accident in the middle of a sentence.

Normalisation matters more than it looks. Persian text arriving from real
keyboards mixes Arabic ي/ك with Persian ی/ک, sprinkles zero-width non-joiners
through compound words, and carries Arabic-Indic digits. Two users typing what
they both read as «هیولا» can send different bytes, so everything goes through
`normalize()` on both sides of the comparison.
"""

from __future__ import annotations

import re

# Concept pages live in game/guide.py — they describe the game, not the group's
# vocabulary, and the DM shows the very same pages. Re-exported here because
# every existing caller reaches them through this module.
from game.guide import CONCEPTS as HELP_TOPICS  # noqa: F401

# Arabic forms -> Persian, plus the invisible characters that survive copy-paste.
_CHAR_MAP = {
    "ي": "ی",  # ARABIC YEH -> FARSI YEH
    "ى": "ی",  # ALEF MAKSURA -> FARSI YEH
    "ك": "ک",  # ARABIC KAF -> KEHEH
    "ة": "ه",  # TEH MARBUTA -> HEH
    "أ": "ا",  # ALEF WITH HAMZA ABOVE
    "إ": "ا",  # ALEF WITH HAMZA BELOW
    "آ": "ا",  # ALEF WITH MADDA
    "‌": " ",       # ZWNJ -> space, so «هم‌نژاد» and «هم نژاد» agree
    "‏": "",        # RTL mark
    "‎": "",        # LTR mark
    "﻿": "",        # BOM
}
# harakat / tatweel — decorative, never meaningful for a keyword
_STRIP = re.compile(r"[ً-ْـ]")
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def normalize(text: str) -> str:
    """Fold a message down to something two keyboards can agree on."""
    if not text:
        return ""
    text = text.strip().lower()
    for src, dst in _CHAR_MAP.items():
        text = text.replace(src, dst)
    text = _STRIP.sub("", text)
    text = text.translate(_DIGITS)
    # collapse runs of whitespace, and drop trailing punctuation people add
    text = re.sub(r"\s+", " ", text).strip()
    return text.strip("!?.،؟؛:")


# action key -> (the ONE word, emoji registry key for the help card, one-line
# description, longer "what actually happens" line for the category page)
#
# Every key is handled in bot/handlers/group_words.py; a key with no handler is
# caught by the test suite rather than failing silently in front of a group.
# action key -> KeywordDef. `word` is the ONE trigger; `emoji` names an entry in
# game/emoji.py so the help honours the owner's Premium choices; `summary` is the
# one-liner beside the word; `how` is the teaching text — the numbered steps and
# rules a player needs to actually learn the feature.
#
# `how` exists because the first help was a table of contents: "🦖 آزمایشگاه من —
# ۴ کلمه" tells a player nothing. A group member has never seen the DM and has no
# idea what a star, a cup or a speed-up card is, so each entry has to teach, not
# just label. Every key is handled in bot/handlers/group_words.py; a key with no
# handler is caught by the test suite rather than failing in front of a group.
KEYWORD_DEFS: dict[str, tuple[str, str, str, tuple[str, ...]]] = {
    # ── my lab ──────────────────────────────────────────────────────────────
    "creature": (
        "هیولا",
        "creature",
        "کارت هیولای فعالت",
        (
            "هیولای «فعال» همونیه که توی همه‌ی نبردها می‌جنگه — شکار، آرنا، دوئل و حمله به باس.",
            "کارت، چهار استت اصلی رو نشون می‌ده: ❤️ جان، ⚔️ حمله، 🛡 دفاع، 💨 سرعت.",
            "⭐ ستاره‌ها فقط از «ترکیب» می‌آن و استت‌ها رو ضرب می‌کنن.",
            "درجه‌ی نایابی (معمولی → اساطیری) هم ضریب استت داره؛ از باکس‌ها و غار هیولا می‌آد.",
        ),
    ),
    "equipment": (
        "تجهیزات",
        "battle",
        "چهار جایگاه تجهیزات هیولات",
        (
            "هر هیولا چهار جایگاه داره: ⚔️ سلاح، 🛡 زره، 💍 طلسم، 🧪 غلاف.",
            "هر آیتم بونوس مستقیم روی استت‌ها می‌ده و سطحش (+۱، +۲، …) بونوس رو بیشتر می‌کنه.",
            "آیتم‌ها از باکس‌ها می‌آن و توی «آهنگری» (پیوی) ارتقا پیدا می‌کنن.",
            "جایگاه خالی یعنی قدرت از دست رفته — همیشه هر چهارتا رو پر نگه دار.",
        ),
    ),
    "collection": (
        "کلکسیون",
        "collection",
        "همه‌ی هیولاهات",
        (
            "لیست کامل با ستاره، درجه و سطح. هیولای فعال با 🟢 مشخصه.",
            "هرچی هیولای بیشتری داشته باشی، شانس پیدا کردن جفت هم‌نام برای «ترکیب» بیشتره.",
            "برای عوض کردن هیولای فعال، «انتخاب» رو بفرست.",
        ),
    ),
    "select": (
        "انتخاب",
        "creature",
        "انتخاب هیولای فعال",
        (
            "لیست هیولاهات رو قوی‌ترین‌اول نشون می‌ده و با یه لمس فعالش می‌کنی.",
            "هیولای فعال توی همه‌ی نبردها می‌جنگه: اتک، شکار، نبرد و دوئل.",
            "همین‌جا توی گروه هم کار می‌کنه — لازم نیست بری پیوی.",
        ),
    ),
    "upgrade": (
        "ارتقا",
        "settings",
        "تغذیه و تمرین هیولا",
        (
            "🍖 تغذیه: طلا می‌ده، XP می‌گیره. هر وقت خواستی می‌تونی.",
            "🏋️ تمرین: رایگانه ولی کول‌داون داره — XP بیشتری از تغذیه می‌ده.",
            "با XP، هیولا سطح می‌گیره و هر سطح استت‌هاش بالا می‌ره.",
            "ارتقای اعضای بدن (بال، زره، نیش، زهر) توی پیوی انجام می‌شه.",
        ),
    ),
    "lab": (
        "آزمایشگاه",
        "profile",
        "سطح کلی و دارایی‌هات",
        (
            "🔬 سطح آزمایشگاه، عددِ پیشرفت کلی توئه — جدا از سطح هیولاها.",
            "با هر کاری که می‌کنی بالا می‌ره: شکار، آرنا، ماموریت، ترکیب و تکمیل ساختمون.",
            "جدول رتبه‌بندی هم بر اساس همین سطحه، نه قدرت هیولا.",
            "دارایی‌هات هم همین‌جاست: 💰 طلا، 🧬 DNA، 💎 الماس و ⚡ انرژی.",
        ),
    ),
    # ── fighting ────────────────────────────────────────────────────────────
    "hunt": (
        "شکار",
        "hunt",
        "شکار هیولای وحشی (تکی)",
        (
            "یه حریف وحشی پیدا می‌شه؛ می‌تونی بجنگی یا «بعدی» رو بزنی تا یکی دیگه بیاد.",
            "هر شکار ۱ ⚡ انرژی می‌خوره. انرژی خودش پر می‌شه (هر ۱۲ دقیقه یکی).",
            "حریف قوی‌تر جایزه‌ی بیشتری داره ولی شکست هم محتمل‌تره.",
            "بردن، طلا و DNA می‌ده و به هیولات XP.",
        ),
    ),
    "arena": (
        "نبرد",
        "trophy",
        "آرنای کاپ — حمله به آزمایشگاه بقیه",
        (
            "حریف بر اساس 🏆 کاپ تو انتخاب می‌شه، پس هم‌سطح خودتن.",
            "بردن: کاپ می‌گیری و بخشی از طلای حریف رو غارت می‌کنی. باختن: کاپ از دست می‌دی.",
            "بعد از اینکه بهت حمله شد، ۸ ساعت 🛡 سپر می‌گیری — ولی اگه خودت حمله کنی سپر می‌پره.",
            "جدول کاپ هفتگیه و آخر هر هفته به یه کف متناسب با رتبه‌ت ریست می‌شه.",
        ),
    ),
    "raid": (
        "احضار",
        "raid_boss",
        "احضار هیولای وحشی برای کل گروه",
        (
            "یه باس مشترک با جان زیاد توی گروه می‌آد.",
            "همه‌ی اعضا می‌تونن بهش «اتک» بزنن تا جانش تموم شه.",
            "وقتی افتاد، غنیمت بین همه‌ی کسایی که زدن پخش می‌شه.",
            "بهترین کار گروهی بازیه — هرچی بیشتر باشید، سریع‌تر می‌افته.",
        ),
    ),
    "attack": (
        "اتک",
        "attack_action",
        "حمله به باس گروه یا به یه بازیکن",
        (
            "بدون ریپلای: به <b>باسِ</b> فعال گروه حمله می‌کنه (اول با «احضار» بیارش).",
            "با <b>ریپلای</b> روی پیام یه بازیکن: بهش حمله می‌کنی — اول قدرت حریف نشون داده می‌شه و منتظر تأییدت می‌مونه.",
            "هر حمله ۱ ⚡ انرژی می‌خوره.",
            "روی باس: دمیجت سهم غنیمتت رو تعیین می‌کنه. روی بازیکن: برنده طلا و XP می‌گیره.",
        ),
    ),
    "duel": (
        "دوئل",
        "battle",
        "دوئل با یکی از اعضای گروه",
        (
            "روی پیام طرف <b>ریپلای</b> کن و بنویس «دوئل».",
            "می‌تونی مقدار شرط هم بذاری: «دوئل ۱۰۰» یعنی ۱۰۰ طلا شرط.",
            "نتیجه خودکار حساب می‌شه؛ برنده شرط رو می‌بره.",
            "عنصر هیولاها مهمه: 🔥 آتش > 🪨 خاک > ⚡ برق > 💧 آب > 🔥 آتش.",
        ),
    ),
    # ── economy ─────────────────────────────────────────────────────────────
    "mine": (
        "معدن",
        "building",
        "ساختمون‌ها و جمع‌آوری تولید",
        (
            "معدن‌ها به‌مرور 💰 طلا، 🧬 DNA و 💎 الماس تولید می‌کنن؛ باید بیای جمعشون کنی.",
            "می‌تونی هیولا بذاری داخل معدن — هرچی سطحش بالاتر، تولید بیشتر.",
            "هیولای فعال و هیولاهایی که توی غار هیولا تخم گذاشتن نمی‌تونن کار کنن.",
            "ساخت و ارتقای ساختمون توی پیوی انجام می‌شه (فقط یه کارگر داری).",
        ),
    ),
    "box": (
        "باکس",
        "diamond",
        "باکس ژنتیکی — هیولا یا تجهیزات",
        (
            "با 💰 طلا باز می‌شه و شانسی هیولا یا تجهیزات می‌ده.",
            "درجه‌ی نایابی شانسیه: معمولی تا اساطیری.",
            "جعبه‌های الماسی (پیوی) گرون‌ترن ولی همیشه هیولا می‌دن و شانس بهتری دارن.",
        ),
    ),
    "wheel": (
        "گردونه",
        "wheel",
        "گردونه‌ی شانس روزانه",
        (
            "روزی یک‌بار رایگان می‌چرخه.",
            "جایزه‌ها: طلا، DNA، الماس یا کارت سرعت.",
            "کارت سرعت، زمان ساخت ساختمون رو کم می‌کنه — خیلی به‌دردبخوره.",
        ),
    ),
    "casino": (
        "کازینو",
        "wheel",
        "کازینوی شرطی (پیوی)",
        (
            "چهار میز: یه چرخ رایگان روزانه و سه میز شرطی از ارزون تا گرون.",
            "میز رو انتخاب و تأیید می‌کنی؛ ممکنه ببری یا ببازی.",
            "شرط‌ها با طلا یا الماسن — قماره، پس با حساب‌وکتاب بازی کن.",
        ),
    ),
    "reward": (
        "جایزه",
        "gift",
        "جایزه‌ی هر ۵ دقیقه",
        (
            "هر ۵ دقیقه یک‌بار می‌تونی بگیری.",
            "جایزه‌ها: طلا، DNA، الماس یا کارت سرعت — گاهی هم جکپات.",
            "تایمرش برای هر گروه جداست.",
        ),
    ),
    "mission": (
        "ماموریت",
        "mission",
        "ماموریت‌های روزانه",
        (
            "هر روز چند کار ساده که جایزه دارن.",
            "بیشترشون خودبه‌خود با بازی کردن تیک می‌خورن.",
            "جایزه‌ها شامل کارت سرعت هم می‌شه.",
            "نیمه‌شب به وقت تهران ریست می‌شن.",
        ),
    ),
    # ── growing your creatures ──────────────────────────────────────────────
    "fusion": (
        "ترکیب",
        "lab",
        "ترکیب دو هیولای هم‌نام → ⭐ بیشتر",
        (
            "دو هیولای <b>هم‌نام</b> و <b>هم‌ستاره</b> رو ترکیب می‌کنی و یکی با ستاره‌ی بالاتر می‌گیری.",
            "همیشه ۱۰۰٪ موفقه — شکست نداره.",
            "هر دو والد <b>سوزونده می‌شن</b> و XP‌شون به فرزند می‌رسه.",
            "سقف ستاره = سطح «تالار مِهر» توئه. انجامش توی پیویه.",
        ),
    ),
    "breeding": (
        "غار",
        "egg",
        "غار هیولا — تخم بذار، هیولای تازه بگیر",
        (
            "دو هیولای آزاد رو می‌فرستی توی غار تا یه <b>تخم</b> بذارن. والدین <b>سالم می‌مونن</b>.",
            "هرچی والدین نایاب‌تر باشن، تخم دیرتر سر باز می‌کنه (تا یک روز).",
            "<b>تا تخم درنیاد هیچ‌کس نمی‌دونه چیه</b> — عنصر و درجه‌ش رازه تا لحظه‌ی هَچ.",
            "هم‌عنصر یا هم‌نژاد بودن و قدرت بالاتر، شانس تخمِ نایاب‌تر رو زیاد می‌کنه. توی پیویه.",
        ),
    ),
    # ── group standing ──────────────────────────────────────────────────────
    "leaderboard": (
        "جدول",
        "trophy",
        "جدول این گروه",
        (
            "ده آزمایشگاه برتر گروه بر اساس 🔬 سطح آزمایشگاه.",
            "سطح آزمایشگاه از فعالیت می‌آد، نه از شانس باکس — پس جدول، تلاش رو نشون می‌ده.",
        ),
    ),
    "guardian": (
        "محافظ",
        "crown",
        "محافظ فعلی گروه",
        (
            "محافظ، قوی‌ترین مدافع گروهه و روزی یک‌بار حقوق می‌گیره.",
            "با «تسخیر» می‌تونی بهش چالش بدی و جاش رو بگیری.",
        ),
    ),
    "guardian_challenge": (
        "تسخیر",
        "attack_action",
        "چالش برای محافظ شدن",
        (
            "با محافظ فعلی می‌جنگی؛ ببری، خودت محافظ می‌شی.",
            "اگه گروه محافظ نداشته باشه، اولین نفر بدون جنگ محافظ می‌شه.",
        ),
    ),
    "guardian_claim": (
        "حقوق",
        "coin",
        "حقوق روزانه‌ی محافظ",
        (
            "فقط محافظ فعلی گروه می‌تونه بگیره.",
            "روزی یک‌بار: طلا و DNA.",
        ),
    ),
    "alliance": (
        "اتحاد",
        "alliance",
        "اتحاد تو",
        (
            "اتحاد یه تیمه با خزانه‌ی مشترک.",
            "اعضا طلا واریز می‌کنن و اتحادهای دیگه می‌تونن بهش شبیخون بزنن.",
            "ساخت و پیوستن توی پیوی انجام می‌شه.",
        ),
    ),
    # ── meta ────────────────────────────────────────────────────────────────
    "start": (
        "شروع",
        "egg",
        "چطور بازی رو شروع کنم؟",
        (
            "۱⃣ برو پیوی ربات و /start بزن — یه هیولای اولیه و هدیه‌ی شروع می‌گیری.",
            "۲⃣ اسم آزمایشگاهت رو انتخاب کن (یک‌بار برای همیشه).",
            "۳⃣ برگرد گروه و «شکار» بزن تا اولین طلات رو بگیری.",
            "۴⃣ «ارتقا» بزن تا هیولات قوی‌تر شه، و «جایزه» رو هر ۵ دقیقه بگیر.",
            "۵⃣ توی پیوی ساختمون بساز — بلندمدت‌ترین بخش بازیه.",
        ),
    ),
    "help": (
        "راهنما",
        "book",
        "همین راهنما",
        ("همه‌ی کلمه‌ها، دسته‌بندی‌شده، با توضیح کامل هر کدوم.",),
    ),
}

# Category -> (key, emoji, title, one-line "what this group of features is for",
# action keys). The blurb matters: a category button that just says a count
# teaches nothing, so each section opens by saying what it's *for*.
KEYWORD_SECTIONS: tuple[tuple[str, str, str, str, tuple[str, ...]], ...] = (
    (
        "start",
        "egg",
        "شروع بازی",
        "اگه تازه اومدی، از اینجا شروع کن.",
        ("start", "help", "lab", "creature"),
    ),
    (
        "fight",
        "battle",
        "نبرد و درآمد",
        "اینجا طلا و XP در می‌آری — قلب بازی همینه.",
        ("hunt", "arena", "raid", "attack", "duel"),
    ),
    (
        "grow",
        "settings",
        "قوی‌تر کردن هیولا",
        "هیولات رو ارتقا بده، تجهیز کن و ستاره‌ش رو بالا ببر.",
        ("upgrade", "equipment", "collection", "select", "fusion", "breeding"),
    ),
    (
        "economy",
        "coin",
        "اقتصاد و جایزه",
        "منبع درآمد ثابت و جایزه‌های رایگان.",
        ("mine", "box", "wheel", "reward", "mission"),
    ),
    (
        "group",
        "crown",
        "جایگاه در گروه",
        "رقابت با بقیه‌ی اعضای گروه.",
        ("leaderboard", "guardian", "guardian_challenge", "guardian_claim", "alliance"),
    ),
)


# Extra brand triggers that map onto an existing action but aren't shown in the
# help list (they'd bloat it). Saying the game's name should always get a friendly
# response: «کایجو»/«kaiju» claim the recurring reward (so the brand word itself is
# rewarding), while «ربات» and the full name open the help card.
ALIASES: dict[str, str] = {
    "کایجو": "reward",
    "kaiju": "reward",
    "ربات": "help",
    "کایجو لجند": "help",
    "کایجولجند": "help",
    "kaiju legend": "help",
    "kaijulegend": "help",
    # easy synonyms so players reach features by the word that comes to mind
    "قرعه کشی": "wheel",     # normalize() already folds the ZWNJ in «قرعه‌کشی» to a space
    "کازینو": "casino",      # the paid gamble (PV) — distinct from the free daily wheel
    "شانس": "wheel",
    "انتخاب کایجو": "select",
    "انتخاب هیولا": "select",
    "کایجو من": "select",
}


def _build_lookup() -> dict[str, str]:
    lookup: dict[str, str] = {}
    for action, (word, _e, _s, _h) in KEYWORD_DEFS.items():
        key = normalize(word)
        # A word must mean exactly one thing. Two actions claiming the same
        # trigger would resolve by dict ordering, which is a coin flip nobody
        # would ever debug — the test suite asserts this stays empty.
        if key in lookup:
            raise ValueError(f"keyword {word!r} claimed by both {lookup[key]} and {action}")
        lookup[key] = action
    for alias, action in ALIASES.items():
        key = normalize(alias)
        if action not in KEYWORD_DEFS:
            raise ValueError(f"alias {alias!r} points at unknown action {action}")
        lookup.setdefault(key, action)  # a real word always wins over an alias
    return lookup


LOOKUP: dict[str, str] = _build_lookup()

ALL_WORDS: tuple[str, ...] = tuple(word for word, _e, _s, _h in KEYWORD_DEFS.values())

SECTION_OF: dict[str, str] = {
    action: cat for cat, _e, _t, _b, actions in KEYWORD_SECTIONS for action in actions
}


def match(text: str) -> str | None:
    """The action a message triggers, or None. Whole-message match only."""
    return LOOKUP.get(normalize(text))


def word_for(action: str) -> str:
    return KEYWORD_DEFS[action][0]


def emoji_key_for(action: str) -> str:
    return KEYWORD_DEFS[action][1]


def describe(action: str) -> str:
    return KEYWORD_DEFS[action][2]


def how(action: str) -> tuple[str, ...]:
    """The teaching lines for an action — what it does and how to use it."""
    return KEYWORD_DEFS[action][3]


def section(key: str):
    return next((s for s in KEYWORD_SECTIONS if s[0] == key), None)


def topic(key: str):
    return HELP_TOPICS.get(key)

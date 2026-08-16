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
KEYWORD_DEFS: dict[str, tuple[str, str, str, str]] = {
    # ── your lab ────────────────────────────────────────────────────────────
    "creature": (
        "هیولا",
        "creature",
        "کارت هیولای فعالت",
        "استت، ستاره، درجه و تعداد جایگاه پرِ تجهیزاتش رو نشون می‌ده.",
    ),
    "equipment": (
        "تجهیزات",
        "battle",
        "تجهیزات هیولای فعالت",
        "هر چهار جایگاه رو می‌بینی — پر و خالی — با بونوسی که هر آیتم می‌ده.",
    ),
    "collection": (
        "کلکسیون",
        "collection",
        "همه‌ی هیولاهات",
        "لیست کامل با ستاره، درجه و سطح. هیولای فعال با 🟢 مشخصه.",
    ),
    "profile": (
        "پروفایل",
        "profile",
        "سطح آزمایشگاه و دارایی‌هات",
        "سطح کلی، کاپ، تعداد هیولا، روزهای پشت‌سرهم و موجودی طلا/DNA/الماس/انرژی.",
    ),
    # ── group battles ───────────────────────────────────────────────────────
    "raid": (
        "احضار",
        "raid_boss",
        "احضار هیولای وحشی توی گروه",
        "یه باس مشترک برای کل گروه می‌آره. همه می‌تونن بهش حمله کنن.",
    ),
    "attack": (
        "اتک",
        "attack_action",
        "حمله به هیولای وحشی گروه",
        "یه انرژی خرج می‌کنه. غنیمت بین همه‌ی کسایی که زدن پخش می‌شه.",
    ),
    "duel": (
        "دوئل",
        "battle",
        "دوئل با کسی که ریپلای کردی",
        "روی پیام طرف ریپلای کن و بنویس «دوئل». می‌تونی مقدار شرط هم بذاری.",
    ),
    # ── group standing ──────────────────────────────────────────────────────
    "leaderboard": (
        "جدول",
        "trophy",
        "برترین هیولاهای این گروه",
        "ده هیولای قوی گروه به ترتیب قدرت.",
    ),
    "guardian": (
        "محافظ",
        "crown",
        "محافظ فعلی گروه",
        "کی الان محافظه و چقدر قدرت داره.",
    ),
    "guardian_challenge": (
        "تسخیر",
        "attack_action",
        "چالش برای محافظ شدن",
        "با محافظ فعلی می‌جنگی؛ ببری، جاش رو می‌گیری.",
    ),
    "guardian_claim": (
        "حقوق",
        "coin",
        "حقوق روزانه‌ی محافظ",
        "فقط محافظ گروه می‌تونه بگیره، روزی یک‌بار.",
    ),
    "alliance": (
        "اتحاد",
        "alliance",
        "اتحاد تو",
        "اسم اتحاد، اعضا و خزانه‌ش.",
    ),
    # ── rewards ─────────────────────────────────────────────────────────────
    "reward": (
        "جایزه",
        "gift",
        "جایزه‌ی دوره‌ای",
        "هر ۵ دقیقه یک‌بار: طلا، DNA، الماس یا کارت سرعت. گاهی جکپات.",
    ),
    "missions": (
        "ماموریت",
        "mission",
        "ماموریت‌های روزانه",
        "کارهای امروزت و جایزه‌ی هرکدوم.",
    ),
    # ── meta ────────────────────────────────────────────────────────────────
    "pm": (
        "پیوی",
        "lab",
        "بخش‌هایی که توی پیوی‌ان",
        "شکار، آرنا، ساختمون‌ها، گردونه، تکثیر و باکس‌ها اونجان.",
    ),
    "help": (
        "راهنما",
        "book",
        "همین راهنما",
        "همه‌ی کلمه‌ها، دسته‌بندی‌شده.",
    ),
}

# Category -> (emoji registry key, title, action keys). Drives both the help
# menu's buttons and its pages, so a new action can't be added to one and
# forgotten in the other.
KEYWORD_SECTIONS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("lab", "creature", "آزمایشگاه من", ("creature", "equipment", "collection", "profile")),
    ("battle", "battle", "نبرد گروهی", ("raid", "attack", "duel")),
    ("rank", "trophy", "جایگاه در گروه", ("leaderboard", "guardian", "guardian_challenge", "guardian_claim", "alliance")),
    ("reward", "gift", "جایزه و ماموریت", ("reward", "missions")),
    ("more", "lab", "بقیه‌ی بازی", ("pm", "help")),
)


def _build_lookup() -> dict[str, str]:
    lookup: dict[str, str] = {}
    for action, (word, _e, _d, _l) in KEYWORD_DEFS.items():
        key = normalize(word)
        # A word must mean exactly one thing. Two actions claiming the same
        # trigger would resolve by dict ordering, which is a coin flip nobody
        # would ever debug — the test suite asserts this stays empty.
        if key in lookup:
            raise ValueError(f"keyword {word!r} claimed by both {lookup[key]} and {action}")
        lookup[key] = action
    return lookup


LOOKUP: dict[str, str] = _build_lookup()

ALL_WORDS: tuple[str, ...] = tuple(word for word, _e, _d, _l in KEYWORD_DEFS.values())

SECTION_OF: dict[str, str] = {
    action: cat for cat, _e, _t, actions in KEYWORD_SECTIONS for action in actions
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


def detail(action: str) -> str:
    return KEYWORD_DEFS[action][3]


def section(key: str) -> tuple[str, str, str, tuple[str, ...]] | None:
    return next((s for s in KEYWORD_SECTIONS if s[0] == key), None)

"""Persian trigger words for group chat.

Groups are where people actually play together, and `/guardian_challenge` is not
something anyone types mid-conversation. This module maps ordinary words — «اتک»,
«هیولا», «جایزه» — onto the same actions the slash commands run.

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


# action key -> (Persian words that trigger it, one-line description for the help card)
#
# Grouped by what they do. Every key is handled in bot/handlers/group_words.py;
# a key with no handler is caught by the test suite rather than failing silently
# in front of a group.
KEYWORD_DEFS: dict[str, tuple[tuple[str, ...], str]] = {
    # ── your lab ────────────────────────────────────────────────────────────
    "creature": (("هیولا", "موجود", "هیولام", "کایجو"), "کارت هیولای فعالت"),
    "equipment": (("تجهیزات", "آیتم", "کوله", "سلاح"), "تجهیزات هیولای فعالت"),
    "collection": (("کلکسیون", "هیولاهام", "لیست"), "همه‌ی هیولاهات"),
    "profile": (("پروفایل", "پروفایلم", "آمار", "اطلاعات"), "پروفایل و سطح آزمایشگاهت"),
    "wallet": (("موجودی", "جیب", "دارایی", "کیف"), "طلا، DNA، الماس و انرژی"),
    "lab": (("آزمایشگاه", "لابم", "پایگاه"), "سطح کلی آزمایشگاهت"),
    # ── fighting ────────────────────────────────────────────────────────────
    "attack": (("اتک", "حمله", "بزن", "یورش"), "حمله به هیولای وحشی گروه"),
    "duel": (("دوئل", "مبارزه", "جنگ", "چالش"), "دوئل با کسی که ریپلای کردی"),
    "raid": (("رید", "احضار", "باس", "هیولای وحشی"), "احضار هیولای وحشی توی گروه"),
    "hunt": (("شکار", "گشت"), "شکار انفرادی (توی پیوی)"),
    "arena": (("آرنا", "کاپ", "لیگ"), "آرنای کاپ (توی پیوی)"),
    # ── group standing ──────────────────────────────────────────────────────
    "leaderboard": (("رتبه", "برترین", "جدول", "لیدربرد", "تاپ"), "جدول گروه"),
    "guardian": (("محافظ", "نگهبان"), "محافظ فعلی گروه"),
    "guardian_challenge": (("تسخیر", "فتح", "چالش محافظ"), "چالش برای محافظ شدن"),
    "guardian_claim": (("حقوق", "مقرری", "دستمزد"), "حقوق روزانه‌ی محافظ"),
    "alliance": (("اتحاد", "تیم", "کلن"), "اتحاد تو"),
    # ── economy ─────────────────────────────────────────────────────────────
    "reward": (
        ("جایزه", "شانس", "گنج", "هدیه", "بخت", "صندوق", "لوت", "پاداش"),
        "جایزه‌ی دوره‌ای (تایمر مشترک بین همه‌ی این کلمه‌ها)",
    ),
    "buildings": (("ساختمون", "ساختمان", "معدن"), "ساختمون‌هات (توی پیوی)"),
    "missions": (("ماموریت", "کوئست", "تسک"), "ماموریت‌های روزانه"),
    "wheel": (("گردونه", "چرخ"), "گردونه‌ی شانس روزانه (توی پیوی)"),
    "breeding": (("تکثیر", "پرورش"), "تکثیر زیستی (توی پیوی)"),
    "shop": (("باکس", "جعبه", "کریت"), "باکس‌ها (توی پیوی)"),
    # ── meta ────────────────────────────────────────────────────────────────
    "help": (("راهنما", "کمک", "دستورات", "بازی", "کلمات"), "همین لیست"),
    "start": (("شروع", "استارت", "عضویت"), "شروع بازی توی پیوی"),
}

# Category headings for the help card, in the order they're shown.
KEYWORD_SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("🧬 آزمایشگاه تو", ("creature", "equipment", "collection", "profile", "wallet", "lab")),
    ("⚔️ نبرد", ("attack", "duel", "raid", "hunt", "arena")),
    ("👑 گروه", ("leaderboard", "guardian", "guardian_challenge", "guardian_claim", "alliance")),
    ("💰 اقتصاد", ("reward", "buildings", "missions", "wheel", "breeding", "shop")),
    ("❓ راهنما", ("help", "start")),
)


def _build_lookup() -> dict[str, str]:
    lookup: dict[str, str] = {}
    for action, (words, _desc) in KEYWORD_DEFS.items():
        for word in words:
            key = normalize(word)
            # A word must mean exactly one thing. Two actions claiming the same
            # trigger would resolve by dict ordering, which is a coin flip nobody
            # would ever debug — the test suite asserts this stays empty.
            if key in lookup and lookup[key] != action:
                raise ValueError(f"keyword {word!r} claimed by both {lookup[key]} and {action}")
            lookup[key] = action
    return lookup


LOOKUP: dict[str, str] = _build_lookup()

ALL_WORDS: tuple[str, ...] = tuple(
    word for words, _desc in KEYWORD_DEFS.values() for word in words
)


def match(text: str) -> str | None:
    """The action a message triggers, or None. Whole-message match only."""
    return LOOKUP.get(normalize(text))


def words_for(action: str) -> tuple[str, ...]:
    return KEYWORD_DEFS[action][0]


def describe(action: str) -> str:
    return KEYWORD_DEFS[action][1]

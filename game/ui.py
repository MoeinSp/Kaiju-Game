"""One text toolkit for every Telegram message the bot sends (owner's rules, 2026-11-12 —
design/ui_polish_prompt.md, design/ui_style.md).

Two halves:

* **Builders** (num, short, pct, dur, iso, fill, bar, res, kv, screen) for screens written by
  hand: English digits, «30/30» fill values in copy mode, Latin names isolated so they can't
  flip a Persian line, one skeleton with the «6-2» divider in fixed places.
* **polish()** — run by the send hook in bot/main.py on EVERY outgoing HTML text/caption,
  after premiumize_html + dividers.emojify. It applies the same rules to the hundreds of
  screens that were never rewritten:
    - Persian/Arabic digits → English (and «٬» «٫» «٪» → «,» «.» «%»)
    - «3 از 7» → <code>3/7</code> (plain «3/7» where a code entity can't nest)
    - a number with a glued sign («-1235», «❤️+341») and every Latin run (names, DNA, VIP)
      wrapped in FSI…PDI so the bidi algorithm can't move it across the Persian text
    - a leading rank «4.» isolated (otherwise phones show «.4»)
    - a line whose first strong letter isn't Persian gets an RLM, so it stays right-to-left
    - spacing: never a blank line next to a divider, never two blank lines in a row
  URLs, @mentions, /commands, #tags, HTML entities and code/pre/tg-emoji contents are never
  touched. polish() is idempotent. The send hook falls back to the unpolished text if
  Telegram rejects the polished one, so this can never block a send.
"""

from __future__ import annotations

import html as _html
import re
import unicodedata

from game.dividers import LINE as _DIV_LINE

FSI, PDI, RLM = "\u2068", "\u2069", "\u200f"
DIV = "━━━━━━━━━━"   # a plain rule; dividers.emojify() turns it into the «6-2» line

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩٬٫٪", "01234567890123456789,.%")


# ── builders ────────────────────────────────────────────────────────────────────

def en(text):
    """Persian/Arabic digits → English digits."""
    return text.translate(_DIGITS) if isinstance(text, str) and text else text


def iso(name) -> str:
    """A name that may be Latin (player, lab, kaiju, group, alliance), HTML-escaped and
    isolated so it can't reorder the Persian line around it."""
    return FSI + _html.escape(str(name or ""), quote=False) + PDI


def num(n) -> str:
    """12,400 — a negative number is isolated so its sign stays on the left of it."""
    try:
        v = int(n)
    except (TypeError, ValueError):
        return en(str(n))
    s = f"{abs(v):,}"
    return FSI + "-" + s + PDI if v < 0 else s


def short(n, exact_below: int = 100_000) -> str:
    """120 هزار · 2.5 میلیون — anything under `exact_below` stays exact."""
    try:
        v = float(n)
    except (TypeError, ValueError):
        return en(str(n))
    a = abs(v)
    if a < max(exact_below, 1_000):
        return num(int(v))
    for size, word in ((1e9, "میلیارد"), (1e6, "میلیون"), (1e3, "هزار")):
        if a >= size:
            q = a / size
            txt = f"{q:.0f}" if q >= 100 else f"{q:.1f}".rstrip("0").rstrip(".")
            return (FSI + "-" + txt + PDI if v < 0 else txt) + " " + word
    return num(int(v))


def pct(v, digits: int = 0) -> str:
    """35% (one decimal under 10% when asked)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return en(str(v))
    if digits and abs(f) < 10:
        return f"{f:.{digits}f}".rstrip("0").rstrip(".") + "%"
    return f"{f:.0f}%"


def dur(seconds) -> str:
    """۲ روز و ۴ ساعت · ۳۵ دقیقه — the two largest units, never «276 ساعت»."""
    try:
        s = max(0, int(seconds))
    except (TypeError, ValueError):
        return en(str(seconds))
    if s < 60:
        return f"{s} ثانیه" if s else "کمتر از 1 دقیقه"
    d, rem = divmod(s, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    parts = [(d, "روز"), (h, "ساعت"), (m, "دقیقه")]
    parts = [f"{v} {w}" for v, w in parts if v]
    return " و ".join(parts[:2])


def jdate(value) -> str:
    """«15 مهر 1405» from a date/datetime (Shamsi, English digits)."""
    from game.battlepass import SHAMSI_MONTH_NAMES, gregorian_to_jalali

    try:
        jy, jm, jd = gregorian_to_jalali(value.year, value.month, value.day)
    except (AttributeError, TypeError, ValueError):
        return en(str(value))
    return f"{jd} {SHAMSI_MONTH_NAMES[jm]} {jy}"


def delta(now, before) -> str:
    """«▲ 12%» / «▼ 5%» / «＝» — the arrow before the number, never a glued sign."""
    try:
        now, before = float(now), float(before)
    except (TypeError, ValueError):
        return ""
    if not before:
        return "▲" if now > 0 else "＝"
    ch = (now - before) / abs(before) * 100
    if abs(ch) < 0.5:
        return "＝"
    return ("▲ " if ch > 0 else "▼ ") + f"{abs(ch):.0f}%"


def fill(cur, cap) -> str:
    """A fillable value — energy, xp, mission step, HP — as «<code>30/30</code>» (copy mode)."""
    return f"<code>{_plain_num(cur)}/{_plain_num(cap)}</code>"


def _plain_num(n) -> str:
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return en(str(n))


def bar(v, vmax, width: int = 8) -> str:
    """<code>██████░░</code> — the one progress bar."""
    try:
        frac = 0 if not vmax else max(0.0, min(1.0, float(v) / float(vmax)))
    except (TypeError, ValueError, ZeroDivisionError):
        frac = 0
    k = round(frac * width)
    return "<code>" + "█" * k + "░" * (width - k) + "</code>"


def res(coins=0, dna=0, diamonds=0, sep: str = " · ") -> str:
    """«💰 20,000 · 🧬 1,000 · 💎 10» with the owner's premium emoji; zero amounts skipped."""
    from game.emoji import get_emoji

    parts = []
    for key, amount in (("coin", coins), ("dna", dna), ("diamond", diamonds)):
        if amount:
            parts.append(f"{get_emoji(key)} {num(amount)}")
    return sep.join(parts)


def kv(label, value, extra=None) -> str:
    """«برچسب: <b>مقدار</b> · extra»"""
    out = f"{label}: <b>{value}</b>"
    return out + (f" · {extra}" if extra else "")


def screen(title, *sections, sub=None, foot=None, divs: bool = True) -> str:
    """The skeleton of every screen (style guide 3.1):

        title
        <i>sub</i>
        ── divider ──
        section 1
        (one blank line)
        section 2
        ── divider ──        (only when there is a foot)
        foot

    A section is a string or a list of lines; empty ones are dropped. Blank lines next to a
    divider and doubled blank lines never survive."""
    head = [title] + ([f"<i>{sub}</i>"] if sub else [])
    body = []
    for sec in sections:
        if not sec:
            continue
        txt = "\n".join(x for x in sec if x is not None) if isinstance(sec, (list, tuple)) else str(sec)
        txt = txt.strip("\n")
        if txt.strip():
            body.append(txt)
    out = list(head)
    if body:
        if divs:
            out.append(DIV)
        out.append("\n\n".join(body))
    if foot:
        out.append(DIV if divs else "")
        out.append(foot if isinstance(foot, str) else "\n".join(foot))
    return spacing("\n".join(out))


# ── polish (send hook) ──────────────────────────────────────────────────────────

_TAG = re.compile(r"(<[^<>]*>)")
_TAGNAME = re.compile(r"^</?\s*([a-zA-Z][\w-]*)")
_PROTECT = re.compile(
    r"&#?\w+;"                                  # an HTML entity
    r"|https?://\S+|www\.\S+|[\w.-]*t\.me/\S*"  # links
    r"|[\w.+-]+@[\w-]+\.[A-Za-z]{2,}"           # e-mail
    r"|@\w+|(?<![\w/])/[A-Za-z]\w*|#\w+"        # mention, /command, #tag
    r"|\u2068[^\u2069]*\u2069"                   # already isolated
)
_WORD = r"[A-Za-z][A-Za-z0-9_'’]*(?:[.&-][A-Za-z0-9_'’]+)*"
_LATIN = re.compile(_WORD + r"(?:[ ]+" + _WORD + r")*")
_SIGNED = re.compile(r"(?<![\w)\]%.,/])([+\-−])(\d[\d,]*(?:\.\d+)?%?)")
_FILL = re.compile(r"(?<![\d,.\u2068])(\d[\d,]*)[ \u00a0]+از[ \u00a0]+(\d[\d,]*)(?![\d,])")
# «120/400» or «157,000 / 157,000» shown as plain text: a fill value, into copy mode (a date
# like 1405/08/21 has two slashes and is left alone)
_SLASH = re.compile(r"(?<![\d,./\u2068])(\d[\d,]*)[ \u00a0]*/[ \u00a0]*(\d[\d,]*)(?![\d,/])")
# English words the owner doesn't want inside Persian text (style guide 3.3)
_ENG_WORDS = {"XP": "تجربه", "HP": "جون", "vs": "⚔️", "VS": "⚔️", "Vs": "⚔️", "Lv": "سطح", "LV": "سطح", "lvl": "سطح"}
_ENG_WORD = re.compile(r"(?<![A-Za-z_\-])(?:" + "|".join(_ENG_WORDS) + r")(?![A-Za-z_\-])")
# league tiers «طلا II» → «طلا 2» (a Roman numeral right after a Persian word)
_ROMAN_NUM = {"I": "1", "II": "2", "III": "3", "IV": "4", "V": "5"}
_ROMAN = re.compile(r"(?<=[\u0600-\u06ff] )(III|II|IV|I|V)(?![A-Za-z0-9])")
_CODE_PAIR = re.compile(r"<code>([\d,]+)</code>[ \u00a0]*(?:/|از)[ \u00a0]*<code>([\d,]+)</code>")
_BAR_BRACKETS = re.compile(r"\[([■□█░▰▱▓▒▏▎▍▌▋▊▉]+)\]")
_XP_PAREN = re.compile(r"[ \u00a0]*\((?:XP|xp)\)")
_PERSIAN = re.compile(r"[\u0600-\u06ff\u0750-\u077f\ufb50-\ufdff\ufe70-\ufeff]")
_RANK = re.compile(r"^((?:\s|\u200f|<[^<>]*>)*)(\d{1,4}[.)])(?=\s)")
_SKIP_TAGS = {"code", "pre", "tg-emoji"}
_PLAIN_RULE = re.compile(r"^[ \t\u200f]*[━─═▬]{5,}[ \t]*$")


def _protected_sub(s: str, fn) -> str:
    """Apply fn to the parts of s that aren't links/entities/mentions/isolates."""
    out, last = [], 0
    for m in _PROTECT.finditer(s):
        out.append(fn(s[last:m.start()]))
        out.append(m.group(0))
        last = m.end()
    out.append(fn(s[last:]))
    return "".join(out)


def _text_part(s: str, code_ok: bool) -> str:
    if not s:
        return s
    s = _BAR_BRACKETS.sub(r"\1", _XP_PAREN.sub("", s))
    for sep in (" | ", " │ ", " ┃ ", " ║ "):
        s = s.replace(sep, " · ")
    s = _ROMAN.sub(lambda m: _ROMAN_NUM[m.group(1)], s)
    s = _ENG_WORD.sub(lambda m: _ENG_WORDS[m.group(0)], s)
    s = _SIGNED.sub(lambda m: FSI + m.group(1) + m.group(2) + PDI, s)
    s = _LATIN.sub(lambda m: FSI + m.group(0) + PDI, s)
    if code_ok:
        s = _SLASH.sub(lambda m: f"<code>{m.group(1)}/{m.group(2)}</code>", s)
        s = _FILL.sub(lambda m: f"<code>{m.group(1)}/{m.group(2)}</code>", s)
    else:
        s = _FILL.sub(lambda m: f"{m.group(1)}/{m.group(2)}", s)
    return s


def _inline(html: str) -> str:
    stack: list[str] = []
    out = []
    for tok in _TAG.split(html):
        if not tok:
            continue
        if tok.startswith("<") and tok.endswith(">"):
            m = _TAGNAME.match(tok)
            if m:
                name = m.group(1).lower()
                if tok.startswith("</"):
                    if name in stack:
                        while stack and stack.pop() != name:
                            pass
                else:
                    stack.append(name)
            out.append(tok)
            continue
        if any(t in _SKIP_TAGS for t in stack):
            out.append(tok)
            continue
        code_ok = all(t == "blockquote" for t in stack)
        out.append(_protected_sub(tok, lambda part, ok=code_ok: _text_part(part, ok)))
    return "".join(out)


def _visible(html: str) -> str:
    return _html.unescape(_TAG.sub("", html))


def _needs_rlm(line: str) -> bool:
    """True when the first strong letter outside the isolates isn't right-to-left (or there
    is none): Telegram would lay such a line out left-to-right."""
    vis = _visible(line)
    for ch in re.sub("\u2068[^\u2069]*\u2069", "", vis):
        b = unicodedata.bidirectional(ch)
        if b in ("R", "AL"):
            return False
        if b == "L":
            return True
    return bool(vis.strip())


def _is_div(line: str) -> bool:
    if _PLAIN_RULE.match(line):
        return True
    return line.strip().lstrip(RLM) == _DIV_LINE.lstrip(RLM)


def spacing(html: str) -> str:
    """No blank line next to a divider, never two blank lines in a row, none at the ends.
    Lines inside <pre> are kept as they are."""
    out: list[str] = []
    depth = 0
    for raw in html.split("\n"):
        line = raw.rstrip()
        in_pre = depth > 0
        depth += raw.count("<pre") - raw.count("</pre>")
        if in_pre:
            out.append(raw)
            continue
        blank = not line.strip()
        if blank:
            if not out or not out[-1].strip() or _is_div(out[-1]):
                continue
            out.append("")
            continue
        if _is_div(line) and out and not out[-1].strip():
            out.pop()
        out.append(line)
    while out and not out[-1].strip():
        out.pop()
    return "\n".join(out)


def _line_pass(html: str) -> str:
    out = []
    depth = 0
    for line in html.split("\n"):
        in_pre = depth > 0 or "<pre" in line
        depth += line.count("<pre") - line.count("</pre>")
        if in_pre or not line.strip() or _is_div(line):
            out.append(line)
            continue
        m = _RANK.match(line)
        if m and not line[m.end(1):].startswith(FSI):
            line = line[:m.end(1)] + FSI + m.group(2) + PDI + line[m.end():]
        if not line.startswith(RLM) and _needs_rlm(line):
            line = RLM + line
        out.append(line)
    return "\n".join(out)


# a plain number shown in a grey copy box — only fill values («30/30»), links and ids
# belong in <code>; a number is just the line's main value, so it becomes bold
_CODE_NUM = re.compile(r"<code>(\s*[+\-−×]?[\d,.]+\s*[%×]?\s*)</code>")
def _code_num(m) -> str:
    """A displayed amount (1,234 · 35% · 140) becomes bold; a bare run of 4+ digits is a
    code or an id the player copies (war code «1234», a Telegram id) and stays in code."""
    v = m.group(1).strip()
    if re.fullmatch(r"[+\-−]?\d{4,}", v):
        return m.group(0)
    return f"<b>{m.group(1)}</b>"


_TG_EMOJI = re.compile(r'<tg-emoji emoji-id="(\d+)">(.*?)</tg-emoji>', re.S)
EMOJI_CAP = 100


def cap_custom_emoji(html: str, cap: int = EMOJI_CAP) -> str:
    """Telegram draws at most 100 custom emoji in a message: past that, the last ones
    (never a divider piece) fall back to their plain glyph."""
    found = list(_TG_EMOJI.finditer(html))
    extra = len(found) - cap
    if extra <= 0:
        return html
    from game.dividers import IDS

    drop = set()
    for m in reversed(found):
        if extra <= 0:
            break
        if m.group(1) not in IDS:
            drop.add(m.start())
            extra -= 1
    return _TG_EMOJI.sub(lambda m: m.group(2) if m.start() in drop else m.group(0), html)


def polish(html: str) -> str:
    """The style rules on one outgoing HTML message (see the module docstring)."""
    if not isinstance(html, str) or not html:
        return html
    text = _CODE_PAIR.sub(r"<code>\1/\2</code>", cap_custom_emoji(en(html)))
    text = _CODE_NUM.sub(_code_num, text)
    if _PERSIAN.search(_visible(text)):
        text = _inline(text)
        text = _line_pass(text)
    else:
        text = _FILL.sub(lambda m: f"{m.group(1)}/{m.group(2)}", text)
    return spacing(text)


def polish_plain(text: str) -> str:
    """A plain-text message, toast or alert: digits and «3/7» only."""
    if not isinstance(text, str) or not text:
        return text
    return _FILL.sub(lambda m: f"{m.group(1)}/{m.group(2)}", en(text))


def polish_label(text: str) -> str:
    """A button label: digits, «3/7» and « · » separators (labels are plain text)."""
    out = polish_plain(text)
    if isinstance(out, str):
        for sep in (" | ", " │ ", " ┃ "):
            out = out.replace(sep, " · ")
    return out


def visible_len(html: str) -> int:
    """Length Telegram counts against its limits (UTF-16 units, entities removed)."""
    return len(_visible(html).encode("utf-16-le")) // 2

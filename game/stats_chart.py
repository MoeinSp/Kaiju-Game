"""PNG charts for the owner's «📊 آمار کلی» (game/admin_stats.py data → one image per tab).

Plain Pillow, no plotting library: thin bars with a rounded data end anchored on the
baseline and a gap between neighbours, 3px lines, a recessive grid, one y-axis per panel
(two measures of different size get two panels, never a second axis), a legend only when
a panel has two series, and direct labels only on the highest and the latest value.
Colours: blue / orange categorical slots (validated light-surface pair) and a neutral grey
for «the day before» context lines; all text in text inks, never series colours.
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from game.media import shape_persian_text

W = 1280
PAD = 48
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK2 = "#52514e"
MUTED = "#8a8984"
GRID = "#e8e7e3"
BLUE = "#2a78d6"
ORANGE = "#eb6834"
GREY = "#a8a7a2"

_FONTS = Path(__file__).resolve().parent.parent / "assets" / "fonts"
_FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def _font(size: int, bold: bool = False):
    try:
        return ImageFont.truetype(str(_FONTS / ("Vazirmatn-Bold.ttf" if bold else "Vazirmatn-Regular.ttf")), size)
    except OSError:
        return ImageFont.load_default()


def fa(text) -> str:
    return shape_persian_text(str(text).translate(_FA))


def num(n: float) -> str:
    """Compact Persian-digit number: ۸۴۲ · ۱۲٬۴۰۰ · ۱٫۲M."""
    n = float(n or 0)
    if abs(n) >= 1_000_000:
        s = f"{n / 1_000_000:.1f}".rstrip("0").rstrip(".") + "M"
    elif abs(n) >= 100_000:
        s = f"{n / 1000:.0f}K"
    else:
        s = f"{n:,.0f}" if n == int(n) else f"{n:,.1f}"
    return s.replace(",", "٬").replace(".", "٫").translate(_FA)


class _Canvas:
    def __init__(self, panels: int, panel_h: int = 360, title: str = "", subtitle: str = ""):
        self.top = 150 if title else 40
        self.ph = panel_h
        self.h = self.top + panels * (panel_h + 40) + 20
        self.img = Image.new("RGB", (W, self.h), SURFACE)
        self.d = ImageDraw.Draw(self.img)
        if title:
            self.text_r(W - PAD, 34, title, _font(38, True), INK)
            if subtitle:
                self.text_r(W - PAD, 84, subtitle, _font(22), INK2)
        self.slot = 0

    # right-aligned Persian text (RTL)
    def text_r(self, x, y, s, font, fill):
        s = fa(s)
        self.d.text((x - self.d.textlength(s, font=font), y), s, font=font, fill=fill)

    def text_c(self, x, y, s, font, fill):
        s = fa(s)
        self.d.text((x - self.d.textlength(s, font=font) / 2, y), s, font=font, fill=fill)

    def next_panel(self, title: str, legend: list[tuple[str, str]] | None = None):
        y0 = self.top + self.slot * (self.ph + 40)
        self.slot += 1
        self.text_r(W - PAD, y0, title, _font(26, True), INK)
        if legend:   # swatch + label, right to left after the title
            f = _font(20)
            x = W - PAD - self.d.textlength(fa(title), font=_font(26, True)) - 40
            for color, label in legend:
                lab = fa(label)
                wl = self.d.textlength(lab, font=f)
                self.d.text((x - wl, y0 + 6), lab, font=f, fill=INK2)
                self.d.rounded_rectangle((x - wl - 26, y0 + 10, x - wl - 8, y0 + 28), 4, fill=color)
                x -= wl + 50
        # plot box: room on the left for y labels, below for x labels
        return (PAD + 70, y0 + 52, W - PAD, y0 + self.ph - 34)

    def grid(self, box, vmax, fmt=num, ticks=4):  # noqa: D401
        x0, y0, x1, y1 = box
        f = _font(18)
        for i in range(ticks + 1):
            v = vmax * i / ticks
            y = y1 - (y1 - y0) * i / ticks
            self.d.line((x0, y, x1, y), fill=GRID if i else MUTED, width=1 if i else 2)
            lab = fmt(v)
            self.d.text((x0 - 12 - self.d.textlength(lab, font=f), y - 12), lab, font=f, fill=MUTED)

    def x_labels(self, box, labels, every):
        x0, _y0, x1, y1 = box
        n = len(labels)
        step = (x1 - x0) / max(1, n)
        f = _font(17)
        for i, lab in enumerate(labels):
            if i % every and i != n - 1:
                continue
            self.text_c(x0 + step * (i + 0.5), y1 + 8, lab, f, MUTED)

    def png(self) -> bytes:
        buf = io.BytesIO()
        self.img.save(buf, "PNG", optimize=True)
        return buf.getvalue()


def _axis(v: float, ticks: int = 4) -> tuple[float, int]:
    """(top, tick count) with round tick steps (1/2/5·10ᵏ, 2.5 only from 25 up) — never «۱۱۲٫۵»."""
    import math

    if v <= 0:
        return 1, 1
    raw = v / ticks
    mag = 10 ** math.floor(math.log10(raw)) if raw >= 1 else 1
    steps = (1, 2, 2.5, 5, 10) if mag >= 10 else (1, 2, 5, 10)
    step = next(m * mag for m in steps if m * mag >= raw)
    n = max(1, math.ceil(v / step))
    return step * n, n


def _bars(c: _Canvas, box, values, color, labels, fmt=num, stack=None, stack_color=None, mark_partial=False):
    """Vertical bars (optionally a second stacked series on top). Labels the highest and
    the latest value directly."""
    x0, y0, x1, y1 = box
    totals = [v + (stack[i] if stack else 0) for i, v in enumerate(values)]
    vmax, ticks = _axis(max(totals) if totals else 0)
    c.grid(box, vmax, fmt, ticks)
    n = len(values)
    step = (x1 - x0) / max(1, n)
    gap = max(2, step * 0.22)
    f = _font(18, True)
    peak = max(range(n), key=lambda i: totals[i]) if n else -1
    for i, v in enumerate(values):
        bx0, bx1 = x0 + step * i + gap / 2, x0 + step * (i + 1) - gap / 2
        h = (y1 - y0) * v / vmax
        top = y1 - h
        partial = mark_partial and i == n - 1
        fill = color if not partial else _tint(color)
        if h >= 1:
            only = not (stack and stack[i])
            c.d.rounded_rectangle((bx0, top, bx1, y1), radius=min(4, (bx1 - bx0) / 2), fill=fill,
                                  corners=(only, only, False, False))
        if stack and stack[i]:
            h2 = (y1 - y0) * stack[i] / vmax
            t2 = top - h2
            if h2 >= 1:
                c.d.rounded_rectangle((bx0, t2, bx1, top - 2), radius=min(4, (bx1 - bx0) / 2),
                                      fill=stack_color if not partial else _tint(stack_color), corners=(True, True, False, False))
            top = t2
        if i in (peak, n - 1) and totals[i]:
            lab = fmt(totals[i])
            c.d.text(((bx0 + bx1) / 2 - c.d.textlength(lab, font=f) / 2, top - 28), lab, font=f, fill=INK)
    c.x_labels(box, labels, max(1, n // 10))


def _tint(hex_color: str) -> str:
    """A lighter step of the same hue — the running (partial) day."""
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % tuple(int(v + (255 - v) * 0.55) for v in (r, g, b))


def _line(c: _Canvas, box, series: list[tuple[list, str, int]], labels, fmt=num, vmax=None):
    """Lines (values may hold None = no data). series: (values, color, width)."""
    x0, y0, x1, y1 = box
    allv = [v for vals, _c, _w in series for v in vals if v is not None]
    if vmax:
        ticks = 4
    else:
        vmax, ticks = _axis(max(allv) if allv else 0)
    c.grid(box, vmax, fmt, ticks)
    n = max(len(s[0]) for s in series)
    step = (x1 - x0) / max(1, n)
    f = _font(18, True)
    for vals, color, width in series:
        pts = [(x0 + step * (i + 0.5), y1 - (y1 - y0) * v / vmax) if v is not None else None for i, v in enumerate(vals)]
        run = []
        for p in pts + [None]:
            if p is None:
                if len(run) > 1:
                    c.d.line(run, fill=color, width=width, joint="curve")
                run = []
            else:
                run.append(p)
        last = max((i for i, p in enumerate(pts) if p is not None), default=None)
        if last is not None:
            px, py = pts[last]
            c.d.ellipse((px - 7, py - 7, px + 7, py + 7), fill=color, outline=SURFACE, width=2)
            lab = fmt(vals[last])
            c.d.text((px - c.d.textlength(lab, font=f) / 2, py - 36), lab, font=f, fill=INK)
    c.x_labels(box, labels, max(1, n // 12))


def _hbars(c: _Canvas, box, rows: list[tuple[str, float]], color, fmt=num, note=None):
    """Horizontal bars for a funnel / a spread: label on the right (RTL), the bar grows left."""
    x0, y0, x1, y1 = box
    n = len(rows)
    lab_w = 150
    right = x1 - lab_w
    vmax = max((v for _l, v in rows), default=0) or 1
    rh = (y1 - y0) / max(1, n)
    f, fb = _font(20), _font(18, True)
    for i, (label, v) in enumerate(rows):
        cy = y0 + rh * i + rh / 2
        c.text_r(x1, cy - 14, label, f, INK2)
        w = (right - x0 - 120) * v / vmax
        bh = min(34, rh * 0.7)
        if w >= 1:
            c.d.rounded_rectangle((right - w, cy - bh / 2, right, cy + bh / 2), radius=4, fill=color,
                                  corners=(True, False, False, True))
        txt = fmt(v) + (("  " + fa(note(i, v))) if note else "")
        c.d.text((right - w - 12 - c.d.textlength(txt, font=fb), cy - 13), txt, font=fb, fill=INK)


def _jd(day) -> str:
    from game.battlepass import gregorian_to_jalali

    _y, m, d = gregorian_to_jalali(day.year, day.month, day.day)
    return f"{m}/{d}"


def _pct_fmt(v) -> str:
    return num(round(v)) + "٪"


# ── one image per tab ─────────────────────────────────────────────────────────
def trend_png(t: dict) -> bytes:
    rows = t["rows"]
    labels = [_jd(r["day"]) for r in rows]
    c = _Canvas(3, title=f"روند {len(rows)} روز اخیر", subtitle="ستون کم‌رنگ = امروز (هنوز تموم نشده)")
    _bars(c, c.next_panel("بازیکن فعال روزانه"), [r["dau"] for r in rows], BLUE, labels, mark_partial=True)
    _bars(c, c.next_panel("بازیکن جدید"), [r["new"] for r in rows], ORANGE, labels, mark_partial=True)
    _line(c, c.next_panel("برگشت روز بعد تازه‌واردها (D1)"), [([r["d1"] for r in rows], BLUE, 4)], labels,
          fmt=_pct_fmt, vmax=100)
    return c.png()


def day_png(h: dict) -> bytes:
    labels = [str(i) for i in range(24)]
    now = [v if i <= h["upto"] else None for i, v in enumerate(h["now"])]
    c = _Canvas(1, panel_h=480, title=f"بازیکن فعال ساعت‌به‌ساعت — {_jd(h['day'])}",
                subtitle="هر ساعت: چند نفر یه کاری توی بازی کردن")
    box = c.next_panel("بازیکن فعال", legend=[(BLUE, "این روز"), (GREY, "روز قبلش")])
    _line(c, box, [(h["before"], GREY, 3), (now, BLUE, 4)], labels)
    return c.png()


def retention_png(r: dict) -> bytes:
    co = r["cohorts"]
    c = _Canvas(3, title="ماندگاری بازیکن‌ها", subtitle="تازه‌واردهای هر روز: چند درصدشون فرداش برگشتن")
    _bars(c, c.next_panel("برگشت روز بعد (D1) — بر اساس روز ثبت‌نام"), [round(x["d1"] or 0) for x in co], BLUE,
          [_jd(x["day"]) for x in co], fmt=_pct_fmt)
    total = r["funnel"][0][1] or 1
    _hbars(c, c.next_panel("قیف پیشرفت همه‌ی بازیکن‌ها"), r["funnel"], BLUE,
           note=lambda i, v: f"({round(100 * v / total)}٪)")
    _hbars(c, c.next_panel("پراکندگی سطح آزمایشگاه"), [(f"سطح {lab}", v) for lab, v in r["buckets"]], ORANGE)
    return c.png()


def money_png(m: dict) -> bytes:
    s = m["series"]
    labels = [_jd(x["day"]) for x in s]
    c = _Canvas(2, title=f"فروش {len(s)} روز اخیر", subtitle="فقط خریدهای تأییدشده، روز تأیید")
    _bars(c, c.next_panel("فروش روزانه (تومان)"), [x["toman"] for x in s], BLUE, labels, mark_partial=True)
    _bars(c, c.next_panel("تعداد خرید"), [x["n"] for x in s], ORANGE, labels, mark_partial=True)
    return c.png()


def growth_png(g: dict) -> bytes:
    rows = g["rows"]
    c = _Canvas(1, panel_h=480, title="بازیکن‌های جدید از کجا میان؟", subtitle="هر روز: با لینک دعوت یا خودشون")
    box = c.next_panel("بازیکن جدید", legend=[(BLUE, "خودشون"), (ORANGE, "با دعوت")])
    _bars(c, box, [r["organic"] for r in rows], BLUE, [_jd(r["day"]) for r in rows],
          stack=[r["invited"] for r in rows], stack_color=ORANGE, mark_partial=True)
    return c.png()

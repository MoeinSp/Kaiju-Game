"""«📊 آمار کلی» — the owner's dashboard: tabs over the day report (owner.global_stats_cmd)
plus how the game is moving (game/admin_stats.py) and a chart image per tab
(game/stats_chart.py).

Tabs (callback `adm_st:<tab>`): trend · ret (retention) · money · grow · top. The chart
button (`adm_ch:<tab>` or `adm_ch:day:<iso>`) sends the tab's PNG as a new photo, so the
text page stays where it is.
"""

from __future__ import annotations

import asyncio
import datetime
import html
import io

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes

from bot.buttons import ADMIN, back_btn, btn
from bot.utils import run_db, safe_edit_message_text
from game import admin_stats as S
from game import stats_chart as C

DIV = "━━━━━━━━━━━━━━━━━━━━"
TABS = (("day", "📅 روز"), ("trend", "📈 روند"), ("ret", "🧲 ماندگاری"),
        ("money", "💰 درآمد و اقتصاد"), ("grow", "🚀 رشد"), ("top", "🏅 برترین‌ها"))
_MONTHS = ("فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور", "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند")


def _is_admin(update: Update) -> bool:
    from bot.handlers.owner import _is_admin as check

    return check(update)


# ── small formatting helpers ──────────────────────────────────────────────────
def jdate(day: datetime.date) -> str:
    from game.battlepass import gregorian_to_jalali

    _y, m, d = gregorian_to_jalali(day.year, day.month, day.day)
    return f"{d} {_MONTHS[m - 1]}"


def bar(v: float, vmax: float, width: int = 8) -> str:
    """A text bar: ▇▇▇▇▅░░░ (eighths for the last cell)."""
    if vmax <= 0:
        return "░" * width
    cells = max(0.0, min(width, width * v / vmax))
    full = int(cells)
    part = " ▁▂▃▄▅▆▇"[int((cells - full) * 8)] if full < width else ""
    s = "█" * full + (part if part.strip() else "")
    return s + "░" * (width - len(s))


def delta(now: float, before: float | None, unit: str = "٪") -> str:
    """«▲ ۱۲٪» / «▼ ۵٪» / «＝» against `before`."""
    if before is None:
        return ""
    if not before:
        return "▲ تازه" if now else "＝"
    pct = 100.0 * (now - before) / before
    if abs(pct) < 2:
        return "＝"
    return f"{'▲' if pct > 0 else '▼'} {abs(pct):.0f}{unit}"


def pct(v: float | None) -> str:
    return "—" if v is None else f"{v:.0f}٪"


def money_fmt(t: float) -> str:
    t = int(t or 0)
    if t >= 1_000_000:
        return f"{t / 1_000_000:.1f}".rstrip("0").rstrip(".") + " میلیون"
    return f"{t:,}"


def keyboard(tab: str, day: datetime.date | None = None) -> InlineKeyboardMarkup:
    from django.utils import timezone

    day = day or timezone.localdate()

    def tb(key, label):
        return btn(("• " if key == tab else "") + label, style=ADMIN,
                   callback_data=f"adm_stats:{day.isoformat()}" if key == "day" else f"adm_st:{key}")

    rows = [[tb(*TABS[0]), tb(*TABS[1]), tb(*TABS[2])], [tb(*TABS[3]), tb(*TABS[4]), tb(*TABS[5])]]
    if tab == "day":
        one = datetime.timedelta(days=1)
        nav = [btn("روز قبل", emoji_key="btn_prev", style=ADMIN, callback_data=f"adm_stats:{(day - one).isoformat()}")]
        if day < timezone.localdate():
            nav.append(btn("روز بعد", emoji_key="btn_next", style=ADMIN, callback_data=f"adm_stats:{(day + one).isoformat()}"))
            nav.append(btn("امروز", style=ADMIN, callback_data=f"adm_stats:{timezone.localdate().isoformat()}"))
        rows.append(nav)
    tail = []
    if tab != "top":
        tail.append(btn("🖼 نمودار", style=ADMIN, callback_data=f"adm_ch:day:{day.isoformat()}" if tab == "day" else f"adm_ch:{tab}"))
    tail.append(btn("بروزرسانی", emoji_key="btn_recheck", style=ADMIN,
                    callback_data=f"adm_stats:{day.isoformat()}" if tab == "day" else f"adm_st:{tab}"))
    rows.append(tail)
    rows.append([back_btn("admin_menu:admin_home", "بازگشت به پنل ادمین")])
    return InlineKeyboardMarkup(rows)


# ── pages ─────────────────────────────────────────────────────────────────────
def trend_text(t: dict) -> str:
    rows = t["rows"]
    vmax = max((r["dau"] for r in rows), default=0)
    best = t["best"]
    lines = [
        f"📈 <b>روند {len(rows)} روز اخیر</b>",
        "<i>هفته = ۷ روز کاملِ قبل از امروز، در برابر ۷ روز قبل‌ترش</i>",
        "", DIV,
        f"🟢 میانگین فعال روزانه: <b>{t['avg_week']:,.0f}</b> · هفته‌ی قبل {t['avg_prev']:,.0f}  <b>{delta(t['avg_week'], t['avg_prev'])}</b>",
        f"🆕 بازیکن جدید هفته: <b>{t['new_week']:,}</b> · هفته‌ی قبل {t['new_prev']:,}  <b>{delta(t['new_week'], t['new_prev'])}</b>",
        f"👥 فعال ۷ روز: <b>{t['wau']:,}</b> · فعال ۳۰ روز: <b>{t['mau']:,}</b>",
        f"🧲 چسبندگی (فعال روزانه ÷ ۳۰ روز): <b>{pct(t['stickiness'])}</b>",
        f"🏆 بهترین روز: <b>{jdate(best['day'])}</b> با {best['dau']:,} نفر",
        "", DIV, "📅 <b>روزبه‌روز</b> <i>(فعال · 🆕 جدید · ↩️ برگشت فرداشون)</i>",
    ]
    for r in reversed(rows):
        tag = " <i>(تا الان)</i>" if r["partial"] else (" <i>(تقریبی)</i>" if r["approx"] else "")
        d1 = f" · ↩️{pct(r['d1'])}" if r["d1"] is not None else ""
        lines.append(f"<code>{bar(r['dau'], vmax)}</code> {jdate(r['day'])}: <b>{r['dau']:,}</b> · 🆕{r['new']:,}{d1}{tag}")
    return "\n".join(lines)


def retention_text(r: dict) -> str:
    funnel = r["funnel"]
    total = funnel[0][1] or 1
    lines = [
        "🧲 <b>ماندگاری بازیکن‌ها</b>",
        f"<i>تازه‌واردهای {len(r['cohorts'])} روز اخیر — چند درصدشون ۱، ۳ و ۷ روز بعد برگشتن</i>",
        "", DIV,
        f"↩️ میانگین: روز بعد <b>{pct(r['d1'])}</b> · روز سوم <b>{pct(r['d3'])}</b> · روز هفتم <b>{pct(r['d7'])}</b>",
        "", "📅 <b>هر روز ثبت‌نام</b> <i>(تعداد · D1 · D3 · D7)</i>",
    ]
    for c in reversed(r["cohorts"]):
        lines.append(f"<code>{bar(c['d1'] or 0, 100, 6)}</code> {jdate(c['day'])}: {c['new']:,} نفر · "
                     f"{pct(c['d1'])} · {pct(c['d3'])} · {pct(c['d7'])}")
    lines += ["", DIV, "🪜 <b>قیف پیشرفت همه‌ی بازیکن‌ها</b>"]
    for label, v in funnel:
        lines.append(f"<code>{bar(v, total)}</code> {label}: <b>{v:,}</b> ({100 * v / total:.0f}٪)")
    rc = r["recent"]
    if rc["total"]:
        n = rc["total"]
        lines.append(f"\n🆕 تازه‌واردهای ۳۰ روز اخیر ({n:,} نفر): سطح ۳ <b>{100 * rc['l3'] / n:.0f}٪</b> · "
                     f"سطح ۵ <b>{100 * rc['l5'] / n:.0f}٪</b> · سطح ۱۰ <b>{100 * rc['l10'] / n:.0f}٪</b>")
    lines += ["", DIV, "🔁 <b>ریزش هفتگی</b>"]
    if r["prev7"]:
        lines.append(f"از <b>{r['prev7']:,}</b> نفرِ فعال هفته‌ی قبل: <b>{r['kept']:,}</b> موندن "
                     f"({100 * r['kept'] / r['prev7']:.0f}٪) · <b>{r['lost']:,}</b> نیومدن")
    lines.append(f"➕ این هفته <b>{r['came']:,}</b> نفر جدید یا برگشتی اومدن")
    lines += ["", DIV, "📊 <b>پراکندگی سطح آزمایشگاه</b>"]
    bmax = max((v for _l, v in r["buckets"]), default=0)
    for label, v in r["buckets"]:
        lines.append(f"<code>{bar(v, bmax)}</code> سطح {label}: <b>{v:,}</b> ({100 * v / total:.0f}٪)")
    return "\n".join(lines)


def money_text(m: dict) -> str:
    t, wa = m["totals"], m["week_ago"]
    lines = [
        "💰 <b>درآمد و اقتصاد</b>", "<i>فقط خریدهای تأییدشده، بر اساس روز تأیید</i>",
        "", DIV, "🧾 <b>فروش</b>",
        f"امروز: <b>{money_fmt(m['today']['toman'])}</b> تومان ({m['today']['n']} خرید) · دیروز {money_fmt(m['yday']['toman'])}",
        f"۷ روز: <b>{money_fmt(m['w7']['toman'])}</b> · ۷ روز قبلش {money_fmt(m['wp7']['toman'])}  <b>{delta(m['w7']['toman'], m['wp7']['toman'])}</b>",
        f"۳۰ روز: <b>{money_fmt(m['w30']['toman'])}</b> تومان · {m['w30']['n']:,} خرید · {m['w30']['buyers']:,} خریدار",
        f"👤 میانگین هر خریدار (۳۰ روز): <b>{money_fmt(m['arppu30'])}</b> تومان",
        f"🎯 نرخ خرید: <b>{pct(m['conv30'])}</b> از {m['mau']:,} بازیکن فعال ۳۰ روز",
        f"👑 اشتراک فعال: <b>{m['subs']:,}</b> · کل خریدارها تا حالا: <b>{m['ever_buyers']:,}</b>",
    ]
    if m["pending"]:
        lines.append(f"⏳ <b>{m['pending']}</b> رسید منتظر تأییده!")
    lines += ["", "📅 <b>۱۴ روز اخیر</b>"]
    s = m["series"][-14:]
    vmax = max((x["toman"] for x in s), default=0)
    for x in reversed(s):
        lines.append(f"<code>{bar(x['toman'], vmax)}</code> {jdate(x['day'])}: <b>{money_fmt(x['toman'])}</b> ({x['n']})")
    lines += ["", DIV, "🏦 <b>اقتصاد بازی</b>" + (f" <i>(تغییر نسبت به {wa['day']})</i>" if wa else "")]
    for key, label in (("coins", "🪙 کل طلا"), ("dna", "🧬 کل DNA"), ("diamonds", "💎 کل الماس")):
        extra = ""
        if wa:
            diff = t[key] - wa[key]
            extra = f" · {'+' if diff >= 0 else ''}{diff:,} {delta(t[key], wa[key])}"
        lines.append(f"{label}: <b>{t[key]:,}</b>{extra}")
    if not wa:
        lines.append("<i>مقایسه با هفته‌ی قبل از ۷ روز بعد از این آپدیت نشون داده می‌شه (تازه ثبتش شروع شده).</i>")
    a, md = m["avg"], m["median"]
    lines += ["", f"👥 <b>دارایی بازیکن‌های فعال ۷ روز</b> ({m['active7']:,} نفر) <i>— میانه · میانگین</i>",
              f"🪙 طلا: <b>{md['coins']:,}</b> · {a['coins']:,.0f}",
              f"🧬 DNA: <b>{md['dna']:,}</b> · {a['dna']:,.0f}",
              f"💎 الماس: <b>{md['diamonds']:,}</b> · {a['diamonds']:,.0f}",
              "<i>فاصله‌ی زیاد میانگین با میانه یعنی ثروت دست چند نفر جمع شده.</i>"]
    return "\n".join(lines)


def growth_text(g: dict) -> str:
    rf, gr, al, nb, bo, wa, du = g["referral"], g["groups"], g["alliances"], g["newbie"], g["bosses"], g["wars"], g["duels"]
    rows = g["rows"]
    vmax = max((r["invited"] + r["organic"] for r in rows), default=0)
    lines = [
        "🚀 <b>رشد</b>", "", DIV, "🤝 <b>دعوت</b>",
        f"جدیدهای ۷ روز: <b>{rf['all7']:,}</b> · با دعوت <b>{rf['new7']:,}</b> ({pct(S._pct(rf['new7'], rf['all7']))})",
        f"دعوت‌شده‌های ۳۰ روز: {rf['joined30']:,} · به سطح ۵ رسیدن (جایزه داد): <b>{rf['grown30']:,}</b> ({pct(S._pct(rf['grown30'], rf['joined30']))})",
    ]
    if rf["top"]:
        lines.append("🏅 دعوت‌کننده‌های برتر ۷ روز: " + " · ".join(f"{html.escape(n)} ({c})" for n, c in rf["top"]))
    lines += ["", "📅 <b>جدیدها روزبه‌روز</b> <i>(خودشون + با دعوت)</i>"]
    for r in reversed(rows[-10:]):
        lines.append(f"<code>{bar(r['invited'] + r['organic'], vmax)}</code> {jdate(r['day'])}: "
                     f"<b>{r['invited'] + r['organic']:,}</b> = {r['organic']:,} + 🤝{r['invited']:,}")
    lines += [
        "", DIV, "👥 <b>گروه‌ها و اتحادها</b>",
        f"گروه‌ها: <b>{gr['total']:,}</b> · فعال این هفته (۳+ بازیکن فعال): <b>{gr['active7']:,}</b>",
        f"گروه جدید ۷ روز: <b>{gr['new7']:,}</b> · با «افزودن ربات» ثبت شده: {gr['added7']:,} · جایزه‌ی افزودن گرفتن: {gr['adder_paid']:,}",
        f"اتحادها: <b>{al['total']:,}</b> ({al['members']:,} عضو) · با پایگاه گروه: <b>{al['home']:,}</b>",
        "", DIV, "🎯 <b>قابلیت‌های رشد</b> <i>(۷ روز اخیر)</i>",
        f"🎁 تقویم تازه‌وارد: {nb['eligible']:,} واجد شرایط · روز ۱: <b>{nb['d1']:,}</b> · روز ۳: <b>{nb['d3']:,}</b> · هر ۷ روز: <b>{nb['d7']:,}</b>",
        f"👹 باس گروهی: {bo['n'] or 0} بار اومد · <b>{bo['dead'] or 0}</b> کشته · {bo['escaped'] or 0} فرار · {bo['players']:,} نفر زدن",
        f"⚔️ جنگ گروه‌ها: {wa['n'] or 0} چالش · <b>{wa['played'] or 0}</b> جنگ واقعی",
        f"🥊 چالش رفیق: {du['links']:,} لینک · <b>{du['runs']:,}</b> مبارزه · {du['newcomers']:,} تازه‌وارد · {du['won']:,} برد رفیق",
    ]
    return "\n".join(lines)


def top_text(data: dict) -> str:
    from game.emoji import get_emoji

    lines = [f"🏅 <b>برترین‌ها و موارد مشکوک</b>", "", DIV, f"{get_emoji('coin')} <b>پولدارترین‌ها (طلا)</b>"]
    for i, p in enumerate(data["top_players"], 1):
        lines.append(f"{i}. {html.escape(p['name'])} — <b>{p['coins']:,}</b>")
    lines += ["", f"{get_emoji('creature')} <b>قوی‌ترین موجودات (سطح)</b>"]
    for i, c in enumerate(data["top_creatures"], 1):
        lines.append(f"{i}. {html.escape(c['name'])} (Lv{c['level']}) — {html.escape(c['owner'])}")
    lines += ["", DIV]
    if data["suspicious"]:
        lines.append(f"{get_emoji('warning')} <b>فعالیت مشکوک امروز</b> <i>(بیشتر از سقف ممکن با انرژی عادی — باگ یا اکسپلویت)</i>")
        for s in data["suspicious"]:
            lines.append(f"• {html.escape(s['name'])} — {s['action']}: {s['count']} بار (سقف: {s['limit']})")
    else:
        lines.append(f"{get_emoji('confirm')} هیچ فعالیت مشکوکی امروز پیدا نشد.")
    return "\n".join(lines)


def _top_sync():
    from game.report import progress_report

    return progress_report()


_PAGES = {
    "trend": (lambda: S.trend(14), trend_text),
    "ret": (S.retention, retention_text),
    "money": (S.money, money_text),
    "grow": (lambda: S.growth(14), growth_text),
    "top": (_top_sync, top_text),
}
_CHARTS = {
    "trend": (lambda: S.trend(14), C.trend_png, "📈 روند ۱۴ روز اخیر"),
    "ret": (S.retention, C.retention_png, "🧲 ماندگاری و قیف پیشرفت"),
    "money": (S.money, C.money_png, "💰 فروش ۳۰ روز اخیر"),
    "grow": (lambda: S.growth(14), C.growth_png, "🚀 بازیکن‌های جدید از کجا میان"),
}


async def show_tab(update: Update, context: ContextTypes.DEFAULT_TYPE, tab: str) -> None:
    fetch, render = _PAGES[tab]
    data = await run_db(fetch)
    text, kb = render(data), keyboard(tab)
    query = update.callback_query
    if query:
        await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=kb)
    else:
        await update.effective_message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def tab_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_admin(update):
        await query.answer()
        return
    tab = query.data.split(":", 1)[1]
    if tab not in _PAGES:
        await query.answer()
        return
    await query.answer("⏳ دارم حساب می‌کنم…")
    await show_tab(update, context, tab)


async def report_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """«گزارش پیشرفت» in the admin home → the «🏅 برترین‌ها» tab."""
    if not _is_admin(update):
        return
    await show_tab(update, context, "top")


async def chart_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_admin(update):
        await query.answer()
        return
    parts = query.data.split(":")
    await query.answer("🖼 دارم نمودار رو می‌کشم…")
    if parts[1] == "day":
        from bot.handlers.owner import _parse_stats_day

        day = _parse_stats_day(parts[2]) if len(parts) > 2 else None
        if day is None:
            return
        data = await run_db(S.hourly, day)
        png = await asyncio.to_thread(C.day_png, data)
        caption = f"📅 بازیکن فعال ساعت‌به‌ساعت — {jdate(day)}"
    elif parts[1] in _CHARTS:
        fetch, draw, caption = _CHARTS[parts[1]]
        data = await run_db(fetch)
        png = await asyncio.to_thread(draw, data)
    else:
        return
    await context.bot.send_photo(update.effective_chat.id, photo=io.BytesIO(png), caption=caption)


def register(application) -> None:
    application.add_handler(CallbackQueryHandler(tab_callback, pattern=r"^adm_st:[a-z]+$"))
    application.add_handler(CallbackQueryHandler(chart_callback, pattern=r"^adm_ch:"))

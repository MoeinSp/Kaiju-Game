"""Admin «تخفیف همگانی» — % off every real-money Toman price, with optional reason
and a broadcast that carries buy buttons (packs/offers + VIP).

Wizard: percent → duration → reason (preset or typed) → broadcast on/off → confirm.
"""

from __future__ import annotations

import asyncio
import logging

from django.utils import timezone
from telegram import InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes

from bot.buttons import ADMIN, CONFIRM, DANGER, NAV, SHOP, back_btn, btn
from bot.utils import alert_text, run_db, safe_edit_message_text
from game import globalsale as GS
from game.creature import GameError

logger = logging.getLogger(__name__)

DRAFT_KEY = "gsale_draft"
AWAIT_REASON = "gsale_reason"
_DIV = "━━━━━━━━━━━━━━━━━━━━"
PERCENTS = (10, 20, 30, 40, 50, 70)
DURATIONS = ((6, "۶ ساعت"), (12, "۱۲ ساعت"), (24, "۲۴ ساعت"), (48, "۲ روز"), (72, "۳ روز"), (168, "۷ روز"))
REASON_PRESETS = ("بلک فرایدی", "نوروز", "یلدا", "جشنواره")


def _is_admin(update: Update) -> bool:
    from bot.handlers.owner import _is_admin as is_admin

    return is_admin(update)


def _hours_label(hours: int) -> str:
    return dict(DURATIONS).get(hours) or (f"{hours // 24} روز" if hours % 24 == 0 else f"{hours} ساعت")


def _weekday(dt) -> str:
    return ("دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه", "شنبه", "یکشنبه")[dt.weekday()]


def _panel_sync() -> tuple[str, bool]:
    lines = ["🔥 <b>تخفیف همگانی</b>", _DIV]
    sale = GS.active_sync()
    if sale is None:
        lines.append("الان هیچ تخفیف همگانی‌ای فعال نیست.")
    else:
        left = int((sale.ends_at - timezone.now()).total_seconds())
        reason = (sale.reason or "").strip() or "—"
        lines.append(f"✅ <b>{sale.percent}٪</b> روی همهٔ خریدهای تومانی")
        lines.append(f"🏷 مناسبت: <b>{reason}</b>")
        lines.append(f"⏳ {left // 3600} ساعت و {left % 3600 // 60} دقیقه مونده")
    lines += [
        _DIV,
        "<i>روی پک‌ها، افرهای روزانه/هفتگی، مقدار دلخواه، اشتراک VIP و هر آیتم تومانی اعمال می‌شه. "
        "قیمت طلا/الماس داخل بازی دست نمی‌خوره.</i>",
    ]
    return "\n".join(lines), sale is not None


def _panel_keyboard(has_active: bool) -> InlineKeyboardMarkup:
    rows = [[btn("تخفیف جدید", emoji_key="btn_gift", style=ADMIN, callback_data="gsale:new")]]
    if has_active:
        rows.append([btn("لغو تخفیف همگانی", emoji_key="btn_cancel", style=DANGER, callback_data="gsale:stop")])
        rows.append([btn("اعلام همگانی دوباره", emoji_key="btn_broadcast", style=SHOP, callback_data="gsale:rebroadcast")])
    rows.append([back_btn("menu:admin", "بازگشت")])
    return InlineKeyboardMarkup(rows)


async def sale_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        return
    text, has_active = await run_db(_panel_sync)
    await safe_edit_message_text(
        update.callback_query, text, parse_mode="HTML", reply_markup=_panel_keyboard(has_active),
    )


def _draft_text(d: dict) -> str:
    lines = ["🔥 <b>تخفیف همگانی جدید</b>", _DIV]
    lines.append(f"۱) تخفیف: <b>{d['percent']}٪</b>" if d.get("percent") else "۱) چند درصد تخفیف؟")
    if d.get("percent"):
        lines.append(f"۲) مدت: <b>{_hours_label(d['hours'])}</b>" if d.get("hours") else "۲) چه مدت؟")
    if d.get("hours"):
        if "reason" in d:
            reason = d["reason"] or "بدون مناسبت"
            lines.append(f"۳) مناسبت: <b>{reason}</b>")
        else:
            lines.append("۳) مناسبت / علت تخفیف؟")
    if "reason" in d and d.get("hours"):
        lines.append(
            "۴) ارسال همگانی: <b>"
            + ("✅ بله، با دکمه‌های خرید" if d.get("broadcast") else "❌ نه، فقط قیمت عوض بشه")
            + "</b>"
        )
        lines += [_DIV, f"نمونه: قیمت ۱۰۰٬۰۰۰ ت ← <b>{GS.discounted(100_000, d['percent']):,}</b> ت"]
    return "\n".join(lines)


def _draft_keyboard(d: dict) -> InlineKeyboardMarkup:
    rows = []
    if not d.get("percent"):
        rows += [[btn(f"{p}٪", style=SHOP, callback_data=f"gsale:p:{p}") for p in PERCENTS[i:i + 3]] for i in (0, 3)]
    elif not d.get("hours"):
        rows += [[btn(label, style=SHOP, callback_data=f"gsale:h:{h}") for h, label in DURATIONS[i:i + 3]] for i in (0, 3)]
    elif "reason" not in d:
        for i in range(0, len(REASON_PRESETS), 2):
            chunk = REASON_PRESETS[i:i + 2]
            rows.append([btn(r, style=SHOP, callback_data=f"gsale:r:{r}") for r in chunk])
        rows.append([btn("✍️ نوشتن مناسبت", emoji_key="btn_report", style=ADMIN, callback_data="gsale:rtype")])
        rows.append([btn("بدون مناسبت", style=NAV, callback_data="gsale:r:")])
    else:
        rows.append([btn(
            ("✅ " if d.get("broadcast") else "⬜️ ") + "ارسال همگانی با دکمه‌های خرید",
            style=ADMIN, callback_data="gsale:b",
        )])
        rows.append([btn(
            "فعال کن" + (" و بفرست" if d.get("broadcast") else ""),
            emoji_key="btn_confirm", style=CONFIRM, callback_data="gsale:go",
        )])
    rows.append([
        btn("از اول", emoji_key="btn_recheck", style=ADMIN, callback_data="gsale:new"),
        back_btn("admin_menu:global_sale", "انصراف"),
    ])
    return InlineKeyboardMarkup(rows)


async def sale_wizard_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_admin(update):
        await query.answer()
        return
    parts = query.data.split(":")
    step = parts[1]

    if step == "stop":
        n = await run_db(GS.stop)
        await query.answer(f"{n} تخفیف لغو شد.")
        await sale_panel(update, context)
        return

    if step == "rebroadcast":
        sale = await run_db(GS.active_sync)
        if sale is None:
            await query.answer("تخفیف فعالی نیست.", show_alert=True)
            return
        await query.answer("📢 ارسال همگانی شروع شد.")
        asyncio.create_task(broadcast_sale(context.bot, sale.id, report_chat=update.effective_user.id))
        return

    if step == "new":
        context.user_data[DRAFT_KEY] = {"broadcast": True}
        context.user_data.pop(AWAIT_REASON, None)

    d = context.user_data.setdefault(DRAFT_KEY, {"broadcast": True})

    if step == "p":
        d["percent"] = int(parts[2])
        d.pop("hours", None)
        d.pop("reason", None)
    elif step == "h":
        d["hours"] = int(parts[2])
        d.pop("reason", None)
    elif step == "r":
        d["reason"] = ":".join(parts[2:])  # empty = no reason
        context.user_data.pop(AWAIT_REASON, None)
    elif step == "rtype":
        context.user_data[AWAIT_REASON] = True
        from bot.handlers.owner import AWAITING_ADMIN_KEY

        context.user_data[AWAITING_ADMIN_KEY] = {"action": "gsale_reason"}
        await query.answer()
        await safe_edit_message_text(
            query,
            "✍️ مناسبت تخفیف رو بفرست (مثلاً بلک فرایدی):\n"
            "<i>یا /cancel برای انصراف از نوشتن.</i>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                back_btn("gsale:new", "انصراف"),
            ]]),
        )
        return
    elif step == "b":
        d["broadcast"] = not d.get("broadcast")
    elif step == "go":
        if not d.get("percent") or not d.get("hours") or "reason" not in d:
            await query.answer("اول همه‌ی مرحله‌ها رو انتخاب کن.", show_alert=True)
            return
        try:
            sale = await run_db(
                GS.start, d["percent"], d["hours"], d.get("reason") or "", bool(d.get("broadcast")),
            )
        except GameError as exc:
            await query.answer(alert_text(exc), show_alert=True)
            return
        context.user_data.pop(DRAFT_KEY, None)
        context.user_data.pop(AWAIT_REASON, None)
        await query.answer("✅ تخفیف همگانی فعال شد.")
        note = "✅ <b>تخفیف همگانی فعال شد.</b>"
        if sale.broadcast:
            note += "\n📢 ارسال همگانی شروع شد؛ آخرش نتیجه رو برات می‌فرستم."
            asyncio.create_task(broadcast_sale(context.bot, sale.id, report_chat=update.effective_user.id))
        text, _ = await run_db(_panel_sync)
        await safe_edit_message_text(
            query, note + "\n\n" + text, parse_mode="HTML", reply_markup=_panel_keyboard(True),
        )
        return

    await query.answer()
    await safe_edit_message_text(query, _draft_text(d), parse_mode="HTML", reply_markup=_draft_keyboard(d))


async def capture_reason(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    """Owner typed the sale reason. Returns True if this awaiting was ours."""
    awaiting = context.user_data.get("awaiting_admin_input") or {}
    if awaiting.get("action") != "gsale_reason":
        return False
    context.user_data.pop("awaiting_admin_input", None)
    context.user_data.pop(AWAIT_REASON, None)
    d = context.user_data.setdefault(DRAFT_KEY, {"broadcast": True})
    d["reason"] = (text or "").strip()[:64]
    await update.effective_message.reply_text(
        _draft_text(d), parse_mode="HTML", reply_markup=_draft_keyboard(d),
    )
    return True


def announcement(sale) -> tuple[str, InlineKeyboardMarkup]:
    hours = max(1, round((sale.ends_at - sale.starts_at).total_seconds() / 3600))
    ends = timezone.localtime(sale.ends_at)
    reason = (sale.reason or "").strip()
    title = f"به مناسبت «{reason}»" if reason else "تخفیف همگانی"
    lines = [
        f"🔥 <b>{title}</b>",
        "",
        f"<b>{sale.percent}٪ تخفیف</b> روی <b>همهٔ خریدهای تومانی</b>:",
        "▫️ افرهای روزانه و هفتگی",
        "▫️ پک‌های ویژه و مقدار دلخواه",
        "▫️ اشتراک طلایی و نقره‌ای",
        "",
        f"⏳ فقط تا <b>{ends.strftime('%H:%M')}</b> روز {_weekday(ends)}"
        f" ({_hours_label(hours)}) — بعدش قیمت برمی‌گرده.",
        "👇 همین الان بخر",
    ]
    rows = [
        [btn("خرید افر و پک‌ها", emoji_key="btn_buy", style=SHOP, callback_data="buy_open")],
        [
            btn("اشتراک طلایی", emoji_key="btn_sub_gold", style=SHOP, callback_data="sub_pick:gold"),
            btn("اشتراک نقره‌ای", emoji_key="btn_sub_silver", style=SHOP, callback_data="sub_pick:silver"),
        ],
    ]
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def _broadcast_targets_sync(sale_id: int):
    from bio_lab.models import GlobalSale, User

    sale = GlobalSale.objects.filter(id=sale_id).first()
    users = list(User.objects.filter(is_banned=False).values_list("id", flat=True))
    return sale, users


async def broadcast_sale(bot, sale_id: int, report_chat: int | None = None) -> dict:
    from bot.handlers.owner import BROADCAST_DELAY_SECONDS, _broadcast_one, _broadcast_summary

    sale, users = await run_db(_broadcast_targets_sync, int(sale_id))
    counts = {"sent": 0, "blocked": 0, "failed": 0}
    if sale is None:
        return counts
    text, keyboard = announcement(sale)
    for uid in users:
        counts[await _broadcast_one(lambda uid=uid: bot.send_message(
            chat_id=uid, text=text, parse_mode="HTML", reply_markup=keyboard,
            disable_web_page_preview=True,
        ))] += 1
        await asyncio.sleep(BROADCAST_DELAY_SECONDS)
    logger.info("global sale broadcast: %s", counts)
    if report_chat:
        try:
            await bot.send_message(
                chat_id=report_chat,
                text="🔥 تخفیف همگانی\n" + _broadcast_summary(counts),
                parse_mode="HTML",
            )
        except Exception:  # noqa: BLE001
            pass
    return counts


def register(application) -> None:
    application.add_handler(CallbackQueryHandler(sale_wizard_callback, pattern=r"^gsale:"))

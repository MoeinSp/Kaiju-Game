"""«تخفیف اشتراک» — admin panel wizard for time-limited subscription discounts, and the
broadcast that announces one (game/subscription.py holds the rules and the prices).

Flow (all buttons, no typing): tier → percent → duration → «ارسال همگانی» on/off → confirm.
The draft lives in context.user_data until it is confirmed."""

from __future__ import annotations

import asyncio
import logging

from django.utils import timezone
from telegram import InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes

from bot.buttons import ADMIN, CONFIRM, DANGER, SHOP, back_btn, btn
from bot.utils import alert_text, run_db, safe_edit_message_text
from game import subscription as S
from game.creature import GameError

logger = logging.getLogger(__name__)

DRAFT_KEY = "subd_draft"
_DIV = "━━━━━━━━━━━━━━━━━━━━"
PERCENTS = (10, 20, 30, 40, 50, 70)
DURATIONS = ((6, "۶ ساعت"), (12, "۱۲ ساعت"), (24, "۲۴ ساعت"), (48, "۲ روز"), (72, "۳ روز"), (168, "۷ روز"))
TIER_LABELS = {"gold": "👑 اشتراک طلایی", "silver": "🥈 اشتراک نقره‌ای", "all": "هر دو اشتراک"}


def _is_admin(update: Update) -> bool:
    from bot.handlers.owner import _is_admin as is_admin

    return is_admin(update)


def _hours_label(hours: int) -> str:
    return dict(DURATIONS).get(hours) or (f"{hours // 24} روز" if hours % 24 == 0 else f"{hours} ساعت")


# ── the panel ────────────────────────────────────────────────────────────────
def _panel_sync() -> str:
    lines = ["🏷 <b>تخفیف اشتراک</b>", _DIV]
    active = S.active_discounts()
    if not active:
        lines.append("الان هیچ تخفیفی فعال نیست.")
    for d in active:
        left = int((d.ends_at - timezone.now()).total_seconds())
        lines.append(f"✅ {TIER_LABELS.get(d.tier, d.tier)}: <b>{d.percent}٪</b> تخفیف")
        lines.append(f"   ⏳ {left // 3600} ساعت و {left % 3600 // 60} دقیقه مونده")
    lines += [_DIV, "<i>قیمت تخفیف‌خورده همون لحظه توی صفحه‌ی اشتراک، فاکتور خرید و مینی‌اپ اعمال می‌شه.</i>"]
    return "\n".join(lines)


def _panel_keyboard(has_active: bool) -> InlineKeyboardMarkup:
    rows = [[btn("تخفیف جدید", emoji_key="btn_gift", style=ADMIN, callback_data="subd:new")]]
    if has_active:
        rows.append([btn("لغو همه‌ی تخفیف‌ها", emoji_key="btn_cancel", style=DANGER, callback_data="subd:stop")])
    rows.append([back_btn("menu:admin", "بازگشت")])
    return InlineKeyboardMarkup(rows)


async def discount_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        return
    text = await run_db(_panel_sync)
    has_active = bool(await run_db(lambda: S.active_discounts()))
    await safe_edit_message_text(update.callback_query, text, parse_mode="HTML", reply_markup=_panel_keyboard(has_active))


# ── the wizard ───────────────────────────────────────────────────────────────
def _draft_text(d: dict) -> str:
    lines = ["🏷 <b>تخفیف جدید</b>", _DIV]
    lines.append(f"۱) اشتراک: <b>{TIER_LABELS[d['tier']]}</b>" if d.get("tier") else "۱) کدوم اشتراک؟")
    if d.get("tier"):
        lines.append(f"۲) تخفیف: <b>{d['percent']}٪</b>" if d.get("percent") else "۲) چند درصد تخفیف؟")
    if d.get("percent"):
        lines.append(f"۳) مدت: <b>{_hours_label(d['hours'])}</b>" if d.get("hours") else "۳) چه مدت؟")
    if d.get("hours"):
        lines.append("۴) ارسال همگانی: <b>" + ("✅ بله، با دکمه‌ی خرید" if d.get("broadcast") else "❌ نه، فقط قیمت عوض بشه") + "</b>")
        lines.append(_DIV)
        for tier in (("gold", "silver") if d["tier"] == "all" else (d["tier"],)):
            base = S.SUBSCRIPTION_TIERS[tier]["price_toman"]
            lines.append(f"{TIER_LABELS[tier]}: <s>{base:,}</s> ← <b>{S.discounted(base, d['percent']):,}</b> تومان")
    return "\n".join(lines)


def _draft_keyboard(d: dict) -> InlineKeyboardMarkup:
    rows = []
    if not d.get("tier"):
        rows += [[btn(TIER_LABELS["gold"], style=SHOP, callback_data="subd:t:gold"),
                  btn(TIER_LABELS["silver"], style=SHOP, callback_data="subd:t:silver")],
                 [btn(TIER_LABELS["all"], style=SHOP, callback_data="subd:t:all")]]
    elif not d.get("percent"):
        rows += [[btn(f"{p}٪", style=SHOP, callback_data=f"subd:p:{p}") for p in PERCENTS[i:i + 3]] for i in (0, 3)]
    elif not d.get("hours"):
        rows += [[btn(label, style=SHOP, callback_data=f"subd:h:{h}") for h, label in DURATIONS[i:i + 3]] for i in (0, 3)]
    else:
        rows.append([btn(("✅ " if d.get("broadcast") else "⬜️ ") + "ارسال همگانی با دکمه‌ی خرید",
                         style=ADMIN, callback_data="subd:b")])
        rows.append([btn("فعال کن" + (" و بفرست" if d.get("broadcast") else ""), emoji_key="btn_confirm",
                         style=CONFIRM, callback_data="subd:go")])
    rows.append([btn("از اول", emoji_key="btn_recheck", style=ADMIN, callback_data="subd:new"),
                 back_btn("admin_menu:sub_discount", "انصراف")])
    return InlineKeyboardMarkup(rows)


async def discount_wizard_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_admin(update):
        await query.answer()
        return
    parts = query.data.split(":")
    step = parts[1]
    if step == "stop":
        n = await run_db(S.stop_discounts)
        await query.answer(f"{n} تخفیف لغو شد.")
        await discount_panel(update, context)
        return
    if step == "new":
        context.user_data[DRAFT_KEY] = {"broadcast": True}
    d = context.user_data.setdefault(DRAFT_KEY, {"broadcast": True})
    if step == "t":
        d["tier"] = parts[2]
    elif step == "p":
        d["percent"] = int(parts[2])
    elif step == "h":
        d["hours"] = int(parts[2])
    elif step == "b":
        d["broadcast"] = not d.get("broadcast")
    elif step == "go":
        if not all(d.get(k) for k in ("tier", "percent", "hours")):
            await query.answer("اول همه‌ی مرحله‌ها رو انتخاب کن.", show_alert=True)
            return
        try:
            discounts = await run_db(S.start_discount, d["tier"], d["percent"], d["hours"], bool(d.get("broadcast")))
        except GameError as exc:
            await query.answer(alert_text(exc), show_alert=True)
            return
        context.user_data.pop(DRAFT_KEY, None)
        await query.answer("✅ تخفیف فعال شد.")
        note = "✅ <b>تخفیف فعال شد.</b>"
        if discounts and discounts[0].broadcast:
            note += "\n📢 ارسال همگانی شروع شد؛ آخرش نتیجه رو برات می‌فرستم."
            asyncio.create_task(broadcast_discount(context.bot, [x.id for x in discounts], report_chat=update.effective_user.id))
        text = await run_db(_panel_sync)
        await safe_edit_message_text(query, note + "\n\n" + text, parse_mode="HTML", reply_markup=_panel_keyboard(True))
        return
    await query.answer()
    await safe_edit_message_text(query, _draft_text(d), parse_mode="HTML", reply_markup=_draft_keyboard(d))


# ── the broadcast ────────────────────────────────────────────────────────────
def announcement(discounts) -> tuple[str, InlineKeyboardMarkup]:
    """The message every player gets, with one buy button per discounted tier."""
    d0 = discounts[0]
    hours = max(1, round((d0.ends_at - d0.starts_at).total_seconds() / 3600))
    ends = timezone.localtime(d0.ends_at)
    tiers = [t for t in ("gold", "silver") if any(x.tier in (t, "all") for x in discounts)]
    lines = [f"🔥 <b>تخفیف ویژه‌ی {_hours_label(hours)}!</b>", ""]
    for tier in tiers:
        cfg = S.SUBSCRIPTION_TIERS[tier]
        pct = max(x.percent for x in discounts if x.tier in (tier, "all"))
        lines += [
            f"{cfg['badge']} <b>{cfg['name']}</b> با <b>{pct}٪ تخفیف</b>",
            f"💰 <s>{cfg['price_toman']:,}</s> ← <b>{S.discounted(cfg['price_toman'], pct):,} تومان</b> (۳۰ روزه)",
            "",
        ]
    main = S.SUBSCRIPTION_TIERS[tiers[0]]
    lines.append("✨ <b>چی می‌گیری؟</b>")
    lines += [f"▫️ {p}" for p in main["perks"]]
    lines += ["", f"⏳ فقط تا <b>{ends.strftime('%H:%M')}</b> روز {_weekday(ends)} — بعدش قیمت برمی‌گرده.",
              "👇 همین الان فعالش کن"]
    rows = [[btn(f"خرید {S.SUBSCRIPTION_TIERS[t]['name']} با تخفیف", emoji_key="btn_sub_gold" if t == "gold" else "btn_sub_silver",
                 style=SHOP, callback_data=f"sub_pick:{t}")] for t in tiers]
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def _weekday(dt) -> str:
    return ("دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه", "شنبه", "یکشنبه")[dt.weekday()]


def _broadcast_targets_sync(ids):
    from bio_lab.models import SubscriptionDiscount, User

    discounts = list(SubscriptionDiscount.objects.filter(id__in=ids).order_by("id"))
    users = list(User.objects.filter(is_banned=False).values_list("id", flat=True))
    return discounts, users


async def broadcast_discount(bot, discount_ids, report_chat: int | None = None) -> dict:
    """Send the announcement to every (non-banned) player. Uses the admin broadcast's
    delivery helper, so flood-waits are waited out instead of skipping people."""
    from bot.handlers.owner import BROADCAST_DELAY_SECONDS, _broadcast_one, _broadcast_summary

    discounts, users = await run_db(_broadcast_targets_sync, list(discount_ids))
    counts = {"sent": 0, "blocked": 0, "failed": 0}
    if not discounts:
        return counts
    text, keyboard = announcement(discounts)
    for uid in users:
        counts[await _broadcast_one(lambda uid=uid: bot.send_message(
            chat_id=uid, text=text, parse_mode="HTML", reply_markup=keyboard))] += 1
        await asyncio.sleep(BROADCAST_DELAY_SECONDS)
    logger.info("subscription discount broadcast: %s", counts)
    if report_chat:
        try:
            await bot.send_message(chat_id=report_chat, text="🏷 تخفیف اشتراک\n" + _broadcast_summary(counts), parse_mode="HTML")
        except Exception:  # noqa: BLE001
            pass
    return counts


def register(application) -> None:
    application.add_handler(CallbackQueryHandler(discount_wizard_callback, pattern=r"^subd:"))

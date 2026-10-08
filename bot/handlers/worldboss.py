"""«👹 غول سرگردان» — the private-chat screen for game/worldboss.py.

Screens are kept short on purpose: they may be shown as a photo caption (~1,000 chars)."""

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

from bio_lab.repository import get_or_create_user
from bot.buttons import BATTLE, NAV, back_btn, btn
from bot.utils import alert_text, run_db, safe_edit_message_text, send_screen
from django.utils import timezone
from game import constants, worldboss
from game.creature import GameError
from game.emoji import get_emoji

_DIV = "━━━━━━━━━━━━━━━━━━━━"


def _fmt(seconds: int) -> str:
    h, rem = divmod(max(0, int(seconds)), 3600)
    m = rem // 60
    if h:
        return f"{h} ساعت و {m} دقیقه"
    return f"{max(1, m)} دقیقه"


def _panel_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    return worldboss.panel_state(user)


def _render(data: dict, note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    boss = data["boss"]
    lines = ["👹 <b>غول سرگردان</b>", _DIV]
    if note:
        lines = [note, ""] + lines
    rows = []
    if boss is None:
        lines.append("<blockquote>روزی دو بار یه غول برای کل سرور ظاهر می‌شه و ۳۰ دقیقه می‌مونه. "
                     f"هر نفر {worldboss.HITS_PER_PLAYER} ضربه داره؛ هر چی سرور بیشتر از جونش بزنه، جعبه‌ی بهتری به همه می‌رسه.</blockquote>")
        nxt = data["next_spawn_at"]
        if nxt is not None:
            local = timezone.localtime(nxt)
            window = next((w for w in worldboss.SPAWN_WINDOWS if w[0] <= local.hour < w[1]), None)
            day = "امروز" if local.date() == timezone.localdate() else "فردا"
            if window:
                lines.append(f"⏳ غول بعدی: <b>{day} بین ساعت {window[0]} تا {window[1]}</b> (زمان دقیقش غافل‌گیریه)")
        last = data.get("last")
        if last:
            how = "از پا دراومد 🏆" if last["status"] == "dead" else "فرار کرد 💨"
            lines.append(f"📜 آخرین غول: {last['name']} — {how} ({last['fighters']} مبارز)")
        lines.append("<i>وقتی غول بیاد بهت خبر می‌دیم.</i>")
    else:
        pct = round(100 * boss.current_hp / max(1, boss.max_hp))
        lines += [
            f"<b>{boss.name}</b> — {constants.element_label(boss.element)}",
            f"❤️ [{constants.render_bar(boss.current_hp, boss.max_hp, width=10)}] <code>{pct}٪</code> "
            f"(<code>{boss.current_hp:,}</code>/<code>{boss.max_hp:,}</code>)",
            f"⏳ <b>{_fmt(data['seconds_left'])}</b> مونده · 👥 {data['fighters']} مبارز",
            "",
            f"🗡 ضربه‌های تو: <code>{data['hits_left']}/{worldboss.HITS_PER_PLAYER}</code> · آسیب تو: <code>{data['my_damage']:,}</code>",
            f"{get_emoji('energy')} انرژی: <code>{data['energy']}/{data['max_energy']}</code> (هر ضربه {worldboss.ENERGY_COST})",
        ]
        if data["advantage"]:
            lines.append("✅ برتری با تو: +۲۰٪ آسیب")
        if data["top"]:
            lines.append("")
            lines.append("🏅 <b>بیشترین آسیب:</b>")
            for r in data["top"]:
                lines.append(f"{r['rank']}. {r['name']} — <code>{r['damage']:,}</code>")
        if data["hits_left"] > 0 and data["has_creature"]:
            rows.append([btn("ضربه بزن", emoji_key="btn_attack", style=BATTLE, callback_data="wboss:hit")])
    rows.append([btn("بروزرسانی", emoji_key="btn_recheck", style=NAV, callback_data="wboss:home")])
    rows.append([back_btn("menu:hub_battle", "بازگشت به نبرد")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def worldboss_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = await run_db(_panel_sync, update.effective_user)
    text, keyboard = _render(data)
    from game.media import get_feature_image_path

    await send_screen(update, text, photo=get_feature_image_path("worldboss"), parse_mode="HTML", reply_markup=keyboard)


async def worldboss_home_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    data = await run_db(_panel_sync, update.effective_user)
    await query.answer()
    text, keyboard = _render(data)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


def _hit_sync(tg_user):
    from game.daily import check_missions, record_action

    user, _ = get_or_create_user(tg_user)
    res = worldboss.hit(user)
    record_action(user, "worldboss_hit")  # festival coins (and any future mission)
    check_missions(user, "worldboss_hit")
    note = (f"🗡 <b>{res['damage']:,}</b> آسیب زدی"
            + (" (با برتری عنصری)" if res["advantage"] else "")
            + f" · {get_emoji('coin')} <code>+{res['coins']:,}</code> · {get_emoji('dna')} <code>+{res['dna']:,}</code>")
    if res["killed"]:
        note += "\n🏆 <b>ضربه‌ی آخر مال تو بود! غول از پا دراومد.</b> جایزه‌ها تا چند دقیقه‌ی دیگه می‌رسه."
    return note, worldboss.panel_state(user)


async def worldboss_hit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        note, data = await run_db(_hit_sync, update.effective_user)
    except GameError as exc:
        from bot.handlers.energy import show_energy_error

        if not await show_energy_error(query, exc):
            await query.answer(alert_text(exc), show_alert=True)
        return
    await query.answer("🗡 خورد!")
    text, keyboard = _render(data, note)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


def register(application) -> None:
    application.add_handler(CommandHandler("boss", worldboss_panel, filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(worldboss_home_callback, pattern=r"^wboss:home$"))
    application.add_handler(CallbackQueryHandler(worldboss_hit_callback, pattern=r"^wboss:hit$"))

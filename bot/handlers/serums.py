"""«آزمایشگاه سرم» and «انبار» — the bot screens of game/serums.py and the backpack (prize boxes
waiting to be opened, speed-up cards, serums).

Callbacks: «sr:» for the lab (home, b:<kind> brew, u:<kind> use, x cancel, up upgrades,
ug:<kind> start an upgrade), «bp:home» for the backpack.
"""

from pathlib import Path

from django.conf import settings
from telegram import InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes

from bio_lab.repository import get_or_create_user
from bot.buttons import BUILD, CONFIRM, NAV, PRIMARY, SHOP, back_btn, btn
from bot.utils import run_db, safe_edit_message_text, send_screen
from game import serums as SR
from game.blackmarket import format_time_remaining as _left
from game.creature import GameError

_DIV = "──────────"
_ICON = {"rage": "🔥", "tracker": "🎯", "warrior": "⚔️", "shell": "🛡", "heal": "💖", "slayer": "🐉"}


def _photo(level: int) -> Path:
    base = Path(settings.BASE_DIR) / "assets" / "images" / "buildings"
    p = base / f"bld_serum_lab_lvl{max(1, min(5, level))}.jpg"
    return p if p.exists() else base / "bld_serum_lab.jpg"


# ── the lab ─────────────────────────────────────────────────────────────────────
def _lab_render(st: dict, note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    if not st["level"]:
        text = "\n".join([
            "🧪 <b>آزمایشگاه سرم</b>", _DIV,
            "🔒 هنوز ساخته نشده.",
            "از سطح ۲ تالار مِهر توی «ساختمان‌ها» می‌سازیش.", _DIV,
            "<i>سرم‌ها مثل اسپل‌ها هستن: می‌سازی، صبر می‌کنی و سر بزنگاه مصرف می‌کنی.</i>",
        ])
        return text, InlineKeyboardMarkup([[btn("ساختمان‌ها", emoji_key="btn_buildings", style=PRIMARY, callback_data="menu:buildings")]])
    lines = [f"🧪 <b>آزمایشگاه سرم · سطح {st['level']}</b>", _DIV,
             f"🧴 قفسه: <code>{st['stock_total']}/{st['capacity']}</code>",
             f"⏱ هر سرم: <code>{st['brew_minutes']}</code> دقیقه",
             f"🧬 هزینه‌ی هر سرم: <code>{st['brew_cost']:,}</code> DNA"]
    if st["queue"]:
        nxt = f" · بعدی تا {_left(st['next_in'])}" if st["next_in"] is not None else " · منتظر جای خالی"
        lines.append(f"⚗️ در حال ساخت: <code>{len(st['queue'])}</code>{nxt}")
    if st["upgrading"]:
        lines.append(f"⏫ ارتقای «{st['upgrading']['label']}»: {_left(st['upgrading']['left'])} مونده")
    armed = [k for k in st["kinds"] if k["armed"]]
    if armed:
        lines += [_DIV] + [f"{_ICON[k['key']]} فعال: <b>{k['label']}</b> · {k['where']}" for k in armed]
    lines.append(_DIV)
    for k in st["kinds"]:
        if not k["unlocked"]:
            lines.append(f"🔒 {k['label']} · از سطح {k['unlock']} آزمایشگاه")
            continue
        lines.append(f"{_ICON[k['key']]} <b>{k['label']}</b> · سطح {k['level']} · موجود <code>{k['stock']}</code>")
        lines.append(f"     {k['effect']}")
    if note:
        lines += [_DIV, note]
    rows = []
    for k in st["kinds"]:
        if k["unlocked"]:
            row = [btn(f"ساخت {k['label']}", emoji_key="btn_serum_brew", style=BUILD, callback_data=f"sr:b:{k['key']}")]
            if k["stock"]:
                row.append(btn(f"مصرف {k['label']}", emoji_key="btn_serum_use", style=CONFIRM, callback_data=f"sr:u:{k['key']}"))
            rows.append(row)
    tail = [btn("ارتقای سرم‌ها", emoji_key="btn_serum_up", style=PRIMARY, callback_data="sr:up"),
            btn("اعلان آماده‌شدن: " + ("روشن" if st.get("notify") else "خاموش"), emoji_key="btn_notify", style=NAV, callback_data="sr:n")]
    if len(st["queue"]) > (1 if st["next_in"] is not None else 0):
        tail.append(btn("لغو آخری", emoji_key="btn_cancel", style=NAV, callback_data="sr:x"))
    rows.append(tail)
    rows.append([btn("انبار", emoji_key="btn_backpack", style=SHOP, callback_data="bp:home"), back_btn("menu:buildings", "ساختمان‌ها")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def _up_render(st: dict, note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    lines = ["⏫ <b>ارتقای سرم‌ها</b>", _DIV,
             f"سطح هر سرم تا سطح آزمایشگاه (<code>{st['level']}</code>) بالا می‌ره؛ هر بار یکی."]
    if st["upgrading"]:
        lines.append(f"⏳ الان: «{st['upgrading']['label']}» · {_left(st['upgrading']['left'])} مونده")
    lines.append(_DIV)
    rows = []
    for k in st["kinds"]:
        if not k["unlocked"]:
            continue
        up = k["upgrade"]
        if not up:
            lines.append(f"{_ICON[k['key']]} <b>{k['label']}</b> · سطح ۵ (کامل)")
            continue
        lines.append(f"{_ICON[k['key']]} <b>{k['label']}</b> · سطح {k['level']} ← {k['level'] + 1}")
        lines.append(f"     {k['next_effect']}")
        if up["capped"]:
            lines.append(f"     🔒 آزمایشگاه باید سطح {k['level'] + 1} باشه")
        else:
            lines.append(f"     💰 {up['gold']:,} طلا · 🧬 {up['dna']:,} DNA · ⏱ {up['hours']} ساعت")
            if not st["upgrading"]:
                rows.append([btn(f"ارتقای {k['label']} به سطح {k['level'] + 1}", emoji_key="btn_serum_up", style=BUILD, callback_data=f"sr:ug:{k['key']}")])
    if note:
        lines += [_DIV, note]
    rows.append([back_btn("sr:home", "آزمایشگاه سرم")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def _lab_sync(tg_user, action: str, kind: str | None):
    user, _ = get_or_create_user(tg_user)
    note = ""
    try:
        if action == "b":
            r = SR.brew(user, kind)
            note = f"⚗️ «{SR.KINDS[kind]['label']}» رفت توی صف ساخت · <code>{r['cost']:,}</code> DNA"
        elif action == "u":
            r = SR.use(user, kind)
            note = f"✅ «{r['label']}» · {r['note']}"
        elif action == "x":
            r = SR.cancel_last(user)
            note = f"↩️ آخرین سرم صف لغو شد · <code>{r['refund']:,}</code> DNA برگشت"
        elif action == "n":
            on = not SR.state(user)["notify"]
            SR.set_notify(user, on)
            note = "🔔 وقتی سرم‌هات آماده شد خبرت می‌کنم (حداکثر روزی ۲ بار)" if on else "🔕 اعلان آماده‌شدن سرم خاموش شد"
        elif action == "ug":
            r = SR.upgrade(user, kind)
            note = f"⏫ ارتقای «{SR.KINDS[kind]['label']}» به سطح {r['to']} شروع شد · {r['hours']} ساعت"
    except GameError as e:
        note = f"⚠️ {e}"
    return SR.state(user), note


async def serum_lab(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    parts = (q.data if q and q.data.startswith("sr:") else "sr:home").split(":")
    action = parts[1] if len(parts) > 1 else "home"
    kind = parts[2] if len(parts) > 2 and parts[2] in SR.KINDS else None
    if q and q.data.startswith("sr:"):   # «menu:serums» was answered by the menu router
        await q.answer()
    st, note = await run_db(_lab_sync, update.effective_user, action if action in ("b", "u", "x", "ug", "n") else "", kind)
    if action in ("up", "ug"):
        text, kb = _up_render(st, note)
    else:
        text, kb = _lab_render(st, note)
    if q and action not in ("home",):
        await safe_edit_message_text(q, text, parse_mode="HTML", reply_markup=kb)
    else:
        await send_screen(update, text, photo=_photo(st["level"]), parse_mode="HTML", reply_markup=kb)


# ── the backpack ────────────────────────────────────────────────────────────────
def _bp_sync(tg_user):
    from bio_lab.models import SerumLab
    from game import reward_chests
    from game.buildings import list_speedup_cards

    user, _ = get_or_create_user(tg_user)
    row = SerumLab.objects.filter(user_id=user.id).first()
    return {
        "boss": reward_chests.unopened(user, "worldboss").count(),
        "cup": reward_chests.unopened(user, "tournament").count(),
        "war": reward_chests.unopened(user, "alliance_war").count(),
        "offer": reward_chests.unopened(user, "offer").count(),
        "cards": [(c.minutes, c.count) for c in list_speedup_cards(user)],
        "serums": [(k, SR.KINDS[k]["label"], int((row.stock or {}).get(k, 0))) for k in SR.ORDER if row and int((row.stock or {}).get(k, 0)) > 0],
    }


def _bp_render(v: dict) -> tuple[str, InlineKeyboardMarkup]:
    lines = ["🎒 <b>انبار</b>", _DIV, "<i>جعبه‌های جایزه‌ی باز نشده، کارت‌های سرعت و سرم‌هات.</i>", _DIV]
    rows = []
    if not (v["boss"] or v["cup"] or v["war"] or v["offer"]):
        lines.append("📦 جعبه‌ی جایزه‌ی باز نشده: <i>نداری</i>")
    if v["boss"]:
        lines.append(f"🐉 جعبه‌های غول: <code>{v['boss']}</code>")
        rows.append([btn("باز کردن جعبه‌های غول", emoji_key="btn_chest_open", style=CONFIRM, callback_data="wboss:open:all")])
    if v["cup"]:
        lines.append(f"🏟 جعبه‌های جام: <code>{v['cup']}</code>")
        rows.append([btn("باز کردن جعبه‌های جام", emoji_key="btn_chest_open", style=CONFIRM, callback_data="tour:open")])
    if v["war"]:
        lines.append(f"⚔️ جعبه‌های جنگ اتحاد: <code>{v['war']}</code>")
        rows.append([btn("باز کردن جعبه‌های جنگ", emoji_key="btn_chest_open", style=CONFIRM, callback_data="ally_war_open")])
    if v["offer"]:
        lines.append(f"🎁 جعبه‌های بسته‌ی خریدنی: <code>{v['offer']}</code>")
        rows.append([btn("باز کردن جعبه‌های بسته", emoji_key="btn_chest_open", style=CONFIRM, callback_data="rc:open:offer")])
    lines.append("⏱ کارت سرعت: " + ("، ".join(f"{m} دقیقه ×{n}" for m, n in v["cards"]) if v["cards"] else "<i>نداری</i>"))
    lines.append("🧪 سرم‌ها: " + ("، ".join(f"{label} ×{n}" for _k, label, n in v["serums"]) if v["serums"] else "<i>نداری</i>"))
    rows.append([btn("آزمایشگاه سرم", emoji_key="btn_serums", style=PRIMARY, callback_data="sr:home"),
                 btn("جعبه‌های آرنا", emoji_key="btn_chests", style=PRIMARY, callback_data="arena_chests")])
    rows.append([back_btn("menu:buildings", "ساختمان‌ها")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def backpack(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if q and q.data.startswith("bp:"):
        await q.answer()
    v = await run_db(_bp_sync, update.effective_user)
    text, kb = _bp_render(v)
    await send_screen(update, text, parse_mode="HTML", reply_markup=kb)


def _open_sync(tg_user, source):
    from game import reward_chests
    from game.worldboss import _chest_line

    user, _ = get_or_create_user(tg_user)
    return [_chest_line(r) for r in reward_chests.open_all(user, source)]


async def open_chests(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """«باز کردن جعبه‌ها» for boxes that have no screen of their own (rc:open:<source>) —
    today the boxes inside a bought pack."""
    q = update.callback_query
    try:
        lines = await run_db(_open_sync, update.effective_user, q.data.split(":")[2])
    except GameError as e:
        await q.answer(str(e), show_alert=True)
        return
    await q.answer("🎁 باز شد!")
    await send_screen(update, "🎁 <b>جعبه‌ها باز شد!</b>\n" + _DIV + "\n" + "\n".join(lines), parse_mode="HTML",
                      reply_markup=InlineKeyboardMarkup([[btn("انبار", emoji_key="btn_backpack", style=PRIMARY, callback_data="bp:home")]]))


def register(application) -> None:
    application.add_handler(CallbackQueryHandler(serum_lab, pattern=r"^sr:(home|up|x|n|[bu]:[a-z]+|ug:[a-z]+)$"))
    application.add_handler(CallbackQueryHandler(backpack, pattern=r"^bp:home$"))
    application.add_handler(CallbackQueryHandler(open_chests, pattern=r"^rc:open:offer$"))

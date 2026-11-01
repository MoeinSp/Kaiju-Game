"""«آزمایشگاه سرم» and «انبار» — the bot screens of game/serums.py and the backpack (prize boxes
waiting to be opened, speed-up cards, serums).

Callbacks: «sr:» for the lab (home, b:<kind> brew, u:<kind> use, c:<kind> put a chosen serum
back, n notify, up upgrades, ug:<kind> start an upgrade, d the «حذف سرم» screen, dq:<i>:<kind>
cancel a queued serum, ds:<kind> / dy:<kind> ask / throw a shelf serum away, f / fy:<price> ask /
finish the brewing with diamonds), «bp:home» for the backpack.
"""

from pathlib import Path

from django.conf import settings
from telegram import InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes

from bio_lab.repository import get_or_create_user
from bot.buttons import BUILD, CONFIRM, DANGER, NAV, PRIMARY, SHOP, back_btn, btn
from bot.utils import run_db, safe_edit_message_text, send_screen
from game import serums as SR
from game.blackmarket import format_time_remaining as _left
from game.creature import GameError
from game.emoji import get_emoji

_DIV = "──────────"


class _Icons:
    """«سرم خشم» … icons: the panel keys serum_<kind> («🆕 بخش‌های جدید»), read at render time."""

    def __getitem__(self, kind: str) -> str:
        return get_emoji(f"serum_{kind}")


_ICON = _Icons()


def _photo(level: int) -> Path:
    base = Path(settings.BASE_DIR) / "assets" / "images" / "buildings"
    p = base / f"bld_serum_lab_lvl{max(1, min(5, level))}.jpg"
    return p if p.exists() else base / "bld_serum_lab.jpg"


# ── the «سر بزنگاه» toggle on an action screen (arena attack, hunt card, group «اتک», boss) ──
def moment_lines(m: dict | None) -> list[str]:
    """The serum part of an action card (empty when there is nothing to offer)."""
    if not m:
        return []
    icon = _ICON[m["kind"]]
    if m["on"]:
        return [f"{icon} <b>سرم {m['label']} انتخاب شد</b> (سطح {m['level']})",
                f"<i>{m['effect']} · اگه نخوای، با دکمه‌ی سرم برش گردون توی قفسه.</i>"]
    return [f"{icon} سرم {m['label']} داری: <code>{m['stock']}</code> عدد (سطح {m['level']})",
            f"<i>{m['effect']} · فقط اگه خودت انتخابش کنی خرج می‌شه.</i>"]


def moment_btn(m: dict | None, callback_data: str):
    """The toggle button for that serum, or None."""
    if not m:
        return None
    if m["on"]:
        return btn(f"سرم {m['label']} انتخاب شده · برداشتن", emoji_key="btn_serum_use", style=NAV, callback_data=callback_data)
    return btn(f"استفاده از سرم {m['label']} · {m['stock']} عدد", emoji_key="btn_serum_use", style=CONFIRM, callback_data=callback_data)


def moment_toast(m: dict | None, kind: str) -> str:
    if m and m["on"]:
        return f"سرم {m['label']} برای این کار انتخاب شد."
    return f"سرم {SR.KINDS[kind]['label']} برگشت توی قفسه."


# ── the lab ─────────────────────────────────────────────────────────────────────
def _queue_lines(st: dict) -> list[str]:
    if not st["queue"]:
        return ["⚗️ صف ساخت: <i>خالی</i>"]
    labels = {k["key"]: k["label"] for k in st["kinds"]}
    out = [f"⚗️ صف ساخت <code>{len(st['queue'])}</code>: " + " ← ".join(labels[q] for q in st["queue"])]
    if st["next_in"] is None:
        out.append("      ⏸ <i>قفسه پره؛ با مصرف یا حذف یه سرم ادامه پیدا می‌کنه.</i>")
    else:
        all_in = st["next_in"] + (len(st["queue"]) - 1) * st["brew_minutes"] * 60
        tail = f" · همه تا {_left(all_in)}" if len(st["queue"]) > 1 else ""
        out.append(f"      ⏳ بعدی تا {_left(st['next_in'])}{tail}")
    return out


def _lab_render(st: dict, note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    if not st["level"]:
        text = "\n".join([
            f"{get_emoji('serum_lab')} <b>آزمایشگاه سرم</b>", _DIV,
            "🔒 هنوز ساخته نشده.",
            "از سطح ۲ تالار مِهر توی «ساختمان‌ها» می‌سازیش.", _DIV,
            "<i>سرم‌ها مثل اسپل‌ها هستن: می‌سازی، صبر می‌کنی و سر بزنگاه مصرف می‌کنی.</i>",
        ])
        return text, InlineKeyboardMarkup([[btn("ساختمان‌ها", emoji_key="btn_buildings", style=PRIMARY, callback_data="menu:buildings")]])
    kinds = st["kinds"]
    lines = [f"{get_emoji('serum_lab')} <b>آزمایشگاه سرم</b> · سطح {st['level']}", _DIV]
    if note:   # the result of the last tap goes on top, where it is seen
        lines += [note, _DIV]
    lines.append(f"🧴 قفسه <code>{st['stock_total']}/{st['capacity']}</code> · ⏱ هر سرم <code>{st['brew_minutes']}</code> دقیقه")
    lines.append(f"🧬 هزینه‌ی ساخت: <code>{st['brew_cost']:,}</code> DNA برای هر سرم")
    shelf = [f"{_ICON[k['key']]} {k['label']} ×{k['stock']}" for k in kinds if k["stock"]]
    lines.append("📦 روی قفسه: " + ("  ·  ".join(shelf) if shelf else "<i>خالی</i>"))
    lines += _queue_lines(st)
    if st["upgrading"]:
        lines.append(f"⏫ ارتقای «{st['upgrading']['label']}»: {_left(st['upgrading']['left'])} مونده")
    for k in kinds:
        if not k["armed"]:
            continue
        if k["key"] == "shell" and st.get("shell_left"):
            lines.append(f"✅ فعال: {_ICON['shell']} <b>{k['label']}</b> · {_left(st['shell_left'])} برای دفاع بعدی")
        else:
            lines.append(f"📌 انتخاب شده: {_ICON[k['key']]} <b>{k['label']}</b> · برای {k['where']}")
    lines += [_DIV, "<b>سرم‌ها</b>"]
    for k in kinds:
        if k["unlocked"]:
            lines.append(f"{_ICON[k['key']]} <b>{k['label']}</b> · سطح {k['level']}")
            lines.append(f"      <i>{k['effect']}</i>")
    locked = [f"{k['label']} (سطح {k['unlock']})" for k in kinds if not k["unlocked"]]
    if locked:
        lines.append("🔒 قفل: " + "، ".join(locked))
    lines += [_DIV, "<i>خشم، ردیاب، جنگاور و غول‌کش خودبه‌خود خرج نمی‌شن؛ همون لحظه توی صفحه‌ی حمله، شکار یا غول انتخابشون می‌کنی.</i>"]

    brew = [btn(f"ساخت {k['label']}", emoji_key="btn_serum_brew", style=BUILD, callback_data=f"sr:b:{k['key']}")
            for k in kinds if k["unlocked"]]
    acts = []
    for k in kinds:
        if k["moment"] and k["armed"]:
            acts.append(btn(f"برداشتن {k['label']}", emoji_key="btn_cancel", style=NAV, callback_data=f"sr:c:{k['key']}"))
        elif k["unlocked"] and k["stock"] and not k["moment"]:
            acts.append(btn(f"مصرف {k['label']}", emoji_key="btn_serum_use", style=CONFIRM, callback_data=f"sr:u:{k['key']}"))
    rows = [brew[i:i + 2] for i in range(0, len(brew), 2)] + [acts[i:i + 2] for i in range(0, len(acts), 2)]
    if st.get("finish"):
        rows.append([btn(f"تموم کردن ساخت با الماس · {st['finish']['cost']} الماس", emoji_key="btn_instant", style=SHOP, callback_data="sr:f")])
    manage = [btn("ارتقای سرم‌ها", emoji_key="btn_serum_up", style=PRIMARY, callback_data="sr:up")]
    if st["queue"] or st["stock_total"]:
        manage.append(btn("حذف سرم", emoji_key="btn_delete", style=NAV, callback_data="sr:d"))
    rows.append(manage)
    rows.append([btn("اعلان آماده‌شدن: " + ("روشن" if st.get("notify") else "خاموش"), emoji_key="btn_notify", style=NAV, callback_data="sr:n")])
    rows.append([btn("انبار", emoji_key="btn_backpack", style=SHOP, callback_data="bp:home"), back_btn("menu:buildings", "ساختمان‌ها")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def _del_render(st: dict, note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    """«حذف سرم»: cancel any queued serum (its DNA back) or throw a shelf serum away (no refund)."""
    labels = {k["key"]: k["label"] for k in st["kinds"]}
    lines = [f"{get_emoji('delete')} <b>حذف سرم</b>", _DIV]
    if note:
        lines += [note, _DIV]
    rows = []
    if st["queue"]:
        lines.append("⚗️ <b>صف ساخت</b> · <i>لغو هر کدوم، DNA‌ش کامل برمی‌گرده</i>")
        for i, q in enumerate(st["queue"]):
            brewing = " · در حال ساخت" if i == 0 and st["next_in"] is not None else ""
            lines.append(f"   {i + 1}. {_ICON[q]} {labels[q]}{brewing}")
            rows.append([btn(f"لغو {i + 1} · {labels[q]}", emoji_key="btn_cancel", style=NAV, callback_data=f"sr:dq:{i}:{q}")])
    shelf = [k for k in st["kinds"] if k["stock"]]
    if shelf:
        if st["queue"]:
            lines.append("")
        lines.append("📦 <b>روی قفسه</b> · <i>دور ریختن جا باز می‌کنه ولی DNA برنمی‌گرده</i>")
        for k in shelf:
            lines.append(f"   {_ICON[k['key']]} {k['label']} ×{k['stock']}")
            rows.append([btn(f"دور ریختن یه {k['label']}", emoji_key="btn_delete", style=DANGER, callback_data=f"sr:ds:{k['key']}")])
    if not st["queue"] and not shelf:
        lines.append("<i>نه سرمی توی صف داری نه روی قفسه.</i>")
    rows.append([back_btn("sr:home", "آزمایشگاه سرم")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def _ask_render(title: str, body: list[str], yes_label: str, yes_cb: str, no_cb: str) -> tuple[str, InlineKeyboardMarkup]:
    return "\n".join([title, _DIV, *body]), InlineKeyboardMarkup([[
        btn(yes_label, emoji_key="btn_confirm", style=CONFIRM, callback_data=yes_cb),
        btn("نه", emoji_key="btn_cancel", style=NAV, callback_data=no_cb)]])


def _discard_ask(st: dict, kind: str) -> tuple[str, InlineKeyboardMarkup]:
    label = SR.KINDS[kind]["label"]
    have = next((k["stock"] for k in st["kinds"] if k["key"] == kind), 0)
    if not have:
        return _del_render(st, f"⚠️ سرم «{label}» روی قفسه نداری.")
    return _ask_render(f"🗑 <b>دور ریختن سرم {label}</b>",
                       [f"یه {_ICON[kind]} «{label}» از قفسه دور ریخته بشه؟ (داری: <code>{have}</code>)",
                        "<i>DNA‌ی ساختش برنمی‌گرده؛ فقط یه جا روی قفسه باز می‌شه.</i>"],
                       "بله، دور بریز", f"sr:dy:{kind}", "sr:d")


def _finish_ask(st: dict) -> tuple[str, InlineKeyboardMarkup]:
    f = st.get("finish")
    if not f:
        return _lab_render(st, "⚠️ چیزی نیست که با الماس تموم بشه (صف خالیه یا قفسه پره).")
    body = [f"⚗️ <code>{f['count']}</code> سرم همین الان آماده می‌شه، به‌جای {_left(f['left'])} صبر.",
            f"{get_emoji('diamond')} هزینه: <code>{f['cost']}</code> الماس · داری: <code>{st.get('diamonds', 0):,}</code>"]
    if f["count"] < len(st["queue"]):
        body.append("<i>بقیه‌ی صف جا روی قفسه نداره و منتظر می‌مونه.</i>")
    return _ask_render("⚡ <b>تموم کردن ساخت با الماس</b>", body, f"بله، {f['cost']} الماس", f"sr:fy:{f['cost']}", "sr:home")


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


def _lab_sync(tg_user, action: str, kind: str | None, arg: int | None = None):
    user, _ = get_or_create_user(tg_user)
    note = ""
    try:
        if action == "b":
            r = SR.brew(user, kind)
            note = f"⚗️ «{SR.KINDS[kind]['label']}» رفت توی صف ساخت · <code>{r['cost']:,}</code> DNA"
        elif action == "u":
            r = SR.use(user, kind)
            note = f"✅ «{r['label']}» · {r['note']}"
        elif action == "c":
            SR.unarm(user, kind)
            note = f"↩️ «{SR.KINDS[kind]['label']}» برگشت توی قفسه."
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
        elif action == "dq":
            r = SR.remove_queued(user, arg, kind)
            note = f"↩️ «{r['label']}» از صف ساخت حذف شد · <code>{r['refund']:,}</code> DNA برگشت"
        elif action == "dy":
            r = SR.discard(user, kind)
            note = f"🗑 یه «{r['label']}» دور ریخته شد."
        elif action == "fy":
            r = SR.finish_with_diamonds(user, max_cost=arg)
            note = f"⚡ <code>{r['count']}</code> سرم آماده شد و رفت روی قفسه · <code>{r['cost']}</code> الماس"
    except GameError as e:
        note = f"⚠️ {e}"
    user.refresh_from_db(fields=["diamonds"])
    st = SR.state(user)
    st["diamonds"] = user.diamonds
    return st, note


async def serum_lab(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    parts = (q.data if q and q.data.startswith("sr:") else "sr:home").split(":")
    action = parts[1] if len(parts) > 1 else "home"
    arg = None
    if action == "dq":       # sr:dq:<index>:<kind>
        arg, parts = int(parts[2]), [*parts[:2], parts[3]]
    elif action == "fy":     # sr:fy:<price seen>
        arg = int(parts[2])
    kind = parts[2] if len(parts) > 2 and parts[2] in SR.KINDS else None
    if q and q.data.startswith("sr:"):   # «menu:serums» was answered by the menu router
        await q.answer()
    act = action if action in ("b", "u", "c", "x", "ug", "n", "dq", "dy", "fy") else ""
    st, note = await run_db(_lab_sync, update.effective_user, act, kind, arg)
    if action in ("up", "ug"):
        text, kb = _up_render(st, note)
    elif action in ("d", "dq", "dy"):
        text, kb = _del_render(st, note)
    elif action == "ds" and kind:
        text, kb = _discard_ask(st, kind)
    elif action == "f":
        text, kb = _finish_ask(st)
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
    lines = [f"{get_emoji('backpack')} <b>انبار</b>", _DIV, "<i>جعبه‌های جایزه‌ی باز نشده، کارت‌های سرعت و سرم‌هات.</i>", _DIV]
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
    lines.append(f"{get_emoji('serum_lab')} سرم‌ها: " + ("، ".join(f"{label} ×{n}" for _k, label, n in v["serums"]) if v["serums"] else "<i>نداری</i>"))
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
    from bot.loot_text import chests_text
    from game import reward_chests

    user, _ = get_or_create_user(tg_user)
    return chests_text(reward_chests.open_all(user, source))


async def open_chests(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """«باز کردن جعبه‌ها» for boxes that have no screen of their own (rc:open:<source>) —
    today the boxes inside a bought pack."""
    q = update.callback_query
    try:
        text = await run_db(_open_sync, update.effective_user, q.data.split(":")[2])
    except GameError as e:
        await q.answer(str(e), show_alert=True)
        return
    await q.answer("🎁 باز شد!")
    await send_screen(update, text, parse_mode="HTML",
                      reply_markup=InlineKeyboardMarkup([[btn("انبار", emoji_key="btn_backpack", style=PRIMARY, callback_data="bp:home")]]))


def register(application) -> None:
    application.add_handler(CallbackQueryHandler(serum_lab, pattern=r"^sr:(home|up|x|n|d|f|[buc]:[a-z]+|ug:[a-z]+|d[sy]:[a-z]+|dq:\d+:[a-z]+|fy:\d+)$"))
    application.add_handler(CallbackQueryHandler(backpack, pattern=r"^bp:home$"))
    application.add_handler(CallbackQueryHandler(open_chests, pattern=r"^rc:open:offer$"))

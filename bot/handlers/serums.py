"""«آزمایشگاه سرم» and «انبار» — the bot screens of game/serums.py and the backpack (prize boxes
waiting to be opened, speed-up cards, serums).

Callbacks: «sr:» for the lab (home, b:<kind> brew, u:<kind> use, c:<kind> put a chosen serum
back, n notify, up upgrades, ug:<kind> start an upgrade, d the «حذف سرم» screen, dk:<q|s>:<kind>:<n>
the how-many picker for a queued / shelf kind, dx:<q|s>:<kind>:<n> remove n of them, da / dax ask /
remove everything, f / fy:<price> ask / finish all the brewing with diamonds; dq / ds / dy are the
older one-by-one buttons, still answered for messages sent before), «bp:home» for the backpack.
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
def _counts(kinds_in_order: list[str]) -> list[tuple[str, int]]:
    """[(kind, how many)] in first-seen order."""
    out: dict[str, int] = {}
    for k in kinds_in_order:
        out[k] = out.get(k, 0) + 1
    return list(out.items())


def _queue_lines(st: dict) -> list[str]:
    if not st["queue"]:
        return ["⚗️ در حال ساخت: <i>هیچی</i>"]
    labels = {k["key"]: k["label"] for k in st["kinds"]}
    parts = [f"{_ICON[k]} {labels[k]}" + (f" ×{n}" if n > 1 else "") for k, n in _counts(st["queue"])]
    out = [f"⚗️ در حال ساخت <code>{len(st['queue'])}</code> (همه با هم): " + "  ·  ".join(parts)]
    if st["all_in"] is not None and st["all_in"] > 0:
        first = f"اولی تا {_left(st['next_in'])} · " if len(st["queue"]) > 1 and st["next_in"] != st["all_in"] else ""
        out.append(f"      ⏳ {first}همه تا {_left(st['all_in'])}")
    elif st["queue"]:
        out.append("      ⏸ <i>آماده‌ست ولی قفسه جا نداره؛ یه سرم مصرف کن یا دور بریز.</i>")
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
        f = st["finish"]
        rows.append([btn(f"تموم کردن همه با الماس · {f['count']} سرم · {f['cost']} الماس", emoji_key="btn_instant", style=SHOP, callback_data="sr:f")])
    manage = [btn("ارتقای سرم‌ها", emoji_key="btn_serum_up", style=PRIMARY, callback_data="sr:up")]
    if st["queue"] or st["stock_total"]:
        manage.append(btn("حذف سرم", emoji_key="btn_delete", style=NAV, callback_data="sr:d"))
    rows.append(manage)
    if not st.get("group"):
        rows.append([btn("اعلان آماده‌شدن: " + ("روشن" if st.get("notify") else "خاموش"), emoji_key="btn_notify", style=NAV, callback_data="sr:n")])
        rows.append([btn("انبار", emoji_key="btn_backpack", style=SHOP, callback_data="bp:home"), back_btn("menu:buildings", "ساختمان‌ها")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def _mini_render(st: dict, note: str = "", just: tuple[str, int] | None = None) -> tuple[str, InlineKeyboardMarkup]:
    """The small group answer to «سرم غول ۲»: what happened, one status line, and only «لغو» and
    «تموم کردن با الماس». `just` = (kind, n) brewed by this word — «لغو» takes back only those."""
    lines = [note] if note else []
    labels = {k["key"]: k["label"] for k in st["kinds"]}
    if st["queue"]:
        parts = [f"{labels[k]}" + (f" ×{n}" if n > 1 else "") for k, n in _counts(st["queue"])]
        when = f" · آماده تا {_left(st['all_in'])}" if st["all_in"] else ""
        lines.append(f"⏳ در حال ساخت: {'، '.join(parts)}{when}")
    lines.append(f"🧴 قفسه <code>{st['stock_total']}/{st['capacity']}</code>")
    row = []
    if st["queue"]:
        if just and st["queue"].count(just[0]) >= 1:
            n = min(just[1], st["queue"].count(just[0]))
            row.append(btn("لغو", emoji_key="btn_cancel", style=NAV, callback_data=f"sr:xk:{just[0]}:{n}"))
        else:
            row.append(btn("لغو همه", emoji_key="btn_cancel", style=NAV, callback_data="sr:xq"))
    if st.get("finish"):
        row.append(btn(f"تموم کردن · {st['finish']['cost']} الماس", emoji_key="btn_instant", style=SHOP, callback_data="sr:f"))
    return "\n".join(lines), InlineKeyboardMarkup([row] if row else [])


def _del_render(st: dict, note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    """«حذف سرم»: pick a kind in the queue (cancel, DNA back) or on the shelf (thrown away, no
    refund) — the next screen asks how many — or «حذف همه»."""
    labels = {k["key"]: k["label"] for k in st["kinds"]}
    lines = [f"{get_emoji('delete')} <b>حذف سرم</b>", _DIV]
    if note:
        lines += [note, _DIV]
    rows = []
    queued = _counts(st["queue"])
    if queued:
        lines.append("⚗️ <b>در حال ساخت</b> · <i>لغو کنی DNA‌ش کامل برمی‌گرده</i>")
        for k, n in queued:
            lines.append(f"   {_ICON[k]} {labels[k]} ×{n}")
            rows.append([btn(f"لغو {labels[k]} (×{n})", emoji_key="btn_cancel", style=NAV, callback_data=f"sr:dk:q:{k}:1")])
    shelf = [k for k in st["kinds"] if k["stock"]]
    if shelf:
        if queued:
            lines.append("")
        lines.append("📦 <b>روی قفسه</b> · <i>دور ریختن جا باز می‌کنه ولی DNA برنمی‌گرده</i>")
        for k in shelf:
            lines.append(f"   {_ICON[k['key']]} {k['label']} ×{k['stock']}")
            rows.append([btn(f"دور ریختن {k['label']} (×{k['stock']})", emoji_key="btn_delete", style=DANGER, callback_data=f"sr:dk:s:{k['key']}:1")])
    if not queued and not shelf:
        lines.append("<i>نه سرمی در حال ساخت داری نه روی قفسه.</i>")
    elif len(st["queue"]) + st["stock_total"] > 1:
        rows.append([btn("حذف همه", emoji_key="btn_delete", style=DANGER, callback_data="sr:da")])
    rows.append([back_btn("sr:home", "آزمایشگاه سرم")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def _have(st: dict, where: str, kind: str) -> int:
    if where == "q":
        return st["queue"].count(kind)
    return next((k["stock"] for k in st["kinds"] if k["key"] == kind), 0)


def _pick_render(st: dict, where: str, kind: str, n: int) -> tuple[str, InlineKeyboardMarkup]:
    """How many to remove: «−  n  +», «حذف n تا» and «حذف همه»."""
    label = SR.KINDS[kind]["label"]
    have = _have(st, where, kind)
    if not have:
        return _del_render(st, f"⚠️ «{label}» دیگه {'توی صف' if where == 'q' else 'روی قفسه'} نیست.")
    n = max(1, min(n, have))
    verb = "لغو" if where == "q" else "دور ریختن"
    lines = [f"{get_emoji('delete')} <b>{verb} {_ICON[kind]} {label}</b>", _DIV,
             f"{'در حال ساخت' if where == 'q' else 'روی قفسه'}: <code>{have}</code> عدد",
             f"انتخاب شده: <b>{n}</b> عدد",
             "<i>DNA‌ی سرم‌های لغو شده کامل برمی‌گرده.</i>" if where == "q"
             else "<i>دور ریختن جا باز می‌کنه ولی DNA برنمی‌گرده.</i>"]
    rows = []
    if have > 1:
        rows.append([
            btn("➖", style=NAV, callback_data=f"sr:dk:{where}:{kind}:{max(1, n - 1)}"),
            btn(f"{n} از {have}", style=NAV, callback_data=f"sr:dk:{where}:{kind}:{n}"),
            btn("➕", style=NAV, callback_data=f"sr:dk:{where}:{kind}:{min(have, n + 1)}"),
        ])
    style = NAV if where == "q" else DANGER
    act = [btn(f"{verb} {n} تا" if have > 1 else verb, emoji_key="btn_confirm", style=style, callback_data=f"sr:dx:{where}:{kind}:{n}")]
    if have > 1 and n < have:
        act.append(btn(f"همه ({have})", emoji_key="btn_delete", style=style, callback_data=f"sr:dx:{where}:{kind}:{have}"))
    rows.append(act)
    rows.append([back_btn("sr:d", "حذف سرم")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def _all_ask(st: dict) -> tuple[str, InlineKeyboardMarkup]:
    if not st["queue"] and not st["stock_total"]:
        return _del_render(st)
    body = []
    if st["queue"]:
        body.append(f"⚗️ <code>{len(st['queue'])}</code> سرم در حال ساخت لغو می‌شه و DNA‌ش برمی‌گرده.")
    if st["stock_total"]:
        body.append(f"📦 <code>{st['stock_total']}</code> سرم روی قفسه دور ریخته می‌شه (بدون برگشت DNA).")
    body.append("<i>سرمی که برای کاری انتخاب کردی (📌) سر جاش می‌مونه.</i>")
    return _ask_render("🗑 <b>حذف همه‌ی سرم‌ها</b>", body, "بله، همه رو حذف کن", "sr:dax", "sr:d")


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
        msg = "⚠️ چیزی نیست که با الماس تموم بشه (صف خالیه یا قفسه پره)."
        return _mini_render(st, msg) if st.get("group") else _lab_render(st, msg)
    body = [f"⚗️ هر <code>{f['count']}</code> سرم با هم همین الان آماده می‌شن، به‌جای {_left(f['left'])} صبر.",
            f"{get_emoji('diamond')} هزینه: <code>{f['cost']}</code> الماس (هر سرم حداکثر {f['per_serum']}، کمتر وقتی ساختش جلو رفته)"
            f" · داری: <code>{st.get('diamonds', 0):,}</code>"]
    if f["count"] < len(st["queue"]):
        body.append("<i>بقیه‌ی صف جا روی قفسه نداره و منتظر می‌مونه.</i>")
    return _ask_render("⚡ <b>تموم کردن همه با الماس</b>", body, f"بله، {f['cost']} الماس", f"sr:fy:{f['cost']}",
                       "sr:mini" if st.get("group") else "sr:home")


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
    brewed = 0
    try:
        if action == "b":
            r = SR.brew(user, kind, arg or 1)
            brewed = r["queued"]
            many = f"<code>{r['queued']}</code> تا " if r["queued"] > 1 else ""
            note = f"⚗️ {many}«{SR.KINDS[kind]['label']}» رفت توی ساخت · <code>{r['cost']:,}</code> DNA"
        elif action == "xk":   # arg = n — take back the serums a group word just queued
            r = SR.remove_kind(user, kind, arg)
            note = f"↩️ <code>{r['count']}</code> «{r['label']}» لغو شد · <code>{r['refund']:,}</code> DNA برگشت"
        elif action == "xq":   # the whole queue, DNA back
            refund = n_all = 0
            for k in dict.fromkeys(SR.state(user)["queue"]):
                r = SR.remove_kind(user, k, 50)
                refund += r["refund"]; n_all += r["count"]
            note = f"↩️ <code>{n_all}</code> سرم لغو شد · <code>{refund:,}</code> DNA برگشت" if n_all else "صفی برای لغو نبود."
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
        elif action == "dx":   # arg = (where, n)
            where, n = arg
            if where == "q":
                r = SR.remove_kind(user, kind, n)
                note = f"↩️ <code>{r['count']}</code> «{r['label']}» از صف ساخت لغو شد · <code>{r['refund']:,}</code> DNA برگشت"
            else:
                r = SR.discard(user, kind, n)
                note = f"🗑 <code>{r['count']}</code> «{r['label']}» دور ریخته شد."
        elif action == "dax":
            r = SR.clear_all(user)
            bits = []
            if r["queued"]:
                bits.append(f"{r['queued']} سرم از صف لغو شد · <code>{r['refund']:,}</code> DNA برگشت")
            if r["shelf"]:
                bits.append(f"{r['shelf']} سرم از قفسه دور ریخته شد")
            note = "🗑 " + " · ".join(bits)
        elif action == "fy":
            r = SR.finish_with_diamonds(user, max_cost=arg)
            note = f"⚡ <code>{r['count']}</code> سرم آماده شد و رفت روی قفسه · <code>{r['cost']}</code> الماس"
    except GameError as e:
        note = f"⚠️ {e}"
    user.refresh_from_db(fields=["diamonds"])
    st = SR.state(user)
    st["diamonds"] = user.diamonds
    st["brewed"] = brewed
    return st, note


def _in_group(update: Update) -> bool:
    chat = update.effective_chat
    return chat is not None and chat.type in ("group", "supergroup")


async def serum_lab(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if q is not None and _in_group(update):
        from bot.gates import is_card_owner

        if not is_card_owner(update):   # a group panel belongs to whoever typed «سرم»
            await q.answer("این پنل مال یه بازیکن دیگه‌ست — خودت «سرم» رو بفرست.", show_alert=True)
            return
    parts = (q.data if q and q.data.startswith("sr:") else "sr:home").split(":")
    action = parts[1] if len(parts) > 1 else "home"
    arg = None
    if action == "dq":       # sr:dq:<index>:<kind>
        arg, parts = int(parts[2]), [*parts[:2], parts[3]]
    elif action == "fy":     # sr:fy:<price seen>
        arg = int(parts[2])
    elif action in ("dk", "dx"):   # sr:dk:<q|s>:<kind>:<n>
        arg, parts = (parts[2], int(parts[4])), [*parts[:2], parts[3]]
    elif action == "xk":           # sr:xk:<kind>:<n>
        arg = int(parts[3])
    kind = parts[2] if len(parts) > 2 and parts[2] in SR.KINDS else None
    if q and q.data.startswith("sr:"):   # «menu:serums» was answered by the menu router
        await q.answer()
    act = action if action in ("b", "u", "c", "x", "ug", "n", "dq", "dy", "dx", "dax", "fy", "xk", "xq") else ""
    st, note = await run_db(_lab_sync, update.effective_user, act, kind, arg)
    st["group"] = _in_group(update)
    if action in ("mini", "xk", "xq") or (st["group"] and action == "fy"):
        text, kb = _mini_render(st, note)
    elif action in ("up", "ug"):
        text, kb = _up_render(st, note)
    elif action in ("d", "dq", "dy", "dx", "dax"):
        text, kb = _del_render(st, note)
    elif action == "dk" and kind:
        text, kb = _pick_render(st, arg[0], kind, arg[1])
    elif action == "da":
        text, kb = _all_ask(st)
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


# ── group words: «سرم», «ساخت سرم غول ۲», «تکمیل سرم» ──────────────────────────
# names a player may type for each serum (compared without spaces / half-spaces)
_NAMES = {
    "rage": ("خشم", "سرمخشم"), "tracker": ("ردیاب", "ردیابی"), "warrior": ("جنگاور", "جنگجو"),
    "shell": ("پوسته", "سپر", "دفاع"), "heal": ("ترمیم", "درمان", "جون"), "slayer": ("غولکش", "غول", "باس"),
}
_KIND_OF = {name: kind for kind, names in _NAMES.items() for name in names}
_OPEN = {"سرم", "سرمها", "سرم ها", "آزمایشگاه سرم", "ازمایشگاه سرم", "ساخت سرم", "پنل سرم", "آزمایشگاهسرم"}
_FINISH = {"تکمیل سرم", "تموم کردن سرم", "تمام کردن سرم", "سرم الماس", "تکمیل سرمها", "تکمیل سرم ها"}
_DELETE = {"حذف سرم", "حذف سرمها", "حذف سرم ها", "پاک کردن سرم"}


def parse_word(norm: str) -> tuple[str, str | None, int] | None:
    """A group message → (action, kind, count): «سرم» opens the lab, «ساخت سرم غول ۳» / «سرم
    خشم» brew, «تکمیل سرم» finishes with diamonds, «حذف سرم» opens the delete screen. `norm` is
    keywords.normalize()d (ASCII digits, half-spaces as spaces). None = not a serum word."""
    if norm in _OPEN:
        return "home", None, 0
    if norm in _FINISH:
        return "f", None, 0
    if norm in _DELETE:
        return "d", None, 0
    for head in ("ساخت سرم ", "بساز سرم ", "سرم "):
        if norm.startswith(head):
            rest = norm[len(head):].split()
            count = 1
            if rest and rest[-1].isdigit():
                count = max(1, min(int(rest.pop()), 8))
            kind = _KIND_OF.get("".join(rest))
            if kind:
                return "b", kind, count
            return ("unknown", None, 0) if head == "ساخت سرم " else None
    return None


async def group_word(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str, kind: str | None, count: int) -> None:
    """Answer a serum word in a group with the sender's own lab panel (locked to them)."""
    from bot.gates import remember_card_owner

    message = update.effective_message
    if action == "unknown":
        names = "، ".join(f"«{SR.KINDS[k]['label']}»" for k in SR.ORDER)
        await message.reply_text(f"🧪 کدوم سرم؟ اسم‌ها: {names}\nمثال: <code>ساخت سرم غول</code> یا <code>سرم خشم ۲</code>",
                                 parse_mode="HTML")
        return
    st, note = await run_db(_lab_sync, update.effective_user, "b" if action == "b" else "", kind, count if action == "b" else None)
    st["group"] = True
    if action == "f":
        text, kb = _finish_ask(st)
    elif action == "d":
        text, kb = _del_render(st)
    elif action == "b":
        text, kb = _mini_render(st, note, just=(kind, st["brewed"]) if st["brewed"] else None)
    else:
        text, kb = _lab_render(st, note)
    sent = await send_screen(update, text, photo=_photo(st["level"]) if action == "home" else None,
                             parse_mode="HTML", reply_markup=kb)
    if sent is not None and getattr(sent, "message_id", None):
        remember_card_owner(message.chat_id, sent.message_id, update.effective_user.id)


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
    application.add_handler(CallbackQueryHandler(serum_lab, pattern=r"^sr:(home|up|x|n|d|f|da|dax|mini|xq|xk:[a-z]+:\d+|[buc]:[a-z]+|ug:[a-z]+|d[sy]:[a-z]+|dq:\d+:[a-z]+|d[kx]:[qs]:[a-z]+:\d+|fy:\d+)$"))
    application.add_handler(CallbackQueryHandler(backpack, pattern=r"^bp:home$"))
    application.add_handler(CallbackQueryHandler(open_chests, pattern=r"^rc:open:offer$"))

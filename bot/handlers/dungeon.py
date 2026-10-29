"""«سیاه‌چال» — the bot screens of game/dungeon.py (home, the run, shop, relics).

Every callback starts with «dg:». The run screen is drawn from the run's phase (doors / fight /
rune / merchant), so any tap that arrives late just redraws the current state.
"""

import html

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes

from bio_lab.repository import get_or_create_user
from bot.buttons import BATTLE, CONFIRM, DANGER, NAV, PRIMARY, SHOP, back_btn, btn
from bot.utils import alert_text, run_db, safe_edit_message_text, send_screen
from game import constants, dungeon as D, relics
from game.creature import GameError

_DIV = "━━━━━━━━━━━━━━━━━━━━"


def _bar(hp: int) -> str:
    full = max(0, min(5, round(hp / 20)))
    return "▰" * full + "▱" * (5 - full)


def _el(element: str) -> str:
    return constants.element_label(element)


def _fmt_left(seconds: int) -> str:
    days, rem = divmod(max(0, seconds), 86400)
    hours = rem // 3600
    return f"{days} روز و {hours} ساعت" if days else f"{hours} ساعت"


# ══════════════════════════ home ══════════════════════════
def _home_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    v = D.view(user)
    v["relics"] = len(relics.active(user)) if v["unlocked"] else 0
    return v


def _home_render(v: dict) -> tuple[str, InlineKeyboardMarkup]:
    if not v["unlocked"]:
        done = min(v["stage"], v["unlock_stage"])
        filled = round(10 * done / v["unlock_stage"])
        text = "\n".join([
            "🕳 <b>سیاه‌چال</b>",
            _DIV,
            f"🔒 بعد از فتح هر <b>{v['unlock_stage']}</b> مرحله‌ی دانجن باز می‌شه.",
            f"{'▰' * filled}{'▱' * (10 - filled)} <code>{done}</code>/<code>{v['unlock_stage']}</code>",
            _DIV,
            "<i>یه سیاه‌چال پر از نبرد و در مرموز، با باس آخر، رون‌های سیاه و آثار باستانی 🏺</i>",
        ])
        return text, InlineKeyboardMarkup([[back_btn("menu:campaign", "بازگشت به دانجن")]])
    lines = [
        "🕳 <b>سیاه‌چال</b>",
        _DIV,
        f"🗝 کلید: <code>{v['keys']}</code>  <i>(هر روز ۲ کلید رایگان)</i>",
        f"🔮 رون سیاه: <code>{v['black_runes']}</code>",
        f"🌑 هسته‌ی سایه: <code>{v['shadow_cores']}</code>",
        f"🏆 ورود موفق: <code>{v['clears']}</code> از <code>{v['runs']}</code>",
        f"🏺 آثار فعال: <code>{v['relics']}</code>",
        _DIV,
        f"🌀 <b>عنصر هفته: {_el(v['week_el'])}</b> — بیشتر دشمن‌ها این عنصرن",
        "✨ برکت هفته: هیولاهای " + "، ".join(_el(e) for e in v["week_favoured"]) + f" <code>+{round(D.WEEK_BOOST * 100)}%</code> قدرت",
    ]
    rows = []
    if v["run"]:
        st = v["run"]["state"]
        lines += [_DIV, f"⚠️ یه ورود نیمه‌کاره داری — <b>{_where(st)}</b>"]
        rows.append([btn("ادامه‌ی ورود", emoji_key="btn_dungeon", style=BATTLE, callback_data="dg:run")])
    else:
        lines += [_DIV, "<i>با تیم حمله‌ات وارد شو: ۷ اتاق، سر هر اتاق چند در، و آخرش باس. "
                        "جون هیولاها تا آخر ورود می‌مونه، پس با فکر در انتخاب کن!</i>"]
        lines.append("<i>هر ورود ۱ کلید.</i>")
        entries = []
        for d in v["difficulties"]:
            if d["open"]:
                entries.append(btn(d["label"], emoji_key="btn_dungeon", style=BATTLE, callback_data=f"dg:start:{d['key']}"))
            else:
                lines.append(f"🔒 <i>سختی «{d['label']}» بعد از یه برد در سختی قبلی باز می‌شه.</i>")
        if v["endless_open"]:
            entries.append(btn("بی‌انتها", emoji_key="btn_dungeon_endless", style=BATTLE, callback_data="dg:start:endless"))
        else:
            lines.append("🔒 <i>«بی‌انتها» بعد از یه برد در سختی «سخت» باز می‌شه.</i>")
        rows += [entries[i:i + 2] for i in range(0, len(entries), 2)]   # two entries a row
    rows.append([
        btn("فرم سیاه", emoji_key="btn_shadow_form", style=PRIMARY, callback_data="dg:sh"),
        btn("جدول بی‌انتها", emoji_key="btn_rank", style=PRIMARY, callback_data="dg:lb"),
    ])
    rows.append([
        btn("فروشگاه", emoji_key="btn_dungeon_shop", style=SHOP, callback_data="dg:shop"),
        btn("آثار باستانی", emoji_key="btn_relics", style=SHOP, callback_data="dg:relics"),
    ])
    rows.append([back_btn("menu:campaign", "بازگشت به دانجن")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def dungeon_home(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.callback_query:
        await update.callback_query.answer()
    from game.media import get_feature_image_path

    v = await run_db(_home_sync, update.effective_user)
    text, kb = _home_render(v)
    await send_screen(update, text, photo=get_feature_image_path("dungeon"), parse_mode="HTML", reply_markup=kb)


# ══════════════════════════ the run ══════════════════════════
def _run_render(v: dict, note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    run = v["run"]
    if run is None:
        return _home_render(v)
    st = run["state"]
    tier = D._diff(st.get("difficulty"))["label"]
    lines = [f"🕳 <b>سیاه‌چال · {tier}</b> · {_where(st, html_=True)}", _DIV]
    if note:
        lines += [note, _DIV]
    for m in run["members"]:
        if m["hp"] <= 0:
            lines.append(f"💀 <s>{html.escape(m['name'])}</s> <i>از پا افتاده</i>")
        else:
            lines.append(f"🟢 <b>{html.escape(m['name'])}</b> [{_el(m['element'])}] {_bar(m['hp'])} "
                         f"<code>{m['hp']}%</code> · 💪 <code>{m['now_power']:,}</code>")
    active = [D.rune_label(r) for r in dict.fromkeys(st["runes"])]
    ready = [D.rune_label(k) for k in ("first_strike", "ward") if st.get(k)]
    if active or ready:
        lines.append("🔮 <b>رون‌های فعال:</b>")
        lines += [f"  ▫️ <b>{label}</b>: <i>{desc}</i>" for label, desc in active]
        lines += [f"  ⚡ <b>{label}</b> آماده‌ست: <i>{desc}</i>" for label, desc in ready]
    loot = st["loot"]
    lines.append(f"🎒 غنیمت: 💰 <code>{loot['coins']:,}</code> · 🧬 <code>{loot['dna']:,}</code> · 🔮 <code>{loot['runes']}</code>")
    if st.get("log"):
        lines += [_DIV] + [f"<i>{html.escape(x)}</i>" for x in st["log"][-2:]]
    lines.append(_DIV)

    rows = []
    phase = st["phase"]
    if phase == "doors":
        lines.append("🚪 <b>کدوم در رو باز می‌کنی؟</b>")
        if all(d in D.COMBAT_DOORS for d in st["doors"]):
            lines.append("<i>اینجا راهی جز نبرد نیست — بعد از هر اتاق آروم، اتاق بعدی نبرده.</i>")
        if "mystery" in st["doors"]:
            lines.append("<i>❓ پشت در مرموز: گنج، تله یا کمین.</i>")
        rows.append([btn(f"{D.DOORS[d][0]} {D.DOORS[d][1]}", style=PRIMARY, callback_data=f"dg:d:{i}")
                     for i, d in enumerate(st["doors"])])
    elif phase == "fight":
        e = st["enemy"]
        kind = {"elite": " 💀 <b>نخبه</b>", "boss": " 👑 <b>باس</b>"}.get(e["kind"], "")
        lines += [f"👹 دشمن: <b>{html.escape(e['name'])}</b> [{_el(e['element'])}]{kind}",
                  f"💪 قدرت دشمن: <code>{e['power']:,}</code>",
                  "", "⚔️ <b>کی بجنگه؟</b>"]
        for m in run["members"]:
            if m["hp"] > 0:
                mark = "✅" if m.get("wins") else "❌"
                rows.append([btn(f"{mark} {m['name']} · {m['vs']:,} در برابر {m['enemy_vs']:,}",
                                 style=CONFIRM if m.get("wins") else NAV, callback_data=f"dg:f:{m['id']}")])
        if st.get("first_strike"):
            lines.append("<i>«ضربه‌ی اول» فعاله: +۳۰٪ قدرت توی این نبرد.</i>")
        if st.get("ward"):
            lines.append("<i>«سپر سایه» آماده‌ست: اگه ببازی، هیولات با ۲۰٪ جون سرپا می‌مونه.</i>")
    elif phase == "rune":
        lines.append("🔮 <b>یه رون انتخاب کن</b> (رون‌ها تا آخر همین ورود می‌مونن؛ هر کدوم یه بار):")
        for i, r in enumerate(st["rune_offer"]):
            label, desc = D.rune_label(r)
            lines.append(f"▫️ <b>{label}</b>: {desc}")
            rows.append([btn(label, style=SHOP, callback_data=f"dg:r:{i}")])
    elif phase == "merchant":
        lines.append("🛒 <b>تاجر سرگردان</b> یه رون با طلا بهت می‌فروشه:")
        for i, o in enumerate(st["merchant"]):
            label, desc = D.rune_label(o["rune"])
            lines.append(f"▫️ <b>{label}</b>: {desc} — <code>{int(o['price']):,}</code> طلا")
            rows.append([btn(f"{label} · {int(o['price']):,} طلا", style=SHOP, callback_data=f"dg:m:{i}")])
        rows.append([btn("رد شدن", style=NAV, callback_data="dg:m:x")])
    if v.get("heal_serums") and any(0 < m["hp"] < 100 for m in run["members"]):
        rows.append([btn(f"سرم ترمیم · {v['heal_serums']} عدد", emoji_key="btn_serum_use", style=CONFIRM, callback_data="dg:h")])
    rows.append([btn("ترک سیاه‌چال", emoji_key="btn_cancel", style=DANGER, callback_data="dg:quit")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def _where(st: dict, html_: bool = False) -> str:
    """«اتاق ۳ از ۷» / «باس» — or «طبقه ۱۲» / «باس طبقه ۲۰» in the endless dungeon."""
    room = st["room"]
    b = (lambda x: f"<b>{x}</b>") if html_ else (lambda x: x)
    c = (lambda x: f"<code>{x}</code>") if html_ else (lambda x: str(x))
    if st.get("endless"):
        return ("👑 " + b(f"باس طبقه‌ی {room}")) if room % D.ENDLESS_BOSS_EVERY == 0 else f"طبقه‌ی {c(room)}"
    return ("👑 " + b("باس سیاه‌چال")) if room > D.ROOMS else f"اتاق {c(room)} از {c(D.ROOMS)}"


def _finish_render(fin: dict, v: dict) -> tuple[str, InlineKeyboardMarkup]:
    loot = fin["loot"]
    head = {
        "won": "🏆 <b>باس سیاه‌چال رو شکست دادی!</b>",
        "lost": "💀 <b>تیمت از پا افتاد…</b>",
        "quit": "🚪 <b>از سیاه‌چال بیرون اومدی.</b>",
    }[fin["status"]]
    if fin.get("endless"):
        head = ("🌀 <b>سفرت توی بی‌انتها تموم شد</b>" if fin["status"] == "lost" else "🚪 <b>از بی‌انتها بیرون اومدی.</b>")
    lines = [head, _DIV]
    if fin.get("endless"):
        lines += [f"🏁 رسیدی به طبقه‌ی <b>{fin['floor']}</b>"
                  + (" — <b>رکورد جدید این هفته!</b> 🎉" if fin.get("new_record") else ""),
                  f"🏆 رکورد هفته‌ات: طبقه‌ی <code>{fin['week_best']}</code> · رتبه‌ی <code>{fin['rank'] or '—'}</code>", _DIV]
    if fin["share"] < 1:
        lines.append(f"<i>نصف غنیمت جمع‌شده بهت رسید (رسیدی به {'باس' if fin['room'] > D.ROOMS else 'اتاق ' + str(fin['room'])}).</i>")
    lines += [
        f"💰 طلا: <code>+{loot['coins']:,}</code>",
        f"🧬 DNA: <code>+{loot['dna']:,}</code>",
        f"🔮 رون سیاه: <code>+{loot['runes']}</code>",
    ]
    if loot.get("cores"):
        lines.append(f"🌑 هسته‌ی سایه: <code>+{loot['cores']}</code>")
    if fin.get("unlocked"):
        lines += [_DIV, f"🔓 <b>سختی «{fin['unlocked']}» باز شد!</b>"]
    if fin.get("badge"):
        lines += [_DIV, f"{fin['badge'][0]} <b>نشان جدید: {fin['badge'][1]}</b>\n<i>از این به بعد کنار اسم آزمایشگاهت دیده می‌شه.</i>"]
    if fin.get("relic"):
        r = fin["relic"]
        lines += [_DIV, f"🏺 <b>اثر باستانی: {r['label']}</b>",
                  f"<i>{r['desc']}</i>",
                  "⏳ " + ("۳ روز به زمانش اضافه شد" if r["extended"] else "۳ روز فعاله")]
    rows = []
    if v["keys"] > 0:
        rows.append([btn(f"ورود دوباره · {v['keys']} کلید", emoji_key="btn_dungeon", style=BATTLE,
                         callback_data=f"dg:start:{fin.get('difficulty', 'normal')}")])
    rows.append([btn("سیاه‌چال", emoji_key="btn_dungeon", style=NAV, callback_data="dg:home")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def _act_sync(tg_user, action: str, arg):
    user, _ = get_or_create_user(tg_user)
    out = None
    if action == "start":
        D.start(user, arg or "normal")
    elif action == "door":
        out = D.choose_door(user, arg)
    elif action == "fight":
        out = D.fight(user, arg)
    elif action == "rune":
        out = D.pick_rune(user, arg)
    elif action == "merchant":
        out = D.merchant_buy(user, arg)
    elif action == "quit":
        out = D.quit_run(user)
    elif action == "heal":
        from game import serums

        out = serums.use(user, "heal")
    v = D.view(user)
    # no run any more (a stale «ادامه‌ی ورود» tap) → _run_render falls back to the home screen
    v["relics"] = len(relics.active(user)) if v["unlocked"] else 0
    return out, v


def _fight_note(out: dict) -> str:
    if out.get("won"):
        got = out.get("got") or {}
        return (f"✅ <b>{html.escape(out['member'])}</b> برد! (<code>{out['mine']:,}</code> در برابر <code>{out['theirs']:,}</code>)"
                f" · −{out.get('hp_lost', 0)}٪ جون\n🎒 +{got.get('coins', 0):,} طلا · +{got.get('dna', 0):,} DNA · +{got.get('runes', 0)} 🔮")
    if out.get("warded"):
        return (f"🛡 <b>{html.escape(out['member'])}</b> باخت ولی «سپر سایه» با ۲۰٪ جون سرپا نگهش داشت "
                f"(<code>{out['mine']:,}</code> در برابر <code>{out['theirs']:,}</code>).")
    return (f"❌ <b>{html.escape(out['member'])}</b> باخت و از پا افتاد "
            f"(<code>{out['mine']:,}</code> در برابر <code>{out['theirs']:,}</code>). یکی دیگه رو بفرست!")


async def dungeon_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    parts = query.data.split(":")
    cmd = parts[1]
    action, arg = {
        "run": ("view", None), "start": ("start", None),
        "d": ("door", None), "f": ("fight", None), "r": ("rune", None), "m": ("merchant", None),
        "quitok": ("quit", None), "h": ("heal", None),
    }.get(cmd, (None, None))
    if cmd == "quit":
        await query.answer()
        await safe_edit_message_text(
            query,
            "🚪 <b>از سیاه‌چال بیرون بیای؟</b>\n\nنصف غنیمتی که تا الان جمع کردی بهت می‌رسه و کلیدت برنمی‌گرده.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                btn("بیرون بیا", emoji_key="btn_confirm", style=DANGER, callback_data="dg:quitok"),
                btn("ادامه می‌دم", emoji_key="btn_cancel", style=NAV, callback_data="dg:run"),
            ]]),
        )
        return
    if action is None:
        await query.answer()
        return
    if cmd == "start":
        arg = parts[2] if len(parts) > 2 else "normal"
    elif cmd in ("d", "f", "r"):
        arg = int(parts[2])
    elif cmd == "m":
        arg = None if parts[2] == "x" else int(parts[2])
    try:
        out, v = await run_db(_act_sync, update.effective_user, action, arg)
    except D.EmptyTeamError as exc:
        # no dead end: the note comes with the way to fill the team (and back here after)
        await query.answer()
        await send_screen(update, f"⚠️ <b>{html.escape(str(exc))}</b>\n\nتا ۳ هیولا انتخاب کن و بعد برگرد و وارد شو.",
                          parse_mode="HTML", reply_markup=InlineKeyboardMarkup([
                              [btn("چیدن تیم حمله", emoji_key="btn_team", style=PRIMARY, callback_data="dg_team")],
                              [back_btn("dg:home", "بازگشت به سیاه‌چال")]]))
        return
    except GameError as exc:
        await query.answer(alert_text(exc), show_alert=True)
        return
    await query.answer()
    note = ""
    if isinstance(out, dict) and out.get("finish"):
        text, kb = _finish_render(out["finish"], v)
        await send_screen(update, text, parse_mode="HTML", reply_markup=kb)
        return
    if action == "door" and isinstance(out, dict) and out.get("mystery"):
        note = {"treasure": "❓ پشت در مرموز <b>گنج</b> بود!", "trap": "❓ پشت در مرموز <b>تله</b> بود!",
                "ambush": "❓ پشت در مرموز <b>کمین</b> بود — آماده‌ی نبرد شو!"}[out["mystery"]]
    elif action == "fight":
        note = _fight_note(out)
    elif action == "rune":
        note = f"🔮 «{D.rune_label(out['rune'])[0]}» گرفتی."
    elif action == "heal":
        note = f"💖 سرم ترمیم · {html.escape(out['note'])}"
    text, kb = _run_render(v, note)
    await send_screen(update, text, parse_mode="HTML", reply_markup=kb)


# ══════════════════════════ shop & relics ══════════════════════════
def _shop_render(v: dict, note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    lines = ["🔮 <b>فروشگاه سیاه‌چال</b>", _DIV, f"🔮 رون سیاه: <code>{v['black_runes']}</code>"]
    if note:
        lines += [_DIV, note]
    rows = []
    for item in v["shop"]:
        if item["left"] == 0:
            label, style = f"{item['label']} · امروز خریدی", NAV
        else:
            cap = " (روزی ۱)" if item["left"] is not None else ""
            label, style = f"{item['label']} · {item['price']} 🔮{cap}", SHOP if v["black_runes"] >= item["price"] else NAV
        rows.append([btn(label, style=style, callback_data=f"dg:buy:{item['key']}")])
    rows.append([back_btn("dg:home", "بازگشت به سیاه‌چال")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def _buy_sync(tg_user, key):
    user, _ = get_or_create_user(tg_user)
    got = D.shop_buy(user, key)
    note = f"✅ «{got['label']}» خریدی."
    if got.get("relic"):
        note += f"\n🏺 {got['relic']['label']}: <i>{got['relic']['desc']}</i>"
    if got.get("item") is not None:
        note += f"\n⚔️ {html.escape(got['item'].name)}"
    if got.get("chest"):
        c = got["chest"]
        note += f"\n💰 +{c.get('coins', 0):,} طلا · 🧬 +{c.get('dna', 0):,} DNA"
    return note, D.view(user)


async def dungeon_shop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    parts = query.data.split(":")
    note = ""
    if parts[1] == "buy":
        try:
            note, v = await run_db(_buy_sync, update.effective_user, parts[2])
        except GameError as exc:
            await query.answer(alert_text(exc), show_alert=True)
            return
    else:
        v = await run_db(_home_sync, update.effective_user)
    await query.answer()
    text, kb = _shop_render(v, note)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=kb)


def _relics_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    return relics.album(user)


async def dungeon_relics(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    rows = await run_db(_relics_sync, update.effective_user)
    await query.answer()
    lines = ["🏺 <b>آثار باستانی</b>", _DIV,
             f"<i>از باس سیاه‌چال میان و {relics.RELIC_DAYS} روز فعالن؛ اگه دوباره بگیریش "
             f"{relics.RELIC_DAYS} روز اضافه می‌شه (تا {relics.RELIC_MAX_DAYS} روز).</i>", _DIV]
    for r in rows:
        if r["active"]:
            lines.append(f"🟢 <b>{r['label']}</b>: {r['desc']}\n    ⏳ {_fmt_left(r['seconds_left'])} مونده")
        elif r["times"]:
            lines.append(f"⚪️ {r['label']}: {r['desc']} <i>(تموم شده)</i>")
        else:
            lines.append(f"🔒 <i>{r['label']}: ؟</i>")
    await safe_edit_message_text(query, "\n".join(lines), parse_mode="HTML",
                                 reply_markup=InlineKeyboardMarkup([[back_btn("dg:home", "بازگشت به سیاه‌چال")]]))


# ══════════════════════════ «فرم سیاه» ══════════════════════════
def _shadow_sync(tg_user, action: str | None, cid: int | None):
    from game import shadow

    user, _ = get_or_create_user(tg_user)
    done = None
    if action == "ok":
        done = shadow.ascend(user, cid)
    prof = D.profile(user) if D.unlocked(user) else None
    return {"rows": shadow.candidates(user), "cores": prof.shadow_cores if prof else 0,
            "coins": type(user).objects.values_list("coins", flat=True).get(id=user.id), "done": done}


def _shadow_render(v: dict, focus: int | None = None) -> tuple[str, InlineKeyboardMarkup]:
    from game import shadow

    lines = ["🌑 <b>فرم سیاه</b>", _DIV,
             f"🌑 هسته‌ی سایه: <code>{v['cores']}</code> · 💰 طلا: <code>{v['coins']:,}</code>",
             f"<i>هیولای اساطیری ۵ ستاره‌ای که قدرتش به سقف ({constants.MAX_KAIJU_POWER:,}) رسیده، با هسته‌ی سایه "
             f"تا {constants.SHADOW_MAX_LEVEL} سطح فرم سیاه می‌گیره: هر سطح +{constants.SHADOW_POWER_PER_LEVEL} قدرت "
             f"و سقفش هم همون‌قدر بالا می‌ره. هسته از باس سخت و کابوس سیاه‌چال یا فروشگاهش میاد.</i>", _DIV]
    if v.get("done"):
        d = v["done"]
        lines += [f"✨ <b>{html.escape(d['creature'].name)} به {shadow.LEVEL_NAMES[d['level']]} رسید!</b>"
                  + ("\n👑 <b>ارباب سایه شدی!</b> نشان 🌑 کنار اسمت نشست." if d["lord"] else ""), _DIV]
    rows = []
    if not v["rows"]:
        lines.append("<i>هنوز هیولای اساطیری ۵ ستاره نداری.</i>")
    for r in v["rows"][:8]:
        c = r["creature"]
        lvl = f" · {shadow.LEVEL_NAMES[r['level']]}" if r["level"] else ""
        lines.append(f"{'🌑' if r['level'] else '▫️'} <b>{html.escape(c.name)}</b>{lvl} · 💪 <code>{r['power']:,}</code>")
        if r["next"] is None:
            lines.append("   👑 <i>آخرین سطح</i>")
        elif not r["ok"]:
            lines.append(f"   <i>{html.escape(r['why'])}</i>")
        else:
            nxt = r["next"]
            lines.append(f"   سطح بعد: 🌑 <code>{nxt['cores']}</code> هسته · 💰 <code>{nxt['coins']:,}</code> طلا")
            if focus == c.id:
                rows.append([btn(f"تأیید: {c.name} ← {shadow.LEVEL_NAMES[r['level'] + 1]}", emoji_key="btn_confirm",
                                 style=CONFIRM, callback_data=f"dg:shok:{c.id}")])
            else:
                rows.append([btn(f"{c.name} ← {shadow.LEVEL_NAMES[r['level'] + 1]}", emoji_key="btn_shadow_form",
                                 style=PRIMARY, callback_data=f"dg:sha:{c.id}")])
    rows.append([back_btn("dg:home", "بازگشت به سیاه‌چال")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def shadow_screen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    parts = query.data.split(":")
    action = {"sha": "ask", "shok": "ok"}.get(parts[1])
    cid = int(parts[2]) if len(parts) > 2 else None
    try:
        v = await run_db(_shadow_sync, update.effective_user, action, cid)
    except GameError as exc:
        await query.answer(alert_text(exc), show_alert=True)
        return
    await query.answer("✨ فرم سیاه!" if v.get("done") else None)
    text, kb = _shadow_render(v, focus=cid if action == "ask" else None)
    await send_screen(update, text, parse_mode="HTML", reply_markup=kb)


# ══════════════════════════ «بی‌انتها»: the weekly table ══════════════════════════
def _board_sync(tg_user):
    from bio_lab.repository import lab_mention

    user, _ = get_or_create_user(tg_user)
    b = D.board(user)
    b["rows"] = [dict(r, name=lab_mention(r["user"])) for r in b["rows"]]
    if b["last"]:
        from bio_lab.models import User as U

        ids = [r["user_id"] for r in b["last"]["results"][:3]]
        names = {u.id: lab_mention(u) for u in U.objects.filter(id__in=ids)}
        b["last_top"] = [(r["rank"], names.get(r["user_id"], "—"), r["floor"]) for r in b["last"]["results"][:3]]
    return b


async def dungeon_board(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    b = await run_db(_board_sync, update.effective_user)
    await query.answer()
    days, rem = divmod(b["left"], 86400)
    lines = ["🌀 <b>جدول هفتگی سیاه‌چال بی‌انتها</b>", _DIV,
             f"⏳ <code>{days}</code> روز و <code>{rem // 3600}</code> ساعت تا پایان هفته", _DIV]
    medals = ("🥇", "🥈", "🥉")
    if not b["rows"]:
        lines.append("<i>هنوز کسی این هفته وارد بی‌انتها نشده — اولین نفر باش!</i>")
    for r in b["rows"]:
        lines.append(f"{medals[r['rank'] - 1] if r['rank'] <= 3 else str(r['rank']) + '.'} {r['name']} — طبقه‌ی <code>{r['floor']}</code>")
    lines.append(_DIV)
    lines.append(f"📍 تو: طبقه‌ی <code>{b['mine']['floor']}</code> · رتبه‌ی <code>{b['mine']['rank']}</code>" if b["mine"]
                 else "📍 تو هنوز این هفته طبقه‌ای ثبت نکردی.")
    lines += [_DIV, "🎁 <b>جایزه‌ی آخر هفته:</b>",
              "🥇 ۳۰۰ 🔮 + ۵ 🌑 · 🥈 ۲۰۰ 🔮 + ۴ 🌑 · 🥉 ۱۵۰ 🔮 + ۳ 🌑",
              "۴ تا ۱۰: ۸۰ 🔮 + ۱ 🌑"]
    if b.get("last_top"):
        lines += [_DIV, f"🏁 <b>برنده‌های هفته‌ی قبل ({b['last']['week']}):</b>"]
        lines += [f"{medals[rank - 1]} {name} — طبقه‌ی <code>{floor}</code>" for rank, name, floor in b["last_top"]]
    await safe_edit_message_text(query, "\n".join(lines), parse_mode="HTML",
                                 reply_markup=InlineKeyboardMarkup([[back_btn("dg:home", "بازگشت به سیاه‌چال")]]))


async def dungeon_week_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Pays last week's «بی‌انتها» top 10 once the week turns over, and DMs them."""
    import asyncio

    from telegram.error import TelegramError

    try:
        dms = await run_db(D.settle_due_week)
    except Exception:  # noqa: BLE001 — a settle hiccup must not stop the job queue
        import logging

        logging.getLogger(__name__).exception("dungeon week settle failed")
        return
    for uid, text in dms:
        try:
            await context.bot.send_message(chat_id=uid, text=text, parse_mode="HTML")
        except TelegramError:
            pass
        await asyncio.sleep(0.05)


def register(application) -> None:
    application.add_handler(CallbackQueryHandler(dungeon_board, pattern=r"^dg:lb$"))
    if application.job_queue:
        application.job_queue.run_repeating(dungeon_week_job, interval=1800, first=90)
    application.add_handler(CallbackQueryHandler(dungeon_home, pattern=r"^dg:home$"))
    application.add_handler(CallbackQueryHandler(dungeon_shop, pattern=r"^dg:(shop|buy:[a-z_]+)$"))
    application.add_handler(CallbackQueryHandler(dungeon_relics, pattern=r"^dg:relics$"))
    application.add_handler(CallbackQueryHandler(dungeon_callback, pattern=r"^dg:(run|start(:[a-z]+)?|quit|quitok|h|d:\d+|f:\d+|r:\d+|m:(\d+|x))$"))
    application.add_handler(CallbackQueryHandler(shadow_screen, pattern=r"^dg:(sh|sha:\d+|shok:\d+)$"))

"""«🧭 مأموریت اعزامی» — the private-chat panel for game/dispatch.py.

Flow: panel (running missions + today's board) → offer → pick an idle creature (paged)
→ confirm (exact reward for that creature) → dispatched. A finished mission shows a
«دریافت جایزه» button; a running one can be called back (no reward).

Everything durable lives in callback_data (offer index, creature id, mission id) — never
in user_data — so every button keeps working across a bot restart."""

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

from bio_lab.repository import creature_name, get_or_create_user
from bot.buttons import BATTLE, CONFIRM, DANGER, LIST, NAV, back_btn, btn
from bot.gates import hall_gated
from bot.utils import alert_text, run_db, safe_edit_message_text, send_screen
from game import constants, dispatch
from game.creature import GameError
from game.emoji import get_emoji

_DIV = "━━━━━━━━━━━━━━━━━━━━"
PICK_PAGE_SIZE = 6
_FOCUS_LABEL = {"gold": "طلا", "dna": "DNA", "mixed": "طلا و DNA"}


def _fmt_left(seconds: int) -> str:
    h, rem = divmod(max(0, int(seconds)), 3600)
    m = rem // 60
    if h and m:
        return f"{h} ساعت و {m} دقیقه"
    if h:
        return f"{h} ساعت"
    return f"{max(1, m)} دقیقه"


def _mission_view(mission) -> dict:
    _key, emoji, title, _flavor, _focus = dispatch.template(mission)
    return {
        "id": mission.id, "emoji": emoji, "title": title, "hours": mission.hours,
        "creature": creature_name(mission.creature), "ready": dispatch.is_ready(mission),
        "left": dispatch.seconds_left(mission), "reward": dict(mission.reward or {}),
    }


# ── panel ─────────────────────────────────────────────────────────────────────
def _panel_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    dispatch.prune_old(user)
    missions = [_mission_view(m) for m in dispatch.active_missions(user)]
    return {"missions": missions, "offers": dispatch.offers_for(user), "slots": dispatch.slots(user)}


def _offer_line(o: dict) -> str:
    need = "" if o["min_rarity"] == "common" else f" · حداقل {constants.RARITY_LABELS[o['min_rarity']]}"
    return (
        f"{o['emoji']} <b>{o['title']}</b>\n"
        f"   ⏱ {o['hours']} ساعت · جایزه: {_FOCUS_LABEL[o['focus']]}{need}\n"
        f"   🔮 عنصر پیشنهادی: {constants.element_label(o['element'])} (+۲۵٪ جایزه)"
    )


def _panel_render(data: dict, note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    missions, offers, slots = data["missions"], data["offers"], data["slots"]
    lines = [
        "🧭 <b>مأموریت‌های اعزامی</b>",
        _DIV,
        "<blockquote>هیولاهای بیکارت رو بفرست مأموریت؛ چند ساعت بعد برگرد و جایزه رو بگیر. "
        "هیولای فعال و هیولاهای مشغول در معدن یا غار نمی‌تونن برن.</blockquote>",
        f"📦 جایگاه اعزام: <code>{len(missions)}/{slots}</code>",
    ]
    if note:
        lines = [note, ""] + lines
    rows = []
    ready = [m for m in missions if m["ready"]]
    if missions:
        lines += ["", "🚶 <b>در مأموریت:</b>"]
        for m in missions:
            state = "✅ <b>برگشته — جایزه آماده‌ست</b>" if m["ready"] else f"⏳ {_fmt_left(m['left'])} مونده"
            lines.append(f"{m['emoji']} {m['title']} — <b>{m['creature']}</b>\n   {state}")
            if m["ready"]:
                rows.append([btn(f"دریافت جایزه: {m['creature']}", emoji_key="btn_confirm", style=CONFIRM,
                                 callback_data=f"dsp:collect:{m['id']}")])
            else:
                rows.append([btn(f"{m['creature']} ({_fmt_left(m['left'])})", emoji_key="btn_dispatch", style=NAV,
                                 callback_data=f"dsp:view:{m['id']}")])
        if len(ready) > 1:
            rows.insert(0, [btn(f"دریافت همه ({len(ready)})", emoji_key="btn_confirm", style=CONFIRM,
                                callback_data="dsp:collect_all")])

    open_offers = [o for o in offers if not o["taken"]]
    lines += ["", _DIV, f"📋 <b>مأموریت‌های امروز</b> (<code>{len(open_offers)}</code> از <code>{len(offers)}</code> مونده)"]
    if not open_offers:
        lines.append("<i>همه‌ی مأموریت‌های امروز رو فرستادی. فردا فهرست تازه می‌آد.</i>")
    full = len(missions) >= slots
    for o in open_offers:
        lines += ["", _offer_line(o)]
        if not full:
            rows.append([btn(f"{o['title']} ({o['hours']} ساعت)", emoji_key="btn_dispatch", style=BATTLE,
                             callback_data=f"dsp:offer:{o['idx']}:0")])
    if full and open_offers:
        lines += ["", "<i>جایگاه‌هات پره؛ یکی که برگشت می‌تونی مأموریت بعدی رو بفرستی.</i>"]
    rows.append([btn("بروزرسانی", emoji_key="btn_recheck", style=NAV, callback_data="dsp:home")])
    rows.append([back_btn("menu:hub_battle", "بازگشت به نبرد")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def _show_panel(update: Update, note: str = "") -> None:
    data = await run_db(_panel_sync, update.effective_user)
    text, keyboard = _panel_render(data, note)
    from game.media import get_feature_image_path

    await send_screen(update, text, photo=get_feature_image_path("idle"), parse_mode="HTML", reply_markup=keyboard)


async def dispatch_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _show_panel(update)


async def dispatch_home_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    await _show_panel(update)


# ── offer → creature picker ───────────────────────────────────────────────────
def _offer_sync(tg_user, idx):
    user, _ = get_or_create_user(tg_user)
    offer = dispatch.get_offer(user, idx)
    if offer["taken"]:
        raise GameError("این مأموریت رو امروز قبلاً فرستادی.")
    creatures = dispatch.eligible_creatures(user, offer)
    previews = {c.id: dispatch.preview_reward(user, offer, c) for c in creatures}
    return offer, creatures, previews


async def dispatch_offer_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, _, idx, page = query.data.split(":")
    idx, page = int(idx), int(page)
    try:
        offer, creatures, previews = await run_db(_offer_sync, update.effective_user, idx)
    except GameError as exc:
        await query.answer(alert_text(exc), show_alert=True)
        return
    await query.answer()
    lines = [
        f"{offer['emoji']} <b>{offer['title']}</b>",
        _DIV,
        f"<blockquote>{offer['flavor']}</blockquote>",
        f"⏱ مدت: <b>{offer['hours']} ساعت</b>",
        f"🎁 جایزه: {_FOCUS_LABEL[offer['focus']]} (بر اساس قدرت هیولایی که می‌فرستی)",
        f"🔮 عنصر پیشنهادی: {constants.element_label(offer['element'])} — <b>+۲۵٪ جایزه</b>",
    ]
    if offer["min_rarity"] != "common":
        lines.append(f"📌 حداقل نایابی: {constants.RARITY_LABELS[offer['min_rarity']]}")
    lines.append(f"🍀 شانس جایزه‌ی شگفتی: <code>{int(dispatch.BONUS_CHANCE[offer['hours']] * 100)}٪</code>")
    rows = []
    if not creatures:
        lines += ["", "😕 <b>هیولای بیکارِ مناسبی نداری.</b>",
                  "<i>هیولای فعال و هیولاهای مشغول نمی‌تونن برن؛ یه هیولای دیگه آزاد کن یا از باکس و غار بگیر.</i>"]
    else:
        pages = max(1, (len(creatures) + PICK_PAGE_SIZE - 1) // PICK_PAGE_SIZE)
        page = max(0, min(page, pages - 1))
        chunk = creatures[page * PICK_PAGE_SIZE:(page + 1) * PICK_PAGE_SIZE]
        lines += ["", "<b>کدوم هیولا بره؟</b>" + (f" <i>(صفحه {page + 1}/{pages})</i>" if pages > 1 else "")]
        for c in chunk:
            p = previews[c.id]
            mark = "🔮 " if p["element_match"] else ""
            rows.append([btn(
                f"{mark}{creature_name(c)} · {c.star_level}⭐ · {p['coins']:,} طلا",
                style=LIST, callback_data=f"dsp:pick:{idx}:{c.id}",
            )])
        nav = []
        if page > 0:
            nav.append(btn("قبلی", emoji_key="btn_prev", style=NAV, callback_data=f"dsp:offer:{idx}:{page - 1}"))
        if page < pages - 1:
            nav.append(btn("بعدی", emoji_key="btn_next", style=NAV, callback_data=f"dsp:offer:{idx}:{page + 1}"))
        if nav:
            rows.append(nav)
        lines.append("<i>🔮 یعنی عنصرش با مأموریت جوره و ۲۵٪ بیشتر می‌آره.</i>")
    rows.append([back_btn("dsp:home", "بازگشت")])
    await safe_edit_message_text(query, "\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))


# ── confirm + go ──────────────────────────────────────────────────────────────
def _pick_sync(tg_user, idx, creature_id):
    from bio_lab.models import Creature

    user, _ = get_or_create_user(tg_user)
    offer = dispatch.get_offer(user, idx)
    creature = Creature.objects.filter(id=creature_id, owner=user).first()
    if creature is None:
        raise GameError("این هیولا توی کلکسیون تو نیست.")
    if not dispatch.meets_rarity(creature, offer):
        raise GameError(f"این مأموریت حداقل یه هیولای {constants.RARITY_LABELS[offer['min_rarity']]} می‌خواد.")
    return offer, creature, dispatch.preview_reward(user, offer, creature)


async def dispatch_pick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, _, idx, cid = query.data.split(":")
    try:
        offer, creature, reward = await run_db(_pick_sync, update.effective_user, int(idx), int(cid))
    except GameError as exc:
        await query.answer(alert_text(exc), show_alert=True)
        return
    await query.answer()
    match = "✅ عنصرش جوره: +۲۵٪ حساب شده" if reward["element_match"] else "➖ عنصرش با مأموریت جور نیست (بدون ۲۵٪ اضافه)"
    text = "\n".join([
        f"{offer['emoji']} <b>{offer['title']}</b>",
        _DIV,
        f"🦖 هیولا: <b>{creature_name(creature)}</b> — {constants.RARITY_LABELS[creature.rarity]} · "
        f"{creature.star_level}⭐ · قدرت <code>{reward['power']:,}</code>",
        f"🔮 {match}",
        f"⏱ مدت: <b>{offer['hours']} ساعت</b>",
        "",
        f"🎁 <b>جایزه‌ی قطعی:</b> {dispatch.reward_text(reward, with_bonus=False)}",
        f"🍀 شانس جایزه‌ی شگفتی: <code>{int(dispatch.BONUS_CHANCE[offer['hours']] * 100)}٪</code>",
        "",
        "<i>تا وقتی برنگشته، این هیولا رو نمی‌تونی فعال، ترکیب، منتقل یا راهی معدن و غار کنی.</i>",
    ])
    keyboard = InlineKeyboardMarkup([
        [btn("بفرستش", emoji_key="btn_confirm", style=CONFIRM, callback_data=f"dsp:go:{idx}:{cid}")],
        [back_btn(f"dsp:offer:{idx}:0", "یه هیولای دیگه")],
    ])
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


def _go_sync(tg_user, idx, creature_id):
    user, _ = get_or_create_user(tg_user)
    mission = dispatch.start(user, idx, creature_id)
    return creature_name(mission.creature), mission.hours


async def dispatch_go_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, _, idx, cid = query.data.split(":")
    try:
        name, hours = await run_db(_go_sync, update.effective_user, int(idx), int(cid))
    except GameError as exc:
        await query.answer(alert_text(exc), show_alert=True)
        return
    await query.answer("🧭 راهی شد!")
    await _show_panel(update, note=f"🧭 <b>{name}</b> راهی مأموریت شد و <b>{hours} ساعت</b> دیگه برمی‌گرده.")


# ── running mission: view / cancel ────────────────────────────────────────────
def _view_sync(tg_user, mission_id):
    from bio_lab.models import DispatchMission

    user, _ = get_or_create_user(tg_user)
    mission = (
        DispatchMission.objects.filter(id=mission_id, owner=user, status=DispatchMission.ACTIVE)
        .select_related("creature").first()
    )
    if mission is None:
        raise GameError("این مأموریت پیدا نشد.")
    return _mission_view(mission)


async def dispatch_view_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    mission_id = int(query.data.split(":")[2])
    try:
        m = await run_db(_view_sync, update.effective_user, mission_id)
    except GameError as exc:
        await query.answer(alert_text(exc), show_alert=True)
        return
    await query.answer()
    state = "✅ برگشته — جایزه آماده‌ست" if m["ready"] else f"⏳ {_fmt_left(m['left'])} تا برگشت"
    text = "\n".join([
        f"{m['emoji']} <b>{m['title']}</b>",
        _DIV,
        f"🦖 هیولا: <b>{m['creature']}</b>",
        f"⏱ {state}",
        f"🎁 جایزه‌ی قطعی: {dispatch.reward_text(m['reward'], with_bonus=False)}",
    ])
    rows = []
    if m["ready"]:
        rows.append([btn("دریافت جایزه", emoji_key="btn_confirm", style=CONFIRM, callback_data=f"dsp:collect:{m['id']}")])
    else:
        rows.append([btn("برگردوندن (بدون جایزه)", emoji_key="btn_cancel", style=DANGER,
                         callback_data=f"dsp:cancel:{m['id']}")])
    rows.append([back_btn("dsp:home", "بازگشت")])
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))


async def dispatch_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """First tap asks; «dsp:cancelok» does it."""
    query = update.callback_query
    mission_id = int(query.data.split(":")[2])
    await query.answer()
    await safe_edit_message_text(
        query,
        "⚠️ <b>هیولا رو برگردونم؟</b>\n\nمأموریت لغو می‌شه و <b>هیچ جایزه‌ای نمی‌گیری</b>. "
        "همین مأموریت رو امروز می‌تونی دوباره بفرستی.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            btn("بله، برگرده", emoji_key="btn_confirm", style=DANGER, callback_data=f"dsp:cancelok:{mission_id}"),
            back_btn(f"dsp:view:{mission_id}", "نه"),
        ]]),
    )


def _cancel_sync(tg_user, mission_id):
    user, _ = get_or_create_user(tg_user)
    dispatch.cancel(user, mission_id)


async def dispatch_cancel_ok_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        await run_db(_cancel_sync, update.effective_user, int(query.data.split(":")[2]))
    except GameError as exc:
        await query.answer(alert_text(exc), show_alert=True)
        return
    await query.answer("برگشت.")
    await _show_panel(update, note="↩️ هیولات برگشت؛ مأموریت لغو شد.")


# ── collect ───────────────────────────────────────────────────────────────────
def _result_line(res: dict) -> str:
    mission = res["mission"]
    _key, emoji, title, _flavor, _focus = dispatch.template(mission)
    name = creature_name(res["creature"]) if res["creature"] is not None else "هیولات"
    line = f"{emoji} <b>{title}</b> — {name}\n   🎁 {dispatch.reward_text(res['reward'])}"
    if dispatch.has_bonus(res["reward"]):
        line += "\n   🍀 <b>جایزه‌ی شگفتی پیدا شد!</b>"
    if res["levels"]:
        line += f"\n   {get_emoji('celebrate')} {name} به سطح <code>{res['creature'].level}</code> رسید!"
    return line


def _collect_sync(tg_user, mission_id):
    user, _ = get_or_create_user(tg_user)
    return _result_line(dispatch.collect(user, mission_id))


def _collect_all_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    return [_result_line(r) for r in dispatch.collect_all(user)]


async def dispatch_collect_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        line = await run_db(_collect_sync, update.effective_user, int(query.data.split(":")[2]))
    except GameError as exc:
        await query.answer(alert_text(exc), show_alert=True)
        return
    await query.answer("🎁 گرفتی!")
    await _show_panel(update, note="🎉 <b>مأموریت تموم شد!</b>\n" + line)


async def dispatch_collect_all_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    lines = await run_db(_collect_all_sync, update.effective_user)
    if not lines:
        await query.answer("هنوز هیچ مأموریتی تموم نشده.", show_alert=True)
        return
    await query.answer("🎁 گرفتی!")
    await _show_panel(update, note="🎉 <b>جایزه‌ی مأموریت‌ها:</b>\n" + "\n".join(lines))


def register(application) -> None:
    gate = lambda h: hall_gated("dispatch", h)  # noqa: E731 — same main-hall gate as the menu button
    application.add_handler(CommandHandler("dispatch", gate(dispatch_panel), filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(gate(dispatch_home_callback), pattern=r"^dsp:home$"))
    application.add_handler(CallbackQueryHandler(gate(dispatch_offer_callback), pattern=r"^dsp:offer:\d+:\d+$"))
    application.add_handler(CallbackQueryHandler(gate(dispatch_pick_callback), pattern=r"^dsp:pick:\d+:\d+$"))
    application.add_handler(CallbackQueryHandler(gate(dispatch_go_callback), pattern=r"^dsp:go:\d+:\d+$"))
    application.add_handler(CallbackQueryHandler(dispatch_view_callback, pattern=r"^dsp:view:\d+$"))
    application.add_handler(CallbackQueryHandler(dispatch_cancel_callback, pattern=r"^dsp:cancel:\d+$"))
    application.add_handler(CallbackQueryHandler(dispatch_cancel_ok_callback, pattern=r"^dsp:cancelok:\d+$"))
    application.add_handler(CallbackQueryHandler(dispatch_collect_callback, pattern=r"^dsp:collect:\d+$"))
    application.add_handler(CallbackQueryHandler(dispatch_collect_all_callback, pattern=r"^dsp:collect_all$"))

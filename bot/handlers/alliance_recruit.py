"""Bot screens of game/alliance_recruit.py: finding an alliance (tabs, filters, pages), the
leaders' «جذب و دعوت» page (ad, «دنبال عضو», invites, invite link), the «صندوق نامه», and the
`/start ally_<code>` invite link."""

from __future__ import annotations

import html

from django.utils import timezone
from telegram import InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import CallbackQueryHandler, ContextTypes

from bio_lab.repository import get_or_create_user
from bot.buttons import CONFIRM, DANGER, NAV, PRIMARY, back_btn, btn
from bot.utils import alert_text, run_db, safe_edit_message_text
from game import alliance_recruit as R
from game.creature import GameError
from game.emoji import get_emoji

_DIV = "━━━━━━━━━━━━━━━━━━━━"
AWAITING_KEY = "awaiting_player_input"   # bot/handlers/private.py AWAITING_PLAYER_KEY
TABS = (("suggested", "پیشنهادی"), ("new", "تازه‌ها"), ("top", "برترین‌ها"))
JOINS = ("", "auto", "approval")
JOIN_FA = {"": "همه", "auto": "فوری", "approval": "با تأیید"}
SIZES = ("", "small", "medium", "large")
SIZE_FA = {"": "همه", "small": "کوچیک", "medium": "متوسط", "large": "بزرگ"}


def _e(s) -> str:
    return html.escape(str(s or ""))


def _when(dt) -> str:
    d = timezone.localtime(dt)
    return d.strftime("%m/%d %H:%M")


# ══════════════════════════ browse ══════════════════════════
def _state(context) -> dict:
    st = context.user_data.get("ally_browse")
    if not isinstance(st, dict):
        st = {"t": "suggested", "j": "", "z": "", "r": True, "c": False, "p": 0}
        context.user_data["ally_browse"] = st
    return st


def _browse_sync(tg_user, st: dict) -> dict:
    user, _ = get_or_create_user(tg_user)
    data = R.browse(user, st["t"], join=st["j"], size=st["z"], reach=st["r"], recruiting=st["c"], page=st["p"])
    data["in_alliance"] = user.alliance_id is not None
    data["unread"] = R.unread_count(user)
    return data


def _card_lines(i: int, c: dict) -> list[str]:
    tags = (" 🆕" if c["is_new"] else "") + (" 📣" if c["recruiting"] else "")
    mode = "🔓 ورود فوری" if c["auto_accept"] else "📝 با تأیید رهبر"
    if c["min_power"]:
        mode += f" · حداقل قدرت <code>{c['min_power']:,}</code>"
    out = [f"<b>{i}. {_e(c['name'])}</b>{tags}",
           f"👥 <code>{c['members']}/{c['capacity']}</code> · 🟢 <code>{c['active']}</code> فعال · 💪 میانگین <code>{c['avg_power']:,}</code>",
           mode]
    if c["note"]:
        out.append(f"<i>«{_e(c['note'])}»</i>")
    return out


def browse_render(data: dict, st: dict) -> tuple[str, InlineKeyboardMarkup]:
    tab_fa = dict(TABS)[st["t"]]
    lines = [f"🤝 <b>پیدا کردن اتحاد</b> · {tab_fa}", _DIV,
             f"<i>قدرت تو: {data['my_power']:,} · ورود: {JOIN_FA[st['j']]} · اندازه: {SIZE_FA[st['z']]}"
             f"{' · فقط قابل‌ورود' if st['r'] else ''}{' · فقط دنبال عضو' if st['c'] else ''}</i>", ""]
    if st["t"] == "suggested":
        lines.append("<i>اتحادهای زنده‌ای که جا دارن و به سطح تو می‌خورن — ترتیبش هر روز عوض می‌شه.</i>\n")
    rows = []
    if not data["cards"]:
        lines.append("هیچ اتحادی با این فیلترها پیدا نشد. فیلترها رو عوض کن یا با اسم جستجو کن.")
    base = data["page"] * data["page_size"]
    for i, c in enumerate(data["cards"], start=base + 1):
        lines += _card_lines(i, c) + [""]
        rows.append([btn(f"{i}. پیوستن به {c['name'][:24]}", emoji_key="btn_ally_join", style=PRIMARY,
                         callback_data=f"ally_browse_join:{c['id']}")])
    rows.append([btn(fa + (" ✔" if st["t"] == k else ""), emoji_key="btn_ally_tab", style=PRIMARY if st["t"] == k else NAV,
                     callback_data=f"ab:t:{k}") for k, fa in TABS])
    rows.append([btn(f"ورود: {JOIN_FA[st['j']]}", emoji_key="btn_filter", style=NAV, callback_data="ab:j"),
                 btn(f"اندازه: {SIZE_FA[st['z']]}", emoji_key="btn_filter", style=NAV, callback_data="ab:z")])
    # btn() drops a leading emoji (the premium icon takes its place): the state is spelled out
    rows.append([btn("فقط قابل‌ورود: " + ("روشن" if st["r"] else "خاموش"), emoji_key="btn_filter", style=PRIMARY if st["r"] else NAV, callback_data="ab:r"),
                 btn("دنبال عضو: " + ("روشن" if st["c"] else "خاموش"), emoji_key="btn_filter", style=PRIMARY if st["c"] else NAV, callback_data="ab:c")])
    nav = []
    if data["has_prev"]:
        nav.append(btn("قبلی", emoji_key="btn_prev", style=NAV, callback_data="ab:p:-1"))
    if data["has_next"]:
        nav.append(btn("بعدی", emoji_key="btn_next", style=NAV, callback_data="ab:p:1"))
    if nav:
        rows.append(nav)
    unread = f" ({data['unread']})" if data.get("unread") else ""
    rows.append([btn("جستجو با اسم", emoji_key="btn_search", style=NAV, callback_data="ally_search"),
                 btn(f"نامه‌ها{unread}", emoji_key="btn_inbox", style=NAV, callback_data="inbox")])
    rows.append([back_btn("menu:alliance_info", "بازگشت")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def browse_open(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """«پیوستن به اتحاد» (callback ally_join) and every tab / filter / page tap (ab:…)."""
    query = update.callback_query
    st = _state(context)
    parts = (query.data or "").split(":")
    if parts[0] == "ab":
        what = parts[1] if len(parts) > 1 else ""
        if what == "t" and len(parts) > 2 and parts[2] in dict(TABS):
            st.update(t=parts[2], p=0)
        elif what == "j":
            st.update(j=JOINS[(JOINS.index(st["j"]) + 1) % len(JOINS)], p=0)
        elif what == "z":
            st.update(z=SIZES[(SIZES.index(st["z"]) + 1) % len(SIZES)], p=0)
        elif what == "r":
            st.update(r=not st["r"], p=0)
        elif what == "c":
            st.update(c=not st["c"], p=0)
        elif what == "p" and len(parts) > 2:
            st["p"] = max(0, st["p"] + (1 if parts[2] == "1" else -1))
    else:
        st["p"] = 0
    await query.answer()
    data = await run_db(_browse_sync, update.effective_user, st)
    if data["in_alliance"]:
        await safe_edit_message_text(query, "تو الان عضو یه اتحادی. برای پیوستن به یکی دیگه، اول از اتحادت خارج شو.",
                                     reply_markup=InlineKeyboardMarkup([[back_btn("menu:alliance_info", "بازگشت به اتحاد")]]))
        return
    text, kb = browse_render(data, st)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=kb)


# ══════════════════════════ «صندوق نامه» ══════════════════════════
def _inbox_sync(tg_user) -> dict:
    user, _ = get_or_create_user(tg_user)
    return {"mails": R.inbox(user, 12), "blocked": user.block_alliance_invites}


def inbox_render(v: dict) -> tuple[str, InlineKeyboardMarkup]:
    lines = [f"{get_emoji('inbox')} <b>صندوق نامه</b>", _DIV]
    rows = []
    if not v["mails"]:
        lines.append("<i>هنوز نامه‌ای نداری. دعوت‌های اتحاد، جواب درخواست‌ها و پاداش‌ها اینجا میان.</i>")
    for m in v["mails"]:
        dot = "🔵" if m.read_at is None else "⚪"
        act = " · ⏳ منتظر جوابته" if (m.kind in ("ally_invite", "ally_request") and not m.done) else ""
        lines.append(f"{dot} <b>{_e(m.title)}</b> <i>({_when(m.created_at)})</i>{act}")
        rows.append([btn(f"{'🔵 ' if m.read_at is None else ''}{m.title[:34]}", emoji_key="btn_inbox", style=PRIMARY if act else NAV,
                         callback_data=f"mail:{m.id}")])
    rows.append([btn("دعوت اتحاد: " + ("بسته 🚫" if v["blocked"] else "باز ✅"), emoji_key="btn_settings", style=NAV,
                     callback_data="inbox_block")])
    rows.append([back_btn("menu:me", "منوی اصلی")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def inbox_open(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is not None:
        await query.answer()
    v = await run_db(_inbox_sync, update.effective_user)
    text, kb = inbox_render(v)
    if query is not None:
        await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=kb)
    else:
        await update.effective_message.reply_text(text, parse_mode="HTML", reply_markup=kb)


def _mail_sync(tg_user, mail_id: int):
    from bio_lab.models import Mail

    user, _ = get_or_create_user(tg_user)
    R.inbox(user, 30)   # refreshes «done» on invites that closed meanwhile
    m = Mail.objects.filter(id=mail_id, user=user).first()
    if m is None:
        raise GameError("این نامه پیدا نشد.")
    R.mark_read(user, m.id)
    return m


async def mail_open(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        m = await run_db(_mail_sync, update.effective_user, int(query.data.split(":")[1]))
    except GameError as exc:
        await query.answer(alert_text(exc), show_alert=True)
        return
    await query.answer()
    lines = [f"{get_emoji('mail')} <b>{_e(m.title)}</b>", f"<i>{_when(m.created_at)}</i>", _DIV, _e(m.body)]
    rows = []
    if not m.done and m.kind == "ally_invite":
        rows.append([btn("قبول دعوت", emoji_key="btn_confirm", style=CONFIRM, callback_data=f"ainv_ok:{m.data.get('invite')}"),
                     btn("رد", emoji_key="btn_cancel", style=DANGER, callback_data=f"ainv_no:{m.data.get('invite')}")])
    elif not m.done and m.kind == "ally_request":
        rows.append([btn("تأیید", emoji_key="btn_confirm", style=CONFIRM, callback_data=f"ally_approve:{m.data.get('request')}"),
                     btn("رد", emoji_key="btn_cancel", style=DANGER, callback_data=f"ally_reject:{m.data.get('request')}")])
    elif m.done and m.kind in ("ally_invite", "ally_request"):
        lines.append("\n<i>این مورد دیگه بسته شده.</i>")
    rows.append([back_btn("inbox", "بازگشت به نامه‌ها")])
    await safe_edit_message_text(query, "\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))


def _block_sync(tg_user) -> dict:
    user, _ = get_or_create_user(tg_user)
    R.set_invite_block(user, not user.block_alliance_invites)
    return _inbox_sync(tg_user)


async def inbox_block(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    v = await run_db(_block_sync, update.effective_user)
    await query.answer("دیگه دعوت اتحاد نمی‌گیری." if v["blocked"] else "دعوت‌های اتحاد دوباره باز شد.")
    text, kb = inbox_render(v)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=kb)


# ══════════════════════════ answering an invite ══════════════════════════
def _accept_sync(tg_user, invite_id: int) -> dict:
    from bio_lab.models import AllianceBonusLog

    user, _ = get_or_create_user(tg_user)
    alliance = R.accept_invite(user, invite_id)
    bonus = AllianceBonusLog.objects.filter(user=user, alliance=alliance).order_by("-id").first()
    return {"name": alliance.name, "bonus": bonus.coins if bonus and (timezone.now() - bonus.created_at).seconds < 60 else 0}


async def invite_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    kind, raw = query.data.split(":")
    try:
        if kind == "ainv_ok":
            out = await run_db(_accept_sync, update.effective_user, int(raw))
        else:
            await run_db(lambda: R.decline_invite(get_or_create_user(update.effective_user)[0], int(raw)))
            out = None
    except GameError as exc:
        await query.answer(alert_text(exc), show_alert=True)
        return
    await query.answer()
    if out is None:
        text = "دعوت رد شد."
    else:
        text = f"✅ <b>عضو اتحاد «{_e(out['name'])}» شدی!</b>"
        if out["bonus"]:
            text += f"\n🎁 پاداش رشد: <code>+{out['bonus']:,}</code> طلا (نصفش هم به خزانه‌ی اتحاد رفت)."
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup([
        [btn("اتحاد من", emoji_key="btn_alliance", style=PRIMARY, callback_data="menu:alliance_info")],
        [back_btn("inbox", "صندوق نامه")]]))


# ══════════════════════════ leaders: «جذب و دعوت» ══════════════════════════
def _manage_sync(tg_user) -> dict:
    user, _ = get_or_create_user(tg_user)
    return R.manage_view(user)


def manage_render(v: dict) -> tuple[str, InlineKeyboardMarkup]:
    from config import BOT_USERNAME

    link = f"https://t.me/{BOT_USERNAME}?start=ally_{v['code']}" if BOT_USERNAME else f"/start ally_{v['code']}"
    lines = [f"{get_emoji('ally_recruit')} <b>جذب و دعوت · {_e(v['alliance'].name)}</b>", _DIV,
             f"👥 اعضا: <code>{v['members']}/{v['capacity']}</code>",
             f"{get_emoji('ally_recruit')} دنبال عضو: <b>{'روشن' if v['recruiting'] else 'خاموش'}</b> <i>(روشن باشه، توی پیشنهادها بالاتر میای)</i>",
             f"✏️ آگهی: <i>{_e(v['note']) or '— هنوز ننوشتی'}</i>", "",
             f"{get_emoji('ally_link')} <b>لینک دعوت</b> (هر کی بازش کنه، بدون تأیید عضو می‌شه):\n<code>{_e(link)}</code>", "",
             f"{get_emoji('ally_invite')} دعوت‌های امروز: <code>{v['sent_today']}/{R.INVITE_DAILY}</code> · منتظر جواب: <code>{len(v['invites'])}</code>"]
    rows = [[btn("دعوت بازیکن", emoji_key="btn_ally_invite", style=PRIMARY, callback_data="ainv_new")],
            [btn("آگهی جذب", emoji_key="btn_ally_recruit", style=NAV, callback_data="arec_note"),
             btn("دنبال عضو: " + ("روشن" if v["recruiting"] else "خاموش"), emoji_key="btn_ally_recruit",
                 style=CONFIRM if v["recruiting"] else NAV, callback_data="arec_tog")],
            [btn("لینک جدید (قبلی باطل می‌شه)", emoji_key="btn_ally_link", style=NAV, callback_data="arec_link")]]
    for inv in v["invites"][:8]:
        who = inv["user"].lab_name or inv["user"].first_name or inv["user"].id
        lines.append(f"  ▫️ {_e(who)} <i>(تا {_when(inv['expires_at'])})</i>")
        rows.append([btn(f"لغو دعوت {str(who)[:20]}", emoji_key="btn_cancel", style=DANGER, callback_data=f"ainv_x:{inv['id']}")])
    if v["requests"]:
        rows.append([btn(f"درخواست‌های عضویت ({len(v['requests'])})", emoji_key="btn_members", style=PRIMARY, callback_data="ally_requests")])
    rows.append([back_btn("ally_settings", "بازگشت به تنظیمات")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def manage_open(update: Update, context: ContextTypes.DEFAULT_TYPE, note: str = "") -> None:
    query = update.callback_query
    data = query.data if query else ""
    try:
        if data == "arec_tog":
            def toggle():
                user = get_or_create_user(update.effective_user)[0]
                R.set_recruit(user, recruiting=not R._managed(user).recruiting)   # GameError unless leader / deputy
                return _manage_sync(update.effective_user)
            v = await run_db(toggle)
        elif data == "arec_link":
            def renew():
                user = get_or_create_user(update.effective_user)[0]
                R.invite_code(R._managed(user), renew=True)
                return _manage_sync(update.effective_user)
            v = await run_db(renew)
            note = f"{get_emoji('ally_link')} لینک جدید ساخته شد؛ لینک قبلی دیگه کار نمی‌کنه."
        elif data.startswith("ainv_x:"):
            def cancel():
                R.cancel_invite(get_or_create_user(update.effective_user)[0], int(data.split(":")[1]))
                return _manage_sync(update.effective_user)
            v = await run_db(cancel)
            note = "دعوت لغو شد."
        else:
            v = await run_db(_manage_sync, update.effective_user)
    except GameError as exc:
        if query:
            await query.answer(alert_text(exc), show_alert=True)
        return
    if query:
        await query.answer(note[:180] if note else None)
    text, kb = manage_render(v)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=kb)


async def ask_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """«دعوت بازیکن» / «آگهی جذب»: the next text message is the answer (private.capture_player_text_reply)."""
    query = update.callback_query
    if query.data == "ainv_new":
        context.user_data[AWAITING_KEY] = {"action": "ally_invite"}
        text = (f"{get_emoji('ally_invite')} <b>کی رو دعوت کنیم؟</b>\n\nیوزرنیمش (با @)، اسم آزمایشگاهش یا آیدی عددیش رو بفرست.\n"
                "<i>با قبول دعوت، بدون تأیید و بدون شرط حداقل قدرت عضو می‌شه.</i>")
    else:
        context.user_data[AWAITING_KEY] = {"action": "ally_note"}
        text = (f"✏️ <b>آگهی جذب اتحاد</b>\n\nیه جمله تا {R.NOTE_MAX} حرف بفرست که روی کارت اتحادت دیده بشه "
                "(مثلاً «جنگ هفتگی رو جدی می‌گیریم، هر روز رید»).\nبرای پاک کردن آگهی یه «-» بفرست.")
    await query.answer()
    await safe_edit_message_text(query, text, parse_mode="HTML",
                                 reply_markup=InlineKeyboardMarkup([[back_btn("arec", "انصراف")]]))


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE, awaiting: dict, text: str) -> None:
    message = update.effective_message
    back = InlineKeyboardMarkup([[btn("جذب و دعوت", emoji_key="btn_ally_recruit", style=NAV, callback_data="arec")]])
    try:
        if awaiting["action"] == "ally_invite":
            inv = await run_db(lambda: R.invite(get_or_create_user(update.effective_user)[0], text))
            who = inv.user.lab_name or inv.user.first_name or inv.user.id
            await message.reply_text(f"✅ دعوت برای <b>{_e(who)}</b> فرستاده شد. توی صندوق نامه‌ش می‌بینه و ۷۲ ساعت وقت داره جواب بده.",
                                     parse_mode="HTML", reply_markup=back)
        else:
            note = "" if text.strip() in ("-", "—") else text
            await run_db(lambda: R.set_recruit(get_or_create_user(update.effective_user)[0], note=note))
            await message.reply_text("✅ آگهی جذب ذخیره شد." if note else "آگهی جذب پاک شد.", reply_markup=back)
    except GameError as exc:
        context.user_data[AWAITING_KEY] = awaiting   # let them try again
        await message.reply_text(alert_text(exc, 3500), reply_markup=back)


# ══════════════════════════ the invite link (/start ally_<code>) ══════════════════════════
def _link_sync(tg_user, code: str):
    user, _ = get_or_create_user(tg_user)
    inv = R.use_link(user, code)
    started = bool(user.lab_name)
    return inv, started, inv.alliance, user.alliance_id


async def start_link(update: Update, context: ContextTypes.DEFAULT_TYPE, code: str) -> bool:
    """Handles `/start ally_<code>`. True = answered; False = a brand-new player: the invite
    waits in their inbox and the normal /start (onboarding) goes on."""
    try:
        inv, started, alliance, current = await run_db(_link_sync, update.effective_user, code)
    except GameError as exc:
        await update.effective_message.reply_text(alert_text(exc, 3500))
        return True
    if not started:
        return False
    text = f"{get_emoji('ally_invite')} <b>دعوت به اتحاد «{_e(alliance.name)}»</b>\n\nبا قبول کردن، همین الان عضو می‌شی (بدون تأیید رهبر)."
    if alliance.recruit_note:
        text += f"\n<i>«{_e(alliance.recruit_note)}»</i>"
    if current:
        text += "\n\n⚠️ تو الان عضو یه اتحادی؛ برای قبول این دعوت اول باید ازش خارج شی."
    await update.effective_message.reply_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup([
        [btn("قبول دعوت", emoji_key="btn_confirm", style=CONFIRM, callback_data=f"ainv_ok:{inv.id}"),
         btn("رد", emoji_key="btn_cancel", style=DANGER, callback_data=f"ainv_no:{inv.id}")]]))
    return True


# ══════════════════════════ DM of a letter (outbox kind "dm") ══════════════════════════
async def send_mail_dm(context: ContextTypes.DEFAULT_TYPE, payload: dict) -> None:
    rows = [[btn(lbl, style=CONFIRM if cb.startswith(("ainv_ok", "ally_approve")) else DANGER if cb.startswith(("ainv_no", "ally_reject")) else NAV,
                 callback_data=cb) for lbl, cb in row] for row in payload.get("buttons") or []]
    rows.append([btn("صندوق نامه", emoji_key="btn_inbox", style=NAV, callback_data="inbox")])
    text = f"{get_emoji('inbox')} <b>{_e(payload.get('title'))}</b>\n\n{_e(payload.get('body'))}"
    try:
        await context.bot.send_message(chat_id=payload["user_id"], text=text, parse_mode="HTML",
                                       reply_markup=InlineKeyboardMarkup(rows))
    except TelegramError:
        pass


def register(application) -> None:
    application.add_handler(CallbackQueryHandler(browse_open, pattern=r"^(ally_join|ab:.*)$"))
    application.add_handler(CallbackQueryHandler(inbox_open, pattern=r"^inbox$"))
    application.add_handler(CallbackQueryHandler(mail_open, pattern=r"^mail:\d+$"))
    application.add_handler(CallbackQueryHandler(inbox_block, pattern=r"^inbox_block$"))
    application.add_handler(CallbackQueryHandler(invite_answer, pattern=r"^ainv_(ok|no):\d+$"))
    application.add_handler(CallbackQueryHandler(manage_open, pattern=r"^(arec|arec_tog|arec_link|ainv_x:\d+)$"))
    application.add_handler(CallbackQueryHandler(ask_text, pattern=r"^(ainv_new|arec_note)$"))

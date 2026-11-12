"""Group & social growth features — the Telegram side.

* group words (game/group_growth.py): «ثبت پایگاه» / «حذف پایگاه» (any alliance member, unless
  a group admin typed «قفل پایگاه» — «باز کردن پایگاه» undoes it), «اتحاد گروه» (the card
  with a join button), «افزودن ربات» (the add-to-group link)
* «لیگ گروه‌ها» — the weekly table of group PvP wins (game/group_war.py, no prizes)
* «جنگ اتحاد» / «جنگ گروه» / «چالش گروه» — the group war IS the alliance war: in an
  alliance's home group it shows that war, and the leader/deputy starts one from there
* buttons: ch:new / ch:go:<code> / ch:mv:<run>:<move> (friend challenge)
"""

from __future__ import annotations

import html
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import CallbackQueryHandler, ContextTypes

from bio_lab.repository import get_or_create_group, get_or_create_user
from bot.buttons import BATTLE, CONFIRM, back_btn, btn
from bot.utils import alert_text, run_db, send_screen
from game import challenge, constants, group_growth, group_war
from game.creature import GameError

log = logging.getLogger(__name__)

# whole message, spaces dropped (keywords.normalize first) → action
WORDS = {
    **{w: "ally_link" for w in ("ثبتاتحاد", "ثبتپایگاه", "ثبتپایگاهاتحاد")},
    **{w: "ally_unlink" for w in ("حذفپایگاه", "حذفپایگاهاتحاد")},
    **{w: "base_lock" for w in ("قفلپایگاه", "قفلپایگاهاتحاد")},
    **{w: "base_unlock" for w in ("بازکردنپایگاه", "بازکردنقفلپایگاه", "آزادکردنپایگاه", "قفلپایگاهبرداشتهبشه")},
    **{w: "ally_card" for w in ("اتحادگروه", "پایگاهاتحاد", "اتحاداینجا")},
    **{w: "league" for w in ("لیگگروه", "لیگگروهها", "جدولگروهها", "رتبهگروه")},
    # the group war is the alliance war now (owner, 2026-11-13): in an alliance's home group
    # these show it, and «چالش گروه» from the leader/deputy starts one
    **{w: "war" for w in ("جنگگروه", "جنگگروهها", "وضعیتجنگ", "جنگاتحاد")},
    **{w: "war_challenge" for w in ("چالشگروه", "چالشگروهی", "شروعجنگ")},
    **{w: "add_bot" for w in ("افزودنربات", "اضافهکردنربات", "رباتتوگروهت")},
}


def parse(norm: str) -> tuple[str, str | None] | None:
    key = norm.replace(" ", "")
    if key in WORDS:
        return WORDS[key], None
    return None


def _e(s) -> str:
    return html.escape(str(s or ""))


def add_group_url() -> str:
    from config import BOT_USERNAME

    return f"https://t.me/{BOT_USERNAME}?startgroup=add"


async def _is_admin(bot, chat_id: int, user_id: int) -> bool:
    try:
        m = await bot.get_chat_member(chat_id, user_id)
    except TelegramError:
        return False
    return m.status in ("administrator", "creator")


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str, arg: str | None) -> None:
    chat, tg = update.effective_chat, update.effective_user
    msg = update.effective_message
    try:
        if action == "ally_link":
            admin = await _is_admin(context.bot, chat.id, tg.id)
            al, locked = await run_db(_ally_link_sync, chat, tg, admin)
            lines = [
                f"🏰 <b>اینجا پایگاه اتحاد «{_e(al.name)}» شد!</b>",
                "━━━━━━━━━━",
                "🐲 باس رید اتحاد فقط همینجا زده می‌شه",
                "⚔️ جنگ اتحاد با «جنگ اتحاد» دیده می‌شه",
                "      و نتیجه‌اش همینجا اعلام می‌شه",
                "🤝 بقیه با «اتحاد گروه» عضو می‌شن",
            ]
            if not locked:
                lines += ["━━━━━━━━━━",
                          "🔒 <i>ادمین‌های گروه با «قفل پایگاه» می‌تونن ثبت و حذف پایگاه رو فقط برای خودشون نگه دارن.</i>"]
            await msg.reply_text("\n".join(lines), parse_mode="HTML")
        elif action == "ally_unlink":
            admin = await _is_admin(context.bot, chat.id, tg.id)
            al = await run_db(_ally_unlink_sync, chat, tg, admin)
            await msg.reply_text(f"🏚 این گروه دیگه پایگاه اتحاد «{_e(al.name)}» نیست.")
        elif action in ("base_lock", "base_unlock"):
            admin = await _is_admin(context.bot, chat.id, tg.id)
            await run_db(_base_lock_sync, chat, admin, action == "base_lock")
            if action == "base_lock":
                await msg.reply_text(
                    "🔒 <b>پایگاه قفل شد</b>\n"
                    "از این به بعد فقط ادمین‌های گروه می‌تونن «ثبت پایگاه» و «حذف پایگاه» بزنن.\n"
                    "<i>برای برداشتن قفل: «باز کردن پایگاه»</i>", parse_mode="HTML")
            else:
                await msg.reply_text(
                    "🔓 <b>قفل پایگاه برداشته شد</b>\n"
                    "هر عضو یه اتحاد می‌تونه «ثبت پایگاه» یا «حذف پایگاه» بزنه.", parse_mode="HTML")
        elif action == "ally_card":
            card = await run_db(_ally_card_sync, chat)
            if card is None:
                await msg.reply_text(
                    "🏰 این گروه هنوز پایگاه هیچ اتحادی نیست.\n"
                    "<i>هر عضو یه اتحاد با «ثبت پایگاه» اینجا رو گروه اتحادش می‌کنه.</i>",
                    parse_mode="HTML")
                return
            await msg.reply_text(
                f"🏰 <b>پایگاه اتحاد «{_e(card['name'])}»</b>\n"
                f"👥 اعضا: <b>{card['members']}</b> · توی این گروه: <b>{card['present']}</b>\n"
                "🐲 باس رید این اتحاد فقط همینجا زده می‌شه"
                + ("\n🔒 ثبت و حذف پایگاه: فقط ادمین‌ها" if card["locked"] else ""),
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🤝 پیوستن به اتحاد", url=card["join_url"])]]))
        elif action == "league":
            text = await run_db(_league_text_sync, chat)
            await msg.reply_text(text, parse_mode="HTML")
        elif action in ("war", "war_challenge"):
            text, kb, started = await run_db(_ally_war_sync, chat, tg, action == "war_challenge")
            await msg.reply_text(text, parse_mode="HTML", reply_markup=kb)
            if started is not None:   # tell the other alliance's home group too
                foe_home, foe_text = started
                if foe_home:
                    try:
                        await context.bot.send_message(foe_home, foe_text, parse_mode="HTML")
                    except TelegramError:
                        pass
        elif action == "add_bot":
            await msg.reply_text(
                "➕ <b>ربات رو به یه گروه شلوغ اضافه کن و جایزه بگیر!</b>\n"
                + "\n".join(f"• {need} بازیکن واقعی ← {_reward_text(r)}" for need, r in group_growth.ADD_STEPS)
                + "\n<i>بازیکن واقعی: توی اون گروه بازی کرده، سطح ۵+ و حداقل ۳ روز جدا بازی کرده.</i>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➕ افزودن به گروه", url=add_group_url())]]))
    except GameError as exc:
        await msg.reply_text(alert_text(exc, 900), parse_mode="HTML")


def _reward_text(r):
    from game.referral import reward_text

    return reward_text(r)


def _ally_link_sync(chat, tg, admin):
    user, _ = get_or_create_user(tg)
    group = get_or_create_group(chat)
    return group_growth.link_home(user, group, admin), group.base_locked


def _ally_unlink_sync(chat, tg, admin):
    user, _ = get_or_create_user(tg)
    return group_growth.unlink_home(user, get_or_create_group(chat), admin)


def _base_lock_sync(chat, admin, locked):
    group_growth.set_base_lock(get_or_create_group(chat), admin, locked)


def _ally_card_sync(chat):
    return group_growth.home_card(get_or_create_group(chat))


def _league_text_sync(chat):
    rank, pts = group_war.group_rank(chat.id)
    top = group_war.league_top(10)
    lines = ["🏆 <b>لیگ گروه‌های این هفته</b>", "━━━━━━━━━━"]
    if not top:
        lines.append("<i>این هفته هنوز هیچ گروهی امتیاز نگرفته — توی گروه اتک بدید!</i>")
    for i, r in enumerate(top, 1):
        me = " 📍" if r["group_id"] == chat.id else ""
        lines.append(f"{i}. {_e(r['title'])} · <b>{r['points']:,}</b>{me}")
    lines += ["━━━━━━━━━━",
              f"📍 این گروه: {'رتبه ' + str(rank) + ' · ' + format(pts, ',') + ' امتیاز' if rank else 'هنوز امتیازی نداره'}",
              f"<i>هر برد اتک توی گروه {group_war.PVP_WIN_POINTS} امتیاز</i>"]
    return "\n".join(lines)


def _ally_war_sync(chat, tg, start: bool):
    """«جنگ اتحاد» / «چالش گروه» in a group — the group war is the alliance war now: in an
    alliance's home group it shows that alliance's one-day war, and the leader or deputy
    starts one with «چالش گروه». Returns (text, keyboard, started) where started is
    (foe_home_group_id, announcement) when a war was just started."""
    from config import BOT_USERNAME
    from game import alliance as A

    group = get_or_create_group(chat)
    al = group_growth.home_alliance(group.id)
    open_btn = InlineKeyboardMarkup([[InlineKeyboardButton(
        "⚔️ شرکت در جنگ (پیوی)", url=f"https://t.me/{BOT_USERNAME}?start=allywar")]])
    if al is None:
        return ("⚔️ این گروه پایگاه هیچ اتحادی نیست.\n"
                "<i>جنگ‌ها بین اتحادهاست: هر عضو یه اتحاد با «ثبت پایگاه» اینجا رو گروه اتحادش می‌کنه.</i>",
                None, None)
    started = None
    if start:
        user, _ = get_or_create_user(tg)
        if user.alliance_id != al.id or user.id not in (al.leader_id, al.deputy_id):
            raise GameError("فقط رهبر یا معاون همین اتحاد می‌تونه جنگ رو شروع کنه.")
        war = A.start_war(user)
        foe = war.alliance_b
        started = (foe.home_group_id,
                   f"⚔️ <b>جنگ اتحاد شروع شد!</b>\nاتحاد «{_e(al.name)}» به اتحاد شما «{_e(foe.name)}» حمله کرد.\n"
                   f"⏳ {A.WAR_DURATION_HOURS} ساعت · همه با «شرکت در جنگ» امتیاز بسازید!")
    war = A.active_war_for(al.id)
    if war is None:
        return (f"⚔️ <b>جنگ اتحاد «{_e(al.name)}»</b>\n"
                "الان توی جنگ نیستید.\n"
                f"<i>رهبر یا معاون با «چالش گروه» یه حریف هم‌قدرت پیدا می‌کنه و {A.WAR_DURATION_HOURS} ساعت می‌جنگید.</i>",
                None, None)
    mine_a = war.alliance_a_id == al.id
    me, foe = (war.alliance_a, war.alliance_b) if mine_a else (war.alliance_b, war.alliance_a)
    my_score, foe_score = (war.score_a, war.score_b) if mine_a else (war.score_b, war.score_a)
    from django.utils import timezone

    left = max(0, int((war.ends_at - timezone.now()).total_seconds()))
    lead = "🟢 جلوییم" if my_score > foe_score else ("🔴 عقبیم" if my_score < foe_score else "🟡 مساوی")
    text = "\n".join([
        ("⚔️ <b>جنگ اتحاد شروع شد!</b>" if started else "⚔️ <b>جنگ اتحاد در جریانه</b>"),
        f"«{_e(me.name)}» ⚔️ «{_e(foe.name)}»",
        "━━━━━━━━━━",
        f"📊 ما: <b>{my_score:,}</b> · حریف: <b>{foe_score:,}</b>",
        f"{lead} · ⏳ {left // 3600} ساعت و {left % 3600 // 60} دقیقه مونده",
        "━━━━━━━━━━",
        "<i>هر عضو یه بار با «شرکت در جنگ» قدرتش رو اضافه می‌کنه و جایزه می‌گیره.</i>",
    ])
    return text, open_btn, started


# ── friend challenge ──────────────────────────────────────────────────────────
def _create_sync(tg):
    user, _ = get_or_create_user(tg)
    ch = challenge.create(user)
    return ch


async def challenge_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is not None:
        await query.answer()
    try:
        ch = await run_db(_create_sync, update.effective_user)
    except GameError as exc:
        await update.effective_message.reply_text(alert_text(exc, 900))
        return
    from game.referral import reward_text

    text = (
        "🥊 <b>چالش رفیق</b>\n"
        f"<blockquote>لینکت رو بفرست — هر کی بازش کنه با یه <b>کایجوی مهمان</b> با «{_e(ch.creature_name)}»ت "
        f"(قدرت {ch.power:,}) سه راند می‌جنگه. حتی کسی که هنوز بازی رو شروع نکرده!</blockquote>\n"
        f"🎁 هر بازیکن تازه‌ای که چالشت رو انجام بده: {reward_text(challenge.SHARER_REWARD)} برای تو "
        f"(روزی تا {challenge.SHARER_DAILY_CAP} نفر)\n"
        "🤝 تازه‌واردها با این لینک «دعوت‌شده‌ی تو» هم حساب می‌شن — همه‌ی جایزه‌های رشدشون مال توئه.\n"
        f"⏳ لینک {challenge.LINK_HOURS} ساعت اعتبار داره.\n\n"
        f"🔗 <code>{challenge.link_for(ch.code)}</code>"
    )
    kb = InlineKeyboardMarkup([
        [btn("فرستادن چالش", emoji_key="btn_referral", style=CONFIRM, url=challenge.share_url(ch.code, ch.creature_name))],
        [back_btn("menu:hub_battle", "بازگشت به نبرد")],
    ])
    await send_screen(update, text, photo=None, parse_mode="HTML", reply_markup=kb)


def _intro_sync(tg, code):
    from game import referral

    user, created = get_or_create_user(tg)
    ch = challenge.get(code)
    if ch is None:
        return None, None
    referral.register_referral(user, created, ch.challenger_id)   # a challenge link is an invite link too
    from bio_lab.repository import lab_display

    return ch, lab_display(ch.challenger)


async def challenge_intro(update: Update, context: ContextTypes.DEFAULT_TYPE, code: str) -> bool:
    """`/start duel_<code>` — show who challenges and the button to fight. True = handled."""
    ch, who = await run_db(_intro_sync, update.effective_user, code)
    if ch is None:
        await update.effective_message.reply_text("این لینک چالش پیدا نشد یا منقضی شده. 🤔")
        return True
    text = (f"🥊 <b>{who} بهت چالش داده!</b>\n"
            f"حریف: «{_e(ch.creature_name)}» — عنصر {constants.ELEMENT_WORDS.get(ch.element, ch.element)}، قدرت {ch.power:,}\n\n"
            f"🦖 بهت یه <b>کایجوی مهمان</b> قوی‌تر (قدرت {int(ch.power * challenge.GUEST_POWER):,}) می‌دیم.\n"
            "سه راند — هر راند یه حرکت انتخاب کن:\n"
            "⚔️ سنگین ← ⚡ عنصری رو می‌بره · ⚡ عنصری ← 🛡 دفاع رو · 🛡 دفاع ← ⚔️ سنگین رو\n"
            "<i>💡 به رفتار حریف دقت کن — معمولاً لو می‌ده چی می‌خواد بزنه!</i>")
    await update.effective_message.reply_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup([
        [btn("شروع نبرد", emoji_key="btn_worldboss", style=BATTLE, callback_data=f"ch:go:{code}")]]))
    return True


def _go_sync(tg, code):
    user, _ = get_or_create_user(tg)
    run = challenge.start(user, code)
    return run.id, challenge.view(run)


def _round_text(v: dict, last: dict | None = None) -> str:
    lines = [f"🥊 <b>چالش · راند {min(v['round'] + 1, v['rounds'])}/{v['rounds']}</b>"]
    if last:
        me, foe = challenge.MOVES[last["me"]], challenge.MOVES[last["foe"]]
        res = {"W": "✅ این راند رو بردی!", "L": "❌ این راند رو باختی!", "T": "⚖️ مساوی!"}[last["result"]]
        lines += [f"تو: {me[0]} {me[1]}", f"حریف: {foe[0]} {foe[1]}", res]
    lines += [f"🦖 مهمان تو: ❤️ <b>{v['my_hp']}</b>", f"👹 «{_e(v['foe_name'])}»: ❤️ <b>{v['foe_hp']}</b>"]
    if v["hint"] and not v["finished"]:
        lines.append(f"\n{v['hint']}\n<b>حرکتت رو انتخاب کن:</b>")
    return "\n".join(lines)


def _moves_kb(run_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(f"{e} {label}", callback_data=f"ch:mv:{run_id}:{key}")
        for key, (e, label) in challenge.MOVES.items()
    ]])


async def challenge_go(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    code = query.data.split(":", 2)[2]
    try:
        run_id, v = await run_db(_go_sync, update.effective_user, code)
    except GameError as exc:
        await query.answer(alert_text(exc), show_alert=True)
        return
    await query.answer()
    if v["finished"]:
        await query.answer("این چالش رو قبلاً انجام دادی!", show_alert=True)
        return
    await query.edit_message_text(_round_text(v), parse_mode="HTML", reply_markup=_moves_kb(run_id))


def _move_sync(tg, run_id, move):
    user, _ = get_or_create_user(tg)
    res = challenge.play(user, run_id, move)
    started = bool(user.lab_name)
    return res, challenge.view(res["run"]), started


async def challenge_move(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, _, run_id, move = query.data.split(":")
    try:
        res, v, started = await run_db(_move_sync, update.effective_user, int(run_id), move)
    except GameError as exc:
        await query.answer(alert_text(exc), show_alert=True)
        return
    await query.answer()
    last = v["history"][-1] if v["history"] else None
    if not res["finished"]:
        await query.edit_message_text(_round_text(v, last), parse_mode="HTML", reply_markup=_moves_kb(int(run_id)))
        return
    text = _round_text(v, last) + "\n\n"
    text += ("🏆 <b>بردی! کایجوی رفیقت رو شکست دادی!</b>" if res["won"] else "💀 <b>این بار باختی!</b> ولی خوب جنگیدی.")
    if res.get("reward_text"):
        text += f"\n🎁 جایزه‌ات: {res['reward_text']}"
    rows = []
    if not started:
        text += "\n\n🦖 <b>حالا وقتشه هیولای خودت رو بگیری!</b> روی /start بزن — یه تقویم ۷ روزه‌ی هدیه هم منتظرته. 🎁"
    else:
        rows.append([btn("چالش خودت رو بساز", emoji_key="btn_referral", style=CONFIRM, callback_data="ch:new")])
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows) if rows else None)


def register(application) -> None:
    application.add_handler(CallbackQueryHandler(challenge_panel, pattern=r"^ch:new$"))
    application.add_handler(CallbackQueryHandler(challenge_go, pattern=r"^ch:go:[0-9a-f]{4,12}$"))
    application.add_handler(CallbackQueryHandler(challenge_move, pattern=r"^ch:mv:\d+:(heavy|guard|element)$"))

"""Group & social growth features — the Telegram side.

* group words: «ثبت اتحاد» / «حذف پایگاه» / «اتحاد گروه» / «پاداش اتحاد» (game/group_growth.py),
  «باس» (game/group_boss.py), «لیگ گروه‌ها» / «جنگ گروه» / «چالش گروه» / «قبول چالش 1234»
  (game/group_war.py), «افزودن ربات» (the add-to-group link)
* the group-boss job: posts new bosses, settles dead / escaped ones (every BOSS_TICK seconds)
* buttons: gb:hit:<boss> (group boss), ch:new / ch:go:<code> / ch:mv:<run>:<move> (friend challenge)
"""

from __future__ import annotations

import html
import logging
import re
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import CallbackQueryHandler, ContextTypes

from bio_lab.repository import get_or_create_group, get_or_create_user
from bot.buttons import BATTLE, CONFIRM, NAV, back_btn, btn
from bot.utils import alert_text, run_db, send_screen
from game import challenge, constants, group_boss, group_growth, group_war
from game.creature import GameError

log = logging.getLogger(__name__)
BOSS_TICK = 120

# whole message, spaces dropped (keywords.normalize first) → action
WORDS = {
    **{w: "ally_link" for w in ("ثبتاتحاد", "ثبتپایگاه", "ثبتپایگاهاتحاد")},
    **{w: "ally_unlink" for w in ("حذفپایگاه", "حذفپایگاهاتحاد")},
    **{w: "ally_card" for w in ("اتحادگروه", "پایگاهاتحاد", "اتحاداینجا")},
    **{w: "ally_claim" for w in ("پاداشاتحاد", "پاداشپایگاه")},
    **{w: "boss" for w in ("باس", "باسگروه", "غولگروه")},
    **{w: "league" for w in ("لیگگروه", "لیگگروهها", "جدولگروهها", "رتبهگروه")},
    **{w: "war" for w in ("جنگگروه", "جنگگروهها", "وضعیتجنگ")},
    **{w: "war_challenge" for w in ("چالشگروه", "چالشگروهی")},
    **{w: "add_bot" for w in ("افزودنربات", "اضافهکردنربات", "رباتتوگروهت")},
}
_ACCEPT = re.compile(r"^قبول\s*چالش\s*(\d{4})$")


def parse(norm: str) -> tuple[str, str | None] | None:
    key = norm.replace(" ", "")
    if key in WORDS:
        return WORDS[key], None
    m = _ACCEPT.match(norm)
    if m:
        return "war_accept", m.group(1)
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
            al = await run_db(_ally_link_sync, chat, tg, admin)
            await msg.reply_text(
                f"🏰 <b>اینجا پایگاه اتحاد «{_e(al.name)}» شد!</b>\n"
                "• اعضای اتحاد که توی این گروه‌ان، هر روز با «پاداش اتحاد» جایزه می‌گیرن — هرچی بیشتر از اتحاد اینجا باشن، بیشتر.\n"
                "• نتیجه‌ی جنگ‌های اتحاد همینجا اعلام می‌شه.\n"
                "• بقیه با «اتحاد گروه» دکمه‌ی پیوستن رو می‌بینن.", parse_mode="HTML")
        elif action == "ally_unlink":
            al = await run_db(_ally_unlink_sync, chat, tg)
            await msg.reply_text(f"🏚 این گروه دیگه پایگاه اتحاد «{_e(al.name)}» نیست.")
        elif action == "ally_card":
            card = await run_db(_ally_card_sync, chat)
            if card is None:
                await msg.reply_text(
                    "🏰 این گروه هنوز پایگاه هیچ اتحادی نیست.\n"
                    "<i>رهبر یا معاون یه اتحاد (که ادمین این گروه هم باشه) با «ثبت اتحاد» اینجا رو پایگاه می‌کنه.</i>",
                    parse_mode="HTML")
                return
            from game.referral import reward_text

            await msg.reply_text(
                f"🏰 <b>پایگاه اتحاد «{_e(card['name'])}»</b>\n"
                f"👥 اعضا: <b>{card['members']}</b> · توی این گروه: <b>{card['present']}</b>\n"
                f"🎁 «پاداش اتحاد» امروز برای هر عضو: {reward_text(card['reward'])}\n"
                "<i>هرچی اعضای بیشتری از اتحاد توی گروه باشن، پاداش بیشتره.</i>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🤝 پیوستن به اتحاد", url=card["join_url"])]]))
        elif action == "ally_claim":
            res = await run_db(_ally_claim_sync, chat, tg)
            from game.referral import reward_text

            await msg.reply_text(
                f"🏰 <b>پاداش پایگاه «{_e(res['alliance'])}»</b>: {reward_text(res['reward'])}\n"
                f"<i>{res['present']} نفر از اتحاد توی این گروه‌ان — رفیقای اتحادت رو بیار تا فردا بیشتر بگیری!</i>",
                parse_mode="HTML")
        elif action == "boss":
            await _boss_word(update, context)
        elif action == "league":
            text = await run_db(_league_text_sync, chat)
            await msg.reply_text(text, parse_mode="HTML")
        elif action == "war":
            text = await run_db(_war_status_sync, chat)
            await msg.reply_text(text, parse_mode="HTML")
        elif action == "war_challenge":
            admin = await _is_admin(context.bot, chat.id, tg.id)
            war = await run_db(_war_challenge_sync, chat, tg, admin)
            await msg.reply_text(
                f"⚔️ <b>چالش جنگ گروه‌ها ساخته شد!</b>\n"
                f"کد: <code>{war.code}</code>\n"
                f"این پیام رو برای ادمین یه گروه دیگه بفرست تا توی گروهش بنویسه:\n<code>قبول چالش {war.code}</code>\n"
                f"<i>کد تا {group_war.CHALLENGE_TTL_MIN} دقیقه اعتبار داره. جنگ {group_war.WAR_HOURS} ساعت طول می‌کشه؛ "
                "امتیاز از کشتن باس گروه و بردن اتک‌ها توی گروه میاد.</i>", parse_mode="HTML")
        elif action == "war_accept":
            admin = await _is_admin(context.bot, chat.id, tg.id)
            war = await run_db(_war_accept_sync, chat, tg, admin, arg)
            start = (f"⚔️ <b>جنگ گروه‌ها شروع شد!</b>\n«{_e(war.group_a.title)}» 🆚 «{_e(war.group_b.title)}»\n"
                     f"⏳ {group_war.WAR_HOURS} ساعت — امتیاز: کشتن باس گروه و آسیب بهش، و هر برد اتک توی گروه.\n"
                     "<i>وضعیت جنگ: «جنگ گروه»</i>")
            await msg.reply_text(start, parse_mode="HTML")
            try:
                await context.bot.send_message(war.group_a_id, start, parse_mode="HTML")
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
        await msg.reply_text(alert_text(exc, 900))


def _reward_text(r):
    from game.referral import reward_text

    return reward_text(r)


def _ally_link_sync(chat, tg, admin):
    user, _ = get_or_create_user(tg)
    return group_growth.link_home(user, get_or_create_group(chat), admin)


def _ally_unlink_sync(chat, tg):
    user, _ = get_or_create_user(tg)
    return group_growth.unlink_home(user, get_or_create_group(chat))


def _ally_card_sync(chat):
    return group_growth.home_card(get_or_create_group(chat))


def _ally_claim_sync(chat, tg):
    user, _ = get_or_create_user(tg)
    return group_growth.claim_base(user, get_or_create_group(chat))


def _league_text_sync(chat):
    rank, pts = group_war.group_rank(chat.id)
    top = group_war.league_top(10)
    lines = ["🏆 <b>لیگ گروه‌های این هفته</b>"]
    if not top:
        lines.append("<i>این هفته هنوز هیچ گروهی امتیاز نگرفته — باس گروه رو بکشید و اتک بدید!</i>")
    for i, r in enumerate(top, 1):
        me = " ← <b>ما</b>" if r["group_id"] == chat.id else ""
        lines.append(f"{i}. {_e(r['title'])} — <b>{r['points']:,}</b>{me}")
    lines.append(f"\n📍 این گروه: {'رتبه ' + str(rank) + ' با ' + format(pts, ',') + ' امتیاز' if rank else 'هنوز امتیازی نداره'}")
    p = group_war.WEEK_PRIZES
    lines.append(f"<i>دوشنبه، سه گروه اول: هر بازیکن فعال 🥇 {_reward_text(p[0])} · 🥈 {_reward_text(p[1])} · 🥉 {_reward_text(p[2])}</i>")
    lines.append(f"<i>امتیاز: کشتن باس گروه +{group_boss.KILL_POINTS} (و آسیب بهش)، هر برد اتک توی گروه +{group_war.PVP_WIN_POINTS}</i>")
    return "\n".join(lines)


def _war_status_sync(chat):
    war = group_war.active_war(chat.id)
    if war is None:
        return ("⚔️ این گروه الان توی جنگ نیست.\n"
                "<i>یه ادمین با «چالش گروه» کد می‌گیره و ادمین یه گروه دیگه با «قبول چالش کد» جنگ رو شروع می‌کنه.</i>")
    return group_war.war_text(war, chat.id)


def _war_challenge_sync(chat, tg, admin):
    user, _ = get_or_create_user(tg)
    return group_war.create_challenge(get_or_create_group(chat), user, admin)


def _war_accept_sync(chat, tg, admin, code):
    user, _ = get_or_create_user(tg)
    return group_war.accept(code, get_or_create_group(chat), user, admin)


# ── group boss ────────────────────────────────────────────────────────────────
def _bar(hp: int, max_hp: int) -> str:
    filled = round(10 * hp / max_hp) if max_hp else 0
    return "■" * filled + "□" * (10 - filled)


def boss_text(b, top: list[dict], fighters: int) -> str:
    from django.utils import timezone

    pct = round(100 * b.hp / b.max_hp) if b.max_hp else 0
    left = max(0, int((b.ends_at - timezone.now()).total_seconds() // 60))
    lines = [
        f"👹 <b>باس گروه: {_e(b.name)}</b> — عنصر {constants.ELEMENT_WORDS.get(b.element, b.element)}",
        f"❤️ [{_bar(b.hp, b.max_hp)}] {pct}% — <code>{b.hp:,}</code> / <code>{b.max_hp:,}</code>",
        f"⏳ تا <b>{left}</b> دقیقه دیگه فرار می‌کنه",
        f"⚔️ هر نفر <b>{group_boss.HITS}</b> ضربه‌ی رایگان — عنصر قوی‌تر ۲۰٪ بیشتر می‌زنه!",
    ]
    if top:
        lines.append("🏅 " + " · ".join(f"{r['name']} {r['damage']:,}" for r in top[:3]))
    lines.append(f"👥 {fighters} نفر تا الان جنگیدن")
    lines.append("<i>بکشیدش تا همه جایزه بگیرن؛ سه نفر اول و آخرین ضربه الماس هم می‌گیرن.</i>")
    return "\n".join(lines)


def _boss_kb(boss_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[btn("ضربه بزن", emoji_key="btn_worldboss", style=BATTLE, callback_data=f"gb:hit:{boss_id}")]])


def _boss_view_sync(boss_id: int):
    from bio_lab.models import GroupBoss

    b = GroupBoss.objects.get(id=boss_id)
    return b, group_boss.leaderboard(boss_id, 3), group_boss.fighters(boss_id)


async def _boss_word(update, context):
    b = await run_db(group_boss.current, update.effective_chat.id)
    if b is None:
        await update.effective_message.reply_text(
            "👹 الان باسی توی این گروه نیست.\n<i>هر چند ساعت یه باس توی گروه‌هایی که حداقل "
            f"{group_boss.MIN_ACTIVE} بازیکن فعال دارن ظاهر می‌شه. همه با هم بزنیدش!</i>", parse_mode="HTML")
        return
    b, top, n = await run_db(_boss_view_sync, b.id)
    await update.effective_message.reply_text(boss_text(b, top, n), parse_mode="HTML", reply_markup=_boss_kb(b.id))


_LAST_EDIT: dict[int, float] = {}


def _result_text(res: dict) -> str:
    b = res["boss"]
    head = (f"🏆 <b>باس «{_e(b.name)}» شکست خورد!</b>" if res["killed"]
            else f"🏃 <b>باس «{_e(b.name)}» فرار کرد…</b>")
    lines = [head, f"👥 {res['fighters']} نفر جنگیدن — آسیب: <code>{res['dealt']:,}</code> / <code>{b.max_hp:,}</code>"]
    medals = "🥇🥈🥉"
    for i, r in enumerate(res["top"]):
        dia = res["rewards"].get(r["user_id"], {}).get("diamonds")
        lines.append(f"{medals[i]} {r['name']} — {r['damage']:,}" + (f" (+{dia} 💎)" if dia else ""))
    last = next((uid for uid, r in res["rewards"].items() if r.get("last_hit")), None)
    if last is not None:
        lines.append(f"🎯 آخرین ضربه: +{group_boss.LAST_HIT_DIAMONDS} 💎")
    lines.append("🎁 همه‌ی مبارزها به نسبت آسیبشون طلا و DNA گرفتن." if res["killed"]
                 else f"🎁 مبارزها {int(group_boss.ESCAPE_SHARE * 100)}٪ جایزه‌ی کامل رو گرفتن. دفعه‌ی بعد با نفرات بیشتر!")
    if res["points"]:
        lines.append(f"🏆 +{res['points']:,} امتیاز لیگ گروه‌ها برای این گروه («لیگ گروه‌ها»)")
    return "\n".join(lines)


async def _settle_and_post(bot, boss_id: int) -> None:
    res = await run_db(group_boss.settle, boss_id)
    if res is None:
        return
    b = res["boss"]
    try:
        if b.message_id:
            await bot.edit_message_text(chat_id=b.group_id, message_id=b.message_id, parse_mode="HTML",
                                        text=boss_text(b, res["top"], res["fighters"]) + "\n\n<b>— تموم شد —</b>")
    except TelegramError:
        pass
    try:
        await bot.send_message(b.group_id, _result_text(res), parse_mode="HTML",
                               reply_to_message_id=b.message_id or None)
    except TelegramError:
        try:
            await bot.send_message(b.group_id, _result_text(res), parse_mode="HTML")
        except TelegramError:
            pass


async def boss_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    bot = context.bot
    try:
        for b in await run_db(group_boss.due_spawns):
            try:
                m = await bot.send_message(b.group_id, boss_text(b, [], 0), parse_mode="HTML", reply_markup=_boss_kb(b.id))
                await run_db(group_boss.set_message, b.id, m.message_id)
            except TelegramError:
                await run_db(group_boss.drop, b.id)
        for bid in await run_db(group_boss.due_settlements):
            await _settle_and_post(bot, bid)
    except Exception:  # noqa: BLE001 — a bad tick must not kill the job
        log.exception("group boss job failed")


def _hit_sync(tg, chat, boss_id):
    user, _ = get_or_create_user(tg)
    get_or_create_group(chat)
    res = group_boss.hit(user, boss_id)
    return res, group_boss.leaderboard(boss_id, 3), group_boss.fighters(boss_id)


async def boss_hit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    boss_id = int(query.data.split(":")[2])
    try:
        res, top, n = await run_db(_hit_sync, update.effective_user, update.effective_chat, boss_id)
    except GameError as exc:
        await query.answer(alert_text(exc), show_alert=True)
        return
    adv = " ⚡ برتری عنصری!" if res["advantage"] else ""
    await query.answer(f"💥 {res['damage']:,} آسیب!{adv} — {res['hits_left']} ضربه‌ی دیگه داری")
    b = res["boss"]
    if res["killed"]:
        await _settle_and_post(context.bot, b.id)
        return
    now = time.monotonic()
    if now - _LAST_EDIT.get(b.id, 0) >= 2.5:   # Telegram limits edits; the next hit refreshes it anyway
        _LAST_EDIT[b.id] = now
        while len(_LAST_EDIT) > 2000:
            _LAST_EDIT.pop(next(iter(_LAST_EDIT)))
        try:
            await query.edit_message_text(boss_text(b, top, n), parse_mode="HTML", reply_markup=_boss_kb(b.id))
        except TelegramError:
            pass


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
        [btn("فرستادن چالش برای رفیق", emoji_key="btn_referral", style=CONFIRM, url=challenge.share_url(ch.code, ch.creature_name))],
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
    lines = [f"🥊 <b>چالش — راند {min(v['round'] + 1, v['rounds'])} از {v['rounds']}</b>"]
    if last:
        me, foe = challenge.MOVES[last["me"]], challenge.MOVES[last["foe"]]
        res = {"W": "✅ این راند رو بردی!", "L": "❌ این راند رو باختی!", "T": "⚖️ مساوی!"}[last["result"]]
        lines.append(f"تو: {me[0]} {me[1]} — حریف: {foe[0]} {foe[1]}\n{res}")
    lines.append(f"🦖 مهمان تو: ❤️ <b>{v['my_hp']}</b>   👹 «{_e(v['foe_name'])}»: ❤️ <b>{v['foe_hp']}</b>")
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
    application.add_handler(CallbackQueryHandler(boss_hit, pattern=r"^gb:hit:\d+$"))
    application.add_handler(CallbackQueryHandler(challenge_panel, pattern=r"^ch:new$"))
    application.add_handler(CallbackQueryHandler(challenge_go, pattern=r"^ch:go:[0-9a-f]{4,12}$"))
    application.add_handler(CallbackQueryHandler(challenge_move, pattern=r"^ch:mv:\d+:(heavy|guard|element)$"))
    if application.job_queue is not None:
        application.job_queue.run_repeating(boss_job, interval=BOSS_TICK, first=60, name="group_boss")

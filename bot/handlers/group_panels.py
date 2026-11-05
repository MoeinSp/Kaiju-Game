"""Group words for the DM-only sections — «سیاه‌چال», «سیاه‌چال کابوس», «فرم سیاه», «امروز»,
«دریافت همه», «برداشت», «پژوهش آتش», «آهنگری», «جعبه‌ها», «باز کردن جعبه», «اعزامی»,
«برداشت اعزامی», «غول سرگردان», «ضربه غول», «جام», «ثبت نام جام», «جشنواره», «تاجر», «انرژی»,
«دانجن», and «هیولاش» (a reply: the other player's kaiju card).

Each word answers with the sender's own panel — the very screen the DM shows, built by the
section's own handler — as a reply to the word. Those panels carry no user id in their buttons,
so a panel opened here is locked to its sender by `guard` (a pre-handler): a tap from anyone
else on a reply to a recorded word is refused. The DM-menu buttons inside them are already
refused in groups by private.menu_callback.

Dispatched from group_words.handle_group_text (the one group text handler), before the
plain keyword table — none of these phrases is a keyword there.
"""

from __future__ import annotations

import re

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import ApplicationHandlerStop, ContextTypes, TypeHandler

from bio_lab.repository import get_or_create_user
from bot.buttons import CONFIRM, PRIMARY, btn
from bot.utils import alert_text, run_db, send_screen
from game.creature import GameError

# (chat_id, message_id) of a word → who typed it; panels are replies to it. A few thousand
# entries is days of groups; a restart forgets them (the panel just stops being locked).
_PANELS: dict[tuple[int, int], int] = {}
_LOCK_TEXT = "این پنل مال یه بازیکن دیگه‌ست — خودت دستورش رو توی گروه بفرست."


def _remember(chat_id: int, message_id: int, user_id: int) -> None:
    _PANELS[(chat_id, message_id)] = user_id
    while len(_PANELS) > 5000:
        _PANELS.pop(next(iter(_PANELS)))


async def guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Refuse a tap on someone else's group panel (opened by one of the words here)."""
    q, chat, user = update.callback_query, update.effective_chat, update.effective_user
    if q is None or q.message is None or chat is None or user is None or chat.type not in ("group", "supergroup"):
        return
    keys = [(chat.id, q.message.message_id)]
    rep = getattr(q.message, "reply_to_message", None)
    if rep is not None:
        keys.append((chat.id, rep.message_id))
    owner = next((_PANELS[k] for k in keys if k in _PANELS), None)
    if owner is not None and owner != user.id:
        await q.answer(_LOCK_TEXT, show_alert=True)
        raise ApplicationHandlerStop


# ── the words ─────────────────────────────────────────────────────────────────
_RESEARCH = {
    "آتش": "elem_fire", "اتش": "elem_fire", "آب": "elem_water", "اب": "elem_water", "خاک": "elem_earth",
    "برق": "elem_electric", "الکتریسیته": "elem_electric", "کریستال": "elem_crystal", "پلاسما": "elem_plasma",
    "خشم": "might", "خشمباستانی": "might", "حمله": "might", "سرزندگی": "vigor", "جون": "vigor", "جان": "vigor",
}
_DIFF = {"معمولی": "normal", "سخت": "hard", "کابوس": "nightmare", "بیانتها": "endless", "بی انتها": "endless"}

# whole message (spaces dropped) → action. Compared without spaces so «سیاه چال» = «سیاه‌چال».
WORDS = {
    **{w: "dungeon" for w in ("سیاهچال", "پنلسیاهچال")},
    **{w: "dungeon_run" for w in ("ادامهسیاهچال", "ادامهورود")},
    **{w: "dungeon_board" for w in ("جدولبیانتها", "جدولسیاهچال", "جدولهفتگیبیانتها")},
    **{w: "shadow" for w in ("فرمسیاه", "فرمسایه")},
    **{w: "today" for w in ("امروز", "پاداشامروز", "پاداشهایامروز")},
    **{w: "today_all" for w in ("دریافتهمه", "دریافتهمهپاداشها", "گرفتنهمه")},
    **{w: "collect" for w in ("برداشت", "برداشتهمه", "جمعآوری", "جمعاوری", "جمعآوریهمه")},
    **{w: "buildings" for w in ("ارتقاساختمان", "ارتقایساختمان")},
    **{w: "research" for w in ("پژوهش", "پژوهشها", "آزمایشگاهپژوهش")},
    **{w: "forge" for w in ("آهنگری", "اهنگری", "فورج", "ارتقاتجهیز", "ارتقایتجهیز", "ارتقاتجهیزات")},
    **{w: "chests" for w in ("جعبهها", "جعبه", "جعبههایآرنا", "جعبهآرنا")},
    **{w: "chests_open" for w in ("بازکردنجعبه", "بازکردنجعبهها", "بازکردنهمهجعبهها")},
    **{w: "dispatch" for w in ("اعزامی", "ماموریتاعزامی", "مأموریتاعزامی", "ماموریتهایاعزامی")},
    **{w: "dispatch_all" for w in ("برداشتاعزامی", "دریافتاعزامی", "جایزهاعزامی")},
    **{w: "worldboss" for w in ("غولسرگردان", "غول", "باسجهانی")},
    **{w: "worldboss_hit" for w in ("ضربهغول", "زدنغول", "حملهبهغول")},   # never a bare «ضربه»: it spends energy
    **{w: "tournament" for w in ("جام", "جامآخرهفته", "تورنمنت")},
    **{w: "tournament_join" for w in ("ثبتنامجام", "ثبتنامتوجام")},
    **{w: "festival" for w in ("جشنواره", "فستیوال")},
    **{w: "merchant" for w in ("تاجر", "سفارشتاجر", "سفارشها")},
    **{w: "energy" for w in ("انرژی", "انرژیم")},
    **{w: "campaign" for w in ("دانجن", "کمپین", "مرحله")},
    **{w: "peek" for w in ("هیولاش", "کایجوش", "پروفایلش", "هیولاشو", "کایجوشو")},
}
# the menu sections behind each action (main-hall gate, same as the DM menu)
_HALL = {"dispatch": "dispatch", "dispatch_all": "dispatch", "merchant": "merchant", "forge": "blacksmith",
         "research": "research", "research_start": "research", "campaign": "campaign"}


def parse(norm: str) -> tuple[str, str | None] | None:
    """keywords.normalize()d group text → (action, arg) or None."""
    key = norm.replace(" ", "")
    if key in WORDS:
        return WORDS[key], None
    for head in ("سیاهچال", "ورود"):
        if key.startswith(head) and key[len(head):] in {k.replace(" ", "") for k in _DIFF}:
            return "dungeon_start", _DIFF.get(key[len(head):], "endless")
    if key.startswith("پژوهش") and key[len("پژوهش"):] in _RESEARCH:
        return "research_start", _RESEARCH[key[len("پژوهش"):]]
    return None


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str, arg: str | None) -> None:
    message = update.effective_message
    user = update.effective_user
    _remember(message.chat_id, message.message_id, user.id)
    section = _HALL.get(action)
    if section:
        from bot.handlers import private

        req = private.SECTION_HALL_REQ.get(section)
        if req is not None:
            level, story_action = await run_db(private._hall_gate_sync, user)
            if level < req and section != story_action:
                await message.reply_text(f"🔒 این بخش از سطح {req} «تالار مِهر» باز می‌شه (الان سطح {level}).")
                return
    try:
        sent = await _ACTIONS[action](update, context, arg)
    except GameError as exc:
        sent = await message.reply_text(alert_text(exc, 3500))
    if sent is not None and getattr(sent, "message_id", None):
        _remember(message.chat_id, sent.message_id, user.id)


# ── one function per action; each returns the message it sent when it knows it ──
async def _dungeon(update, context, _arg):
    from bot.handlers.dungeon import dungeon_home

    await dungeon_home(update, context)


async def _dungeon_start(update, context, diff):
    from bot.handlers import dungeon as DH
    from game import dungeon as D

    try:
        _out, v = await run_db(DH._act_sync, update.effective_user, "start", diff)
    except D.EmptyTeamError as exc:
        return await update.effective_message.reply_text(
            f"⚠️ {exc}\nتیم حمله‌ت رو توی پیوی ربات بچین (تا ۳ هیولا) و دوباره بفرست.")
    text, kb = DH._run_render(v)
    return await send_screen(update, text, parse_mode="HTML", reply_markup=kb)


async def _dungeon_run(update, context, _arg):
    from bot.handlers import dungeon as DH

    _out, v = await run_db(DH._act_sync, update.effective_user, "view", None)
    text, kb = DH._run_render(v)
    return await send_screen(update, text, parse_mode="HTML", reply_markup=kb)


async def _dungeon_board(update, context, _arg):
    from bot.handlers import dungeon as DH

    b = await run_db(DH._board_sync, update.effective_user)
    text, kb = DH._board_render(b)
    return await send_screen(update, text, parse_mode="HTML", reply_markup=kb)


async def _shadow(update, context, _arg):
    from bot.handlers import dungeon as DH

    v = await run_db(DH._shadow_sync, update.effective_user, None, None)
    text, kb = DH._shadow_render(v)
    return await send_screen(update, text, parse_mode="HTML", reply_markup=kb)


async def _today(update, context, _arg):
    from bot.handlers.today import today_panel

    await today_panel(update, context)


async def _today_all(update, context, _arg):
    from bot.handlers import today as T

    res = await run_db(T._collect_all_sync, update.effective_user)
    if not res["sections"]:
        await T.today_panel(update, context, note="<i>فعلاً چیزی برای دریافت نیست.</i>")
        return None
    await T.today_panel(update, context, note=T._result_note(res), photo=res["photo"])


def _collect_sync(tg_user):
    from game import constants
    from game.buildings import collect
    from game.today import _pending_buildings

    user, _ = get_or_create_user(tg_user)
    lines, total = [], {}
    for building, _amount, _res in _pending_buildings(user):
        try:
            amount, resource = collect(user, building)
        except GameError:
            continue
        if amount:
            total[resource] = total.get(resource, 0) + amount
            lines.append(f"• {constants.BUILDING_LABELS.get(building.building_type, building.building_type)}: "
                         f"<code>+{amount:,}</code> {_RES_LABEL.get(resource, resource)}")
    return lines, total


_RES_LABEL = {"coins": "طلا", "dna_fragments": "DNA", "diamonds": "الماس"}


async def _collect(update, context, _arg):
    lines, total = await run_db(_collect_sync, update.effective_user)
    if not lines:
        return await update.effective_message.reply_text("🏗 فعلاً ساختمونی چیزی برای برداشت نداره (هر چند دقیقه یه بار پر می‌شه).")
    head = "🏗 <b>برداشت از ساختمون‌ها</b> · " + " · ".join(f"<code>+{v:,}</code> {_RES_LABEL.get(k, k)}" for k, v in total.items())
    return await update.effective_message.reply_text("\n".join([head, *lines]), parse_mode="HTML")


async def _buildings(update, context, _arg):
    from bot.handlers.buildings import buildings_panel

    await buildings_panel(update, context)


async def _research(update, context, _arg):
    from bot.handlers.research import research_panel

    await research_panel(update, context)


async def _research_start(update, context, key):
    from bot.handlers import research as RH

    lab_level, level, remaining, target, fin_price, _d = await run_db(RH._start_sync, update.effective_user, key)
    return await send_screen(update, "🔬 <b>پژوهش شروع شد!</b>\n\n" + RH._detail_text(key, lab_level, level, remaining, target),
                             parse_mode="HTML", reply_markup=RH._detail_keyboard(key, lab_level, level, remaining, fin_price))


async def _forge(update, context, _arg):
    from bot.handlers.inventory import blacksmith_panel

    await blacksmith_panel(update, context)


async def _chests(update, context, _arg):
    from bot.handlers.arena import arena_chests_panel

    await arena_chests_panel(update, context)


def _open_ready_sync(tg_user):
    from bio_lab.models import ArenaChest
    from game.arena_chests import advance_user_chests, open_chest

    user, _ = get_or_create_user(tg_user)
    advance_user_chests(user)
    out = []
    for cid in ArenaChest.objects.filter(user=user, status="ready").order_by("slot").values_list("id", flat=True):
        try:
            out.append(open_chest(user, cid))
        except GameError:
            continue
    return out


async def _chests_open(update, context, _arg):
    from bot.loot_text import chest_card

    opened = await run_db(_open_ready_sync, update.effective_user)
    if not opened:
        return await update.effective_message.reply_text("🎁 هیچ جعبه‌ی آرنایی آماده‌ی باز شدن نیست. «جعبه‌ها» رو بفرست تا ببینی کی آماده می‌شن.")
    text = f"🎉 <b>{len(opened)} جعبه باز شد!</b>\n\n" + "\n\n".join(chest_card(r, sub=f"جایگاه <code>{r['slot']}</code>") for r in opened[:4])
    return await update.effective_message.reply_text(text[:4000], parse_mode="HTML")


async def _dispatch(update, context, _arg):
    from bot.handlers.dispatch import dispatch_panel

    await dispatch_panel(update, context)


async def _dispatch_all(update, context, _arg):
    from bot.handlers import dispatch as DSP

    lines = await run_db(DSP._collect_all_sync, update.effective_user)
    note = ("🎉 <b>جایزه‌ی مأموریت‌ها:</b>\n" + "\n".join(lines)) if lines else "<i>هنوز هیچ مأموریتی تموم نشده.</i>"
    await DSP._show_panel(update, note=note)


async def _worldboss(update, context, _arg):
    from bot.handlers.worldboss import worldboss_panel

    await worldboss_panel(update, context)


async def _worldboss_hit(update, context, _arg):
    from bot.handlers import worldboss as WB

    note, data = await run_db(WB._hit_sync, update.effective_user)
    text, kb = WB._render(data, note)
    return await send_screen(update, text, parse_mode="HTML", reply_markup=kb)


async def _tournament(update, context, _arg):
    from bot.handlers.tournament import tournament_panel

    await tournament_panel(update, context)


async def _tournament_join(update, context, _arg):
    from bot.handlers import tournament as TR

    await run_db(TR._join_sync, update.effective_user)
    await TR._refresh(update, "✅ <b>توی جام این هفته ثبت‌نام شدی.</b> جمعه ساعت ۱۹:۳۰ قرعه‌کشی می‌شه.")


async def _festival(update, context, _arg):
    from bot.handlers.festival import festival_panel

    await festival_panel(update, context)


async def _merchant(update, context, _arg):
    from bot.handlers.merchant import merchant_board

    await merchant_board(update, context)


def _energy_sync(tg_user):
    from game.energy import get_max_energy, seconds_until_next_point, sync_energy

    user, _ = get_or_create_user(tg_user)
    now, cap = sync_energy(user), get_max_energy(user)
    return now, cap, (seconds_until_next_point(user) if now < cap else 0)


async def _energy(update, context, _arg):
    from game.blackmarket import format_time_remaining
    from game.emoji import get_emoji

    now, cap, nxt = await run_db(_energy_sync, update.effective_user)
    lines = [f"{get_emoji('energy')} <b>انرژی:</b> <code>{now}/{cap}</code>"]
    rows = []
    if now < cap:
        lines.append(f"⏳ یه واحد دیگه تا {format_time_remaining(nxt)}")
        rows.append([btn("پر کردن انرژی با الماس", emoji_key="btn_charge", style=CONFIRM,
                         callback_data=f"enr:ask:{update.effective_user.id}")])
    else:
        lines.append("✅ انرژیت پره — برو شکار!")
    return await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML",
                                                     reply_markup=InlineKeyboardMarkup(rows) if rows else None)


async def _campaign(update, context, _arg):
    from bot.handlers.campaign import campaign_panel

    await campaign_panel(update, context)


_ANON_ADMIN_ID = 1087968824   # @GroupAnonymousBot — an anonymous admin's messages come from it
_SCOPED_ID = re.compile(r"^(?:grp|grpa):[a-z_]+:(\d+)(?::|$)|^enr:ask:(\d+)$")


def _owner_of_bot_message(chat_id: int, rep) -> int | None:
    """Whose card a message of the bot is: the owners recorded when it was sent, else the user
    id the card's own buttons carry (`grp:creature:<id>`, `enr:ask:<id>`, …)."""
    from bot.gates import _CARD_OWNERS

    key = (chat_id, rep.message_id)
    if key in _PANELS or key in _CARD_OWNERS:
        return _PANELS.get(key) or _CARD_OWNERS.get(key)
    markup = getattr(rep, "reply_markup", None)
    for row in getattr(markup, "inline_keyboard", None) or ():
        for b in row:
            m = _SCOPED_ID.search(getattr(b, "callback_data", None) or "")
            if m:
                return int(m.group(1) or m.group(2))
    for ent in (getattr(rep, "entities", None) or ()) + (getattr(rep, "caption_entities", None) or ()):
        if ent.type == "text_mention" and ent.user is not None and not ent.user.is_bot:
            return ent.user.id
    return None


def _peek_target(message, bot):
    """→ (user id | None, @username | None, why-not text | None) for «هیولاش»: a reply to a
    player, a reply to one of the bot's cards (its owner), or «هیولاش @someone»."""
    for ent in message.entities or ():
        if ent.type == "text_mention" and ent.user is not None and not ent.user.is_bot:
            return ent.user.id, None, None
        if ent.type == "mention":
            name = message.text[ent.offset + 1:ent.offset + ent.length]
            if name.lower() != (bot.username or "").lower():
                return None, name, None
    rep = message.reply_to_message
    if rep is None or getattr(rep, "forum_topic_created", None) is not None:
        return None, None, "👀 روی پیام یه بازیکن ریپلای کن و بنویس «هیولاش» (یا «هیولاش @آیدی»)."
    if getattr(rep, "sender_chat", None) is not None or getattr(rep.from_user, "id", None) == _ANON_ADMIN_ID:
        return None, None, "این پیام به اسم گروه/کانال فرستاده شده؛ نمی‌شه فهمید مال کدوم بازیکنه."
    sender = rep.from_user
    if sender is None:
        return None, None, "👀 روی پیام یه بازیکن ریپلای کن و بنویس «هیولاش»."
    if sender.id == bot.id:
        owner = _owner_of_bot_message(message.chat_id, rep)
        if owner is None:
            return None, None, "این پیام ربات مال بازیکن خاصی نیست — روی پیام خود بازیکن ریپلای کن."
        return owner, None, None
    if sender.is_bot:
        return None, None, "ربات‌ها هیولا ندارن 🙂 روی پیام یه بازیکن ریپلای کن."
    return sender.id, None, None


def _peek_sync(target_id: int | None, chat, username: str | None = None):
    """The data of group_words' «هیولا» card for ANOTHER player — read without
    get_or_create_user, which would rewrite their name from a made-up Telegram user."""
    from bio_lab.models import User
    from bio_lab.repository import get_active_creature, get_or_create_group
    from game.equipment import get_equipped_items, slot_loadout

    if target_id is not None:
        user = User.objects.filter(id=target_id).first()
    else:
        user = User.objects.filter(username__iexact=username).order_by("-id").first()
    if user is None:
        return None
    creature = get_active_creature(user)
    data = {"user": user, "group": get_or_create_group(chat) if chat is not None else None, "creature": creature}
    if creature is not None:
        data["equipped"] = get_equipped_items(creature)
        data["slots"] = slot_loadout(user, creature)
    return data


async def _peek(update, context, _arg):
    """A reply «هیولاش»: the replied-to player's kaiju card (read-only)."""
    from bot.handlers import group_words as GW

    message = update.effective_message
    target_id, username, why = _peek_target(message, context.bot)
    if why:
        return await message.reply_text(why)
    data = await run_db(_peek_sync, target_id, message.chat, username)
    if data is None:
        who = f"@{username}" if username else "این بازیکن"
        return await message.reply_text(f"{who} هنوز توی بازی نیست.")
    if not data.get("creature"):
        return await message.reply_text("این بازیکن هنوز هیولای فعالی نداره.")
    text, _kb = GW._render("creature", data)
    from game.media import get_creature_image_path

    photo = get_creature_image_path(data["creature"])
    return await send_screen(update, text, photo=photo, parse_mode="HTML", reply_markup=None)


_ACTIONS = {
    "dungeon": _dungeon, "dungeon_start": _dungeon_start, "dungeon_run": _dungeon_run,
    "dungeon_board": _dungeon_board, "shadow": _shadow, "today": _today, "today_all": _today_all,
    "collect": _collect, "buildings": _buildings, "research": _research, "research_start": _research_start,
    "forge": _forge, "chests": _chests, "chests_open": _chests_open, "dispatch": _dispatch,
    "dispatch_all": _dispatch_all, "worldboss": _worldboss, "worldboss_hit": _worldboss_hit,
    "tournament": _tournament, "tournament_join": _tournament_join, "festival": _festival,
    "merchant": _merchant, "energy": _energy, "campaign": _campaign, "peek": _peek,
}


def register(application) -> None:
    # before every other handler group (the middlewares are -3..-1): a refused tap stops there
    application.add_handler(TypeHandler(Update, guard), group=-4)

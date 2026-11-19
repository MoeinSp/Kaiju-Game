"""Friends: a private list, and four group words that only work as a reply.

«تف» asks. The other person has to tap «دوست شویم». «تشویق»، «غرش» and «طعنه»
never move gold, energy, cups, or creatures. Buttons that decide a request
carry both ids and only the person who was asked can tap them.
"""

from __future__ import annotations

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes

from bio_lab.models import User
from bio_lab.repository import get_or_create_user, lab_plain
from bot.buttons import CONFIRM, DANGER, NAV, PRIMARY, back_btn, btn
from bot.utils import run_db, send_screen
from game import friends
from game.creature import GameError

_ANON = 1087968824


def _rows(buttons: list) -> InlineKeyboardMarkup | None:
    return InlineKeyboardMarkup(buttons) if buttons else None


def _home_sync(user_id: int) -> tuple[str, list[User]]:
    pals = friends.friends_of(user_id)
    lines = [f"🤝 <b>دوستان</b> <code>{len(pals)}/{friends.MAX_FRIENDS}</code>"]
    cheered = friends.cheers_today(user_id)
    if cheered:
        lines.append(f"امروز <b>{cheered}</b> دوست تشویقت کردن.")
    for mark in friends.unseen_pings(user_id):
        lines.append(f"{lab_plain(mark.actor)} گفت بیا بازی.")
    friends.see_pings(user_id)
    if not pals:
        lines.append("توی گروه روی پیام یکی ریپلای کن و بنویس «تف». تا «دوست شویم» را نزند، دوست نمی‌شوید.")
    lines.append(f"لیس بین دوست‌هاست: روزی {friends.LICK_CAP} بار می‌دهی و {friends.LICK_CAP} بار می‌گیری.")
    lines.append("<i>آخرین بازی فقط برای خودتان است. طلا و سپر اینجا نیست.</i>")
    return "\n".join(lines), pals


def _keyboard(viewer: int, pals: list[User], *, private: bool) -> InlineKeyboardMarkup | None:
    rows: list[list] = []
    row: list = []
    for pal in pals:
        row.append(btn(
            lab_plain(pal)[:24], emoji_key="btn_friends", style=NAV,
            callback_data=f"fr:card:{viewer}:{pal.id}",
        ))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    if private:
        rows.append([back_btn("menu:hub_city_more")])
    if not rows:
        return None
    return InlineKeyboardMarkup(rows)


def _card_keyboard(viewer: int, friend_id: int, *, confirm: bool = False) -> InlineKeyboardMarkup:
    if confirm:
        return InlineKeyboardMarkup([
            [
                btn("حذف شود", emoji_key="btn_delete", style=DANGER, callback_data=f"fr:drop:{viewer}:{friend_id}"),
                btn("بی‌خیال", emoji_key="btn_cancel", style=NAV, callback_data=f"fr:card:{viewer}:{friend_id}"),
            ],
        ])
    return InlineKeyboardMarkup([
        [
            btn("سر زدن", emoji_key="btn_friends", style=PRIMARY, callback_data=f"fr:visit:{viewer}:{friend_id}"),
            btn("تمرین", emoji_key="btn_train", style=PRIMARY, callback_data=f"fr:spar:{viewer}:{friend_id}"),
        ],
        [btn("بیا بازی", emoji_key="btn_friends", style=CONFIRM, callback_data=f"fr:ping:{viewer}:{friend_id}")],
        [btn("حذف دوست", emoji_key="btn_delete", style=DANGER, callback_data=f"fr:askdrop:{viewer}:{friend_id}")],
        [btn("دوستان", emoji_key="btn_friends", style=NAV, callback_data=f"fr:home:{viewer}")],
    ])


async def friends_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return
    private = update.effective_chat is not None and update.effective_chat.type == "private"
    text, pals = await run_db(_home_sync, user.id)
    await send_screen(update, text, parse_mode="HTML", reply_markup=_keyboard(user.id, pals, private=private))


def _card_sync(viewer: int, friend_id: int) -> str:
    friend = User.objects.filter(id=friend_id).first()
    if friend is None:
        raise GameError("این بازیکن دیگر در بازی نیست.")
    return friends.card(viewer, friend)


def _visit_sync(viewer: int, friend_id: int) -> str:
    created = friends.visit(viewer, friend_id)
    return "سر زدی. تا فردا روی صفحه‌اش می‌ماند." if created else "امروز قبلاً سر زدی."


def _ping_sync(viewer: int, friend_id: int) -> str:
    created = friends.ping(viewer, friend_id)
    return "گفتی بیا بازی. دفعه‌ی بعد که دوست‌ها را باز کند می‌بیند." if created else "این هفته قبلاً بهش گفتی."


def _spar_sync(viewer: int, friend_id: int) -> str:
    a = User.objects.filter(id=viewer).first()
    b = User.objects.filter(id=friend_id).first()
    if a is None or b is None:
        raise GameError("این بازیکن دیگر در بازی نیست.")
    return friends.spar(a, b)


def _drop_sync(viewer: int, friend_id: int) -> None:
    friends.remove(viewer, friend_id)


def _decide_sync(uid: int, lo: int, hi: int, accept_it: bool) -> str:
    from bio_lab.repository import lab_mention

    row = friends.link_of(lo, hi)
    if row is None or row.status != "pending":
        raise GameError("درخواست دوستی‌ای باز نیست.")
    if uid == row.asked_by_id or uid not in (lo, hi):
        raise GameError("این درخواست مال تو نیست.")
    other = hi if uid == lo else lo
    asker = User.objects.filter(id=row.asked_by_id).first()
    receiver = User.objects.filter(id=uid).first()
    if asker is None or receiver is None:
        raise GameError("این بازیکن دیگر در بازی نیست.")
    if accept_it:
        friends.accept(uid, other)
        return (
            "🤝 <b>دوست شدید</b>\n\n"
            f"از: {lab_mention(asker)}\n"
            f"به: {lab_mention(receiver)}\n\n"
            "درخواست قبول شد."
        )
    friends.decline(uid, other)
    return (
        "🚫 <b>رد شد</b>\n\n"
        f"از: {lab_mention(asker)}\n"
        f"به: {lab_mention(receiver)}\n\n"
        "درخواست پذیرفته نشد.\n"
        "<i>تا ۲۴ ساعت از همین نفر درخواست تازه نمی‌آید.</i>"
    )


def _play_sync(sender, target_id: int, word: str) -> dict:
    me, _created = get_or_create_user(sender)
    target = User.objects.filter(id=target_id).first()
    return friends.play(me, target, word, target_id)


def _aim(message, bot) -> tuple[int | None, str | None]:
    from bot.handlers.group_panels import _owner_of_bot_message

    rep = message.reply_to_message
    if rep is None or getattr(rep, "forum_topic_created", None) is not None:
        return None, "روی پیام یه بازیکن ریپلای کن و «تف»، «لیس»، «تشویق»، «غرش» یا «طعنه» را بفرست."
    if getattr(rep, "sender_chat", None) is not None or getattr(rep.from_user, "id", None) == _ANON:
        return None, "این پیام به اسم گروه است؛ معلوم نیست مال کدام بازیکن است."
    sender = rep.from_user
    if sender is None:
        return None, "روی پیام یه بازیکن ریپلای کن."
    if sender.id == bot.id:
        owner = _owner_of_bot_message(message.chat_id, rep)
        if owner is None:
            return None, "روی پیام خود بازیکن ریپلای کن."
        return owner, None
    if sender.is_bot:
        return None, "روی ربات نه."
    return sender.id, None


async def group_word(update: Update, context: ContextTypes.DEFAULT_TYPE, word: str) -> None:
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return
    if user.is_bot or user.id == _ANON:
        await message.reply_text("از حساب خودت بفرست.")
        return
    target_id, why = _aim(message, context.bot)
    if why or target_id == user.id:
        gate = await run_db(friends.hint_gate, user.id)
        if gate == "quiet":
            return
        await message.reply_text("روی خودت نه." if target_id == user.id else why)
        return
    result = await run_db(_play_sync, user, target_id, word)
    if result["quiet"]:
        return
    markup = None
    if result.get("spit"):
        markup = InlineKeyboardMarkup([[
            btn("تف برای دوستی", emoji_key="btn_friends", style=PRIMARY, callback_data=f"fr:lickspit:{user.id}:{target_id}"),
        ]])
    elif result["ask"]:
        lo, hi = friends.pair_ids(user.id, target_id)
        markup = _request_keyboard(lo, hi)
    await message.reply_text(result["text"], parse_mode="HTML", reply_markup=markup)


def _request_keyboard(lo: int, hi: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        btn("دوست شویم", emoji_key="btn_confirm", style=CONFIRM, callback_data=f"fr:yes:{lo}:{hi}"),
        btn("بی‌خیال", emoji_key="btn_cancel", style=DANGER, callback_data=f"fr:no:{lo}:{hi}"),
    ]])


def _spit_button_sync(sender_id: int, target_id: int) -> dict:
    sender = User.objects.filter(id=sender_id).first()
    target = User.objects.filter(id=target_id).first()
    if sender is None or target is None:
        raise GameError("این بازیکن دیگر در بازی نیست.")
    return friends.spit_request(sender, target)


async def on_tap(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    if query is None or user is None or not query.data:
        return
    parts = query.data.split(":")
    kind = parts[1] if len(parts) > 1 else ""
    try:
        if kind == "lickspit" and len(parts) == 4:
            sender_id, target_id = int(parts[2]), int(parts[3])
            if user.id != sender_id:
                await query.answer("این دکمه مال تو نیست.", show_alert=True)
                return
            result = await run_db(_spit_button_sync, sender_id, target_id)
            lo, hi = friends.pair_ids(sender_id, target_id)
            markup = _request_keyboard(lo, hi) if result["ask"] else None
            await query.answer()
            await query.edit_message_text(result["text"], parse_mode="HTML", reply_markup=markup)
            return
        if kind in ("yes", "no") and len(parts) == 4:
            lo, hi = int(parts[2]), int(parts[3])
            note = await run_db(_decide_sync, user.id, lo, hi, kind == "yes")
            await query.answer()
            await query.edit_message_text(note, parse_mode="HTML")
            return
        if len(parts) < 3:
            await query.answer()
            return
        viewer = int(parts[2])
        if user.id != viewer:
            await query.answer("این لیست مال تو نیست.", show_alert=True)
            return
        if kind == "home":
            await query.answer()
            await friends_panel(update, context)
            return
        friend_id = int(parts[3])
        if kind == "card":
            text = await run_db(_card_sync, viewer, friend_id)
            await query.answer()
            await send_screen(update, text, parse_mode="HTML", reply_markup=_card_keyboard(viewer, friend_id))
            return
        if kind == "askdrop":
            await query.answer()
            await send_screen(
                update,
                "حذف دوست هر دو طرف را پاک می‌کند و توی گروه اعلام نمی‌شود.",
                reply_markup=_card_keyboard(viewer, friend_id, confirm=True),
            )
            return
        if kind == "drop":
            await run_db(_drop_sync, viewer, friend_id)
            await query.answer("حذف شد.", show_alert=True)
            await friends_panel(update, context)
            return
        if kind == "visit":
            note = await run_db(_visit_sync, viewer, friend_id)
            text = await run_db(_card_sync, viewer, friend_id)
            await query.answer(note, show_alert=True)
            await send_screen(update, text, parse_mode="HTML", reply_markup=_card_keyboard(viewer, friend_id))
            return
        if kind == "ping":
            note = await run_db(_ping_sync, viewer, friend_id)
            await query.answer(note, show_alert=True)
            return
        if kind == "spar":
            text = await run_db(_spar_sync, viewer, friend_id)
            await query.answer()
            await send_screen(update, text, parse_mode="HTML", reply_markup=_card_keyboard(viewer, friend_id))
            return
        await query.answer()
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)


def register(application) -> None:
    application.add_handler(CallbackQueryHandler(on_tap, pattern=r"^fr:"))

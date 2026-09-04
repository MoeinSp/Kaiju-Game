import secrets
import time

from django.db import transaction
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

from bio_lab.models import Creature, DuelLog, User
from bio_lab.repository import (
    display_name,
    get_active_creature,
    get_or_create_group,
    get_or_create_user,
    group_member_creatures,
    mention,
    touch_membership,
)
from bot.buttons import CONFIRM, DANGER, NAV, PRIMARY, back_btn, btn
from bot.handlers.group_words import group_footer_keyboard
from bot.utils import mission_reward_text, run_db, safe_edit_message_text
from game import constants
from game.buildings import maybe_award_speedup_card
from game.combat import battle_report, resolve_battle, resolve_duel_detailed
from game.creature import GameError, add_xp
from game.daily import check_missions, consume_daily, record_action
from game.emoji import get_emoji
from game.energy import spend_energy
from game.guardian import challenge_guardian, ensure_guardian, get_guardian
from game.raid import RaidError, attack_boss, distribute_rewards, get_active_boss, spawn_boss


def _speedup_note(minutes: int | None) -> str:
    if minutes is None:
        return ""
    return f"\n{get_emoji('speedup')} جایزه‌ی شانسی: {constants.speedup_label(minutes)}!"


def _mission_lines(completed: list[dict]) -> str:
    if not completed:
        return ""
    lines = []
    for m in completed:
        lines.append(f"{get_emoji('mission')} ماموریت «{m['label']}» تکمیل شد! {mission_reward_text(m)}")
    return "\n" + "\n".join(lines)


async def _reply_error(message, exc, owner_id: int) -> None:
    """Reply to a group message with an error. An out-of-energy error also gets the
    diamond-refill button (scoped to `owner_id`) so the player can act inline. Was
    referenced by `attack()` but never defined — its absence meant a raised
    RaidError/GameError (e.g. «هیولات خسته‌ست») crashed the handler with a NameError
    and no message was ever sent to the group."""
    from bot.handlers.energy import energy_refill_markup
    from game.energy import EnergyError

    markup = energy_refill_markup(owner_id) if isinstance(exc, EnergyError) else None
    await message.reply_text(str(exc), parse_mode="HTML", reply_markup=markup)


def _gold_transfer_sync(chat, sender_tg, receiver_id, amount):
    from django.db import transaction

    group = get_or_create_group(chat)
    sender, _ = get_or_create_user(sender_tg)
    touch_membership(group, sender)
    if amount <= 0:
        raise GameError("مقدار باید بیشتر از صفر باشه.")
    if sender.id == receiver_id:
        raise GameError("به خودت نمی‌تونی انتقال بدی.")

    # LOCK both rows and re-read their CURRENT balances inside the transaction. Without
    # this the old code read the receiver's coins unlocked, then saved coins ± amount —
    # so if either player's balance changed between the read and the save (a mine
    # collect, an arena win, another transfer), that concurrent change was overwritten
    # and gold "disappeared". Locking by ascending id also keeps the lock order
    # consistent across transfers so two crossing transfers can't deadlock.
    with transaction.atomic():
        ids = sorted({sender.id, receiver_id})
        locked = {u.id: u for u in User.objects.select_for_update().filter(id__in=ids).order_by("id")}
        sender = locked.get(sender.id)
        receiver = locked.get(receiver_id)
        if receiver is None:
            raise GameError("این بازیکن هنوز بازی رو شروع نکرده.")
        if sender.coins < amount:
            raise GameError(f"طلا کافی نداری! فقط {sender.coins} طلا داری.")
        sender.coins -= amount
        receiver.coins += amount
        sender.save(update_fields=["coins"])
        receiver.save(update_fields=["coins"])
    return sender, receiver


async def gold_transfer(update: Update, context: ContextTypes.DEFAULT_TYPE, amount: int) -> None:
    """Word command «انتقال طلا [عدد]» — reply to the recipient. Gold only."""
    reply = update.message.reply_to_message
    if reply is None or reply.from_user is None:
        await update.message.reply_text(
            f"{get_emoji('gift')} برای انتقال طلا، روی پیام طرف <b>ریپلای</b> کن و بنویس «انتقال طلا 50».",
            parse_mode="HTML",
        )
        return
    recipient = reply.from_user
    if recipient.id == update.effective_user.id or recipient.is_bot:
        await update.message.reply_text("🙅 به خودت یا به یه بات نمی‌تونی انتقال بدی!")
        return
    try:
        sender, receiver = await run_db(_gold_transfer_sync, update.effective_chat, update.effective_user, recipient.id, amount)
    except GameError as exc:
        await update.message.reply_text(str(exc))
        return
    await update.message.reply_text(
        f"{get_emoji('coin')} {display_name(sender)} مقدار <b>{amount}</b> طلا به {display_name(receiver)} انتقال داد! ✅",
        parse_mode="HTML",
    )


async def _reply_transfer_error(message, exc) -> None:
    """Insufficient-diamond errors get a pretty HTML message with the diamond emoji
    and a «راهنمای هزینه‌ها» button; everything else stays a plain reply."""
    from game.transfer import TransferFundsError

    if isinstance(exc, TransferFundsError):
        guide = "c" if exc.kind == "creature" else "e"
        keyboard = InlineKeyboardMarkup([[
            btn("💎 راهنمای هزینه‌ها", style=NAV, callback_data=f"xfo:prices:{guide}"),
        ]])
        await message.reply_text(
            f"{get_emoji('diamond')} <b>الماس گیرنده کافی نیست</b>\n\n"
            f"این انتقال <b>{exc.cost}</b> {get_emoji('diamond')} لازم داره، "
            f"ولی گیرنده الان فقط <b>{exc.have}</b> تا داره.\n"
            "<blockquote>گیرنده اول باید الماس تهیه کنه — از جعبه‌ی الماسی، معدن الماس یا گردونه‌ی شانس.</blockquote>",
            parse_mode="HTML", reply_markup=keyboard,
        )
    else:
        await message.reply_text(str(exc))


def _preview_creature_sync(chat, sender_tg, receiver_id, creature_id):
    from game import transfer

    group = get_or_create_group(chat)
    sender, _ = get_or_create_user(sender_tg)
    touch_membership(group, sender)
    receiver = User.objects.filter(id=receiver_id).first()
    if receiver is None:
        raise GameError("این بازیکن هنوز بازی رو شروع نکرده.")
    preview = transfer.preview_creature_transfer(sender, receiver, creature_id)
    return sender, receiver, preview


# ── Player-to-player trading with a seller-set price ─────────────────────────
# A trade is a two-step, two-party handshake held in memory for 5 minutes:
#   1) SELLER replies «انتقال هیولا/تجهیزات [کد]» → gets تعیین قیمت / رایگان / لغو
#   2) after a price is set, the RECEIVER sees the price + diamond fee and قبول/رد.
# Offers live only in _PENDING_OFFERS (ephemeral — a restart drops them, which is
# fine for something that expires in 5 min anyway).
_PENDING_OFFERS: dict[str, dict] = {}
_OFFER_TTL_SECONDS = 300


def _prune_offers() -> None:
    now = time.time()
    for tok in [t for t, o in _PENDING_OFFERS.items() if o["expires_at"] < now]:
        _PENDING_OFFERS.pop(tok, None)


def _new_offer(kind: str, sender_id: int, receiver_id: int, item_id: int, fee: int, desc: str,
               sender_name: str, receiver_name: str) -> str:
    _prune_offers()
    token = secrets.token_urlsafe(6)
    _PENDING_OFFERS[token] = {
        "kind": kind, "sender_id": sender_id, "receiver_id": receiver_id,
        "item_id": item_id, "fee": fee, "desc": desc, "price": 0,
        "sender_name": sender_name, "receiver_name": receiver_name,
        "expires_at": time.time() + _OFFER_TTL_SECONDS,
    }
    return token


def _get_offer(token: str) -> dict | None:
    offer = _PENDING_OFFERS.get(token)
    if offer is None:
        return None
    if offer["expires_at"] < time.time():
        _PENDING_OFFERS.pop(token, None)
        return None
    return offer


def _sender_active_offer(sender_id: int) -> dict | None:
    """The sender's own still-valid outgoing offer, if any. Used to stop a player
    from opening a second transfer (or re-offering the same item) while one is
    already live — the offer holds a claim on that item for its 5-minute window."""
    _prune_offers()
    now = time.time()
    for o in _PENDING_OFFERS.values():
        if o["sender_id"] == sender_id and o["expires_at"] >= now:
            return o
    return None


def _item_active_offer(kind: str, item_id: int) -> dict | None:
    """A live offer that already involves this exact creature/equipment."""
    _prune_offers()
    now = time.time()
    for o in _PENDING_OFFERS.values():
        if o["kind"] == kind and o["item_id"] == item_id and o["expires_at"] >= now:
            return o
    return None


def _seller_step_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [btn("💰 تعیین قیمت", style=PRIMARY, callback_data=f"xfo:setp:{token}"),
         btn("🎁 رایگان", style=CONFIRM, callback_data=f"xfo:free:{token}")],
        [btn("❌ لغو", style=DANGER, callback_data=f"xfo:cancel:{token}")],
    ])


async def _begin_offer(update, kind: str, sender, receiver, item_id: int, desc: str, fee: int) -> None:
    # one live offer per sender: while an offer is pending it holds a claim on its item,
    # so the sender can't open a second transfer or re-offer the same thing elsewhere
    # until the current one is accepted, rejected, cancelled, or expires (5 min).
    if _sender_active_offer(sender.id) is not None:
        await update.message.reply_text(
            "⏳ یه پیشنهاد انتقالِ باز داری. اول همون رو کامل یا لغو کن، "
            "یا تا ۵ دقیقه صبر کن تا خودش منقضی شه، بعد انتقال جدید بزن."
        )
        return
    if _item_active_offer(kind, item_id) is not None:
        await update.message.reply_text("⏳ این مورد همین الان توی یه پیشنهاد انتقالِ بازه.")
        return
    token = _new_offer(kind, sender.id, receiver.id, item_id=item_id, fee=fee, desc=desc,
                       sender_name=display_name(sender), receiver_name=display_name(receiver))
    await update.message.reply_text(
        f"🤝 <b>{display_name(sender)}</b> می‌خواد {desc} رو به <b>{display_name(receiver)}</b> بده.\n"
        f"{get_emoji('diamond')} کارمزد انتقال: <b>{fee}</b> الماس (گیرنده می‌ده)\n\n"
        f"<b>{display_name(sender)}</b>، قیمت (به طلا) رو تعیین کن یا رایگان بفرست 👇\n"
        "<i>5 دقیقه اعتبار داره.</i>",
        parse_mode="HTML", reply_markup=_seller_step_keyboard(token),
    )


async def transfer_creature_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, creature_id: int) -> None:
    """Word «انتقال کایجو/هیولا [کد]» — reply to the recipient. Opens the seller's
    price step; the receiver only pays once they accept the final offer."""
    reply = update.message.reply_to_message
    if reply is None or reply.from_user is None:
        from game import transfer

        await update.message.reply_text(
            "🦖 برای انتقال هیولا، روی پیام گیرنده <b>ریپلای</b> کن و بنویس «انتقال کایجو [کد]».\n"
            "<i>کد هیولا رو از «کلکسیون» توی پیوی ربات می‌بینی. اول قیمت می‌ذاری، بعد گیرنده قیمت و "
            f"کارمزد الماس رو می‌بینه و تأیید می‌کنه؛ برای هر دو طرف {constants.TRANSFER_COOLDOWN_HOURS} ساعت کول‌داون داره.</i>\n\n"
            + transfer.creature_prices_text(),
            parse_mode="HTML",
        )
        return
    recipient = reply.from_user
    if recipient.id == update.effective_user.id or recipient.is_bot:
        await update.message.reply_text("🙅 به خودت یا به یه بات نمی‌تونی انتقال بدی!")
        return
    try:
        sender, receiver, preview = await run_db(
            _preview_creature_sync, update.effective_chat, update.effective_user, recipient.id, creature_id
        )
    except GameError as exc:
        await _reply_transfer_error(update.message, exc)
        return
    c = preview["creature"]
    desc = f"هیولای <b>{c.name}</b> {constants.RARITY_LABELS[c.rarity]} {'⭐' * c.star_level}"
    await _begin_offer(update, "c", sender, receiver, c.id, desc, preview["cost"])


def _preview_equip_sync(chat, sender_tg, receiver_id, equip_id):
    from game import transfer

    group = get_or_create_group(chat)
    sender, _ = get_or_create_user(sender_tg)
    touch_membership(group, sender)
    receiver = User.objects.filter(id=receiver_id).first()
    if receiver is None:
        raise GameError("این بازیکن هنوز بازی رو شروع نکرده.")
    preview = transfer.preview_equip_transfer(sender, receiver, equip_id)
    return sender, receiver, preview


async def transfer_equip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, equip_id: int) -> None:
    """Word «انتقال تجهیز/تجهیزات [کد]» — reply to the recipient. Opens the seller's
    price step; the receiver pays only on final accept."""
    reply = update.message.reply_to_message
    if reply is None or reply.from_user is None:
        from game import transfer

        await update.message.reply_text(
            "🎒 برای انتقال تجهیزات، روی پیام گیرنده <b>ریپلای</b> کن و بنویس «انتقال تجهیزات [کد]».\n"
            "<i>کد تجهیزات رو از «تجهیزات» توی پیوی ربات می‌بینی. اول قیمت می‌ذاری، بعد گیرنده قیمت و "
            "کارمزد الماس رو می‌بینه و تأیید می‌کنه.</i>\n\n"
            + transfer.equip_prices_text(),
            parse_mode="HTML",
        )
        return
    recipient = reply.from_user
    if recipient.id == update.effective_user.id or recipient.is_bot:
        await update.message.reply_text("🙅 به خودت یا به یه بات نمی‌تونی انتقال بدی!")
        return
    try:
        sender, receiver, preview = await run_db(
            _preview_equip_sync, update.effective_chat, update.effective_user, recipient.id, equip_id
        )
    except GameError as exc:
        await _reply_transfer_error(update.message, exc)
        return
    it = preview["item"]
    desc = f"تجهیزاتِ <b>{it.name} +{it.level}</b> {constants.RARITY_LABELS[it.rarity]}"
    await _begin_offer(update, "e", sender, receiver, it.id, desc, preview["cost"])


def _transfer_do_sync(kind, sender_id, receiver_id, item_id, price):
    from game import transfer

    sender = User.objects.filter(id=sender_id).first()
    receiver = User.objects.filter(id=receiver_id).first()
    if sender is None or receiver is None:
        raise GameError("یکی از طرف‌ها دیگه پیدا نشد.")
    if kind == "c":
        return sender, receiver, transfer.transfer_creature(sender, receiver, item_id, price)
    return sender, receiver, transfer.transfer_equipment(sender, receiver, item_id, price)


def _offer_receiver_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [btn("✅ قبول", style=CONFIRM, callback_data=f"xfo:acc:{token}"),
         btn("❌ رد", style=DANGER, callback_data=f"xfo:rej:{token}")],
        [btn("💎 راهنمای هزینه‌ها", style=NAV, callback_data="xfo:prices:c")],
    ])


def _offer_receiver_text(offer: dict) -> str:
    price = offer["price"]
    price_line = (
        f"{get_emoji('coin')} قیمت: <b>{price:,}</b> طلا (به فروشنده می‌رسه)"
        if price > 0 else f"{get_emoji('gift')} <b>رایگان</b> (بدون قیمت)"
    )
    return (
        f"🤝 <b>پیشنهاد انتقال</b>\n"
        f"{offer['desc']}\n"
        f"از <b>{offer['sender_name']}</b> به <b>{offer['receiver_name']}</b>\n\n"
        f"{price_line}\n"
        f"{get_emoji('diamond')} کارمزد: <b>{offer['fee']}</b> الماس\n\n"
        f"<b>{offer['receiver_name']}</b>، قبول می‌کنی؟ 👇  <i>(5 دقیقه اعتبار · 1 روز کول‌داون برای هر دو طرف)</i>"
    )


async def _present_offer_to_receiver(update, token: str, via_query=None) -> None:
    """Show the final offer (price + diamond fee) to the receiver with accept/reject.
    Called after the seller sets a price (new message) or taps «رایگان» (edit)."""
    offer = _get_offer(token)
    if offer is None:
        text = "⌛ این پیشنهاد منقضی شد. دوباره از «انتقال …» شروع کن."
        if via_query is not None:
            await safe_edit_message_text(via_query, text)
        else:
            await update.message.reply_text(text)
        return
    text, keyboard = _offer_receiver_text(offer), _offer_receiver_keyboard(token)
    if via_query is not None:
        await safe_edit_message_text(via_query, text, parse_mode="HTML", reply_markup=keyboard)
    else:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def maybe_capture_transfer_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """If the sender is mid-«تعیین قیمت», treat their next numeric message as the
    price and move the offer to the receiver. Returns True if it consumed the
    message (so the group text router stops here)."""
    token = context.user_data.get("xfer_price_token")
    if not token:
        return False
    message = update.effective_message
    if message is None or not message.text:
        return False
    raw = message.text.strip().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
    if raw in ("لغو", "انصراف", "کنسل"):
        context.user_data.pop("xfer_price_token", None)
        await message.reply_text("باشه، قیمت‌گذاری لغو شد.")
        return True
    if not raw.isdigit():
        await message.reply_text("فقط یه عدد بفرست (مثلا 5000) — یا «لغو».")
        return True
    offer = _get_offer(token)
    context.user_data.pop("xfer_price_token", None)
    if offer is None:
        await message.reply_text("⌛ این پیشنهاد منقضی شد. دوباره از «انتقال …» شروع کن.")
        return True
    if update.effective_user.id != offer["sender_id"]:
        return True  # not the seller — ignore
    offer["price"] = int(raw)
    await _present_offer_to_receiver(update, token)
    return True


async def transfer_offer_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """All buttons of the trade handshake (xfo:…). Seller-only steps: setp/free/cancel.
    Receiver-only steps: acc/rej. Anyone: prices guide."""
    query = update.callback_query
    parts = query.data.split(":")
    verb = parts[1]

    if verb == "prices":
        from game import transfer

        await query.answer()
        which = parts[2] if len(parts) > 2 else "c"
        text = transfer.creature_prices_text() if which == "c" else transfer.equip_prices_text()
        other = ("🎒 هزینه‌ی تجهیزات", "xfo:prices:e") if which == "c" else ("🦖 هزینه‌ی هیولا", "xfo:prices:c")
        await safe_edit_message_text(
            query, text, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[btn(other[0], style=NAV, callback_data=other[1])]]),
        )
        return

    token = parts[2]
    offer = _get_offer(token)
    if offer is None:
        await query.answer("⌛ این پیشنهاد منقضی شد.", show_alert=True)
        await safe_edit_message_text(query, "⌛ این پیشنهاد منقضی شد. دوباره از «انتقال …» شروع کن.")
        return

    # ── seller-only steps ────────────────────────────────────────────────────
    if verb in ("setp", "free", "cancel"):
        if update.effective_user.id != offer["sender_id"]:
            await query.answer("فقط فرستنده می‌تونه اینجا تصمیم بگیره.", show_alert=True)
            return
        if verb == "cancel":
            _PENDING_OFFERS.pop(token, None)
            context.user_data.pop("xfer_price_token", None)
            await query.answer("لغو شد.")
            await safe_edit_message_text(query, "❌ انتقال لغو شد.")
            return
        if verb == "free":
            offer["price"] = 0
            await query.answer()
            await _present_offer_to_receiver(update, token, via_query=query)
            return
        # setp → ask the seller to type a number; captured in maybe_capture_transfer_price
        context.user_data["xfer_price_token"] = token
        await query.answer()
        await safe_edit_message_text(
            query,
            f"{offer['desc']}\n\n💰 <b>{offer['sender_name']}</b>، قیمت رو به طلا بفرست "
            "(فقط یه عدد، مثلا <code>5000</code>) — یا «لغو».\n<i>5 دقیقه اعتبار.</i>",
            parse_mode="HTML",
        )
        return

    # ── receiver-only steps ──────────────────────────────────────────────────
    if update.effective_user.id != offer["receiver_id"]:
        await query.answer("فقط گیرنده می‌تونه قبول یا رد کنه.", show_alert=True)
        return
    if verb == "rej":
        _PENDING_OFFERS.pop(token, None)
        await query.answer("رد شد.")
        await safe_edit_message_text(query, "❌ گیرنده پیشنهاد رو رد کرد.")
        return
    if verb == "acc":
        try:
            sender, receiver, result = await run_db(
                _transfer_do_sync, offer["kind"], offer["sender_id"], offer["receiver_id"],
                offer["item_id"], offer["price"],
            )
        except GameError as exc:
            from game.transfer import TransferFundsError

            if isinstance(exc, TransferFundsError):
                await query.answer()
                await safe_edit_message_text(
                    query,
                    f"{get_emoji('diamond')} <b>الماس گیرنده کافی نیست</b>\n\n"
                    f"این انتقال <b>{exc.cost}</b> {get_emoji('diamond')} کارمزد لازم داره، "
                    f"ولی گیرنده فقط <b>{exc.have}</b> تا داره.",
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup([[_offer_receiver_keyboard(token).inline_keyboard[0][0]]]),
                )
                return
            await query.answer(str(exc), show_alert=True)
            return
        _PENDING_OFFERS.pop(token, None)
        if offer["kind"] == "c":
            c = result["creature"]
            body = (f"🦖 هیولای <b>{c.name}</b> {constants.RARITY_LABELS[c.rarity]} {'⭐' * c.star_level} "
                    f"به <b>{display_name(receiver)}</b> منتقل شد! ✅")
        else:
            it = result["item"]
            body = (f"🎒 تجهیزاتِ <b>{it.name} +{it.level}</b> {constants.RARITY_LABELS[it.rarity]} "
                    f"به <b>{display_name(receiver)}</b> منتقل شد! ✅")
        price_line = (
            f"\n{get_emoji('coin')} گیرنده <b>{result['price']:,}</b> طلا به فروشنده داد."
            if result.get("price") else ""
        )
        await query.answer("✅ انجام شد!")
        await safe_edit_message_text(
            query,
            f"{body}{price_line}\n{get_emoji('diamond')} کارمزد <b>{result['cost']}</b> الماس پرداخت شد.\n"
            "<i>1 روز کول‌داون برای هر دو طرف فعال شد.</i>",
            parse_mode="HTML",
        )


def _raid_spawn_sync(chat, spawner_tg):
    group = get_or_create_group(chat)
    spawner_user, _ = get_or_create_user(spawner_tg)
    touch_membership(group, spawner_user)
    return spawn_boss(group)


async def raid_spawn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        boss = await run_db(_raid_spawn_sync, update.effective_chat, update.effective_user)
    except RaidError as exc:
        await update.message.reply_text(str(exc))
        return
    await update.message.reply_text(
        f"👻 <b>باس رید لِوِل {boss.level} ظاهر شد: {boss.name}!</b>\n"
        f"{constants.render_bar(boss.current_hp, boss.max_hp, width=14)} {boss.current_hp:,}/{boss.max_hp:,} HP\n"
        f"عنصر: {constants.element_label(boss.element)}\n\n"
        f"• {get_emoji('energy')} هزینه هر حمله: ۱ انرژی (پاداش بیشتر با دمیج بالاتر)\n"
        f"• ⏳ بدون محدودیت روزانه (+۱ دقیقه زمان انتظار پس از هر اتک)\n"
        f"• ⚔️ حمله به باس: ارسال کلمه «اتک»\n"
        f"• 🎯 حمله به بازیکن: ریپلای روی پیامش و ارسال «اتک»",
        parse_mode="HTML",
    )


def _attack_sync(chat, tg_user):
    group = get_or_create_group(chat)
    boss = get_active_boss(group.id)
    if boss is None:
        raise GameError("😴 الان هیچ باسی توی گروه نیست. با فرستادن «احضار» یه باس بیار، بعد «اتک» بزن.")

    user, _ = get_or_create_user(tg_user)
    touch_membership(group, user)
    creature = get_active_creature(user)
    if creature is None:
        raise GameError("اول باید توی پیوی بات /start بزنی تا موجودت رو بگیری.")

    spend_energy(user, constants.RAID_ATTACK_ENERGY_COST, "حمله")
    dmg, defeated, dna_gain = attack_boss(user, creature, boss)
    user.save(update_fields=["energy", "energy_updated_at"])

    record_action(user, "raid_attack")
    completed_missions = check_missions(user, "raid_attack")

    reward_lines = None
    speedup_won = None
    if defeated:
        rewards = distribute_rewards(boss)
        total_dmg = sum(r["damage"] for r in rewards.values()) or 1
        reward_lines = []
        for i, (uid, r) in enumerate(sorted(rewards.items(), key=lambda kv: kv[1]["damage"], reverse=True)):
            member = User.objects.filter(id=uid).first()
            name = display_name(member) if member else str(uid)
            pct = round(100 * r["damage"] / total_dmg)
            # same board style as the live «جدول رید»: premium icons + RTL-safe branch
            reward_lines.append(
                f"{_raid_rank_label(i)} {name}\n"
                f"{_RAID_BRANCH} {get_emoji('crit')} {r['damage']:,} ({pct}٪) · "
                f"{get_emoji('gift')} {get_emoji('coin')} {r['coins']:,} · {get_emoji('dna')} {r['dna']:,}"
            )
        speedup_won = maybe_award_speedup_card(user)  # bonus chance for whoever lands the killing blow

    return creature, boss, dmg, defeated, completed_missions, reward_lines, speedup_won, user.energy, dna_gain


async def attack(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # replying to another player's message turns «اتک» into a PvP challenge instead
    # of a hit on the raid boss.
    reply = update.message.reply_to_message
    if reply is not None and reply.from_user is not None and not reply.from_user.is_bot:
        await _pvp_attack_prompt(update, context, reply.from_user)
        return

    try:
        creature, boss, dmg, defeated, completed_missions, reward_lines, speedup_won, energy_left, dna_gain = await run_db(
            _attack_sync, update.effective_chat, update.effective_user
        )
    except (RaidError, GameError) as exc:
        await _reply_error(update.message, exc, update.effective_user.id)
        return

    from bot.handlers.private import pct_bar as _pct_bar

    hp = max(boss.current_hp, 0)
    div = "──────────────"
    lines = [
        f"{get_emoji('attack_action')} <b>گزارش نبرد با باس | Raid Attack</b>",
        "",
        f"🦅 مهاجم: <b>{creature.name}</b>",
        f"💥 آسیب وارده: <b>{dmg:,}</b> DMG",
        "",
        div,
        "",
        f"{get_emoji('raid_boss')} وضعیت باس: <b>{boss.name}</b> [سطح {boss.level}]",
        f"{get_emoji('hp')} سلامت باس: {_pct_bar(hp, boss.max_hp)} ({hp:,}/{boss.max_hp:,} HP)",
        "",
        div,
        "",
        f"🎁 پاداش این ضربه: +{dna_gain} {get_emoji('dna')}",
        f"{get_emoji('energy')} انرژی باقی‌مانده: {energy_left} (-1⚡)",
    ]
    text = "\n".join(lines)
    text += _mission_lines(completed_missions)
    if defeated:
        text += (
            f"\n\n{get_emoji('celebrate')} <b>باس لِوِل {boss.level} شکست خورد!</b> "
            f"لِوِل رید گروه رفت رو <b>{boss.level + 1}</b> — باس بعدی قوی‌تر و پرجایزه‌تره.\n\n"
            f"📊 <b>جدول نهایی رید — {boss.name}</b>\n"
            "──────────────\n\n" + "\n\n".join(reward_lines)
        )
        text += _speedup_note(speedup_won)
    else:
        text += f"\n\n{div}\n💡 <i>برای حمله به بازیکن دیگه، روی پیامش ریپلای کن و «اتک» بفرست.</i>"

    # only a relevant button: the raid standings (who hit how much + their share).
    # the boss card is gone once it's defeated, so hide the button then.
    from bot.handlers.group_words import _pm_button

    kb_rows = []
    if not defeated:
        kb_rows.append([btn("📊 جدول اتک به رید", style=NAV, callback_data=f"raidlb:{update.effective_chat.id}")])
    kb_rows.append([_pm_button()])
    await update.message.reply_text(
        text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb_rows)
    )


# RLM-prefixed bullet: forces the stats sub-line to render right-to-left so its marker
# sits on the RIGHT (under the name) — a plain box-drawing corner (└/┘) has no strong
# RTL char on the line, so it flipped to the wrong side. A neutral bullet can't reverse.
_RAID_BRANCH = "‏•"


def _raid_rank_label(i: int) -> str:
    """Rank badge: premium 🥇🥈🥉 for the top three (owner-themeable), keycap 4️⃣… to 10
    (premiumised as literal glyphs), then plain N."""
    if i == 0:
        return get_emoji("medal_gold")
    if i == 1:
        return get_emoji("medal_silver")
    if i == 2:
        return get_emoji("medal_bronze")
    if i < 10:
        return f"{i + 1}️⃣"
    return f"{i + 1}."


def _raid_leaderboard_text(lb: dict) -> str:
    """The «📊 جدول رید» board — boss HP + total damage, then each attacker with their
    damage share and DNA reward. All icons go through get_emoji so the owner's Premium
    set applies; the stats sub-line is RTL-forced so its marker never reverses."""
    pct = round(100 * max(lb["hp"], 0) / max(1, lb["max_hp"]))
    lines = [
        f"📊 <b>جدول رید: {lb['boss_name']} (سطح {lb['boss_level']})</b>",
        "",
        f"{get_emoji('hp')} باس: {constants.render_bar(lb['hp'], lb['max_hp'], width=10)} {pct}٪",
        f"{get_emoji('crit')} کل آسیب: <b>{lb['total_damage']:,}</b>",
        "",
        "──────────────",
    ]
    if not lb["rows"]:
        lines.append("\n<i>هنوز کسی به این باس ضربه نزده.</i>")
    else:
        for i, r in enumerate(lb["rows"][:15]):
            lines.append("")
            lines.append(f"{_raid_rank_label(i)} {r['name']}")
            lines.append(
                f"{_RAID_BRANCH} {get_emoji('crit')} {r['damage']:,} ({r['share_pct']}٪) · "
                f"{get_emoji('gift')} {r['dna']:,} {get_emoji('dna')}"
            )
    return "\n".join(lines)


async def raid_leaderboard_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """The «📊 جدول اتک به رید» button: current damage standings for the active boss,
    with each attacker's projected reward share."""
    query = update.callback_query
    from game.raid import damage_leaderboard

    lb = await run_db(damage_leaderboard, query.message.chat_id)
    if lb is None:
        await query.answer("الان هیچ باسی توی گروه فعال نیست.", show_alert=True)
        return
    await query.answer()
    text = _raid_leaderboard_text(lb)
    from bot.handlers.group_words import _pm_button

    kb = InlineKeyboardMarkup([
        [btn("🔄 به‌روزرسانی", style=NAV, callback_data=f"raidlb:{query.message.chat_id}")],
        [_pm_button()],
    ])
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=kb)


# ── PvP: reply-to-attack another player ───────────────────────────────────────

def _pvp_preview_sync(attacker_tg, target_tg):
    """Read-only power comparison before a reply-attack is confirmed."""
    if target_tg.id == attacker_tg.id or target_tg.is_bot:
        raise GameError("🙅 نمی‌تونی به خودت یا به یه بات حمله کنی!")
    attacker, _ = get_or_create_user(attacker_tg)
    target = User.objects.filter(id=target_tg.id).first()
    if target is None:
        raise GameError("این بازیکن هنوز بازی رو شروع نکرده — نمی‌شه بهش حمله کرد.")
    a_creature = get_active_creature(attacker)
    t_creature = get_active_creature(target)
    if a_creature is None:
        raise GameError("اول باید توی پیوی بات /start بزنی تا موجودت رو بگیری.")
    if t_creature is None:
        raise GameError("این بازیکن موجود فعالی نداره.")
    from game.arena import group_shield_remaining_seconds
    from game.energy import sync_energy

    return (
        display_name(attacker), _creature_power(a_creature), a_creature.element,
        display_name(target), _creature_power(t_creature), t_creature.element,
        group_shield_remaining_seconds(target),
        a_creature.name, t_creature.name, sync_energy(attacker),
        group_shield_remaining_seconds(attacker),
    )


def _fmt_shield_hm(seconds: int) -> str:
    hours, rem = divmod(max(0, int(seconds)), 3600)
    minutes = rem // 60
    if hours and minutes:
        return f"{hours} ساعت و {minutes} دقیقه"
    if hours:
        return f"{hours} ساعت"
    return f"{minutes} دقیقه"


def _pvp_prompt_render(attacker_id, target_id, a_name, a_power, a_elem, t_name, t_power, t_elem,
                       t_shield_secs=0, a_cname="—", t_cname="—", a_energy=0, a_shield_secs=0):
    from bot.handlers.private import (element_advantage_line, pct_bar, win_chance_pct, win_label)

    # target is group-shielded → say it LOUDLY at the very top so the attacker
    # doesn't waste a tap, and drop the attack button (the attack would be blocked).
    if t_shield_secs and t_shield_secs > 0:
        div = "──────────────"
        text = "\n".join([
            "🛡 <b>حمله ناموفق | هدف تحت حفاظت است</b>",
            "",
            f"👤 کاربر هدف: <b>{t_name}</b>",
            f"⏳ مدت زمان سپر: <b>{_fmt_shield_hm(t_shield_secs)}</b> باقی‌مانده",
            "",
            div,
            "",
            "📊 مقایسه توان رزمی:",
            f"▫️ قدرت شما: <b>{a_power:,}</b> 💪",
            f"▫️ قدرت حریف: <b>{t_power:,}</b> 💪",
            "",
            div,
            "",
            "⚠️ تا زمان فروپاشی سپر محافظ گروه، امکان هجوم به این پایگاه وجود نداره.",
        ])
        keyboard = InlineKeyboardMarkup([
            [btn("🔍 جزییات حریف", style=NAV, callback_data=f"gatk_opp:{attacker_id}:{target_id}"),
             btn("بی‌خیال", emoji_key="btn_cancel", style=DANGER, callback_data=f"gatk_cancel:{attacker_id}")],
        ])
        return text, keyboard

    pct = win_chance_pct(a_power, t_power, a_elem, t_elem)
    adv = element_advantage_line(a_elem, t_elem)
    a_tag = f" [{constants.element_label(a_elem)}]" if a_elem else ""
    t_tag = f" [{constants.element_label(t_elem)}]" if t_elem else ""
    div = "──────────────"
    lines = [
        f"{get_emoji('battle')} <b>حمله به بازیکن | Battle Arena</b>",
        "",
        f"👤 حریف شما: <b>{t_name}</b>",
        f"👹 موجود حریف: <b>{t_cname}</b>{t_tag}",
        f"💀 قدرت حریف: <b>{t_power:,}</b>",
        "",
        div,
        "",
        f"🦅 موجود شما: <b>{a_cname}</b>{a_tag}",
        f"💪 قدرت شما: <b>{a_power:,}</b>",
        f"{get_emoji('energy')} انرژی فعلی: {pct_bar(a_energy, constants.MAX_ENERGY)} ({a_energy}/{constants.MAX_ENERGY})",
        "",
        div,
        "",
        "🎯 تحلیل تاکتیکی نبرد:",
        f"شانس پیروزی: {pct_bar(pct, 100)} {win_label(pct)}",
    ]
    if adv:
        lines.append(f"🔮 مزیت عنصری: {adv}")
    lines += [
        "",
        f"🎁 اگه ببری: {get_emoji('coin')} تا {int(constants.GROUP_ATTACK_LOOT_PERCENT * 100)}٪ طلای حریف + {get_emoji('dna')} بونوس",
        "<i>اگه ببازی هیچی ازت کم نمی‌شه · اتک گروهی کاپ نداره</i>",
        "",
        div,
        f"{get_emoji('energy')} هزینه حمله: {constants.RAID_ATTACK_ENERGY_COST} انرژی",
    ]
    if a_shield_secs and a_shield_secs > 0:
        lines.append(
            f"🛡 <i>توجه: تو الان سپر گروهی داری — با این حمله <b>{constants.SHIELD_ATTACK_COST_HOURS} ساعت</b> "
            "از سپرت کم می‌شه.</i>"
        )
    keyboard = InlineKeyboardMarkup([
        [btn(f"⚔️ شروع حمله (-{constants.RAID_ATTACK_ENERGY_COST}⚡)", emoji_key="btn_attack", style=CONFIRM,
             callback_data=f"gatk:{attacker_id}:{target_id}")],
        [btn("🔄 انتخاب موجود دیگر از تیم", style=NAV, callback_data=f"gatk_swap:{attacker_id}:{target_id}")],
        [btn("🔍 جزییات حریف", style=NAV, callback_data=f"gatk_opp:{attacker_id}:{target_id}"),
         btn("بی‌خیال", emoji_key="btn_cancel", style=DANGER,
             callback_data=f"gatk_cancel:{attacker_id}")],
    ])
    return "\n".join(lines), keyboard


async def _pvp_attack_prompt(update, context, target_tg) -> None:
    try:
        data = await run_db(_pvp_preview_sync, update.effective_user, target_tg)
    except GameError as exc:
        await update.message.reply_text(str(exc))
        return
    text, keyboard = _pvp_prompt_render(update.effective_user.id, target_tg.id, *data)
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)
    # remember the user's own «اتک» message so a later «لغو» can delete it too (the
    # bot's prompt is cleaned up already; the user's command line was left behind)
    context.user_data["pvp_prompt_user_msg"] = (update.effective_chat.id, update.message.message_id)


def _pvp_preview_by_ids_sync(attacker_id, target_id):
    """Same as _pvp_preview_sync but keyed by DB ids — for the «بازگشت» from the
    opponent-details view, where we only have the ids in the callback data."""
    attacker = User.objects.filter(id=attacker_id).first()
    target = User.objects.filter(id=target_id).first()
    if attacker is None or target is None:
        raise GameError("یکی از طرف‌ها دیگه پیدا نشد.")
    a_creature = get_active_creature(attacker)
    t_creature = get_active_creature(target)
    if a_creature is None or t_creature is None:
        raise GameError("یکی از دو طرف موجود فعال نداره.")
    from game.arena import group_shield_remaining_seconds
    from game.energy import sync_energy

    return (
        display_name(attacker), _creature_power(a_creature), a_creature.element,
        display_name(target), _creature_power(t_creature), t_creature.element,
        group_shield_remaining_seconds(target),
        a_creature.name, t_creature.name, sync_energy(attacker),
        group_shield_remaining_seconds(attacker),
    )


def _gatk_opp_details_sync(target_id):
    from bio_lab.repository import lab_display
    from bot.handlers.arena import _opponent_details_sync

    target = User.objects.filter(id=target_id).first()
    if target is None:
        raise GameError("این بازیکن دیگه پیدا نشد.")
    creature = get_active_creature(target)
    power = _creature_power(creature) if creature is not None else 0
    element = creature.element if creature is not None else None
    pending = {"is_fake": False, "user_id": target_id, "label": lab_display(target),
               "power": power, "element": element}
    return _opponent_details_sync(pending)


async def gatk_opp_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """«🔍 جزییات حریف» under a group attack prompt — attacker-only, full readout."""
    query = update.callback_query
    _, attacker_id, target_id = query.data.split(":")
    if update.effective_user.id != int(attacker_id):
        await query.answer("این دکمه مال تو نیست 🙂", show_alert=True)
        return
    try:
        d = await run_db(_gatk_opp_details_sync, int(target_id))
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    from bot.handlers.arena import opponent_details_text

    await query.answer()
    keyboard = InlineKeyboardMarkup([[btn("↩️ بازگشت", style=NAV, callback_data=f"gatk_back:{attacker_id}:{target_id}")]])
    await safe_edit_message_text(query, opponent_details_text(d), parse_mode="HTML", reply_markup=keyboard)


async def gatk_back_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """«↩️ بازگشت» from opponent details back to the attack prompt — attacker-only."""
    query = update.callback_query
    _, attacker_id, target_id = query.data.split(":")
    if update.effective_user.id != int(attacker_id):
        await query.answer("این دکمه مال تو نیست 🙂", show_alert=True)
        return
    try:
        data = await run_db(_pvp_preview_by_ids_sync, int(attacker_id), int(target_id))
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer()
    text, keyboard = _pvp_prompt_render(int(attacker_id), int(target_id), *data)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


def _gatk_team_choices_sync(attacker_id):
    from bio_lab.repository import team_choices
    from game.workers import creature_status

    attacker = User.objects.filter(id=attacker_id).first()
    if attacker is None:
        raise GameError("پیدا نشدی.")
    out = []
    for c in team_choices(attacker):
        busy = (not c.is_active) and creature_status(attacker, c) is not None
        out.append((c.id, c.name, c.element, _creature_power(c), c.is_active, busy))
    return out


async def gatk_swap_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Attacker picks a different team creature to hit THIS same target with."""
    query = update.callback_query
    _, attacker_id, target_id = query.data.split(":")
    if update.effective_user.id != int(attacker_id):
        await query.answer("این دکمه مال تو نیست 🙂", show_alert=True)
        return
    try:
        choices = await run_db(_gatk_team_choices_sync, int(attacker_id))
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer()
    rows = []
    for cid, name, element, power, is_active, busy in choices:
        tag = "🟢 " if is_active else ("⛔ " if busy else "")
        note = " (مشغول)" if busy else ""
        rows.append([btn(f"{tag}{name} [{constants.ELEMENT_LABELS[element]}] · 💪{power:,}{note}",
                         style=CONFIRM, callback_data=f"gatk_swap_pick:{attacker_id}:{target_id}:{cid}")])
    rows.append([btn("↩️ بازگشت به حریف", style=NAV, callback_data=f"gatk_swap_pick:{attacker_id}:{target_id}:0")])
    await safe_edit_message_text(
        query,
        "🔄 <b>کدوم موجود با این حریف بجنگه؟</b>\n<blockquote>حریف عوض نمی‌شه؛ فقط موجودِ خودت. "
        "عنصر مناسب رو انتخاب کن تا شانس بردت بره بالا.</blockquote>",
        parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows),
    )


def _gatk_swap_pick_sync(attacker_id, target_id, creature_id):
    from game.creature import set_active_creature

    attacker = User.objects.filter(id=attacker_id).first()
    if attacker is None:
        raise GameError("پیدا نشدی.")
    if creature_id:
        set_active_creature(attacker, creature_id)  # may raise if the creature is busy
    return _pvp_preview_by_ids_sync(attacker_id, target_id)


async def gatk_swap_pick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, attacker_id, target_id, cid = query.data.split(":")
    if update.effective_user.id != int(attacker_id):
        await query.answer("این دکمه مال تو نیست 🙂", show_alert=True)
        return
    try:
        data = await run_db(_gatk_swap_pick_sync, int(attacker_id), int(target_id), int(cid))
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer("موجودت عوض شد." if int(cid) else "")
    text, keyboard = _pvp_prompt_render(int(attacker_id), int(target_id), *data)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


@transaction.atomic
def _pvp_attack_sync(chat, attacker_tg, target_id):
    group = get_or_create_group(chat)
    attacker, _ = get_or_create_user(attacker_tg)
    touch_membership(group, attacker)
    # LOCK the target's row and re-check the group shield UNDER the lock, so two
    # attackers hitting the same person at once serialise — the first applies the 4h
    # group shield and the second bounces (was raceable: both read shield=None).
    target = User.objects.select_for_update().filter(id=target_id).first()
    if target is None:
        raise GameError("این بازیکن دیگه پیدا نشد.")
    a_creature = get_active_creature(attacker)
    t_creature = get_active_creature(target)
    if a_creature is None or t_creature is None:
        raise GameError("یکی از دو طرف موجود فعال نداره.")

    from game.arena import group_shield_remaining_seconds, is_group_shielded

    if is_group_shielded(target):
        secs = group_shield_remaining_seconds(target)
        raise GameError(f"این بازیکن الان محافظ گروهی داره ({secs // 3600} ساعت و {(secs % 3600) // 60} دقیقه دیگه می‌پره).")

    spend_energy(attacker, constants.RAID_ATTACK_ENERGY_COST, "حمله")

    a_power = _creature_power(a_creature)
    battle = resolve_battle(a_creature, t_creature)
    winner_creature = battle["winner"]
    log_text, detail_log = battle["compact"], battle["detail"]
    attacker_won = winner_creature.id == a_creature.id
    winner_user = attacker if attacker_won else target
    winner_creature_obj = a_creature if attacker_won else t_creature
    loser_creature_obj = t_creature if attacker_won else a_creature

    # The group «اتک» is CUP-NEUTRAL: it never changes anyone's cup — only gold moves.
    delta = 0
    defender_cup_change = 0

    # Only the ATTACKER loots, and only on a WIN: they take 10% of the target's gold
    # (+ a little DNA). A LOSING attacker loses nothing at all — the defender never
    # loots the attacker.
    # launching an attack burns 8h off the ATTACKER's own group shield (if they have
    # one) — same rule as the arena. The «حمله» button IS the confirmation.
    from game.arena import spend_group_shield_on_attack

    attacker_fields = ["energy", "energy_updated_at"]
    if spend_group_shield_on_attack(attacker):
        attacker_fields.append("group_shield_until")
    elif attacker.group_shield_until is not None:
        # was shielded a moment ago but the 8h drop cleared it — persist the reset
        attacker_fields.append("group_shield_until")

    loot = 0
    dna_win = 0
    target_fields = []
    if attacker_won:
        loot = max(0, target.coins // 10)
        target.coins -= loot
        attacker.coins += loot
        dna_win = constants.GROUP_ATTACK_WIN_DNA
        attacker.dna_fragments += dna_win
        attacker_fields += ["coins", "dna_fragments"]
        target_fields += ["coins"]

    winner_levels = add_xp(winner_creature_obj, constants.DUEL_WIN_XP)
    add_xp(loser_creature_obj, constants.DUEL_LOSE_XP)
    attacker.save(update_fields=attacker_fields)
    if target_fields:
        target.save(update_fields=target_fields)
    winner_creature_obj.save()
    loser_creature_obj.save()

    record_action(attacker, "duel_win" if attacker_won else "duel_loss")
    completed_missions = check_missions(winner_user, "duel_win") if attacker_won else []
    speedup_won = maybe_award_speedup_card(winner_user) if attacker_won else None

    DuelLog.objects.create(
        group_id=group.id, challenger_id=attacker.id, opponent_id=target.id,
        winner_id=winner_user.id, wager_gold=loot, log_text=log_text,
    )
    # log it as a real attack too, so the target gets a report + can revenge,
    # and give the target a 4h group shield so they can't be farmed in the group
    from bio_lab.models import AttackLog
    from bio_lab.repository import lab_display
    from game.arena import apply_group_shield

    log = AttackLog.objects.create(
        attacker=attacker,
        attacker_label=lab_display(attacker),
        attacker_power=a_power,
        defender=target,
        defender_label=lab_display(target),
        is_fake_defender=False,
        attacker_won=attacker_won,
        loot_gold=loot,
        cup_delta=delta,
        defender_notified=True,  # DM'd instantly from the callback below
    )
    apply_group_shield(target)
    return {
        "log_text": log_text,
        "detail_log": detail_log,
        "battle_a": battle["a"],
        "battle_b": battle["b"],
        "battle_rounds": battle["rounds"],
        "battle_mult": battle["mult"],
        "battle_winner_name": battle["winner_name"],
        "attacker_won": attacker_won,
        "winner_name": display_name(winner_user),
        "target_name": display_name(target),
        "target_alliance": target.alliance.name if target.alliance_id else None,
        "attacker_cup_change": delta,  # attacker's own swing (+ if won, − if lost)
        "defender_cup_change": defender_cup_change,
        "attacker_new_cup": attacker.cup,
        "loot": loot,
        "dna": dna_win,
        "winner_level_up": bool(winner_levels),
        "winner_creature": winner_creature_obj.name,
        "winner_new_level": winner_creature_obj.level,
        "energy_left": attacker.energy,
        "missions": completed_missions,
        "speedup": speedup_won,
        "defense": {
            "defender_id": target.id,
            "notifications_on": target.notifications_on,
            "log_id": log.id,
            "attacker_id": attacker.id,
            "attacker_name": lab_display(attacker),
            "attacker_alliance": attacker.alliance.name if attacker.alliance_id else None,
            "attacker_power": a_power,
            "attacker_won": attacker_won,
            "loot": loot,
            "attacker_cup": attacker.cup,
            "defender_cup": target.cup,
            "cup_change": defender_cup_change,
        },
    }


async def pvp_attack_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, attacker_id, target_id = query.data.split(":")
    if update.effective_user.id != int(attacker_id):
        await query.answer("این حمله مال تو نیست — خودت روی پیام حریف «اتک» بفرست.", show_alert=True)
        return
    try:
        result = await run_db(_pvp_attack_sync, update.effective_chat, update.effective_user, int(target_id))
    except (RaidError, GameError) as exc:
        await query.answer(str(exc), show_alert=True)
        return
    # INSTANT defense report DM to the attacked player (no 5-minute delay)
    from bot.handlers.notify import send_defense_report_now

    await send_defense_report_now(context, result.get("defense"), group=True)
    await query.answer("🟢 بردی!" if result["attacker_won"] else "🔴 باختی.")
    if result["attacker_won"]:
        reward_block = (
            f"{get_emoji('gift')} <b>غنائم کسب‌شده از حریف:</b>\n"
            f"{get_emoji('coin')} طلا: <b>{result['loot']:,}+</b> ┃ "
            f"{get_emoji('dna')} دی‌ان‌ای: <b>{result.get('dna', 0)}+</b> ┃ "
            f"📈 تجربه: <b>{constants.DUEL_WIN_XP}+ XP</b>"
        )
    else:
        reward_block = "😔 <b>باختی</b> — ولی هیچی ازت کم نشد (اتک گروهی کاپ نداره)."
    if result["winner_level_up"]:
        reward_block += f"\n{get_emoji('celebrate')} {result['winner_creature']} رسید به سطح {result['winner_new_level']}!"
    reward_block += f"\n⚡️ انرژی باقی‌مانده: <b>{result['energy_left']}</b> (-1⚡️)"
    reward_block += _mission_lines(result["missions"]) + _speedup_note(result["speedup"])
    _tally = result.get("target_alliance")
    opp_tag = result.get("target_name", "") + (f" <i>(🤝 {_tally})</i>" if _tally else " 🚫 <i>بدون اتحاد</i>")
    text = battle_report(
        result["battle_a"], result["battle_b"], result["battle_winner_name"],
        result["battle_rounds"], result["battle_mult"],
        victor_line=result["winner_name"], reward_block=reward_block,
    )
    if opp_tag.strip():
        text = f"🏭 حریف: <b>{opp_tag}</b>\n\n" + text
    context.user_data["pvp_last_detail"] = result.get("detail_log", "")
    # keep it uncluttered — only the relevant action (fight details) + a PM shortcut,
    # not the generic reward/help footer that had nothing to do with this attack.
    from bot.handlers.group_words import _pm_button

    keyboard = InlineKeyboardMarkup([
        [btn("🔍 جزییات حمله", style=NAV, callback_data=f"gatk_detail:{update.effective_user.id}")],
        [_pm_button()],
    ])
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


async def pvp_detail_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, owner_id = query.data.split(":")
    if update.effective_user.id != int(owner_id):
        await query.answer("این جزییات مال تو نیست.", show_alert=True)
        return
    detail = context.user_data.get("pvp_last_detail")
    if not detail:
        await query.answer("جزییاتی ذخیره نشده.", show_alert=True)
        return
    await query.answer()
    # edit the result message in place instead of posting a new one, to keep the group tidy
    await safe_edit_message_text(query, detail, parse_mode="HTML")


async def pvp_attack_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, attacker_id = query.data.split(":")
    if update.effective_user.id != int(attacker_id):
        await query.answer()
        return
    await query.answer("لغو شد.")
    await safe_edit_message_text(query, "🚫 حمله لغو شد.")
    # tidy up: remove the cancelled-attack message after ~1 minute so the group
    # doesn't fill up with dead prompts (default TTL is 60s)
    from bot.handlers.group_words import _schedule_cleanup

    if query.message is not None:
        ids = [query.message.message_id]
        stored = context.user_data.pop("pvp_prompt_user_msg", None)
        if stored and stored[0] == query.message.chat_id:
            ids.append(stored[1])  # also remove the user's own «اتک» command line
        _schedule_cleanup(context, query.message.chat_id, ids, "pvp_cancel")


def _creature_power(c: Creature) -> int:
    """Canonical power INCLUDING equipped gear — the same number the PV creature
    card, the arena matchmaker and actual combat (resolve_duel_detailed) use. Gear
    is fetched from the DB, so this is sync-only: never call it from an async
    handler (precompute in the *_sync layer and pass the value through instead)."""
    from game.creature import creature_power
    from game.equipment import get_equipped_items

    return creature_power(c, get_equipped_items(c))


def _leaderboard_sync(chat, tg_user):
    group = get_or_create_group(chat)
    user, _ = get_or_create_user(tg_user)
    touch_membership(group, user)
    ranked = sorted(group_member_creatures(group), key=_creature_power, reverse=True)[:10]
    # pair each with its (gear-inclusive) power now, in sync context, so the async
    # render never has to touch the DB again
    return [(c, _creature_power(c)) for c in ranked]


async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    creatures = await run_db(_leaderboard_sync, update.effective_chat, update.effective_user)
    keyboard = group_footer_keyboard(update.effective_user.id, skip="leaderboard")
    if not creatures:
        await update.message.reply_text(
            "هنوز هیچ موجودی توی این گروه ثبت نشده.", reply_markup=keyboard
        )
        return
    medals = [get_emoji("medal_gold"), get_emoji("medal_silver"), get_emoji("medal_bronze")]
    lines = [f"{get_emoji('trophy')} <b>برترین بازیکن‌های این گروه</b>\n"]
    for i, (c, power) in enumerate(creatures, start=1):
        rank = medals[i - 1] if i <= 3 else f"{i}."
        lines.append(f"{rank} {mention(c.owner)} — 💪{power}  <i>(Lv{c.level})</i>")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML", reply_markup=keyboard)


def _guardian_sync(chat, tg_user):
    group = get_or_create_group(chat)
    user, _ = get_or_create_user(tg_user)
    touch_membership(group, user)

    creatures = group_member_creatures(group)
    top = ensure_guardian(group, creatures)
    if top is None:
        raise GameError("هنوز کسی توی این گروه موجودی ثبت نکرده.")
    owner = User.objects.filter(id=top.owner_id).first()
    return top, display_name(owner) if owner else str(top.owner_id), _creature_power(top)


async def guardian(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        top, owner_name, top_power = await run_db(_guardian_sync, update.effective_chat, update.effective_user)
    except GameError as exc:
        await update.message.reply_text(str(exc))
        return
    stars = get_emoji("star") * top.star_level
    div = "──────────────"
    await update.message.reply_text(
        "\n".join([
            f"{get_emoji('guardian')} <b>جایگاه محافظ گروه | Group Guardian</b>",
            "",
            f"👑 مالک عنوان: <b>{owner_name}</b>",
            f"🦅 موجود نگهبان: <b>{top.name}</b> [{constants.RARITY_LABELS[top.rarity]}] {stars}",
            f"{constants.element_label(top.element)} ┃ 🎖 سطح {top.level} ┃ 💪 قدرت کل: <b>{top_power:,}</b>",
            "",
            div,
            "",
            f"{get_emoji('battle')} چالش جایگاه: برای تصاحب عنوان محافظ، کلمه «تسخیر» را بفرست.",
            f"{get_emoji('gift')} حقوق روزانه: محافظ هر روز با «حقوق» طلا و DNA می‌گیره — "
            f"مبلغش به قدرت هیولای محافظ بستگی داره (تا ۵۰٬۰۰۰ طلا و ۲٬۰۰۰ DNA).",
            f"🚪 کناره‌گیری: با «استعفا» می‌تونی جایگاه رو به نفر بعدی واگذار کنی.",
        ]),
        parse_mode="HTML",
    )


def _guardian_challenge_sync(chat, tg_user):
    group = get_or_create_group(chat)
    user, _ = get_or_create_user(tg_user)
    touch_membership(group, user)

    creature = get_active_creature(user)
    if creature is None:
        raise GameError("اول باید توی پیوی بات /start بزنی تا موجودت رو بگیری.")

    spend_energy(user, constants.GUARDIAN_CHALLENGE_ENERGY_COST, "چالش نگهبان")
    user.save(update_fields=["energy", "energy_updated_at"])
    ensure_guardian(group, group_member_creatures(group))
    won, report = challenge_guardian(group, user, creature)

    record_action(user, "guardian_challenge")
    completed_missions = check_missions(user, "guardian_challenge")
    speedup_won = maybe_award_speedup_card(user) if won else None
    return won, report, completed_missions, speedup_won


def _element_cycle_line() -> str:
    order = ["fire", "earth", "electric", "water", "fire"]
    return " > ".join(constants.element_label(e) for e in order)


def _guardian_report_text(report: dict, won: bool) -> str:
    from bot.handlers.private import pct_bar

    a, b = report["a"], report["b"]
    mult = report["mult"]
    if mult > 1:
        elem_status = "برتری با مهاجم (ضریب آسیب فعال)"
    elif mult < 1:
        elem_status = "برتری با مدافع (ضریب آسیب فعال)"
    else:
        elem_status = "خنثی (بدون ضریب آسیب)"

    def hp_line(s: dict) -> str:
        icon = "💀" if s["hp"] <= 0 else "❤️"
        crit = f"  💥 {s['crits']} ضربه" if s["crits"] else ""
        return f"{icon} {s['name']}: {pct_bar(s['hp'], s['max_hp'])} ({s['hp']:,}/{s['max_hp']:,} HP){crit}"

    div = "──────────────"
    result_line = ("✅ نتیجه نهایی: بردی و محافظ جدید گروه شدی!" if won
                   else "❌ نتیجه نهایی: شکست خوردی! محافظ جایگاه تغییر نکرد.")
    return "\n".join([
        f"🗡 مهاجم: <b>{a['name']}</b> [{constants.element_label(a['element'])}]",
        f"🛡 مدافع: <b>{b['name']}</b> [{constants.element_label(b['element'])}]",
        f"⚖️ وضعیت عنصرها: {elem_status}",
        "",
        div,
        "",
        "📊 وضعیت سلامت مبارزان:",
        hp_line(a),
        hp_line(b),
        "",
        div,
        "",
        f"{get_emoji('trophy')} پیروز میدان: <b>{report['winner'].name}</b> (در {report['rounds']} راند)",
        result_line,
        "",
        div,
        "",
        f"🔁 چرخه برتری عناصر:\n{_element_cycle_line()}",
    ])


async def guardian_challenge(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        won, report, completed_missions, speedup_won = await run_db(
            _guardian_challenge_sync, update.effective_chat, update.effective_user
        )
    except GameError as exc:
        await update.message.reply_text(str(exc))
        return
    text = _guardian_report_text(report, won)
    text += _mission_lines(completed_missions) + _speedup_note(speedup_won)
    await update.message.reply_text(text, parse_mode="HTML")


def _guardian_claim_sync(chat, tg_user):
    group = get_or_create_group(chat)
    user, _ = get_or_create_user(tg_user)
    touch_membership(group, user)

    top = get_guardian(group)
    if top is None or top.owner_id != user.id:
        raise GameError("😅 تو محافظ فعلی این گروه نیستی. با «محافظ» ببین کیه.")

    from game.guardian import salary_for

    coins, dna = salary_for(top)
    # atomic daily consume BEFORE paying, so a rapid double-tap can't collect the
    # salary twice in one day (was check-then-record — spammable). The daily log is
    # keyed on the USER, so this is one salary per day across EVERY group they're in.
    with transaction.atomic():
        try:
            consume_daily(user, "guardian_stipend")
        except GameError:
            raise GameError(
                "📛 شما امروز حقوق محافظ خود را قبلاً (در همین گروه یا گروهی دیگر) دریافت کرده‌اید.\n"
                "حقوق محافظ فقط روزی یک‌بار پرداخت می‌شود؛ فردا دوباره سر بزن."
            )
        user.coins += coins
        user.dna_fragments += dna
        user.save(update_fields=["coins", "dna_fragments"])
        from game.ledger import record_gain

        record_gain(user, "salary", coins=coins, dna=dna)
    return coins, dna


async def guardian_claim(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        coins, dna = await run_db(_guardian_claim_sync, update.effective_chat, update.effective_user)
    except GameError as exc:
        await update.message.reply_text(str(exc))
        return
    await update.message.reply_text(
        f"{get_emoji('guardian')} <b>حقوق محافظ گروه پرداخت شد!</b>\n"
        f"به پاس نگهبانی از قلمرو، امروز <b>{coins:,} {get_emoji('coin')}</b> و "
        f"<b>{dna:,} {get_emoji('dna')}</b> دریافت کردی.\n"
        f"<i>مبلغ حقوق بر اساس قدرت هیولای محافظ محاسبه می‌شود — هرچه قوی‌تر، حقوق بیشتر.</i>",
        parse_mode="HTML",
    )


def _guardian_resign_sync(chat, tg_user):
    group = get_or_create_group(chat)
    user, _ = get_or_create_user(tg_user)
    touch_membership(group, user)

    from game.guardian import resign_guardian

    current = get_guardian(group)
    if current is None or current.owner_id != user.id:
        raise GameError("😅 تو محافظ فعلی این گروه نیستی که بخوای استعفا بدی. با «محافظ» ببین کیه.")
    resign_guardian(group, user, group_member_creatures(group))
    successor = get_guardian(group)
    if successor is None:
        return None
    owner = User.objects.filter(id=successor.owner_id).first()
    return display_name(owner) if owner else str(successor.owner_id)


async def guardian_resign(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        successor_name = await run_db(_guardian_resign_sync, update.effective_chat, update.effective_user)
    except GameError as exc:
        await update.message.reply_text(str(exc))
        return
    if successor_name:
        text = (
            f"{get_emoji('guardian')} <b>از جایگاه محافظ کناره‌گیری کردی.</b>\n"
            f"👑 محافظ جدید گروه: <b>{successor_name}</b>\n"
            f"هر وقت خواستی دوباره با «تسخیر» جایگاه رو پس بگیر."
        )
    else:
        text = (
            f"{get_emoji('guardian')} <b>از جایگاه محافظ کناره‌گیری کردی.</b>\n"
            f"جایگاه محافظ اکنون خالیه — اولین نفری که «تسخیر» بزنه بدون جنگ محافظ می‌شه."
        )
    await update.message.reply_text(text, parse_mode="HTML")


def register(application) -> None:
    group_filter = filters.ChatType.GROUPS
    # The gameplay slash commands (/attack /raid_spawn /leaderboard /guardian*)
    # are gone — groups are word-driven («اتک», «احضار», «جدول», «نگهبان» …),
    # and the words call the same functions. /give was removed too: it bypassed the
    # priced-transfer flow (and transferred a creature by id with NO ownership check).
    # Trading is «انتقال …» only. /mutation_event was removed — it was farmable by
    # spamming it across many groups for free stat mutations. The player-vs-player
    # «دوئل» feature was removed entirely (PvP happens via «اتک» reply-attacks now).
    application.add_handler(CallbackQueryHandler(transfer_offer_callback, pattern=r"^xfo:"))
    application.add_handler(CallbackQueryHandler(raid_leaderboard_callback, pattern=r"^raidlb:-?\d+$"))
    application.add_handler(CallbackQueryHandler(pvp_attack_callback, pattern=r"^gatk:\d+:\d+$"))
    application.add_handler(CallbackQueryHandler(pvp_attack_cancel_callback, pattern=r"^gatk_cancel:\d+$"))
    application.add_handler(CallbackQueryHandler(pvp_detail_callback, pattern=r"^gatk_detail:\d+$"))
    application.add_handler(CallbackQueryHandler(gatk_opp_callback, pattern=r"^gatk_opp:\d+:\d+$"))
    application.add_handler(CallbackQueryHandler(gatk_back_callback, pattern=r"^gatk_back:\d+:\d+$"))
    application.add_handler(CallbackQueryHandler(gatk_swap_callback, pattern=r"^gatk_swap:\d+:\d+$"))
    application.add_handler(CallbackQueryHandler(gatk_swap_pick_callback, pattern=r"^gatk_swap_pick:\d+:\d+:\d+$"))

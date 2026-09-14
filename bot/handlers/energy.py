"""The «شارژ انرژی با الماس» button + its confirm→charge flow.

Offered wherever the bot says "not enough energy" (game.energy.EnergyError). The
button and both callbacks work in a group or the DM, and a confirm step always runs
before any diamonds are spent.
"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes

from bio_lab.repository import get_or_create_user
from bot.utils import run_db, safe_edit_message_text
from game import constants
from game.creature import GameError


def energy_refill_button(owner_id: int) -> InlineKeyboardButton:
    from game import botconfig

    return InlineKeyboardButton(
        f"⚡ شارژ کامل انرژی ({botconfig.get_energy_refill_cost()} 💎)",
        callback_data=f"enr:ask:{owner_id}",
    )


def energy_refill_markup(owner_id: int, is_group: bool = False, origin: str | None = None) -> InlineKeyboardMarkup:
    from config import BOT_USERNAME
    from game import botconfig

    cost = botconfig.get_energy_refill_cost()
    row1 = [InlineKeyboardButton(f"⚡ شارژ کامل با {cost} الماس 💎", callback_data=f"enr:ask:{owner_id}")]
    if is_group:
        row2 = [InlineKeyboardButton("🥈 خرید اشتراک نقره‌ای (۱۰۰ هزار تومان)", url=f"https://t.me/{BOT_USERNAME}?start=sub_silver")]
    else:
        cb = f"sub_pick:silver:{origin}" if origin else "sub_pick:silver"
        row2 = [InlineKeyboardButton("🥈 خرید اشتراک نقره‌ای (۱۰۰ هزار تومان)", callback_data=cb)]
    return InlineKeyboardMarkup([row1, row2])


def _owner_ok(query, parts) -> bool:
    """True if the tapping user owns this scoped energy button (or it's an old,
    unscoped button with no owner encoded — those stay tap-by-anyone as before)."""
    if len(parts) < 3:
        return True
    return query.from_user is not None and query.from_user.id == int(parts[2])


async def show_energy_error(query, exc, owner_id: int | None = None, origin: str | None = None) -> bool:
    """If `exc` is an out-of-energy error, replace the message with it + the refill
    button and return True; otherwise return False so the caller shows it normally."""
    from game.energy import EnergyError

    if isinstance(exc, EnergyError):
        await query.answer()
        oid = owner_id if owner_id is not None else query.from_user.id
        is_group = query.message.chat.type in ("group", "supergroup") if query.message and query.message.chat else False
        if origin is None and getattr(query, "data", None):
            qdata = str(query.data)
            if qdata.startswith("hunt") or qdata.startswith("autohunt"):
                origin = "hunt"
            elif qdata.startswith("arena"):
                origin = "arena"
            elif qdata.startswith("camp"):
                origin = "camp"
            elif qdata.startswith("feed") or qdata.startswith("lab") or qdata.startswith("up_"):
                origin = "upg"
        caption = (
            f"{str(exc)}\n\n"
            f"👑 <b>با تهیه اشتراک نقره‌ای:</b>\n"
            f"  ⚡️ <b>سقف انرژیت ۲ برابر می‌شه (۱۰۰ به جای ۵۰)!</b>\n"
            f"  📋 جعبه‌های آرنا خودکار و پشت‌سرهم باز می‌شن\n"
            f"  🏹 درآمدت از شکار خودکار ۲۵٪ بیشتر می‌شه!\n"
            f"  🥈 نشان پرمیوم نقره‌ای کنار اسمت قرار می‌گیره\n\n"
            f"<i>💡 فقط با ۱۰۰ هزار تومان، محدودیت انرژی رو برای همیشه فراموش کن!</i>"
        )
        await safe_edit_message_text(query, caption, parse_mode="HTML", reply_markup=energy_refill_markup(oid, is_group=is_group, origin=origin))
        return True
    return False


def _user_max_energy_sync(tg_user) -> int:
    from game.energy import get_max_energy
    user, _ = get_or_create_user(tg_user)
    return get_max_energy(user)


async def energy_ask_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    parts = query.data.split(":")
    if not _owner_ok(query, parts):
        await query.answer("این دکمه مال تو نیست 🙂", show_alert=True)
        return
    owner_id = parts[2] if len(parts) > 2 else query.from_user.id
    from game import botconfig

    cost = botconfig.get_energy_refill_cost()
    max_en = await run_db(_user_max_energy_sync, update.effective_user)
    await query.answer()
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(f"✅ بله ({cost} 💎)",
                             callback_data=f"enr:do:{owner_id}"),
        InlineKeyboardButton("❌ بی‌خیال", callback_data=f"enr:no:{owner_id}"),
    ]])
    await safe_edit_message_text(
        query,
        f"⚡ <b>شارژ کامل انرژی</b>\n\nانرژیت به <b>{max_en}</b> پر می‌شه و "
        f"<b>{cost}</b> الماس ازت کم می‌شه. تأیید می‌کنی؟",
        parse_mode="HTML", reply_markup=keyboard,
    )


async def energy_no_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _owner_ok(query, query.data.split(":")):
        await query.answer("این دکمه مال تو نیست 🙂", show_alert=True)
        return
    await query.answer()
    await safe_edit_message_text(query, "باشه، فعلاً شارژ نشد.")


def _refill_sync(tg_user):
    from game.energy import refill_energy

    user, _ = get_or_create_user(tg_user)
    return refill_energy(user)


async def energy_do_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _owner_ok(query, query.data.split(":")):
        await query.answer("این دکمه مال تو نیست 🙂", show_alert=True)
        return
    try:
        result = await run_db(_refill_sync, update.effective_user)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer("⚡ پر شد!")
    max_en = await run_db(_user_max_energy_sync, update.effective_user)
    is_private = update.effective_chat is not None and update.effective_chat.type == "private"
    if is_private:
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 بازگشت به بازی", callback_data="menu:me"),
        ]])
    else:
        from config import BOT_USERNAME

        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 ادامه توی پیوی ربات", url=f"https://t.me/{BOT_USERNAME}?start=play"),
        ]])
    await safe_edit_message_text(
        query,
        f"⚡ <b>انرژی پر شد!</b> الان {result['energy']}/{max_en} داری "
        f"(<b>{result['cost']}</b> الماس کم شد).\n<i>برگرد و کارتو ادامه بده 👇</i>",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


def register(application) -> None:
    application.add_handler(CallbackQueryHandler(energy_ask_callback, pattern=r"^enr:ask(:|$)"))
    application.add_handler(CallbackQueryHandler(energy_do_callback, pattern=r"^enr:do(:|$)"))
    application.add_handler(CallbackQueryHandler(energy_no_callback, pattern=r"^enr:no(:|$)"))

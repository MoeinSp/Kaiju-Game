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
    from bot.buttons import PRIMARY, SHOP, btn
    from config import BOT_USERNAME
    from game import botconfig

    cost = botconfig.get_energy_refill_cost()
    cb_ask = f"enr:ask:{owner_id}:{origin}" if origin else f"enr:ask:{owner_id}"
    row1 = [btn(f"شارژ کامل با {cost} الماس", emoji_key="btn_charge", style=PRIMARY, callback_data=cb_ask)]
    if is_group:
        row2 = [btn("خرید اشتراک نقره‌ای (۱۰۰ هزار تومان)", emoji_key="btn_sub_silver", style=SHOP, url=f"https://t.me/{BOT_USERNAME}?start=sub_silver")]
    else:
        cb = f"sub_pick:silver:{origin}" if origin else "sub_pick:silver"
        row2 = [btn("خرید اشتراک نقره‌ای (۱۰۰ هزار تومان)", emoji_key="btn_sub_silver", style=SHOP, callback_data=cb)]
    return InlineKeyboardMarkup([row1, row2])


def _owner_ok(query, parts) -> bool:
    """True if the tapping user owns this scoped energy button (or it's an old,
    unscoped button with no owner encoded — those stay tap-by-anyone as before)."""
    if len(parts) < 3:
        return True
    return query.from_user is not None and query.from_user.id == int(parts[2])


def _user_sub_info_sync(tg_user):
    from game.subscription import get_subscription_info
    user, _ = get_or_create_user(tg_user)
    return get_subscription_info(user)


async def show_energy_error(query, exc, owner_id: int | None = None, origin: str | None = None) -> bool:
    """If `exc` is an out-of-energy error, replace the message with it + the refill
    button and return True; otherwise return False so the caller shows it normally."""
    from bot.buttons import NAV, PRIMARY, btn
    from game import botconfig
    from game.energy import EnergyError

    if isinstance(exc, EnergyError):
        await query.answer()
        oid = owner_id if owner_id is not None else query.from_user.id
        is_group = query.message.chat.type in ("group", "supergroup") if query.message and query.message.chat else False
        if origin is None and getattr(query, "data", None):
            qdata = str(query.data)
            if is_group and ("hunt" in qdata or "autohunt" in qdata):
                origin = "ghunt"
            elif qdata.startswith("hunt") or qdata.startswith("autohunt"):
                origin = "hunt"
            elif qdata.startswith("arena"):
                origin = "arena"
            elif qdata.startswith("camp"):
                origin = "camp"
            elif qdata.startswith("feed") or qdata.startswith("lab") or qdata.startswith("up_"):
                origin = "upg"
        elif is_group and origin == "hunt":
            origin = "ghunt"

        info = await run_db(_user_sub_info_sync, query.from_user)
        cost = botconfig.get_energy_refill_cost()

        cb_ask = f"enr:ask:{oid}:{origin}" if origin else f"enr:ask:{oid}"
        if info["is_active"]:
            caption = (
                f"{str(exc)}\n\n"
                f"✨ <b>اشتراک {info['badge']} {info['tier_name']} برای شما فعال است</b> "
                f"(<b>{info['days_left']} روز و {info['hours_left']} ساعت</b> باقی‌مانده).\n\n"
                f"<i>💡 سقف انرژی شما ۱۰۰ است. می‌توانید با الماس آن را فوراً شارژ کامل کنید:</i>"
            )
            rows = [[btn(f"شارژ کامل با {cost} الماس", emoji_key="btn_charge", style=PRIMARY, callback_data=cb_ask)]]
            if origin == "hunt":
                rows.append([btn("بازگشت به شکار", emoji_key="btn_hunt", style=NAV, callback_data="hunt_next")])
            elif origin == "arena":
                rows.append([btn("بازگشت به آرنا", emoji_key="btn_arena", style=NAV, callback_data="arena_find")])
            markup = InlineKeyboardMarkup(rows)
        else:
            caption = (
                f"{str(exc)}\n\n"
                f"👑 <b>با تهیه اشتراک نقره‌ای:</b>\n"
                f"  ⚡️ <b>سقف انرژیت ۲ برابر می‌شه (۱۰۰ به جای ۵۰)!</b>\n"
                f"  📋 جعبه‌های آرنا خودکار و پشت‌سرهم باز می‌شن\n"
                f"  🏹 درآمدت از شکار خودکار ۲۵٪ بیشتر می‌شه!\n"
                f"  🥈 نشان پرمیوم نقره‌ای کنار اسمت قرار می‌گیره\n\n"
                f"<i>💡 فقط با ۱۰۰ هزار تومان، محدودیت انرژی رو برای همیشه فراموش کن!</i>"
            )
            markup = energy_refill_markup(oid, is_group=is_group, origin=origin)

        await safe_edit_message_text(query, caption, parse_mode="HTML", reply_markup=markup)
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
    origin = parts[3] if len(parts) > 3 else None
    from bot.buttons import CONFIRM, DANGER, btn
    from game import botconfig

    cost = botconfig.get_energy_refill_cost()
    max_en = await run_db(_user_max_energy_sync, update.effective_user)
    await query.answer()

    cb_do = f"enr:do:{owner_id}:{origin}" if origin else f"enr:do:{owner_id}"
    cb_no = f"enr:no:{owner_id}:{origin}" if origin else f"enr:no:{owner_id}"

    keyboard = InlineKeyboardMarkup([[
        btn(f"بله ({cost} 💎)", emoji_key="btn_confirm", style=CONFIRM, callback_data=cb_do),
        btn("بی‌خیال", emoji_key="btn_cancel", style=DANGER, callback_data=cb_no),
    ]])
    await safe_edit_message_text(
        query,
        f"⚡ <b>شارژ کامل انرژی</b>\n\nانرژیت به <b>{max_en}</b> پر می‌شه و "
        f"<b>{cost}</b> الماس ازت کم می‌شه. تأیید می‌کنی؟",
        parse_mode="HTML", reply_markup=keyboard,
    )


async def energy_no_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    parts = query.data.split(":")
    if not _owner_ok(query, parts):
        await query.answer("این دکمه مال تو نیست 🙂", show_alert=True)
        return
    owner_id = parts[2] if len(parts) > 2 else query.from_user.id
    origin = parts[3] if len(parts) > 3 else None
    await query.answer("منصرف شدید.")

    if origin == "ghunt":
        from bot.handlers.group_words import _card_sync, _render
        try:
            data = await run_db(_card_sync, update.effective_user, query.message.chat, "hunt")
            text, keyboard = _render("hunt", data)
            await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)
            return
        except Exception:
            pass

    await safe_edit_message_text(query, "باشه، فعلاً شارژ نشد.")


def _refill_sync(tg_user):
    from game.energy import refill_energy

    user, _ = get_or_create_user(tg_user)
    return refill_energy(user)


async def energy_do_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    parts = query.data.split(":")
    if not _owner_ok(query, parts):
        await query.answer("این دکمه مال تو نیست 🙂", show_alert=True)
        return
    owner_id = parts[2] if len(parts) > 2 else query.from_user.id
    origin = parts[3] if len(parts) > 3 else None

    try:
        result = await run_db(_refill_sync, update.effective_user)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer("⚡ پر شد!")
    max_en = await run_db(_user_max_energy_sync, update.effective_user)

    is_group = query.message.chat.type in ("group", "supergroup") if query.message and query.message.chat else False
    if origin == "ghunt" or (is_group and (origin == "hunt" or not origin)):
        from bot.handlers.group_words import _card_sync, _render
        try:
            data = await run_db(_card_sync, update.effective_user, query.message.chat, "hunt")
            text, keyboard = _render("hunt", data)
            note = f"⚡ <b>انرژی شما با موفقیت شارژ شد! ({result['energy']}/{max_en})</b> (<b>{result['cost']}</b> الماس کم شد)\n\n"
            await safe_edit_message_text(query, note + text, parse_mode="HTML", reply_markup=keyboard)
            return
        except Exception:
            pass

    is_private = update.effective_chat is not None and update.effective_chat.type == "private"
    if is_private:
        from bot.buttons import NAV, btn
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

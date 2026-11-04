"""Group flash-drop job + claim button.

game/groupdrops decides *what/where/when* (sync); this posts the drops, edits
lapsed ones, and settles the first-tap-wins claim.
"""

import asyncio
import datetime
import os

from django.utils import timezone
from telegram import InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import CallbackQueryHandler, ContextTypes

from bio_lab.models import GroupDrop
from bot.buttons import btn
from bot.utils import get_cached_file_id, invalidate_cached_file_id, run_db, safe_edit_message_text, send_screen, store_cached_file_id
from game import groupdrops
from game.emoji import get_emoji
from game.media import get_drop_image_path

DROPS_INTERVAL_SECONDS = 300  # a spawn check every 5 minutes
SEND_DELAY = 0.05


def _spawn_text(d: dict) -> str:
    """The 'a drop appeared' message — a couple of kinds get a bespoke layout."""
    if d["kind"] == "chest":
        return (
            "🎁 <b>سقوط صندوقچه‌ی گنج!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "🔥 یه صندوق پر از طلا افتاد وسط چت!\n"
            "⚡️ <i>اولین نفری که بزنه روی دکمه همه رو می‌بره:</i>"
        )
    if d["kind"] == "jackpot":
        return (
            "🌟 <b>ظهور جک‌پات نادر!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "💰 جایزه بزرگ طلا (تا سقف ۱۰,۰۰۰ طلا بر اساس قدرت کایجو)!\n"
            "⚡️ <i>اولین نفری که بزنه برنده می‌شه:</i>"
        )
    return (
        f"{d['emoji']} <b>{d['title']}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"{d['flavor']}\n\n"
        "⚡️ <i>اولین نفری که بزنه می‌بره! 👇</i>"
    )


def _win_text(kind: str, who: str, reward: dict) -> str:
    """The 'X won' message, with a clean structured layout for all drops."""
    cfg = groupdrops.DROP_KINDS.get(kind, {})
    title = cfg.get("title", "جایزه گروهی")
    emoji = cfg.get("emoji", "🎁")

    coins = reward.get("coins", 0)
    dna = reward.get("dna", 0)
    diamonds = reward.get("diamonds", 0)
    energy = reward.get("energy")

    reward_items = []
    if coins:
        reward_items.append(f"{get_emoji('coin')} طلا: <b>+{coins:,}</b>")
    if dna:
        reward_items.append(f"{get_emoji('dna')} دی‌ان‌ای: <b>+{dna:,}</b>")
    if diamonds:
        reward_items.append(f"{get_emoji('diamond')} الماس: <b>+{diamonds:,}</b>")
    if energy == "full":
        reward_items.append(f"{get_emoji('energy')} انرژی: <b>فول شارژ (۱۰۰٪)</b>")

    rewards_block = "\n".join(f"▫️ {item}" for item in reward_items) if reward_items else "▫️ بدون جایزه"

    lines = [
        f"{emoji} <b>{title}</b>",
        f"👤 برنده: {who}",
        "",
        "📦 <b>غنیمت دریافت شده:</b>",
        rewards_block,
    ]
    return "\n".join(lines)


def _delete_drop(drop_id: int) -> None:
    GroupDrop.objects.filter(id=drop_id).delete()


async def _delete_drop_message(context: ContextTypes.DEFAULT_TYPE) -> None:
    """An hour after a drop is won: delete its «X برد» message from the group, then the row
    (owner, 2026-11-04 — this replaces the 2026-10-16 «never auto-delete in groups» rule for drops)."""
    data = context.job.data
    try:
        await context.bot.delete_message(chat_id=data["group_id"], message_id=data["message_id"])
    except TelegramError:
        pass   # no delete right / already gone: nothing else to tidy
    await run_db(groupdrops.delete_row, data["drop_id"])


async def _remove_lapsed(context: ContextTypes.DEFAULT_TYPE, e: dict) -> None:
    """A drop nobody took is removed from the group as soon as it lapses (owner, 2026-11-01) —
    no «time's up» note. Without delete rights the button is just taken off the message."""
    try:
        await context.bot.delete_message(chat_id=e["group_id"], message_id=e["message_id"])
    except TelegramError:
        try:
            await context.bot.edit_message_reply_markup(chat_id=e["group_id"], message_id=e["message_id"], reply_markup=None)
        except TelegramError:
            pass
    await run_db(groupdrops.delete_row, e["id"])


async def _lapse_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    e = await run_db(groupdrops.expire_one, context.job.data)
    if e is not None:
        await _remove_lapsed(context, e)


async def drops_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    # spawn new drops
    for d in await run_db(groupdrops.due_spawns):
        text = _spawn_text(d)
        keyboard = InlineKeyboardMarkup([[btn(d["btn"], emoji_key=d["btn_key"], callback_data=f"gdrop:{d['id']}")]])
        photo_path = get_drop_image_path(d["kind"], state="locked")
        msg = None
        try:
            if photo_path and os.path.exists(photo_path):
                cached_fid = get_cached_file_id(photo_path)
                if cached_fid:
                    try:
                        msg = await context.bot.send_photo(
                            chat_id=d["group_id"], photo=cached_fid, caption=text, parse_mode="HTML", reply_markup=keyboard
                        )
                    except Exception:
                        invalidate_cached_file_id(photo_path)
                        msg = None
                if msg is None:
                    with open(photo_path, "rb") as f:
                        msg = await context.bot.send_photo(
                            chat_id=d["group_id"], photo=f, caption=text, parse_mode="HTML", reply_markup=keyboard
                        )
                    if msg and msg.photo:
                        store_cached_file_id(photo_path, msg.photo[-1].file_id)
            else:
                msg = await context.bot.send_message(chat_id=d["group_id"], text=text, parse_mode="HTML", reply_markup=keyboard)
            if msg:
                await run_db(groupdrops.set_message_id, d["id"], msg.message_id)
                if context.job_queue is not None:   # removed the moment it lapses (the sweep below is the fallback)
                    context.job_queue.run_once(_lapse_job, groupdrops.EXPIRE_MINUTES * 60 + 2, data=d["id"])
        except TelegramError:
            await run_db(_delete_drop, d["id"])  # bot not in the group anymore, etc.
        await asyncio.sleep(SEND_DELAY)

    # fallback for drops whose exact-time job a restart lost
    for e in await run_db(groupdrops.expire_due):
        await _remove_lapsed(context, e)
        await asyncio.sleep(SEND_DELAY)

    # fallback sweep: won drops whose scheduled delete a restart lost
    for e in await run_db(groupdrops.delete_due):
        try:
            await context.bot.delete_message(chat_id=e["group_id"], message_id=e["message_id"])
        except TelegramError:
            pass
        await asyncio.sleep(SEND_DELAY)


def _free_at(seconds_left: int) -> str:
    """Clock time (game timezone, Persian digits) a cooldown ends — «۱۴:۳۵»."""
    t = timezone.localtime(timezone.now() + datetime.timedelta(seconds=int(seconds_left) + 59))
    return f"{t:%H:%M}".translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))


async def drop_claim_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    drop_id = int(query.data.split(":")[1])
    result = await run_db(groupdrops.claim, drop_id, update.effective_user)

    status = result["status"]
    if status == "won":
        await query.answer(f"🎉 بردی! {groupdrops.reward_text(result['reward'])}", show_alert=True)
        who = result.get("winner_mention") or f"<b>{result['winner']}</b>"
        win_text = _win_text(result["kind"], who, result["reward"])
        open_photo = get_drop_image_path(result["kind"], state="open")
        try:
            await send_screen(
                query,
                win_text,
                photo=open_photo,
                reply_markup=InlineKeyboardMarkup([]),
                parse_mode="HTML",
            )
        except Exception:
            await safe_edit_message_text(
                query,
                win_text,
                parse_mode="HTML",
            )
        # keep the "X won Y" moment up for an hour, then delete it from the group
        if context.job_queue is not None and result.get("message_id"):
            context.job_queue.run_once(
                _delete_drop_message, groupdrops.DELETE_AFTER_WIN_SECONDS,
                data={"group_id": result["group_id"], "message_id": result["message_id"], "drop_id": result["drop_id"]},
            )
    elif status == "taken":
        await query.answer(f"⛔ دیر رسیدی! «{result['winner']}» زودتر زد.", show_alert=True)
    elif status == "cooldown":
        await query.answer(f"⏳ به‌تازگی یه جایزه گرفتی!\n\n🕐 جایزه‌ی بعدی از ساعت {_free_at(result['seconds_left'])} آزاد می‌شه.",
                           show_alert=True)
    elif status == "vein_cooldown":
        await query.answer(f"💎 به‌تازگی یه رگه‌ی الماس گرفتی!\n\n🕐 رگه‌ی بعدی از ساعت {_free_at(result['seconds_left'])} آزاد می‌شه.",
                           show_alert=True)
    elif status == "vein_limit":
        await query.answer(
            f"💎 سقف امروزت برای رگه‌ی الماس پر شده (روزی {result['cap']} تا). فردا دوباره.",
            show_alert=True,
        )
    elif status == "capsule_limit":
        await query.answer(
            "⚡ کپسول انرژی رو امروز قبلاً گرفتی (روزی فقط یک‌بار). فردا دوباره سر بزن.",
            show_alert=True,
        )
    elif status == "jackpot_limit":
        await query.answer(
            f"🌟 سقف امروزت برای جک‌پات پر شده (روزی حداکثر {result['cap']} بار). فردا دوباره شانس داری!",
            show_alert=True,
        )
    elif status == "expired":
        await query.answer("⌛ زمان این جایزه تموم شده.", show_alert=True)
    else:
        await query.answer("این جایزه دیگه در دسترس نیست.", show_alert=True)


def register(application) -> None:
    application.add_handler(CallbackQueryHandler(drop_claim_callback, pattern=r"^gdrop:"))
    job_queue = application.job_queue
    if job_queue is not None:
        job_queue.run_repeating(drops_job, interval=DROPS_INTERVAL_SECONDS, first=90)

"""Group flash-drop job + claim button.

game/groupdrops decides *what/where/when* (sync); this posts the drops, edits
lapsed ones, and settles the first-tap-wins claim.
"""

import asyncio

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import CallbackQueryHandler, ContextTypes

from bio_lab.models import GroupDrop
from bot.utils import run_db, safe_edit_message_text
from game import groupdrops
from game.emoji import get_emoji

DROPS_INTERVAL_SECONDS = 300  # a spawn check every 5 minutes
SEND_DELAY = 0.05


def _spawn_text(d: dict) -> str:
    """The 'a drop appeared' message — a couple of kinds get a bespoke layout."""
    if d["kind"] == "chest":
        return (
            "🎁 <b>سقوط صندوقچه‌ی گنج!</b>\n"
            "🔥 یه صندوق پر از طلا افتاد وسط چت!\n"
            "⚡️ اولین نفری که بزنه روی دکمه همه رو می‌بره:"
        )
    return (
        f"{d['emoji']} <b>{d['title']}</b>\n{d['flavor']}\n\n"
        "<i>اولین نفری که بزنه می‌بره! 👇</i>"
    )


def _win_text(kind: str, who: str, reward: dict) -> str:
    """The 'X won' message, with a per-kind bespoke layout for the flashy drops.
    `who` is an HTML mention of the winner."""
    coins = reward.get("coins", 0)
    dna = reward.get("dna", 0)
    diamonds = reward.get("diamonds", 0)
    if kind == "vein":
        return (
            "💎 <b>رگه‌ی الماس کشف شد!</b>\n"
            f"⚡️ دست‌جنبون‌ترین معدنچی: {who}\n"
            f"🎁 پاداش غارت: {diamonds} {get_emoji('diamond')}"
        )
    if kind == "capsule":
        return (
            f"🔋⚡️ {who} کپسول رو هوا زد!\n"
            f"🎁 {coins} طلا و انرژی کامل واریز شد."
        )
    if kind == "ambush":
        return (
            "⚔️ <b>رویداد: شکست هیولای وحشی</b>\n\n"
            f"🥇 قاتل هیولا: {who}\n"
            f"{get_emoji('coin')} طلا: {coins}\n"
            f"{get_emoji('dna')} دی‌ان‌ای: {dna}"
        )
    cfg = groupdrops.DROP_KINDS[kind]
    return (
        f"{cfg['emoji']} <b>{cfg['title']}</b>\n"
        f"🎉 {who} اولین نفر بود و <b>{groupdrops.reward_text(reward)}</b> برد!"
    )


def _delete_drop(drop_id: int) -> None:
    GroupDrop.objects.filter(id=drop_id).delete()


async def _delete_drop_message(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scheduled ~1 min after a drop lapses: remove the 'time's up' message so the
    group doesn't fill up with dead drops, and drop the row."""
    data = context.job.data
    try:
        await context.bot.delete_message(chat_id=data["group_id"], message_id=data["message_id"])
    except TelegramError:
        pass
    await run_db(groupdrops.delete_row, data["drop_id"])


async def drops_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    # spawn new drops
    for d in await run_db(groupdrops.due_spawns):
        text = _spawn_text(d)
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(d["btn"], callback_data=f"gdrop:{d['id']}")]])
        try:
            msg = await context.bot.send_message(chat_id=d["group_id"], text=text, parse_mode="HTML", reply_markup=keyboard)
            await run_db(groupdrops.set_message_id, d["id"], msg.message_id)
        except TelegramError:
            await run_db(_delete_drop, d["id"])  # bot not in the group anymore, etc.
        await asyncio.sleep(SEND_DELAY)

    # edit any that lapsed unclaimed, then schedule their message for deletion a
    # minute later so the "time's up" note doesn't linger and clutter the group
    for e in await run_db(groupdrops.expire_due):
        try:
            await context.bot.edit_message_text(
                chat_id=e["group_id"], message_id=e["message_id"],
                text="⌛ <b>زمان این جایزه تموم شد</b> — کسی به‌موقع نزد.", parse_mode="HTML",
            )
        except TelegramError:
            pass
        if context.job_queue is not None:
            context.job_queue.run_once(
                _delete_drop_message, groupdrops.DELETE_AFTER_EXPIRE_SECONDS,
                data={"group_id": e["group_id"], "message_id": e["message_id"], "drop_id": e["id"]},
            )
        await asyncio.sleep(SEND_DELAY)

    # fallback sweep: delete lapsed messages whose scheduled delete was lost to a
    # restart (their grace minute is already up)
    for d in await run_db(groupdrops.delete_due):
        try:
            await context.bot.delete_message(chat_id=d["group_id"], message_id=d["message_id"])
        except TelegramError:
            pass
        await asyncio.sleep(SEND_DELAY)


async def drop_claim_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    drop_id = int(query.data.split(":")[1])
    result = await run_db(groupdrops.claim, drop_id, update.effective_user)

    status = result["status"]
    if status == "won":
        await query.answer(f"🎉 بردی! {groupdrops.reward_text(result['reward'])}", show_alert=True)
        who = result.get("winner_mention") or f"<b>{result['winner']}</b>"
        await safe_edit_message_text(
            query,
            _win_text(result["kind"], who, result["reward"]),
            parse_mode="HTML",
        )
        # keep the "X won Y" moment up for a while, then tidy it away
        if context.job_queue is not None and result.get("message_id"):
            context.job_queue.run_once(
                _delete_drop_message, groupdrops.DELETE_AFTER_WIN_SECONDS,
                data={"group_id": result["group_id"], "message_id": result["message_id"], "drop_id": result["drop_id"]},
            )
    elif status == "taken":
        await query.answer(f"⛔ دیر رسیدی! «{result['winner']}» زودتر زد.", show_alert=True)
    elif status == "cooldown":
        m, s = divmod(result["seconds_left"], 60)
        await query.answer(
            f"⏳ به‌تازگی یه جایزه گرفتی — {m}:{s:02d} دیگه صبر کن (تا نشه توی چند گروه همزمان جمعش کرد).",
            show_alert=True,
        )
    elif status == "vein_cooldown":
        total_min = result["seconds_left"] // 60
        h, m = divmod(total_min, 60)
        wait_txt = f"{h} ساعت و {m} دقیقه" if h else f"{m} دقیقه"
        await query.answer(
            f"💎 رگه‌ی الماس کول‌داون داره — {wait_txt} دیگه می‌تونی یکی دیگه برداری "
            "(تا نشه الماس رو بیش از حد از چند گروه جمع کرد).",
            show_alert=True,
        )
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
    elif status == "expired":
        await query.answer("⌛ زمان این جایزه تموم شده.", show_alert=True)
    else:
        await query.answer("این جایزه دیگه در دسترس نیست.", show_alert=True)


def register(application) -> None:
    application.add_handler(CallbackQueryHandler(drop_claim_callback, pattern=r"^gdrop:"))
    job_queue = application.job_queue
    if job_queue is not None:
        job_queue.run_repeating(drops_job, interval=DROPS_INTERVAL_SECONDS, first=90)

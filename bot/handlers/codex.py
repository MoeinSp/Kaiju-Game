"""«📖 دانشنامه» — the Codex screen: which of the 20 species you've discovered,
grouped by element, with milestone rewards to claim."""

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

from bio_lab.repository import get_or_create_user
from bot.buttons import CONFIRM, back_btn, btn
from bot.utils import run_db, safe_edit_message_text, send_screen
from game import codex


def _panel_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    return user, codex.status(user)


def _render(st: dict) -> tuple[str, InlineKeyboardMarkup]:
    lines = [
        f"📖 <b>دانشنامه‌ی هیولاها</b>  ({st['discovered']}/{st['total']})",
        "<blockquote>هر گونه‌ای که یه‌بار داشته باشی برای همیشه اینجا ثبت می‌شه. "
        "با کامل‌کردن دسته‌ها و کل دانشنامه جایزه بگیر.</blockquote>",
    ]
    for grp in st["elements"]:
        tick = " ✅" if grp["complete"] else ""
        names = "  ".join(f"✅ {s['name']}" if s["found"] else "❓ ؟؟؟" for s in grp["species"])
        lines.append(f"\n{grp['label']}{tick}\n  {names}")
    milestones = "، ".join(
        f"{n} گونه" for n in st["count_milestones"]
    )
    lines.append(f"\n<i>پاداش‌ها: {milestones}، و کامل‌کردن هر دسته.</i>")

    rows = []
    if st["claimable"]:
        rows.append([btn(f"🎁 دریافت جوایز ({st['claimable']})", emoji_key="btn_confirm", style=CONFIRM, callback_data="codex_claim")])
    rows.append([back_btn("menu:cat_rewards", "بازگشت به جایزه‌ها")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def codex_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user, st = await run_db(_panel_sync, update.effective_user)
    text, keyboard = _render(st)
    await send_screen(update, text, parse_mode="HTML", reply_markup=keyboard)


def _claim_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    result = codex.claim(user)
    return result, codex.status(user)


async def codex_claim_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    result, st = await run_db(_claim_sync, update.effective_user)
    if not result["claimed"]:
        await query.answer("چیزی برای دریافت نیست.", show_alert=True)
        return
    await query.answer(f"🎉 {result['claimed']} جایزه‌ی دانشنامه گرفتی!")
    got = codex._reward_text(result["reward"])
    text, keyboard = _render(st)
    await safe_edit_message_text(
        query,
        f"🎉 <b>جایزه‌ی دانشنامه دریافت شد!</b>\n🎁 <b>{got}</b>\n\n━━━━━━━━━━\n" + text,
        parse_mode="HTML",
        reply_markup=keyboard,
    )


def register(application) -> None:
    application.add_handler(CommandHandler("codex", codex_panel, filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(codex_claim_callback, pattern=r"^codex_claim$"))

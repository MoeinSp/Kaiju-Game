"""«🧫 تکثیر زیستی» — the propagation screens.

Three states, one screen each: nothing running (pick two parents), a job ticking
down (wait / cancel), and a finished job (collect). The panel always re-renders
into whichever state applies, so a player can leave and come back without the
bot needing to remember where they were.
"""

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

from bio_lab.models import Creature
from bio_lab.repository import get_or_create_user
from bot.buttons import BUILD, CONFIRM, DANGER, LIST, PRIMARY, back_btn, back_only_keyboard, btn
from bot.utils import run_db, safe_edit_message_text, send_screen
from game import breeding, constants
from game.buildings import is_built
from game.creature import GameError
from game.emoji import get_emoji

_AWAIT_KEY = "breeding_parent_a"


def _format_remaining(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, _ = divmod(rem, 60)
    if hours:
        return f"{hours} ساعت و {minutes} دقیقه"
    if minutes:
        return f"{minutes} دقیقه"
    return "چند لحظه"


def _panel_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    job = breeding.active_job(user)
    return {
        "user": user,
        "built": is_built(user, breeding.BREEDING_BUILDING),
        "job": job,
        "ready": job is not None and breeding.ready(job),
        "seconds_left": breeding.seconds_left(job) if job is not None else 0,
        "candidates": breeding.parent_candidates(user),
    }


def _panel_render(view: dict) -> tuple[str, InlineKeyboardMarkup]:
    hall = constants.BUILDING_LABELS[breeding.BREEDING_BUILDING]

    if not view["built"]:
        text = (
            f"{get_emoji('lab')} <b>تکثیر زیستی</b>\n\n"
            f"🔒 اول باید {hall} رو از «🏗 ساختمون‌ها» بسازی."
        )
        rows = [
            [btn("رفتن به ساختمون‌ها", emoji_key="btn_buildings", style=PRIMARY, callback_data="menu:buildings")],
            [back_btn("menu:me")],
        ]
        return text, InlineKeyboardMarkup(rows)

    job = view["job"]
    if job is not None:
        parents = f"{job.parent_a.name} + {job.parent_b.name}"
        if view["ready"]:
            text = (
                f"{get_emoji('egg')} <b>تکثیر آماده‌ست!</b>\n\n"
                f"<blockquote>{parents}</blockquote>\n"
                "بزن تا فرزندشون رو تحویل بگیری."
            )
            rows = [
                [btn("تحویل بگیر", emoji_key="btn_confirm", style=CONFIRM, callback_data="brd_collect")],
                [back_btn("menu:me")],
            ]
        else:
            text = (
                f"{get_emoji('lab')} <b>تکثیر در جریانه</b>\n\n"
                f"<blockquote>{parents}</blockquote>\n"
                f"⏳ <b>{_format_remaining(view['seconds_left'])}</b> مونده\n\n"
                "<i>تا وقتی تموم نشده، این دو تا نه می‌جنگن نه سر کار می‌رن.</i>"
            )
            rows = [
                [btn("لغو تکثیر", emoji_key="btn_cancel", style=DANGER, callback_data="brd_cancel")],
                [back_btn("menu:me")],
            ]
        return text, InlineKeyboardMarkup(rows)

    candidates = view["candidates"]
    text = (
        f"{get_emoji('lab')} <b>تکثیر زیستی</b>\n"
        "<blockquote>دو هیولای آزاد رو انتخاب کن تا از روشون یه هیولای تازه ساخته بشه. "
        "والدین سوزونده <b>نمی‌شن</b> — فقط تا آخر کار مشغولن.\n"
        "هم‌عنصر یا هم‌نژاد بودن و قدرت بالاتر، شانس درجه‌ی بهتر رو بیشتر می‌کنه.</blockquote>\n"
    )
    if len(candidates) < 2:
        text += (
            "\n⚠️ حداقل <b>دو</b> هیولای آزاد لازم داری.\n"
            "<i>موجود فعال و هیولاهایی که سر کارن حساب نمی‌شن.</i>"
        )
        return text, InlineKeyboardMarkup([[back_btn("menu:me")]])

    text += "\n<b>والد اول رو انتخاب کن:</b>"
    rows = [
        [
            btn(
                f"{c.name} {'⭐' * c.star_level} · {constants.RARITY_LABELS[c.rarity]} · Lv{c.level}",
                style=LIST,
                callback_data=f"brd_a:{c.id}",
            )
        ]
        for c in candidates[:12]
    ]
    rows.append([back_btn("menu:me")])
    return text, InlineKeyboardMarkup(rows)


async def breeding_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    view = await run_db(_panel_sync, update.effective_user)
    text, keyboard = _panel_render(view)
    await send_screen(update, text, reply_markup=keyboard)


def _pick_b_sync(tg_user, parent_a_id):
    user, _ = get_or_create_user(tg_user)
    try:
        parent_a = Creature.objects.get(id=parent_a_id, owner=user)
    except Creature.DoesNotExist:
        raise GameError("این موجود توی کلکسیون تو نیست.")
    return parent_a, breeding.parent_candidates(user, exclude_id=parent_a.id)


async def breeding_pick_a_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    parent_a_id = int(query.data.split(":")[1])
    try:
        parent_a, candidates = await run_db(_pick_b_sync, update.effective_user, parent_a_id)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer()
    if not candidates:
        await safe_edit_message_text(
            query,
            "⚠️ هیولای آزاد دیگه‌ای برای والد دوم نداری.",
            parse_mode="HTML",
            reply_markup=back_only_keyboard("menu:breeding"),
        )
        return
    rows = [
        [
            btn(
                f"{c.name} {'⭐' * c.star_level} · {constants.RARITY_LABELS[c.rarity]} · Lv{c.level}",
                style=LIST,
                callback_data=f"brd_b:{parent_a.id}:{c.id}",
            )
        ]
        for c in candidates[:12]
    ]
    rows.append([back_btn("menu:breeding")])
    await safe_edit_message_text(
        query,
        f"{get_emoji('lab')} والد اول: <b>{parent_a.name}</b> "
        f"({constants.RARITY_LABELS[parent_a.rarity]})\n\n<b>حالا والد دوم رو انتخاب کن:</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(rows),
    )


def _preview_sync(tg_user, id_a, id_b):
    user, _ = get_or_create_user(tg_user)
    try:
        parent_a = Creature.objects.get(id=id_a, owner=user)
        parent_b = Creature.objects.get(id=id_b, owner=user)
    except Creature.DoesNotExist:
        raise GameError("این موجود توی کلکسیون تو نیست.")
    return user, parent_a, parent_b, breeding.preview(user, parent_a, parent_b)


async def breeding_pick_b_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, id_a, id_b = query.data.split(":")
    try:
        user, parent_a, parent_b, info = await run_db(
            _preview_sync, update.effective_user, int(id_a), int(id_b)
        )
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer()

    reasons = []
    if info["same_element"]:
        reasons.append(f"هم‌عنصر (+{constants.BREEDING_SAME_ELEMENT_BONUS * 100:.0f}٪)")
    if info["same_species"]:
        reasons.append(f"هم‌نژاد (+{constants.BREEDING_SAME_SPECIES_BONUS * 100:.0f}٪)")
    bonus_line = f"\n✨ {'، '.join(reasons)}" if reasons else ""

    next_rarity = constants.next_rarity(info["base_rarity"])
    upgrade_line = (
        f"🎲 شانس ارتقا به {constants.RARITY_LABELS[next_rarity]}: <b>{info['upgrade_chance'] * 100:.0f}٪</b>"
        if next_rarity != info["base_rarity"]
        else "🎲 بالاترین درجه — بالاتر از این نمی‌ره"
    )

    text = (
        f"{get_emoji('lab')} <b>تأیید تکثیر</b>\n\n"
        f"<blockquote>{parent_a.name} ({constants.RARITY_LABELS[parent_a.rarity]}) "
        f"+ {parent_b.name} ({constants.RARITY_LABELS[parent_b.rarity]})</blockquote>\n"
        f"⏳ زمان: <b>{_format_remaining(info['minutes'] * 60)}</b>\n"
        f"{get_emoji('dna')} هزینه: <b>{info['dna']}</b> (موجودی: {user.dna_fragments})\n"
        f"🧬 درجه‌ی پایه: {constants.RARITY_LABELS[info['base_rarity']]}\n"
        f"{upgrade_line}{bonus_line}\n\n"
        "<i>هر دو والد تا آخر کار مشغول می‌مونن — نه می‌جنگن، نه سر کار می‌رن.</i>"
    )
    rows = [
        [
            btn("شروع کن", emoji_key="btn_confirm", style=CONFIRM, callback_data=f"brd_go:{parent_a.id}:{parent_b.id}"),
            btn("بی‌خیال", emoji_key="btn_cancel", style=DANGER, callback_data="menu:breeding"),
        ]
    ]
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))


def _start_sync(tg_user, id_a, id_b):
    user, _ = get_or_create_user(tg_user)
    try:
        parent_a = Creature.objects.get(id=id_a, owner=user)
        parent_b = Creature.objects.get(id=id_b, owner=user)
    except Creature.DoesNotExist:
        raise GameError("این موجود توی کلکسیون تو نیست.")
    breeding.start(user, parent_a, parent_b)
    return _panel_sync(tg_user)


async def breeding_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, id_a, id_b = query.data.split(":")
    try:
        view = await run_db(_start_sync, update.effective_user, int(id_a), int(id_b))
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer("🧫 شروع شد!")
    text, keyboard = _panel_render(view)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


def _collect_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    child, info = breeding.collect(user)
    return child, info, _panel_sync(tg_user)


async def breeding_collect_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        child, info, view = await run_db(_collect_sync, update.effective_user)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer("🥚 متولد شد!")
    upgrade_note = (
        f"\n✨ <b>درجه‌ش یه پله بالاتر از والدین دراومد!</b>" if info["upgraded"] else ""
    )
    text, keyboard = _panel_render(view)
    await safe_edit_message_text(
        query,
        f"{get_emoji('egg')} <b>یه هیولای تازه متولد شد!</b>\n\n"
        f"<tg-spoiler>{child.name} · {constants.RARITY_LABELS[child.rarity]} · سطح {child.level}</tg-spoiler>"
        f"{upgrade_note}\n\n"
        f"<i>از {info['parents'][0]} و {info['parents'][1]}</i>\n\n"
        "━━━━━━━━━━\n" + text,
        parse_mode="HTML",
        reply_markup=keyboard,
    )


def _cancel_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    breeding.cancel(user)
    return _panel_sync(tg_user)


async def breeding_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        view = await run_db(_cancel_sync, update.effective_user)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer("لغو شد — DNA برنمی‌گرده.")
    text, keyboard = _panel_render(view)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


def register(application) -> None:
    application.add_handler(CommandHandler("breeding", breeding_panel, filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(breeding_pick_a_callback, pattern=r"^brd_a:"))
    application.add_handler(CallbackQueryHandler(breeding_pick_b_callback, pattern=r"^brd_b:"))
    application.add_handler(CallbackQueryHandler(breeding_start_callback, pattern=r"^brd_go:"))
    application.add_handler(CallbackQueryHandler(breeding_collect_callback, pattern=r"^brd_collect$"))
    application.add_handler(CallbackQueryHandler(breeding_cancel_callback, pattern=r"^brd_cancel$"))

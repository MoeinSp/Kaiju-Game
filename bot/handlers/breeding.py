"""«🕳 غار هیولا» — the Monster Cave (propagation) screens.

Two creatures go into the cave and lay a mystery egg that hatches over real
time. The whole point is the surprise: while the egg incubates, NOTHING about
what's inside is shown — not the species, not the rarity — because the child
isn't even rolled until the moment it hatches (see game/breeding.collect). The
reveal only happens on hatch, under a Telegram spoiler.

Three states, one screen each: nothing running (send two parents in), an egg
ticking down (wait / abandon), and a ready egg (hatch). The panel always
re-renders into whichever state applies, so a player can leave and come back
without the bot needing to remember where they were.
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


def _odds_band(chance: float) -> str:
    """Qualitative label for the rarer-egg chance. Deliberately NOT an exact
    percentage — the cave is meant to feel like a gamble, and a precise number
    turns the mystery into a spreadsheet. Still enough signal to reward a good
    pairing (matching element/species/power all push the band up)."""
    if chance <= 0:
        return "—"
    if chance >= 0.35:
        return "🟢 زیاد"
    if chance >= 0.18:
        return "🟡 متوسط"
    return "🔴 کم"


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
    eggs = breeding.active_eggs(user)
    return {
        "user": user,
        "built": is_built(user, breeding.BREEDING_BUILDING),
        "job": job,
        "job_ready": job is not None and breeding.ready(job),
        "job_seconds_left": breeding.seconds_left(job) if job is not None else 0,
        "cave_finish_price": breeding.cave_finish_price(job) if job is not None else 0,
        "eggs": [
            {
                "id": e.id,
                "ready": breeding.egg_ready(e),
                "seconds_left": breeding.egg_seconds_left(e),
                "finish_price": breeding.egg_finish_price(e),
            }
            for e in eggs
        ],
        "free_count": len(breeding.parent_candidates(user)),
    }


def _parent_a_render(candidates: list) -> tuple[str, InlineKeyboardMarkup]:
    text = (
        "🕳 <b>غار هیولا — جفت بفرست</b>\n"
        "<blockquote>دو هیولای آزاد رو بفرست توی غار. اول جفت‌گیری می‌کنن، بعد یه <b>تخم</b> "
        "می‌ذارن و آزاد می‌شن؛ تخم جدا رشد می‌کنه تا سر باز کنه.</blockquote>\n"
        "\n<b>والد اول رو انتخاب کن:</b>"
    )
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
    rows.append([back_btn("menu:breeding")])
    return text, InlineKeyboardMarkup(rows)


def _panel_render(view: dict) -> tuple[str, InlineKeyboardMarkup]:
    hall = constants.BUILDING_LABELS[breeding.BREEDING_BUILDING]

    if not view["built"]:
        text = (
            f"🕳 <b>غار هیولا</b>\n\n"
            f"🔒 اول باید {hall} رو از «🏗 ساختمون‌ها» بسازی تا غار باز شه."
        )
        rows = [
            [btn("رفتن به ساختمون‌ها", emoji_key="btn_buildings", style=PRIMARY, callback_data="menu:buildings")],
            [back_btn("menu:me")],
        ]
        return text, InlineKeyboardMarkup(rows)

    lines = ["🕳 <b>غار هیولا</b>", ""]
    rows: list = []

    # ── cave (phase 1: mating) ────────────────────────────────────────────────
    job = view["job"]
    if job is not None:
        parents = f"{job.parent_a.name} + {job.parent_b.name}"
        if view["job_ready"]:
            lines.append(f"💞 <b>جفت‌گیری تموم شد!</b>  <blockquote>{parents}</blockquote>")
            lines.append("بزن تا تخم بذارن و از غار آزاد شن.")
            rows.append([btn("🥚 تخم بذار", emoji_key="btn_confirm", style=CONFIRM, callback_data="brd_lay")])
        else:
            lines.append(f"💞 یه جفت توی غارن:  <blockquote>{parents}</blockquote>")
            lines.append(f"⏳ <b>{_format_remaining(view['job_seconds_left'])}</b> تا تخم‌گذاری")
            rows.append(
                [
                    btn(f"💎 فوری‌کن ({view['cave_finish_price']})", style=PRIMARY, callback_data="brd_cave_finish"),
                    btn("لغو", emoji_key="btn_cancel", style=DANGER, callback_data="brd_cancel"),
                ]
            )
    else:
        if view["free_count"] >= 2:
            lines.append("🕳 غار خالیه — یه جفت بفرست تا جفت‌گیری کنن و تخم بذارن.")
            rows.append([btn("🐣 جفت بفرست غار", emoji_key="btn_confirm", style=CONFIRM, callback_data="brd_new")])
        else:
            lines.append("🕳 غار خالیه. برای جفت‌گیری حداقل <b>دو</b> هیولای آزاد لازم داری.")
            lines.append("<i>موجود فعال و هیولاهایی که سر کارن حساب نمی‌شن.</i>")

    # ── eggs (phase 2: incubating, decoupled from the cave) ───────────────────
    eggs = view["eggs"]
    if eggs:
        lines.append("")
        lines.append(f"{get_emoji('egg')} <b>تخم‌های در حال رشد ({len(eggs)}):</b>")
        for i, e in enumerate(eggs, 1):
            if e["ready"]:
                lines.append(f"  🐣 تخم #{i} — <b>آماده‌ی سر باز کردنه!</b>")
                rows.append([btn(f"🐣 سر باز کن تخم #{i}", style=CONFIRM, callback_data=f"brd_hatch:{e['id']}")])
            else:
                lines.append(f"  🥚 تخم #{i} — ⏳ {_format_remaining(e['seconds_left'])}")
                rows.append(
                    [btn(f"💎 فوری‌کن تخم #{i} ({e['finish_price']})", style=PRIMARY, callback_data=f"brd_egg_finish:{e['id']}")]
                )
        lines.append("\n<i>چی توی تخم‌هاست؟ تا سر باز نکنن هیچ‌کس نمی‌دونه.</i>")

    lines.append(f"\n{get_emoji('diamond')} موجودی الماس: {view['user'].diamonds}")
    rows.append([back_btn("menu:me")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


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
        reasons.append("هم‌عنصر")
    if info["same_species"]:
        reasons.append("هم‌نژاد")
    bonus_line = f"\n✨ جفت خوبیه: {'، '.join(reasons)}" if reasons else ""

    next_rarity = constants.next_rarity(info["base_rarity"])
    rarer_line = (
        f"🎲 شانس یه تخمِ نایاب‌تر: <b>{_odds_band(info['upgrade_chance'])}</b>"
        if next_rarity != info["base_rarity"]
        else "🎲 والدینت از بالاترین درجه‌ان — نایاب‌تر از این نمی‌شه"
    )

    text = (
        f"🕳 <b>فرستادن به غار هیولا</b>\n\n"
        f"<blockquote>{parent_a.name} ({constants.RARITY_LABELS[parent_a.rarity]}) "
        f"+ {parent_b.name} ({constants.RARITY_LABELS[parent_b.rarity]})</blockquote>\n"
        f"💞 زمان جفت‌گیری: <b>{_format_remaining(info['mating_minutes'] * 60)}</b> "
        "<i>(بعدش والدها آزاد می‌شن)</i>\n"
        f"🥚 بعدش رشد تخم: <b>{_format_remaining(info['hatch_minutes'] * 60)}</b>\n"
        f"{get_emoji('dna')} هزینه: <b>{info['dna']}</b> DNA (موجودی: {user.dna_fragments})\n"
        f"{rarer_line}{bonus_line}\n\n"
        f"{get_emoji('egg')} <b>چی از تخم درمیاد؟ تا سر باز نکنه هیچ‌کس نمی‌دونه.</b>\n"
        "<i>فقط تا وقتِ تخم‌گذاری این دو مشغولن؛ بعدش می‌تونی جفت بعدی رو بفرستی.</i>"
    )
    rows = [
        [
            btn("بذارش توی غار", emoji_key="btn_confirm", style=CONFIRM, callback_data=f"brd_go:{parent_a.id}:{parent_b.id}"),
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
    await query.answer("💞 رفتن توی غار!")
    text, keyboard = _panel_render(view)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


def _new_pair_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    return breeding.parent_candidates(user)


async def breeding_new_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """«جفت بفرست غار» — open the parent picker."""
    query = update.callback_query
    candidates = await run_db(_new_pair_sync, update.effective_user)
    await query.answer()
    if len(candidates) < 2:
        await safe_edit_message_text(
            query,
            "⚠️ حداقل <b>دو</b> هیولای آزاد لازم داری.",
            parse_mode="HTML",
            reply_markup=back_only_keyboard("menu:breeding"),
        )
        return
    text, keyboard = _parent_a_render(candidates)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


def _lay_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    breeding.lay_egg(user)
    return _panel_sync(tg_user)


async def breeding_lay_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Mating done → lay the egg and free the parents."""
    query = update.callback_query
    try:
        view = await run_db(_lay_sync, update.effective_user)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer("🥚 تخم گذاشته شد! والدها آزاد شدن.")
    text, keyboard = _panel_render(view)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


def _hatch_sync(tg_user, egg_id):
    user, _ = get_or_create_user(tg_user)
    child, info = breeding.hatch(user, egg_id)
    return child, info, _panel_sync(tg_user)


async def breeding_hatch_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    egg_id = int(query.data.split(":")[1])
    try:
        child, info, view = await run_db(_hatch_sync, update.effective_user, egg_id)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer("🐣 تخم سر باز کرد!")
    upgrade_note = "\n✨ <b>نایاب‌تر از والدینش دراومد!</b>" if info["upgraded"] else ""
    text, keyboard = _panel_render(view)
    await safe_edit_message_text(
        query,
        f"🐣 <b>تخم سر باز کرد!</b> ببین چی توش بود:\n\n"
        f"<tg-spoiler>{child.name} · {constants.RARITY_LABELS[child.rarity]} · سطح {child.level}</tg-spoiler>"
        f"{upgrade_note}\n\n"
        f"<i>از {info['parents'][0]} و {info['parents'][1]}</i>\n\n"
        "━━━━━━━━━━\n" + text,
        parse_mode="HTML",
        reply_markup=keyboard,
    )


def _cave_finish_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    breeding.finish_cave_with_diamonds(user)
    return _panel_sync(tg_user)


async def breeding_cave_finish_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        view = await run_db(_cave_finish_sync, update.effective_user)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer("💎 فوری شد — تخم گذاشته شد!")
    text, keyboard = _panel_render(view)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


def _egg_finish_sync(tg_user, egg_id):
    user, _ = get_or_create_user(tg_user)
    breeding.finish_egg_with_diamonds(user, egg_id)
    return _panel_sync(tg_user)


async def breeding_egg_finish_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    egg_id = int(query.data.split(":")[1])
    try:
        view = await run_db(_egg_finish_sync, update.effective_user, egg_id)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer("💎 فوری شد — آماده‌ی سر باز کردنه!")
    text, keyboard = _panel_render(view)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


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
    application.add_handler(CallbackQueryHandler(breeding_new_callback, pattern=r"^brd_new$"))
    application.add_handler(CallbackQueryHandler(breeding_pick_a_callback, pattern=r"^brd_a:"))
    application.add_handler(CallbackQueryHandler(breeding_pick_b_callback, pattern=r"^brd_b:"))
    application.add_handler(CallbackQueryHandler(breeding_start_callback, pattern=r"^brd_go:"))
    application.add_handler(CallbackQueryHandler(breeding_lay_callback, pattern=r"^brd_lay$"))
    application.add_handler(CallbackQueryHandler(breeding_hatch_callback, pattern=r"^brd_hatch:"))
    application.add_handler(CallbackQueryHandler(breeding_cave_finish_callback, pattern=r"^brd_cave_finish$"))
    application.add_handler(CallbackQueryHandler(breeding_egg_finish_callback, pattern=r"^brd_egg_finish:"))
    application.add_handler(CallbackQueryHandler(breeding_cancel_callback, pattern=r"^brd_cancel$"))

"""Mugen Tower (برج موگن - 無限の塔) UI & Bot Handlers."""

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

from bio_lab.models import Creature, User
from bio_lab.repository import get_active_creature, get_or_create_user
from bot.buttons import BATTLE, CONFIRM, LIST, NAV, back_btn, back_only_keyboard, btn
from bot.utils import run_db, safe_edit_message_text, send_screen
from game import constants, mugen_tower
from game.creature import GameError
from game.emoji import get_emoji
from game.energy import sync_energy


def _mugen_panel_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    st = mugen_tower.get_mugen_status(user)
    creature = get_active_creature(user)
    return {
        "status": st,
        "energy": sync_energy(user),
        "creature": creature,
    }


def _render_mugen_text(view: dict) -> str:
    st = view["status"]
    c = view["creature"]
    rew = st["rewards"]

    c_name = c.name if c else "بدون موجود فعال"
    elem_label = constants.element_label(st["guardian_element"])

    lines = [
        f"{get_emoji('mugen')} <b>برج موگن (無限の塔) — طبقه <code>{st['floor']}</code></b>",
        "<i>سیاه‌چال بی‌پایان و نبردهای مرگبار با نگهبانان باستانی</i>\n",
        "━━━━━━━━━━━━━━━━━━━━",
        f"{get_emoji('hunt')} <b>نگهبان این طبقه:</b> {st['guardian_name']}",
        f"🏷 نایابی: <b>{constants.RARITY_LABELS.get(st['guardian_rarity'], st['guardian_rarity'])}</b>",
        f"🔮 عنصر: <b>{elem_label}</b>",
        f"{get_emoji('power')} قدرت نگهبان: <code>{st['guardian_power']:,}</code>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"🦖 موجود فعال شما: <b>{c_name}</b>",
        "",
        "<blockquote>",
        f"{get_emoji('gift')} <b>پاداش فتح این طبقه:</b>\n",
        f"{get_emoji('coin')} طلا: <code>+{rew['coins']:,}</code>\n",
        f"{get_emoji('dna')} DNA: <code>+{rew['dna']:,}</code>",
    ]
    if rew.get("diamonds"):
        lines.append(f"{get_emoji('diamond')} الماس: <code>+{rew['diamonds']:,}</code>")
    if rew.get("tickets"):
        lines.append(f"🎫 بلیط: <code>+{rew['tickets']:,}</code>")

    lines += [
        f"\n\n{get_emoji('energy')} <b>هزینه ورود:</b> <code>{mugen_tower.MUGEN_ENERGY_COST:,}</code> انرژی\n",
        f"{get_emoji('energy')} <b>انرژی فعلی:</b> <code>{view['energy']:,}</code>",
        "</blockquote>",
    ]
    return "\n".join(lines)


def _render_mugen_keyboard(view: dict, is_group: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [btn("نبرد با نگهبان", emoji_key="btn_attack", style=BATTLE, callback_data="mugen:fight")],
        [
            btn("انتخاب کایجو", emoji_key="btn_swap", style=NAV, callback_data="mugen:swap"),
            btn("برترین فاتحان", emoji_key="btn_rank", style=NAV, callback_data="mugen:lb"),
        ],
    ]
    if not is_group:
        rows.append([back_btn("menu:me")])
    return InlineKeyboardMarkup(rows)


def _team_choices_sync(tg_user):
    from bio_lab.repository import team_choices
    from game.workers import creature_status
    from game.creature import creature_power
    from game.equipment import get_equipped_items

    user, _ = get_or_create_user(tg_user)
    out = []
    for c in team_choices(user):
        busy = (not c.is_active) and creature_status(user, c) is not None
        pwr = creature_power(c, get_equipped_items(c))
        out.append((c.id, c.name, c.element, pwr, c.is_active, busy))
    return out


async def mugen_swap_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    choices = await run_db(_team_choices_sync, update.effective_user)
    rows = []
    for cid, name, element, power, is_active, busy in choices:
        tag = "🟢 " if is_active else ("⛔ " if busy else "🦖 ")
        elem_lbl = constants.ELEMENT_LABELS.get(element, "")
        note = " (مشغول)" if busy else ""
        rows.append([btn(f"{tag}{name} ({elem_lbl}) · {power:,}{note}",
                         style=BATTLE, callback_data=f"mugen:swap_pick:{cid}")])
    rows.append([back_btn("mugen:panel", "بازگشت به برج")])
    await safe_edit_message_text(
        query,
        f"{get_emoji('mugen')} <b>انتخاب هیولا برای صعود در برج موگن:</b>\n"
        "<blockquote>یکی از کایجوهای خود را انتخاب کنید تا موجود فعال شما در نبردهای برج شود.</blockquote>",
        parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows),
    )


def _swap_pick_sync(tg_user, creature_id: int):
    from game.creature import set_active_creature
    user, _ = get_or_create_user(tg_user)
    set_active_creature(user, creature_id)
    return _mugen_panel_sync(tg_user)


async def mugen_swap_pick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    creature_id = int(query.data.split(":")[2])
    try:
        view = await run_db(_swap_pick_sync, update.effective_user, creature_id)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer("هیولای فعال تنظیم شد.")
    is_group = bool(update.effective_chat and update.effective_chat.type in ("group", "supergroup"))
    await safe_edit_message_text(
        query,
        _render_mugen_text(view),
        parse_mode="HTML",
        reply_markup=_render_mugen_keyboard(view, is_group=is_group),
    )


async def mugen_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    is_group = bool(update.effective_chat and update.effective_chat.type in ("group", "supergroup"))
    try:
        view = await run_db(_mugen_panel_sync, update.effective_user)
    except GameError as exc:
        await send_screen(update, str(exc), parse_mode=None, reply_markup=back_only_keyboard() if not is_group else None)
        return

    from game.media import get_feature_image_path
    photo = get_feature_image_path("mugen_tower")
    await send_screen(
        update,
        _render_mugen_text(view),
        photo=photo,
        parse_mode="HTML",
        reply_markup=_render_mugen_keyboard(view, is_group=is_group),
    )


async def mugen_fight_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    is_group = bool(update.effective_chat and update.effective_chat.type in ("group", "supergroup"))

    def _do_fight(tg_user):
        user, _ = get_or_create_user(tg_user)
        creature = get_active_creature(user)
        if creature is None:
            raise GameError("ابتدا باید یک موجود فعال انتخاب کنی.")
        return mugen_tower.fight_mugen_floor(user, creature)

    try:
        res = await run_db(_do_fight, update.effective_user)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return

    await query.answer()
    if res["won"]:
        rew = res["rewards"]
        rew_lines = [
            f"{get_emoji('coin')} طلا: <code>+{rew['coins']:,}</code>",
            f"{get_emoji('dna')} DNA: <code>+{rew['dna']:,}</code>",
        ]
        if rew["diamonds"]:
            rew_lines.append(f"{get_emoji('diamond')} الماس: <code>+{rew['diamonds']:,}</code>")
        if rew["tickets"]:
            rew_lines.append(f"🎫 بلیط: <code>+{rew['tickets']:,}</code>")

        text = (
            f"{get_emoji('celebrate')} <b>پیروزی در طبقه <code>{res['floor']}</code> برج موگن!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"{get_emoji('battle')} <b>{res['guardian_name']}</b> با قدرت شکست خورد!\n\n"
            f"<blockquote>{get_emoji('gift')} <b>غنائم دریافتی:</b>\n"
            + "\n".join(rew_lines)
            + "</blockquote>\n\n"
            f"🔓 <b>طبقه <code>{res['next_floor']}</code> باز شد!</b>"
        )
    else:
        text = (
            f"💀 <b>شکست در طبقه <code>{res['floor']}</code>!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<blockquote><i>نگهبان برج بسیار قدرتمند بود. هیولای خود را ارتقا داده و دوباره تلاش کنید!</i></blockquote>"
        )

    rows = [[btn("ادامه صعود", emoji_key="btn_mugen", style=BATTLE, callback_data="mugen:panel")]]
    if not is_group:
        rows.append([back_btn("menu:me")])
    kb = InlineKeyboardMarkup(rows)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=kb)


async def mugen_lb_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    def _get_lb():
        return list(
            User.objects.filter(mugen_tower_floor__gt=1)
            .order_by("-mugen_tower_floor")[:15]
            .values("id", "username", "first_name", "lab_name", "mugen_tower_floor")
        )

    lb = await run_db(_get_lb)
    lines = [f"{get_emoji('trophy')} <b>برترین فاتحان برج موگن (無限の塔)</b>", "━━━━━━━━━━━━━━━━━━━━"]
    if not lb:
        lines.append("<i>هنوز کسی طبقات اول رو فتح نکرده است!</i>")
    else:
        medals = [get_emoji("medal_gold"), get_emoji("medal_silver"), get_emoji("medal_bronze")]
        for idx, u in enumerate(lb, start=1):
            name = u["lab_name"] or u["first_name"] or u["username"] or f"Player {u['id']}"
            badge = medals[idx - 1] if idx <= 3 else f"{idx}."
            lines.append(f"{badge} <b>{name}</b>\n  {get_emoji('mugen')} طبقه: <code>{u['mugen_tower_floor']}</code>")

    kb = InlineKeyboardMarkup([
        [back_btn("mugen:panel", "بازگشت به برج")],
    ])
    await safe_edit_message_text(query, "\n".join(lines), parse_mode="HTML", reply_markup=kb)


async def mugen_panel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    is_group = bool(update.effective_chat and update.effective_chat.type in ("group", "supergroup"))
    await query.answer()
    try:
        view = await run_db(_mugen_panel_sync, update.effective_user)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    from game.media import get_feature_image_path
    photo = get_feature_image_path("mugen_tower")
    await send_screen(
        update,
        _render_mugen_text(view),
        photo=photo,
        parse_mode="HTML",
        reply_markup=_render_mugen_keyboard(view, is_group=is_group),
    )


def register(application) -> None:
    application.add_handler(CommandHandler(["mugen", "tower"], mugen_panel))
    application.add_handler(CallbackQueryHandler(mugen_panel_callback, pattern=r"^mugen:panel$"))
    application.add_handler(CallbackQueryHandler(mugen_fight_callback, pattern=r"^mugen:fight$"))
    application.add_handler(CallbackQueryHandler(mugen_lb_callback, pattern=r"^mugen:lb$"))
    application.add_handler(CallbackQueryHandler(mugen_swap_callback, pattern=r"^mugen:swap$"))
    application.add_handler(CallbackQueryHandler(mugen_swap_pick_callback, pattern=r"^mugen:swap_pick:\d+$"))

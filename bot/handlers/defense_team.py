"""«تیم دفاع» — pick up to three creatures that defend you in the arena (game/defense.py).

Same toggle picker as «تیم من» (bot/handlers/team.py): tap a creature to add it, tap again to
remove. A creature may be in both teams. When someone attacks you, the bot picks the member
that does best against the attacker's creature.
"""

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes

from bio_lab.models import Creature
from bio_lab.repository import get_or_create_user
from bot.buttons import LIST, NAV, PRIMARY, back_btn, btn
from bot.utils import alert_text, run_db, safe_edit_message_text, send_screen
from django.db import transaction
from game import constants, defense
from game.creature import GameError
from game.emoji import get_emoji

PAGE = 8
_VIEW_KEY = "dteam_view"
_DIV = "━━━━━━━━━━━━━━━━━━━━"


def _panel_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    creatures = list(Creature.objects.filter(owner=user))
    ids = defense.member_ids(user)
    by_id = {c.id: c for c in creatures}
    members = [(by_id[i], defense.power_of(user, by_id[i])) for i in ids if i in by_id]
    rank = {r: i for i, r in enumerate(constants.RARITY_ORDER)}
    ranked = sorted(
        creatures,
        key=lambda c: (rank.get(c.rarity, 0), c.star_level, c.base_hp + c.base_atk + c.base_def + c.base_spd),
        reverse=True,
    )
    return {"member_ids": ids, "members": members, "ranked": ranked}


def _render(view: dict, filt: str = "all", page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    from bot.handlers.private import creature_picker_frame

    lines = [f"🛡 <b>تیم دفاع</b>  (دفاع در آرنا)", _DIV]
    if view["members"]:
        for c, power in view["members"]:
            lines.append(f"• <b>{c.name}</b> [{constants.element_label(c.element)}] · 💪 <code>{power:,}</code>")
    else:
        lines.append("<i>تیم دفاعت خالیه؛ فعلاً هیولای فعالت ازت دفاع می‌کنه.</i>")
    lines += [
        _DIV,
        "🧠 وقتی کسی بهت حمله کنه، ربات از بین این‌ها <b>بهترین جواب</b> به هیولای مهاجم رو "
        "خودکار انتخاب می‌کنه (با حساب برتری عنصری).",
        "💡 <i>هیولاهای قوی با عنصرهای مختلف بذار تا جلوی هر مهاجمی یکی برتری داشته باشه. "
        "یه هیولا می‌تونه هم توی تیم عادی باشه هم توی تیم دفاع. هیولایی که توی معدن، مأموریت یا غاره دفاع نمی‌کنه.</i>",
    ]
    tab_rows, chunk, nav_rows, total_pages, page, _n = creature_picker_frame(
        view["ranked"], filt, page, PAGE,
        tab_cb=lambda f: f"dteam_page:{f}:0",
        nav_cb=lambda f, p: f"dteam_page:{f}:{p}",
    )
    page_note = f"  <i>(صفحه {page + 1}/{total_pages})</i>" if total_pages > 1 else ""
    lines.append(f"\n<i>روی هیولا بزن تا اضافه/حذف شه (حداکثر {defense.MAX_SLOTS}).</i>{page_note}")

    rows = list(tab_rows)
    for c in chunk:
        on = c.id in view["member_ids"]
        mark = f"{get_emoji('confirm')} " if on else ""
        rarity_short = constants.RARITY_LABELS[c.rarity].split()[0]
        rows.append([
            btn(f"{mark}{c.name} ({rarity_short})", style=PRIMARY if on else LIST, callback_data=f"dteam_tog:{c.id}"),
            btn(f"{constants.element_label(c.element)} | سطح {c.level} | {c.star_level}⭐", style=PRIMARY if on else NAV,
                callback_data=f"dteam_tog:{c.id}"),
        ])
    rows += nav_rows
    rows.append([back_btn("menu:arena", "بازگشت به آرنا")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def _view(context):
    return context.user_data.get(_VIEW_KEY, ("all", 0))


async def defense_team_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.callback_query:
        await update.callback_query.answer()
    view = await run_db(_panel_sync, update.effective_user)
    filt, page = _view(context)
    text, keyboard = _render(view, filt, page)
    await send_screen(update, text, parse_mode="HTML", reply_markup=keyboard)


async def defense_team_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, filt, page = query.data.split(":")
    context.user_data[_VIEW_KEY] = (filt, int(page))
    view = await run_db(_panel_sync, update.effective_user)
    await query.answer()
    text, keyboard = _render(view, filt, int(page))
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


def _toggle_sync(tg_user, creature_id: int):
    user, _ = get_or_create_user(tg_user)
    with transaction.atomic():
        defense.toggle(user, creature_id)
    return _panel_sync(tg_user)


async def defense_team_toggle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        view = await run_db(_toggle_sync, update.effective_user, int(query.data.split(":")[1]))
    except GameError as exc:
        await query.answer(alert_text(exc), show_alert=True)
        return
    await query.answer()
    filt, page = _view(context)
    text, keyboard = _render(view, filt, page)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


def register(application) -> None:
    application.add_handler(CallbackQueryHandler(defense_team_panel, pattern=r"^dteam:open$"))
    application.add_handler(CallbackQueryHandler(defense_team_toggle_callback, pattern=r"^dteam_tog:\d+$"))
    application.add_handler(CallbackQueryHandler(defense_team_page_callback, pattern=r"^dteam_page:"))

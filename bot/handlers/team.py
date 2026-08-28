"""«⚔️ تیم من» — pick up to three creatures for 3v3 team battles (the campaign).

A toggle picker: tap a creature to add it to the team, tap again to remove. The
panel shows the current squad, its team power, and any same-element synergy.
"""

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

from bio_lab.models import Creature, Team
from bio_lab.repository import get_or_create_user
from bot.buttons import LIST, NAV, PRIMARY, back_btn, btn
from bot.utils import run_db, safe_edit_message_text, send_screen
from game import constants
from game.creature import GameError
from game.teambattle import team_power

MAX_SLOTS = 3
TEAM_PAGE = 8


def _team_members(team: Team) -> list[int]:
    return [cid for cid in (team.slot1_id, team.slot2_id, team.slot3_id) if cid is not None]


def _panel_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    team, _ = Team.objects.get_or_create(owner=user)
    member_ids = _team_members(team)
    creatures = list(Creature.objects.filter(owner=user))
    # rarest-then-strongest first, so the best options are on the first page
    rank = {r: i for i, r in enumerate(constants.RARITY_ORDER)}
    ranked = sorted(
        creatures,
        key=lambda c: (rank.get(c.rarity, 0), c.star_level, c.base_hp + c.base_atk + c.base_def + c.base_spd),
        reverse=True,
    )
    members = [c for c in creatures if c.id in member_ids]
    power = team_power(members) if members else 0
    same_element = len(members) == 3 and len({c.element for c in members}) == 1
    return {
        "member_ids": member_ids,
        "members": members,
        "ranked": ranked,  # full list — the renderer paginates
        "power": power,
        "synergy": same_element,
    }


def _render(view: dict, filt: str = "all", page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    from bot.handlers.private import creature_picker_frame

    lines = [f"⚔️ <b>تیم من</b>  (برای کمپین و نبرد تیمی)"]
    if view["members"]:
        for c in view["members"]:
            lines.append(f"  {constants.RARITY_LABELS[c.rarity]} <b>{c.name}</b> {'⭐' * c.star_level} · Lv{c.level}")
        lines.append(f"\n💪 قدرت تیم: <b>{view['power']}</b>")
        if view["synergy"]:
            lines.append("✨ <b>هم‌افزایی عنصری فعاله!</b> (+۱۰٪ حمله چون هر ۳ هم‌عنصرن)")
    else:
        lines.append("<blockquote>هنوز کسی توی تیمت نیست. تا ۳ هیولا انتخاب کن.</blockquote>")

    tab_rows, chunk, nav_rows, total_pages, page, _n = creature_picker_frame(
        view["ranked"], filt, page, TEAM_PAGE,
        tab_cb=lambda f: f"team_page:{f}:0",
        nav_cb=lambda f, p: f"team_page:{f}:{p}",
    )
    page_note = f"  <i>(صفحه {page + 1}/{total_pages})</i>" if total_pages > 1 else ""
    lines.append(f"\n<i>با تب‌های بالا بر اساس نایابی جدا کن. روی هیولا بزن تا اضافه/حذف شه (حداکثر ۳).</i>{page_note}")

    rows = list(tab_rows)
    for c in chunk:
        in_team = c.id in view["member_ids"]
        mark = "✅ " if in_team else ""
        power = c.base_hp + c.base_atk + c.base_def + c.base_spd
        rows.append([btn(
            f"{mark}{c.name} {'⭐' * c.star_level} · Lv{c.level} · {constants.RARITY_LABELS[c.rarity]} · 💪{power}",
            style=PRIMARY if in_team else LIST,
            callback_data=f"team_tog:{c.id}",
        )])
    rows += nav_rows
    rows.append([back_btn("menu:me")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def _team_view(context):
    return context.user_data.get("team_view", ("all", 0))


async def team_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    view = await run_db(_panel_sync, update.effective_user)
    filt, page = _team_view(context)
    text, keyboard = _render(view, filt, page)
    await send_screen(update, text, parse_mode="HTML", reply_markup=keyboard)


async def team_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, filt, page = query.data.split(":")
    context.user_data["team_view"] = (filt, int(page))
    view = await run_db(_panel_sync, update.effective_user)
    await query.answer()
    text, keyboard = _render(view, filt, int(page))
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


def _toggle_sync(tg_user, creature_id):
    user, _ = get_or_create_user(tg_user)
    creature = Creature.objects.filter(id=creature_id, owner=user).first()
    if creature is None:
        raise GameError("این موجود توی کلکسیون تو نیست.")
    team, _ = Team.objects.get_or_create(owner=user)
    members = _team_members(team)
    if creature_id in members:
        members = [m for m in members if m != creature_id]
    else:
        if len(members) >= MAX_SLOTS:
            raise GameError("تیم پره! (حداکثر ۳ هیولا) — اول یکی رو بردار.")
        members.append(creature_id)
    members = (members + [None, None, None])[:3]
    team.slot1_id, team.slot2_id, team.slot3_id = members
    team.save(update_fields=["slot1", "slot2", "slot3", "updated_at"])
    return _panel_sync(tg_user)


async def team_toggle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    creature_id = int(query.data.split(":")[1])
    try:
        view = await run_db(_toggle_sync, update.effective_user, creature_id)
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer()
    filt, page = _team_view(context)
    text, keyboard = _render(view, filt, page)
    await safe_edit_message_text(query, text, parse_mode="HTML", reply_markup=keyboard)


def register(application) -> None:
    application.add_handler(CommandHandler("team", team_panel, filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(team_toggle_callback, pattern=r"^team_tog:"))
    application.add_handler(CallbackQueryHandler(team_page_callback, pattern=r"^team_page:"))

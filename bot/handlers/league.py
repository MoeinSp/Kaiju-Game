"""«🏆 لیگ» — the ranked division ladder built on the weekly cup season."""

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

from bot.gates import hall_gated
from bio_lab.repository import display_name, get_or_create_user, lab_display
from bot.buttons import NAV, back_btn, btn
from bot.utils import run_db, safe_edit_message_text, send_screen
from game import league, season


def _fmt_left(seconds: int) -> str:
    d, rem = divmod(max(0, seconds), 86400)
    h = rem // 3600
    return f"{d} روز و {h} ساعت" if d else f"{h} ساعت"


def _panel_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    season.close_due_season()  # lazy settle, like the arena screens
    user.refresh_from_db()
    from bio_lab.models import User

    rank = User.objects.filter(is_banned=False, cup__gt=user.cup).count() + 1
    return {
        "cup": user.cup,
        "division": league.division_for(user.cup),
        "next": league.next_division(user.cup),
        "seconds_left": season.seconds_until_season_end(),
        "standings": season.standings(limit=10),
        "reward": league.season_reward(user.cup),
        "user_id": user.id,
        "rank": rank,
        "rank_reward": league.rank_reward(rank) if user.cup > 0 else None,
    }


def _league_table_text(my_key: str | None) -> str:
    """All 16 leagues with their end-of-week reward (kept short: this is shown as a
    photo caption, which Telegram caps at ~1,000 characters)."""
    lines = ["🏅 <b>پاداش آخر فصل هر لیگ</b> <i>(هر دو هفته)</i>", _DIV]
    for div in league.DIVISIONS:
        here = " 📍" if div["key"] == my_key else ""
        lines.append(f"{div['emoji']} {div['title']} ({div['min_cup']:,}+): {_reward_fmt(league.DIVISION_REWARD[div['key']])}{here}")
    return "\n".join(lines)


def _rank_table_text() -> str:
    lines = ["🎖 <b>جایزه‌ی رتبه‌ی آخر فصل</b>", _DIV,
             "<i>علاوه بر پاداش لیگ، به ۵۰ نفر اول جدول کاپ داده می‌شه:</i>", ""]
    prev = 0
    for max_rank, reward in league.RANK_REWARDS:
        label = f"رتبه‌ی {max_rank}" if max_rank == prev + 1 else f"رتبه‌ی {prev + 1} تا {max_rank}"
        lines.append(f"• {label}: {_reward_fmt(reward)}")
        prev = max_rank
    return "\n".join(lines)


_DIV = "━━━━━━━━━━━━━━━━━━━━"
_RANK_MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}


def _rank_badge(rank: int) -> str:
    return _RANK_MEDALS.get(rank, f"{rank}.")


def _reward_fmt(reward: dict) -> str:
    """«🪙 12 هزار · 💎 28 · 🧬 400» — icon before each amount (mobile layout, 2026-11-12)."""
    from game import ui

    parts = []
    if reward.get("coins"):
        parts.append(f"🪙 {ui.short(reward['coins'], 10_000)}")
    if reward.get("diamonds"):
        parts.append(f"💎 {reward['diamonds']:,}")
    if reward.get("dna"):
        parts.append(f"🧬 {reward['dna']:,}")
    return " · ".join(parts) or "—"


async def league_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    view = await run_db(_panel_sync, update.effective_user)
    d = view["division"]
    lines = [
        "🏆 <b>لیگ رتبه‌بندی</b>",
        _DIV,
        f"{d['emoji']} لیگ تو: <b>{d['title']}</b> · {view['cup']:,} کاپ",
    ]
    nxt = view["next"]
    if nxt:
        need = nxt["min_cup"] - view["cup"]
        lines.append(f"🎯 {need:,} کاپ تا {nxt['emoji']} {nxt['title']}")
    else:
        lines.append("👑 <b>توی بالاترین سطح لیگی!</b>")
    if view["cup"] > 0:
        lines.append(f"📍 رتبه‌ی تو: <b>{view['rank']:,}</b>")

    lines += ["", f"🎁 <b>جایزه‌ی آخر فصل</b> · {_fmt_left(view['seconds_left'])} دیگه",
              f"{d['emoji']} لیگ: {_reward_fmt(view['reward'])}"]
    if view["cup"] > 0:
        bonus = view["rank_reward"]
        lines.append(f"🎖 رتبه: {_reward_fmt(bonus)}" if bonus else "🎖 رتبه: از 50 نفر اول شروع می‌شه")

    lines += [_DIV, "📊 <b>صدرنشین‌های فصل</b>"]
    for row in view["standings"]:
        mine = " 📍" if row["user"].id == view["user_id"] else ""
        lines.append(f"{_rank_badge(row['rank'])} {lab_display(row['user'])} · {row['cup']:,}{mine}")
    lines.append("<i>بین 50 نفر اول باشی، جایزه‌ی رتبه هم می‌گیری.</i>")

    from game.media import get_feature_image_path
    photo = get_feature_image_path("league")
    await send_screen(
        update, "\n".join(lines), photo=photo, parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [btn("جوایز لیگ‌ها", emoji_key="btn_league", style=NAV, callback_data="league_tbl:l"),
             btn("جوایز رتبه", emoji_key="btn_rank", style=NAV, callback_data="league_tbl:r")],
            [back_btn("menu:hub_city", "بازگشت به شهر")],
        ]),
    )


def _my_league_key_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    return league.division_for(user.cup)["key"]


async def league_table_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    which = query.data.split(":")[1]
    if which == "r":
        text = _rank_table_text()
    else:
        text = _league_table_text(await run_db(_my_league_key_sync, update.effective_user))
    await query.answer()
    await safe_edit_message_text(
        query, text, parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[back_btn("menu:league", "بازگشت به لیگ")]]),
    )


def register(application) -> None:
    application.add_handler(CommandHandler("league", hall_gated("league", league_panel), filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(league_table_callback, pattern=r"^league_tbl:[lr]$"))

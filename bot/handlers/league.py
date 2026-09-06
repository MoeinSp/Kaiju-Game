"""«🏆 لیگ» — the ranked division ladder built on the weekly cup season."""

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import CommandHandler, ContextTypes, filters

from bio_lab.repository import display_name, get_or_create_user, lab_display
from bot.buttons import back_btn
from bot.utils import run_db, send_screen
from game import league, season


def _fmt_left(seconds: int) -> str:
    d, rem = divmod(max(0, seconds), 86400)
    h = rem // 3600
    return f"{d} روز و {h} ساعت" if d else f"{h} ساعت"


def _panel_sync(tg_user):
    user, _ = get_or_create_user(tg_user)
    season.close_due_season()  # lazy settle, like the arena screens
    user.refresh_from_db()
    return {
        "cup": user.cup,
        "division": league.division_for(user.cup),
        "next": league.next_division(user.cup),
        "seconds_left": season.seconds_until_next_week(),
        "standings": season.standings(limit=10),
        "reward": league.season_reward(user.cup),
        "user_id": user.id,
    }


_DIV = "━━━━━━━━━━━━━━━━━━━━"
_RANK_MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}


def _rank_badge(rank: int) -> str:
    return _RANK_MEDALS.get(rank, f"{rank}.")


def _reward_fmt(reward: dict) -> str:
    """«2,000 طلا + 50💎» — the compact reward style used in the league layout."""
    parts = []
    if reward.get("coins"):
        parts.append(f"{reward['coins']:,} طلا")
    if reward.get("diamonds"):
        parts.append(f"{reward['diamonds']}💎")
    if reward.get("dna"):
        parts.append(f"{reward['dna']} DNA")
    return " + ".join(parts) or "—"


async def league_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    view = await run_db(_panel_sync, update.effective_user)
    d = view["division"]
    lines = [
        "🏆 <b>لیگ رتبه‌بندی</b>",
        _DIV,
        f"{d['emoji']} دیویژن تو: <b>{d['title']}</b> │ 🏆 {view['cup']} کاپ",
        f"🎁 پاداش فصل: {_reward_fmt(view['reward'])} │ ⏳ پایان فصل: {_fmt_left(view['seconds_left'])}",
    ]
    nxt = view["next"]
    if nxt:
        need = nxt["min_cup"] - view["cup"]
        lines.append(f"🎯 تا {nxt['emoji']} {nxt['title']}: <b>{need}</b> کاپ دیگر")
    else:
        lines.append("👑 <b>توی بالاترین دیویژنی!</b>")

    lines.append("\n🏅 <b>دیویژن‌ها و پاداش‌ها:</b>")
    for div in league.DIVISIONS:
        cup_label = "0 کاپ" if div["min_cup"] == 0 else f"+{div['min_cup']} کاپ"
        here = " 📍 (جایگاه فعلی تو)" if div["key"] == d["key"] else ""
        lines.append(
            f"• {div['emoji']} {div['title']}: {cup_label} ⟵ {_reward_fmt(league.DIVISION_REWARD[div['key']])}{here}"
        )

    lines.append(f"\n{_DIV}")
    lines.append("📊 <b>صدرنشین‌های فصل:</b>")
    for row in view["standings"]:
        mine = " 📍" if row["user"].id == view["user_id"] else ""
        lines.append(f"{_rank_badge(row['rank'])} {lab_display(row['user'])} │ 🏆 {row['cup']}{mine}")

    await send_screen(
        update, "\n".join(lines), parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[back_btn("menu:cat_social", "بازگشت به اجتماعی")]]),
    )


def register(application) -> None:
    application.add_handler(CommandHandler("league", league_panel, filters.ChatType.PRIVATE))

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


async def league_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    view = await run_db(_panel_sync, update.effective_user)
    d = view["division"]
    lines = [
        f"🏆 <b>لیگ رتبه‌بندی</b>",
        f"<blockquote>دیویژن تو: {d['emoji']} <b>{d['title']}</b>  ·  🏆 {view['cup']} کاپ\n"
        f"🎁 جایزه‌ی پایان فصلِ این دیویژن: {league.reward_text(view['reward'])}\n"
        f"⏳ تا پایان فصل: <b>{_fmt_left(view['seconds_left'])}</b></blockquote>",
    ]
    nxt = view["next"]
    if nxt:
        need = nxt["min_cup"] - view["cup"]
        lines.append(f"⬆️ تا دیویژن {nxt['emoji']} <b>{nxt['title']}</b>: <b>{need}</b> کاپ دیگه")
    else:
        lines.append("👑 <b>توی بالاترین دیویژنی!</b>")

    lines.append("\n🏅 <b>دیویژن‌ها:</b>")
    for div in league.DIVISIONS:
        here = " ⬅️ تو" if div["key"] == d["key"] else ""
        lines.append(f"  {div['emoji']} {div['title']} — از {div['min_cup']} کاپ  ({league.reward_text(league.DIVISION_REWARD[div['key']])}){here}")

    lines.append("\n📊 <b>صدرنشین‌های این فصل:</b>")
    medals = ["🥇", "🥈", "🥉"]
    for row in view["standings"]:
        tag = medals[row["rank"] - 1] if row["rank"] <= 3 else f"{row['rank']}."
        mine = " ⬅️" if row["user"].id == view["user_id"] else ""
        lines.append(f"  {tag} {lab_display(row['user'])} — {row['cup']} کاپ{mine}")

    await send_screen(
        update, "\n".join(lines), parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[back_btn("menu:me")]]),
    )


def register(application) -> None:
    application.add_handler(CommandHandler("league", league_panel, filters.ChatType.PRIVATE))

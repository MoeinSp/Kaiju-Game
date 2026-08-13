from sqlalchemy import select
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes, filters

from db.models import Creature, DuelLog
from db.repository import get_active_creature, get_or_create_group, get_or_create_user
from db.session import get_session
from game.combat import resolve_duel
from game.raid import RaidError, attack_boss, distribute_rewards, get_active_boss, spawn_boss


async def duel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message.reply_to_message is None:
        await update.message.reply_text("برای دوئل، روی پیام حریف ریپلای کن و بنویس /duel")
        return

    opponent_tg = update.message.reply_to_message.from_user
    challenger_tg = update.effective_user
    if opponent_tg.id == challenger_tg.id or opponent_tg.is_bot:
        await update.message.reply_text("نمی‌تونی با خودت یا با یه بات دوئل کنی!")
        return

    session = get_session()
    try:
        group = get_or_create_group(session, update.effective_chat)
        challenger_user, _ = get_or_create_user(session, challenger_tg)
        opponent_user, _ = get_or_create_user(session, opponent_tg)

        challenger_creature = get_active_creature(session, challenger_user)
        opponent_creature = get_active_creature(session, opponent_user)

        if challenger_creature is None:
            await update.message.reply_text("اول باید توی پیوی بات /start بزنی تا موجودت رو بگیری.")
            return
        if opponent_creature is None:
            await update.message.reply_text(f"{opponent_tg.first_name} هنوز موجودی نداره (باید /start بزنه).")
            return

        winner_creature, log_text = resolve_duel(challenger_creature, opponent_creature)
        winner_user = challenger_user if winner_creature.id == challenger_creature.id else opponent_user

        session.add(
            DuelLog(
                group_id=group.id,
                challenger_id=challenger_user.id,
                opponent_id=opponent_user.id,
                winner_id=winner_user.id,
                log_text=log_text,
            )
        )
        session.commit()

        await update.message.reply_text(log_text)
    finally:
        session.close()


async def raid_spawn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = get_session()
    try:
        group = get_or_create_group(session, update.effective_chat)
        try:
            boss = spawn_boss(session, group.id)
        except RaidError as exc:
            await update.message.reply_text(str(exc))
            return
        await update.message.reply_text(
            f"🐲 یک هیولای وحشی ظاهر شد: <b>{boss.name}</b>!\n"
            f"HP: {boss.current_hp}/{boss.max_hp}\n"
            f"برای حمله دستور /attack رو بزن.",
            parse_mode="HTML",
        )
    finally:
        session.close()


async def attack(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = get_session()
    try:
        group = get_or_create_group(session, update.effective_chat)
        boss = get_active_boss(session, group.id)
        if boss is None:
            await update.message.reply_text("هیچ هیولایی فعال نیست. با /raid_spawn یکی رو صدا بزن.")
            return

        user, _ = get_or_create_user(session, update.effective_user)
        creature = get_active_creature(session, user)
        if creature is None:
            await update.message.reply_text("اول باید توی پیوی بات /start بزنی تا موجودت رو بگیری.")
            return

        try:
            dmg, defeated = attack_boss(session, user, creature, boss)
        except RaidError as exc:
            await update.message.reply_text(str(exc))
            return

        text = f"{creature.name} به {boss.name} {dmg} دمیج زد! ({max(boss.current_hp, 0)}/{boss.max_hp} HP)"
        if defeated:
            rewards = distribute_rewards(session, boss)
            reward_lines = []
            for uid, r in sorted(rewards.items(), key=lambda kv: kv[1]["damage"], reverse=True):
                member = session.get(type(user), uid)
                name = member.username or str(uid)
                reward_lines.append(f"@{name}: {r['dna']} DNA, {r['coins']} سکه (دمیج: {r['damage']})")
            text += "\n\n🎉 هیولا شکست خورد! غنایم:\n" + "\n".join(reward_lines)

        await update.message.reply_text(text)
    finally:
        session.close()


async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = get_session()
    try:
        stmt = select(Creature).order_by(
            (Creature.base_hp + Creature.base_atk + Creature.base_def + Creature.base_spd).desc()
        ).limit(10)
        creatures = session.execute(stmt).scalars().all()
        if not creatures:
            await update.message.reply_text("هنوز هیچ موجودی ثبت نشده.")
            return

        lines = ["🏆 <b>برترین موجودات:</b>"]
        for i, c in enumerate(creatures, start=1):
            power = c.base_hp + c.base_atk + c.base_def + c.base_spd
            lines.append(f"{i}. {c.name} (Lv{c.level}) — قدرت: {power}")
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    finally:
        session.close()


def register(application) -> None:
    group_filter = filters.ChatType.GROUPS
    application.add_handler(CommandHandler("duel", duel, group_filter))
    application.add_handler(CommandHandler("raid_spawn", raid_spawn, group_filter))
    application.add_handler(CommandHandler("attack", attack, group_filter))
    application.add_handler(CommandHandler("leaderboard", leaderboard, group_filter))

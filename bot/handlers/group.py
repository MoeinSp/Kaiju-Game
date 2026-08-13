from sqlalchemy import select
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes, filters

from db.models import Creature, DuelLog
from db.repository import (
    display_name,
    get_active_creature,
    get_or_create_group,
    get_or_create_user,
    group_member_creatures,
    touch_membership,
)
from db.session import get_session
from game import constants
from game.combat import resolve_duel
from game.creature import GameError, add_xp, apply_random_mutation
from game.daily import assert_energy_available, check_missions, group_event_available, mark_group_event, record_action
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
        touch_membership(session, group, challenger_user)
        touch_membership(session, group, opponent_user)

        challenger_creature = get_active_creature(session, challenger_user)
        opponent_creature = get_active_creature(session, opponent_user)

        if challenger_creature is None:
            await update.message.reply_text("اول باید توی پیوی بات /start بزنی تا موجودت رو بگیری.")
            return
        if opponent_creature is None:
            await update.message.reply_text(f"{opponent_tg.first_name} هنوز موجودی نداره (باید /start بزنه).")
            return

        winner_creature, log_text = resolve_duel(challenger_creature, opponent_creature)
        is_challenger_winner = winner_creature.id == challenger_creature.id
        winner_user = challenger_user if is_challenger_winner else opponent_user
        loser_creature = opponent_creature if is_challenger_winner else challenger_creature

        winner_user.coins += constants.DUEL_WIN_COINS
        winner_levels = add_xp(winner_creature, constants.DUEL_WIN_XP)
        add_xp(loser_creature, constants.DUEL_LOSE_XP)
        record_action(session, winner_user, "duel_win")
        completed_missions = check_missions(session, winner_user, "duel_win")

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

        reward_text = f"\n\n💰 {winner_creature.name} +{constants.DUEL_WIN_COINS} سکه, +{constants.DUEL_WIN_XP} XP"
        if winner_levels:
            reward_text += f" 🎉 سطح {winner_creature.level} شد!"
        for m in completed_missions:
            reward_text += f"\n🎯 ماموریت «{m['label']}» کامل شد! +{m['coins']} سکه" + (
                f", +{m['dna']} DNA" if m["dna"] else ""
            )
        await update.message.reply_text(log_text + reward_text, parse_mode="HTML")
    finally:
        session.close()


async def give(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    usage = (
        "استفاده درست: روی پیام طرف مقابل ریپلای کن و بنویس /give و نوع منبع و مقدار\n"
        "مثلاً: /give coins 50  یا  /give dna 10"
    )
    if update.message.reply_to_message is None:
        await update.message.reply_text(usage)
        return
    if len(context.args) != 2:
        await update.message.reply_text(usage)
        return

    resource_key = constants.GIVE_RESOURCE_ALIASES.get(context.args[0].lower())
    amount_str = context.args[1]
    if resource_key is None or not amount_str.isdigit() or int(amount_str) <= 0:
        await update.message.reply_text(usage)
        return
    amount = int(amount_str)

    receiver_tg = update.message.reply_to_message.from_user
    sender_tg = update.effective_user
    if receiver_tg.id == sender_tg.id or receiver_tg.is_bot:
        await update.message.reply_text("نمی‌تونی به خودت یا به یه بات چیزی بدی!")
        return

    session = get_session()
    try:
        group = get_or_create_group(session, update.effective_chat)
        sender, _ = get_or_create_user(session, sender_tg)
        receiver, _ = get_or_create_user(session, receiver_tg)
        touch_membership(session, group, sender)
        touch_membership(session, group, receiver)

        current = getattr(sender, resource_key)
        if current < amount:
            label = constants.GIVE_RESOURCE_LABELS[resource_key]
            await update.message.reply_text(f"به این اندازه {label} نداری! ({current} تا داری)")
            return

        setattr(sender, resource_key, current - amount)
        setattr(receiver, resource_key, getattr(receiver, resource_key) + amount)
        session.commit()

        label = constants.GIVE_RESOURCE_LABELS[resource_key]
        await update.message.reply_text(
            f"🎁 {display_name(sender)} مقدار {amount} {label} به {display_name(receiver)} هدیه داد!"
        )
    finally:
        session.close()


async def raid_spawn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = get_session()
    try:
        group = get_or_create_group(session, update.effective_chat)
        spawner_user, _ = get_or_create_user(session, update.effective_user)
        touch_membership(session, group, spawner_user)
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
        touch_membership(session, group, user)
        creature = get_active_creature(session, user)
        if creature is None:
            await update.message.reply_text("اول باید توی پیوی بات /start بزنی تا موجودت رو بگیری.")
            return

        try:
            assert_energy_available(session, user, "raid_attack")
            dmg, defeated = attack_boss(session, user, creature, boss)
        except (RaidError, GameError) as exc:
            await update.message.reply_text(str(exc))
            return

        record_action(session, user, "raid_attack")
        completed_missions = check_missions(session, user, "raid_attack")

        text = f"{creature.name} به {boss.name} {dmg} دمیج زد! ({max(boss.current_hp, 0)}/{boss.max_hp} HP)"
        for m in completed_missions:
            text += f"\n🎯 ماموریت «{m['label']}» کامل شد! +{m['coins']} سکه" + (
                f", +{m['dna']} DNA" if m["dna"] else ""
            )
        if defeated:
            rewards = distribute_rewards(session, boss)
            reward_lines = []
            for uid, r in sorted(rewards.items(), key=lambda kv: kv[1]["damage"], reverse=True):
                member = session.get(type(user), uid)
                name = display_name(member) if member else str(uid)
                reward_lines.append(f"{name}: {r['dna']} DNA, {r['coins']} سکه (دمیج: {r['damage']})")
            text += "\n\n🎉 هیولا شکست خورد! غنایم:\n" + "\n".join(reward_lines)

        await update.message.reply_text(text)
    finally:
        session.close()


async def mutation_event(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = get_session()
    try:
        group = get_or_create_group(session, update.effective_chat)
        user, _ = get_or_create_user(session, update.effective_user)
        touch_membership(session, group, user)

        if not group_event_available(session, group, "mutation"):
            await update.message.reply_text("امروز قبلاً یه رویداد جهش تو این گروه اتفاق افتاده. فردا دوباره امتحان کن.")
            return

        members = group_member_creatures(session, group)
        if not members:
            await update.message.reply_text("هنوز کسی توی این گروه موجودی ثبت نکرده.")
            return

        mark_group_event(session, group, "mutation")
        lines = ["☄️ یه شهاب‌سنگ جهش‌زا روی گروه فرود اومد! همه‌ی موجودهای فعال این جهش رایگان رو گرفتن:"]
        for c in members:
            stat, bonus = apply_random_mutation(c)
            lines.append(f"{c.name}: +{bonus} {constants.MUTATION_EVENT_STAT_LABELS[stat]}")
        session.commit()
        await update.message.reply_text("\n".join(lines))
    finally:
        session.close()


def _creature_power(c: Creature) -> int:
    return c.base_hp + c.base_atk + c.base_def + c.base_spd


async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = get_session()
    try:
        group = get_or_create_group(session, update.effective_chat)
        user, _ = get_or_create_user(session, update.effective_user)
        touch_membership(session, group, user)

        creatures = sorted(group_member_creatures(session, group), key=_creature_power, reverse=True)[:10]
        if not creatures:
            await update.message.reply_text("هنوز هیچ موجودی توی این گروه ثبت نشده.")
            return

        lines = ["🏆 <b>برترین موجودات این گروه:</b>"]
        for i, c in enumerate(creatures, start=1):
            lines.append(f"{i}. {c.name} (Lv{c.level}) — قدرت: {_creature_power(c)}")
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    finally:
        session.close()


async def guardian(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = get_session()
    try:
        group = get_or_create_group(session, update.effective_chat)
        user, _ = get_or_create_user(session, update.effective_user)
        touch_membership(session, group, user)

        creatures = group_member_creatures(session, group)
        if not creatures:
            await update.message.reply_text("هنوز کسی توی این گروه موجودی ثبت نکرده.")
            return

        top = max(creatures, key=_creature_power)
        owner = session.get(type(user), top.owner_id)
        owner_name = display_name(owner) if owner else str(top.owner_id)
        await update.message.reply_text(
            f"🛡 <b>محافظ فعلی گروه:</b>\n{top.name} ({constants.RARITY_LABELS[top.rarity]}, Lv{top.level}) "
            f"متعلق به {owner_name}\nقدرت کل: {_creature_power(top)}\n\n"
            f"برای پس زدنش، موجودت رو قوی‌تر کن و دوباره چک کن!",
            parse_mode="HTML",
        )
    finally:
        session.close()


def register(application) -> None:
    group_filter = filters.ChatType.GROUPS
    application.add_handler(CommandHandler("duel", duel, group_filter))
    application.add_handler(CommandHandler("raid_spawn", raid_spawn, group_filter))
    application.add_handler(CommandHandler("attack", attack, group_filter))
    application.add_handler(CommandHandler("leaderboard", leaderboard, group_filter))
    application.add_handler(CommandHandler("guardian", guardian, group_filter))
    application.add_handler(CommandHandler("give", give, group_filter))
    application.add_handler(CommandHandler("mutation_event", mutation_event, group_filter))

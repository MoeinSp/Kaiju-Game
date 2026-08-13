from telegram import Update
from telegram.ext import CommandHandler, ContextTypes, filters

from bio_lab.models import Creature, DuelLog, User
from bio_lab.repository import (
    display_name,
    get_active_creature,
    get_or_create_group,
    get_or_create_user,
    group_member_creatures,
    touch_membership,
)
from bot.utils import run_db
from game import constants
from game.combat import resolve_duel
from game.creature import GameError, add_xp, apply_random_mutation
from game.daily import assert_energy_available, check_missions, group_event_available, mark_group_event, record_action
from game.energy import spend_energy
from game.guardian import challenge_guardian, ensure_guardian, get_guardian
from game.raid import RaidError, attack_boss, distribute_rewards, get_active_boss, spawn_boss
from game.trade import gift_creature


def _mission_lines(completed: list[dict]) -> str:
    if not completed:
        return ""
    lines = []
    for m in completed:
        reward = f"+{m['coins']} 💰"
        if m["dna"]:
            reward += f" +{m['dna']} 🧬"
        lines.append(f"🎯 ماموریت «{m['label']}» تکمیل شد! {reward}")
    return "\n" + "\n".join(lines)


def _duel_sync(chat, challenger_tg, opponent_tg):
    group = get_or_create_group(chat)
    challenger_user, _ = get_or_create_user(challenger_tg)
    opponent_user, _ = get_or_create_user(opponent_tg)
    touch_membership(group, challenger_user)
    touch_membership(group, opponent_user)

    challenger_creature = get_active_creature(challenger_user)
    opponent_creature = get_active_creature(opponent_user)
    if challenger_creature is None:
        raise GameError("اول باید توی پیوی بات /start بزنی تا موجودت رو بگیری.")
    if opponent_creature is None:
        raise GameError(f"{opponent_tg.first_name} هنوز موجودی نداره (باید /start بزنه).")

    winner_creature, log_text = resolve_duel(challenger_creature, opponent_creature)
    is_challenger_winner = winner_creature.id == challenger_creature.id
    winner_user = challenger_user if is_challenger_winner else opponent_user
    loser_creature = opponent_creature if is_challenger_winner else challenger_creature

    winner_user.coins += constants.DUEL_WIN_COINS
    winner_levels = add_xp(winner_creature, constants.DUEL_WIN_XP)
    add_xp(loser_creature, constants.DUEL_LOSE_XP)
    winner_user.save(update_fields=["coins"])
    winner_creature.save()
    loser_creature.save()

    record_action(winner_user, "duel_win")
    completed_missions = check_missions(winner_user, "duel_win")

    DuelLog.objects.create(
        group_id=group.id,
        challenger_id=challenger_user.id,
        opponent_id=opponent_user.id,
        winner_id=winner_user.id,
        log_text=log_text,
    )
    return winner_creature, winner_levels, completed_missions, log_text


async def duel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message.reply_to_message is None:
        await update.message.reply_text("⚔️ برای دوئل، روی پیام حریف ریپلای کن و بنویس /duel")
        return

    opponent_tg = update.message.reply_to_message.from_user
    challenger_tg = update.effective_user
    if opponent_tg.id == challenger_tg.id or opponent_tg.is_bot:
        await update.message.reply_text("🙅 نمی‌تونی با خودت یا با یه بات دوئل کنی!")
        return

    try:
        winner_creature, winner_levels, completed_missions, log_text = await run_db(
            _duel_sync, update.effective_chat, challenger_tg, opponent_tg
        )
    except GameError as exc:
        await update.message.reply_text(str(exc))
        return

    reward_text = f"\n\n💰 {winner_creature.name} +{constants.DUEL_WIN_COINS} سکه · +{constants.DUEL_WIN_XP} XP"
    if winner_levels:
        reward_text += f" 🎉 رسید به سطح {winner_creature.level}!"
    reward_text += _mission_lines(completed_missions)
    await update.message.reply_text(log_text + reward_text, parse_mode="HTML")


def _give_sync(chat, sender_tg, receiver_tg, kind, amount_arg):
    group = get_or_create_group(chat)
    sender, _ = get_or_create_user(sender_tg)
    receiver, _ = get_or_create_user(receiver_tg)
    touch_membership(group, sender)
    touch_membership(group, receiver)

    if kind in ("creature", "موجود"):
        try:
            creature = Creature.objects.get(id=amount_arg)
        except Creature.DoesNotExist:
            raise GameError("این شماره موجود پیدا نشد.")
        gift_creature(sender, receiver, creature)
        return "creature", sender, receiver, creature

    resource_key = constants.GIVE_RESOURCE_ALIASES[kind]
    current = getattr(sender, resource_key)
    if current < amount_arg:
        label = constants.GIVE_RESOURCE_LABELS[resource_key]
        raise GameError(f"به این اندازه {label} نداری! ({current} تا داری)")

    setattr(sender, resource_key, current - amount_arg)
    setattr(receiver, resource_key, getattr(receiver, resource_key) + amount_arg)
    sender.save(update_fields=[resource_key])
    receiver.save(update_fields=[resource_key])
    return "resource", sender, receiver, resource_key, amount_arg


async def give(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    usage = (
        "🎁 استفاده درست: روی پیام طرف مقابل ریپلای کن و بنویس /give به‌همراه نوع و مقدار\n"
        "<code>/give coins 50</code> · <code>/give dna 10</code> · <code>/give creature 7</code> "
        "(شماره موجود از /collection)"
    )
    if update.message.reply_to_message is None or len(context.args) != 2:
        await update.message.reply_text(usage, parse_mode="HTML")
        return

    receiver_tg = update.message.reply_to_message.from_user
    sender_tg = update.effective_user
    if receiver_tg.id == sender_tg.id or receiver_tg.is_bot:
        await update.message.reply_text("🙅 نمی‌تونی به خودت یا به یه بات چیزی بدی!")
        return

    kind = context.args[0].lower()
    is_creature_gift = kind in ("creature", "موجود")
    if not is_creature_gift and constants.GIVE_RESOURCE_ALIASES.get(kind) is None:
        await update.message.reply_text(usage, parse_mode="HTML")
        return
    if not context.args[1].isdigit() or int(context.args[1]) <= 0:
        await update.message.reply_text(usage, parse_mode="HTML")
        return
    amount_arg = int(context.args[1])

    try:
        result = await run_db(_give_sync, update.effective_chat, sender_tg, receiver_tg, kind, amount_arg)
    except GameError as exc:
        await update.message.reply_text(str(exc))
        return

    if result[0] == "creature":
        _, sender, receiver, creature = result
        await update.message.reply_text(
            f"🎁 {display_name(sender)} موجود <b>{creature.name}</b> رو به {display_name(receiver)} هدیه داد!",
            parse_mode="HTML",
        )
    else:
        _, sender, receiver, resource_key, amount = result
        label = constants.GIVE_RESOURCE_LABELS[resource_key]
        await update.message.reply_text(
            f"🎁 {display_name(sender)} مقدار {amount} {label} به {display_name(receiver)} هدیه داد!"
        )


def _raid_spawn_sync(chat, spawner_tg):
    group = get_or_create_group(chat)
    spawner_user, _ = get_or_create_user(spawner_tg)
    touch_membership(group, spawner_user)
    return spawn_boss(group.id)


async def raid_spawn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        boss = await run_db(_raid_spawn_sync, update.effective_chat, update.effective_user)
    except RaidError as exc:
        await update.message.reply_text(str(exc))
        return
    await update.message.reply_text(
        f"🐲 <b>یک هیولای وحشی ظاهر شد: {boss.name}!</b>\n"
        f"{constants.render_bar(boss.current_hp, boss.max_hp, width=14)}  {boss.current_hp}/{boss.max_hp} HP\n"
        f"{constants.ELEMENT_LABELS[boss.element]}\n\n"
        f"همه با /attack بهش حمله کنین — هرچی سهم دمیج بیشتر، غنیمت بیشتر! 💪",
        parse_mode="HTML",
    )


def _attack_sync(chat, tg_user):
    group = get_or_create_group(chat)
    boss = get_active_boss(group.id)
    if boss is None:
        raise GameError("😴 هیچ هیولایی فعال نیست. با /raid_spawn یکی رو صدا بزن.")

    user, _ = get_or_create_user(tg_user)
    touch_membership(group, user)
    creature = get_active_creature(user)
    if creature is None:
        raise GameError("اول باید توی پیوی بات /start بزنی تا موجودت رو بگیری.")

    spend_energy(user, constants.RAID_ATTACK_ENERGY_COST, "حمله")
    dmg, defeated = attack_boss(user, creature, boss)
    user.save(update_fields=["energy", "energy_updated_at"])

    record_action(user, "raid_attack")
    completed_missions = check_missions(user, "raid_attack")

    reward_lines = None
    if defeated:
        rewards = distribute_rewards(boss)
        reward_lines = []
        for uid, r in sorted(rewards.items(), key=lambda kv: kv[1]["damage"], reverse=True):
            member = User.objects.filter(id=uid).first()
            name = display_name(member) if member else str(uid)
            reward_lines.append(f"{name} — 🧬{r['dna']} 💰{r['coins']} (دمیج: {r['damage']})")

    return creature, boss, dmg, defeated, completed_missions, reward_lines


async def attack(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        creature, boss, dmg, defeated, completed_missions, reward_lines = await run_db(
            _attack_sync, update.effective_chat, update.effective_user
        )
    except (RaidError, GameError) as exc:
        await update.message.reply_text(str(exc))
        return

    text = (
        f"⚔️ <b>{creature.name}</b> به {boss.name} <b>{dmg}</b> دمیج زد!\n"
        f"{constants.render_bar(boss.current_hp, boss.max_hp, width=14)}  {max(boss.current_hp, 0)}/{boss.max_hp} HP"
    )
    text += _mission_lines(completed_missions)
    if defeated:
        text += "\n\n🎉 <b>هیولا شکست خورد!</b> غنایم:\n" + "\n".join(reward_lines)

    await update.message.reply_text(text, parse_mode="HTML")


def _mutation_event_sync(chat, tg_user):
    group = get_or_create_group(chat)
    user, _ = get_or_create_user(tg_user)
    touch_membership(group, user)

    if not group_event_available(group, "mutation"):
        raise GameError("☄️ امروز قبلاً یه رویداد جهش تو این گروه اتفاق افتاده. فردا دوباره امتحان کن.")

    members = group_member_creatures(group)
    if not members:
        raise GameError("هنوز کسی توی این گروه موجودی ثبت نکرده.")

    mark_group_event(group, "mutation")
    results = []
    for c in members:
        stat, bonus = apply_random_mutation(c)
        results.append((c.name, stat, bonus))
    return results


async def mutation_event(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        results = await run_db(_mutation_event_sync, update.effective_chat, update.effective_user)
    except GameError as exc:
        await update.message.reply_text(str(exc))
        return

    lines = ["☄️ <b>یه شهاب‌سنگ جهش‌زا روی گروه فرود اومد!</b> همه‌ی موجودهای فعال این جهش رایگان رو گرفتن:\n"]
    for name, stat, bonus in results:
        lines.append(f"• {name}: +{bonus} {constants.MUTATION_EVENT_STAT_LABELS[stat]}")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


def _creature_power(c: Creature) -> int:
    return c.base_hp + c.base_atk + c.base_def + c.base_spd


def _leaderboard_sync(chat, tg_user):
    group = get_or_create_group(chat)
    user, _ = get_or_create_user(tg_user)
    touch_membership(group, user)
    return sorted(group_member_creatures(group), key=_creature_power, reverse=True)[:10]


async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    creatures = await run_db(_leaderboard_sync, update.effective_chat, update.effective_user)
    if not creatures:
        await update.message.reply_text("هنوز هیچ موجودی توی این گروه ثبت نشده.")
        return
    medals = ["🥇", "🥈", "🥉"]
    lines = ["🏆 <b>برترین موجودات این گروه</b>\n"]
    for i, c in enumerate(creatures, start=1):
        rank = medals[i - 1] if i <= 3 else f"{i}."
        lines.append(f"{rank} {c.name} (Lv{c.level}) — قدرت {_creature_power(c)}")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


def _guardian_sync(chat, tg_user):
    group = get_or_create_group(chat)
    user, _ = get_or_create_user(tg_user)
    touch_membership(group, user)

    creatures = group_member_creatures(group)
    top = ensure_guardian(group, creatures)
    if top is None:
        raise GameError("هنوز کسی توی این گروه موجودی ثبت نکرده.")
    owner = User.objects.filter(id=top.owner_id).first()
    return top, display_name(owner) if owner else str(top.owner_id)


async def guardian(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        top, owner_name = await run_db(_guardian_sync, update.effective_chat, update.effective_user)
    except GameError as exc:
        await update.message.reply_text(str(exc))
        return
    await update.message.reply_text(
        f"🛡 <b>محافظ فعلی گروه</b>\n"
        f"{top.name} ({constants.RARITY_LABELS[top.rarity]}, Lv{top.level}) — متعلق به {owner_name}\n"
        f"قدرت کل: {_creature_power(top)}\n\n"
        f"⚔️ برای گرفتن عنوان: /guardian_challenge\n"
        f"🎁 محافظ فعلی هر روز با /guardian_claim جایزه می‌گیره",
        parse_mode="HTML",
    )


def _guardian_challenge_sync(chat, tg_user):
    group = get_or_create_group(chat)
    user, _ = get_or_create_user(tg_user)
    touch_membership(group, user)

    creature = get_active_creature(user)
    if creature is None:
        raise GameError("اول باید توی پیوی بات /start بزنی تا موجودت رو بگیری.")

    ensure_guardian(group, group_member_creatures(group))
    won, log_text = challenge_guardian(group, user, creature)

    record_action(user, "guardian_challenge")
    completed_missions = check_missions(user, "guardian_challenge")
    return won, log_text, completed_missions


async def guardian_challenge(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        won, log_text, completed_missions = await run_db(
            _guardian_challenge_sync, update.effective_chat, update.effective_user
        )
    except GameError as exc:
        await update.message.reply_text(str(exc))
        return
    result_text = "🎉 <b>بردی و محافظ جدید گروه شدی!</b>" if won else "😔 باختی، محافظ همون قبلیه."
    await update.message.reply_text(
        log_text + "\n\n" + result_text + _mission_lines(completed_missions), parse_mode="HTML"
    )


def _guardian_claim_sync(chat, tg_user):
    group = get_or_create_group(chat)
    user, _ = get_or_create_user(tg_user)
    touch_membership(group, user)

    top = get_guardian(group)
    if top is None or top.owner_id != user.id:
        raise GameError("😅 تو محافظ فعلی این گروه نیستی. با /guardian ببین کیه.")

    assert_energy_available(user, "guardian_stipend")

    user.coins += constants.GUARDIAN_STIPEND_COINS
    user.dna_fragments += constants.GUARDIAN_STIPEND_DNA
    user.save(update_fields=["coins", "dna_fragments"])
    record_action(user, "guardian_stipend")


async def guardian_claim(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        await run_db(_guardian_claim_sync, update.effective_chat, update.effective_user)
    except GameError as exc:
        await update.message.reply_text(str(exc))
        return
    await update.message.reply_text(
        f"🛡 به‌عنوان محافظ گروه، امروز <b>{constants.GUARDIAN_STIPEND_COINS} 💰</b> و "
        f"<b>{constants.GUARDIAN_STIPEND_DNA} 🧬</b> گرفتی!",
        parse_mode="HTML",
    )


def register(application) -> None:
    group_filter = filters.ChatType.GROUPS
    application.add_handler(CommandHandler("duel", duel, group_filter))
    application.add_handler(CommandHandler("raid_spawn", raid_spawn, group_filter))
    application.add_handler(CommandHandler("attack", attack, group_filter))
    application.add_handler(CommandHandler("leaderboard", leaderboard, group_filter))
    application.add_handler(CommandHandler("guardian", guardian, group_filter))
    application.add_handler(CommandHandler("guardian_challenge", guardian_challenge, group_filter))
    application.add_handler(CommandHandler("guardian_claim", guardian_claim, group_filter))
    application.add_handler(CommandHandler("give", give, group_filter))
    application.add_handler(CommandHandler("mutation_event", mutation_event, group_filter))

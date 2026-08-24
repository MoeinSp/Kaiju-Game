from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

from bio_lab.models import Creature, DuelLog, User
from bio_lab.repository import (
    display_name,
    get_active_creature,
    get_or_create_group,
    get_or_create_user,
    group_member_creatures,
    mention,
    touch_membership,
)
from bot.buttons import CONFIRM, DANGER, NAV, PRIMARY, back_btn, btn
from bot.handlers.group_words import group_footer_keyboard
from bot.utils import mission_reward_text, run_db, safe_edit_message_text
from game import constants
from game.buildings import maybe_award_speedup_card
from game.combat import resolve_duel, resolve_duel_detailed
from game.creature import GameError, add_xp, apply_random_mutation
from game.daily import assert_energy_available, check_missions, group_event_available, mark_group_event, record_action
from game.emoji import get_emoji
from game.energy import spend_energy
from game.guardian import challenge_guardian, ensure_guardian, get_guardian
from game.raid import RaidError, attack_boss, distribute_rewards, get_active_boss, spawn_boss
from game.trade import gift_creature


def _speedup_note(minutes: int | None) -> str:
    if minutes is None:
        return ""
    return f"\n{get_emoji('speedup')} جایزه‌ی شانسی: {constants.SPEEDUP_LABELS[minutes]}!"


def _mission_lines(completed: list[dict]) -> str:
    if not completed:
        return ""
    lines = []
    for m in completed:
        lines.append(f"{get_emoji('mission')} ماموریت «{m['label']}» تکمیل شد! {mission_reward_text(m)}")
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
    speedup_won = maybe_award_speedup_card(winner_user)

    DuelLog.objects.create(
        group_id=group.id,
        challenger_id=challenger_user.id,
        opponent_id=opponent_user.id,
        winner_id=winner_user.id,
        log_text=log_text,
    )
    return winner_creature, winner_levels, completed_missions, log_text, speedup_won


def _duel_wager_challenge_sync(chat, challenger_tg, opponent_tg):
    """Validates both sides can actually duel (used before showing the accept/decline
    prompt for a wagered duel) without resolving combat or moving any gold yet."""
    group = get_or_create_group(chat)
    challenger_user, _ = get_or_create_user(challenger_tg)
    opponent_user, _ = get_or_create_user(opponent_tg)
    touch_membership(group, challenger_user)
    touch_membership(group, opponent_user)

    if get_active_creature(challenger_user) is None:
        raise GameError("اول باید توی پیوی بات /start بزنی تا موجودت رو بگیری.")
    if get_active_creature(opponent_user) is None:
        raise GameError(f"{opponent_tg.first_name} هنوز موجودی نداره (باید /start بزنه).")
    return challenger_user, opponent_user


async def duel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message.reply_to_message is None:
        await update.message.reply_text(
            f"{get_emoji('battle')} برای دوئل، روی پیام حریف ریپلای کن و بنویس /duel\n"
            f"برای دوئل با شرط طلا: <code>/duel 50</code> (حداکثر {constants.DUEL_WAGER_MAX})",
            parse_mode="HTML",
        )
        return

    opponent_tg = update.message.reply_to_message.from_user
    challenger_tg = update.effective_user
    if opponent_tg.id == challenger_tg.id or opponent_tg.is_bot:
        await update.message.reply_text("🙅 نمی‌تونی با خودت یا با یه بات دوئل کنی!")
        return

    wager = 0
    if context.args:
        if not context.args[0].isdigit() or int(context.args[0]) <= 0:
            await update.message.reply_text(
                f"مقدار شرط باید عدد مثبت باشه (حداکثر {constants.DUEL_WAGER_MAX}). مثلاً: <code>/duel 50</code>",
                parse_mode="HTML",
            )
            return
        wager = int(context.args[0])
        if wager > constants.DUEL_WAGER_MAX:
            await update.message.reply_text(f"حداکثر شرط مجاز {constants.DUEL_WAGER_MAX} طلاست.")
            return

    if wager == 0:
        try:
            winner_creature, winner_levels, completed_missions, log_text, speedup_won = await run_db(
                _duel_sync, update.effective_chat, challenger_tg, opponent_tg
            )
        except GameError as exc:
            await update.message.reply_text(str(exc))
            return

        reward_text = (
            f"\n\n{get_emoji('coin')} {winner_creature.name} +{constants.DUEL_WIN_COINS} طلا · "
            f"+{constants.DUEL_WIN_XP} XP"
        )
        if winner_levels:
            reward_text += f" {get_emoji('celebrate')} رسید به سطح {winner_creature.level}!"
        reward_text += _mission_lines(completed_missions) + _speedup_note(speedup_won)
        await update.message.reply_text(
            log_text + reward_text,
            parse_mode="HTML",
            reply_markup=group_footer_keyboard(update.effective_user.id),
        )
        return

    try:
        challenger_user, opponent_user = await run_db(
            _duel_wager_challenge_sync, update.effective_chat, challenger_tg, opponent_tg
        )
    except GameError as exc:
        await update.message.reply_text(str(exc))
        return

    keyboard = InlineKeyboardMarkup(
        [
            [
                btn(
                    "قبول می‌کنم",
                    emoji_key="btn_confirm",
                    style=CONFIRM,
                    callback_data=f"duelwager_accept:{challenger_tg.id}:{opponent_tg.id}:{wager}",
                ),
                btn(
                    "رد می‌کنم",
                    emoji_key="btn_cancel",
                    style=DANGER,
                    callback_data=f"duelwager_decline:{challenger_tg.id}:{opponent_tg.id}",
                ),
            ]
        ]
    )
    await update.message.reply_text(
        f"{get_emoji('coin')} <b>{display_name(challenger_user)}</b> با شرط <b>{wager} طلا</b> به "
        f"<b>{display_name(opponent_user)}</b> پیشنهاد دوئل داد! برنده هر دو شرط رو می‌بره.\n"
        "قبول می‌کنی؟ 👇",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


def _duel_wager_resolve_sync(chat, challenger_id, opponent_id, wager, acceptor_id):
    if acceptor_id != opponent_id:
        raise GameError("فقط طرف مقابل می‌تونه این دوئل رو قبول یا رد کنه.")

    group = get_or_create_group(chat)
    try:
        challenger_user = User.objects.get(id=challenger_id)
        opponent_user = User.objects.get(id=opponent_id)
    except User.DoesNotExist:
        raise GameError("یکی از بازیکن‌ها دیگه پیدا نشد.")

    challenger_creature = get_active_creature(challenger_user)
    opponent_creature = get_active_creature(opponent_user)
    if challenger_creature is None or opponent_creature is None:
        raise GameError("یکی از دو نفر دیگه موجود فعال نداره.")
    if challenger_user.coins < wager or opponent_user.coins < wager:
        raise GameError(f"یکی از دو نفر دیگه {wager} طلا نداره، دوئل لغو شد.")

    winner_creature, log_text = resolve_duel(challenger_creature, opponent_creature)
    is_challenger_winner = winner_creature.id == challenger_creature.id
    winner_user = challenger_user if is_challenger_winner else opponent_user
    loser_user = opponent_user if is_challenger_winner else challenger_user
    loser_creature = opponent_creature if is_challenger_winner else challenger_creature

    winner_user.coins += wager
    loser_user.coins -= wager
    winner_levels = add_xp(winner_creature, constants.DUEL_WIN_XP)
    add_xp(loser_creature, constants.DUEL_LOSE_XP)
    winner_user.save(update_fields=["coins"])
    loser_user.save(update_fields=["coins"])
    winner_creature.save()
    loser_creature.save()

    record_action(winner_user, "duel_win")
    completed_missions = check_missions(winner_user, "duel_win")
    speedup_won = maybe_award_speedup_card(winner_user)

    DuelLog.objects.create(
        group_id=group.id,
        challenger_id=challenger_user.id,
        opponent_id=opponent_user.id,
        winner_id=winner_user.id,
        wager_gold=wager,
        log_text=log_text,
    )
    return winner_creature, winner_levels, completed_missions, log_text, speedup_won


async def duel_wager_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    action, challenger_id_str, opponent_id_str, *rest = query.data.split(":")
    challenger_id, opponent_id = int(challenger_id_str), int(opponent_id_str)

    if action == "duelwager_decline":
        if update.effective_user.id != opponent_id:
            await query.answer("فقط طرف مقابل می‌تونه این دوئل رو رد کنه.", show_alert=True)
            return
        await query.answer()
        await safe_edit_message_text(query, f"{get_emoji('cancel')} پیشنهاد دوئل با شرط رد شد.", parse_mode="HTML")
        return

    wager = int(rest[0])
    try:
        winner_creature, winner_levels, completed_missions, log_text, speedup_won = await run_db(
            _duel_wager_resolve_sync, update.effective_chat, challenger_id, opponent_id, wager, update.effective_user.id
        )
    except GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return

    await query.answer()
    reward_text = f"\n\n{get_emoji('coin')} {winner_creature.name} +{wager} طلا (شرط) · +{constants.DUEL_WIN_XP} XP"
    if winner_levels:
        reward_text += f" {get_emoji('celebrate')} رسید به سطح {winner_creature.level}!"
    reward_text += _mission_lines(completed_missions) + _speedup_note(speedup_won)
    await safe_edit_message_text(query, log_text + reward_text, parse_mode="HTML")


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
        f"{get_emoji('gift')} استفاده درست: روی پیام طرف مقابل ریپلای کن و بنویس /give به‌همراه نوع و مقدار\n"
        "<code>/give gold 50</code> · <code>/give dna 10</code> · <code>/give creature 7</code> "
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
            f"{get_emoji('gift')} {display_name(sender)} موجود <b>{creature.name}</b> رو به "
            f"{display_name(receiver)} هدیه داد!",
            parse_mode="HTML",
        )
    else:
        _, sender, receiver, resource_key, amount = result
        label = constants.GIVE_RESOURCE_LABELS[resource_key]
        await update.message.reply_text(
            f"{get_emoji('gift')} {display_name(sender)} مقدار {amount} {label} به "
            f"{display_name(receiver)} هدیه داد!",
            parse_mode="HTML",
        )


def _raid_spawn_sync(chat, spawner_tg):
    group = get_or_create_group(chat)
    spawner_user, _ = get_or_create_user(spawner_tg)
    touch_membership(group, spawner_user)
    return spawn_boss(group)


async def raid_spawn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        boss = await run_db(_raid_spawn_sync, update.effective_chat, update.effective_user)
    except RaidError as exc:
        await update.message.reply_text(str(exc))
        return
    await update.message.reply_text(
        f"{get_emoji('raid_boss')} <b>باس رید لِوِل {boss.level} ظاهر شد: {boss.name}!</b>\n"
        f"{constants.render_bar(boss.current_hp, boss.max_hp, width=14)}  {boss.current_hp}/{boss.max_hp} HP\n"
        f"{constants.element_label(boss.element)}\n\n"
        f"همه «اتک» بفرستن تا به <b>باس</b> حمله کنن — هر حمله ۱ ⚡ انرژی می‌بره و "
        f"هرچی سهم دمیجت بیشتر، غنیمت بیشتر! 💪\n"
        f"<i>سقف روزانه نداره؛ ولی هر اتک، کول‌داون اتک بعدیت رو ۱ دقیقه بیشتر می‌کنه.</i>\n"
        f"باس تایم‌اوت نداره؛ می‌مونه تا بکشیدش — و بعدش لِوِل رید گروه یکی بالا می‌ره و باس بعدی قوی‌تر و پرجایزه‌تره.\n"
        f"<i>می‌خوای به یه بازیکن حمله کنی؟ روی پیامش ریپلای کن و «اتک» بفرست.</i>",
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
    speedup_won = None
    if defeated:
        rewards = distribute_rewards(boss)
        reward_lines = []
        for uid, r in sorted(rewards.items(), key=lambda kv: kv[1]["damage"], reverse=True):
            member = User.objects.filter(id=uid).first()
            name = display_name(member) if member else str(uid)
            reward_lines.append(
                f"{name} — {get_emoji('dna')}{r['dna']} {get_emoji('coin')}{r['coins']} (دمیج: {r['damage']})"
            )
        speedup_won = maybe_award_speedup_card(user)  # bonus chance for whoever lands the killing blow

    return creature, boss, dmg, defeated, completed_missions, reward_lines, speedup_won, user.energy


async def attack(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # replying to another player's message turns «اتک» into a PvP challenge instead
    # of a hit on the raid boss.
    reply = update.message.reply_to_message
    if reply is not None and reply.from_user is not None and not reply.from_user.is_bot:
        await _pvp_attack_prompt(update, context, reply.from_user)
        return

    try:
        creature, boss, dmg, defeated, completed_missions, reward_lines, speedup_won, energy_left = await run_db(
            _attack_sync, update.effective_chat, update.effective_user
        )
    except (RaidError, GameError) as exc:
        await update.message.reply_text(str(exc))
        return

    text = (
        f"{get_emoji('attack_action')} <b>{creature.name}</b> به باس <b>{boss.name}</b> (لِوِل {boss.level}) "
        f"<b>{dmg}</b> دمیج زد!\n"
        f"{constants.render_bar(boss.current_hp, boss.max_hp, width=14)}  {max(boss.current_hp, 0)}/{boss.max_hp} HP\n"
        f"⚡ ۱ انرژی کم شد (باقی‌مونده: {energy_left})"
    )
    text += _mission_lines(completed_missions)
    if defeated:
        text += (
            f"\n\n{get_emoji('celebrate')} <b>باس لِوِل {boss.level} شکست خورد!</b> "
            f"لِوِل رید گروه رفت رو <b>{boss.level + 1}</b> — باس بعدی قوی‌تر و پرجایزه‌تره.\n"
            "غنایم بین همه‌ی مهاجم‌ها:\n" + "\n".join(reward_lines)
        )
        text += _speedup_note(speedup_won)
    else:
        text += "\n<i>💡 برای حمله به یه بازیکن، روی پیامش ریپلای کن و «اتک» بفرست.</i>"

    await update.message.reply_text(
        text, parse_mode="HTML", reply_markup=group_footer_keyboard(update.effective_user.id)
    )


# ── PvP: reply-to-attack another player ───────────────────────────────────────

def _pvp_preview_sync(attacker_tg, target_tg):
    """Read-only power comparison before a reply-attack is confirmed."""
    if target_tg.id == attacker_tg.id or target_tg.is_bot:
        raise GameError("🙅 نمی‌تونی به خودت یا به یه بات حمله کنی!")
    attacker, _ = get_or_create_user(attacker_tg)
    target = User.objects.filter(id=target_tg.id).first()
    if target is None:
        raise GameError("این بازیکن هنوز بازی رو شروع نکرده — نمی‌شه بهش حمله کرد.")
    a_creature = get_active_creature(attacker)
    t_creature = get_active_creature(target)
    if a_creature is None:
        raise GameError("اول باید توی پیوی بات /start بزنی تا موجودت رو بگیری.")
    if t_creature is None:
        raise GameError("این بازیکن موجود فعالی نداره.")
    return (
        display_name(attacker), _creature_power(a_creature), a_creature.element,
        display_name(target), _creature_power(t_creature), t_creature.element,
    )


async def _pvp_attack_prompt(update, context, target_tg) -> None:
    try:
        a_name, a_power, a_elem, t_name, t_power, t_elem = await run_db(
            _pvp_preview_sync, update.effective_user, target_tg
        )
    except GameError as exc:
        await update.message.reply_text(str(exc))
        return
    gap = t_power - a_power
    odds = "🟢 شانس بالا" if gap < -15 else ("🔴 خطرناک" if gap > 15 else "🟡 نزدیک")
    matchup = constants.element_matchup_note(a_elem, t_elem)
    keyboard = InlineKeyboardMarkup([
        [btn("⚔️ حمله!", emoji_key="btn_attack", style=CONFIRM,
             callback_data=f"gatk:{update.effective_user.id}:{target_tg.id}")],
        [btn("بی‌خیال", emoji_key="btn_cancel", style=DANGER,
             callback_data=f"gatk_cancel:{update.effective_user.id}")],
    ])
    await update.message.reply_text(
        f"{get_emoji('battle')} <b>حمله به {t_name}؟</b>\n\n"
        f"💪 قدرت حریف: <b>{t_power}</b>  ({constants.element_label(t_elem)})\n"
        f"💪 قدرت تو: <b>{a_power}</b>  ({constants.element_label(a_elem)}) — {odds}\n"
        + (f"{matchup}\n" if matchup else "")
        + f"\n<i>هر حمله ۱ ⚡ انرژی می‌بره. برنده تا {int(constants.GROUP_ATTACK_LOOT_PERCENT * 100)}٪ طلای بازنده رو غارت می‌کنه.</i>",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


def _pvp_attack_sync(chat, attacker_tg, target_id):
    group = get_or_create_group(chat)
    attacker, _ = get_or_create_user(attacker_tg)
    touch_membership(group, attacker)
    target = User.objects.filter(id=target_id).first()
    if target is None:
        raise GameError("این بازیکن دیگه پیدا نشد.")
    a_creature = get_active_creature(attacker)
    t_creature = get_active_creature(target)
    if a_creature is None or t_creature is None:
        raise GameError("یکی از دو طرف موجود فعال نداره.")

    spend_energy(attacker, constants.RAID_ATTACK_ENERGY_COST, "حمله")
    attacker.save(update_fields=["energy", "energy_updated_at"])

    winner_creature, log_text, detail_log = resolve_duel_detailed(a_creature, t_creature)
    attacker_won = winner_creature.id == a_creature.id
    winner_user = attacker if attacker_won else target
    loser_user = target if attacker_won else attacker
    winner_creature_obj = a_creature if attacker_won else t_creature
    loser_creature_obj = t_creature if attacker_won else a_creature

    # the winner loots exactly 10% of the LOSER's gold (integer, no cap, no decimals)
    loot = max(0, loser_user.coins // 10)
    loser_user.coins -= loot
    winner_user.coins += loot
    winner_levels = add_xp(winner_creature_obj, constants.DUEL_WIN_XP)
    add_xp(loser_creature_obj, constants.DUEL_LOSE_XP)
    winner_user.save(update_fields=["coins"])
    loser_user.save(update_fields=["coins"])
    winner_creature_obj.save()
    loser_creature_obj.save()

    record_action(attacker, "duel_win" if attacker_won else "duel_loss")
    completed_missions = check_missions(winner_user, "duel_win") if attacker_won else []
    speedup_won = maybe_award_speedup_card(winner_user) if attacker_won else None

    DuelLog.objects.create(
        group_id=group.id, challenger_id=attacker.id, opponent_id=target.id,
        winner_id=winner_user.id, wager_gold=loot, log_text=log_text,
    )
    return {
        "log_text": log_text,
        "detail_log": detail_log,
        "attacker_won": attacker_won,
        "winner_name": display_name(winner_user),
        "loot": loot,
        "winner_level_up": bool(winner_levels),
        "winner_creature": winner_creature_obj.name,
        "winner_new_level": winner_creature_obj.level,
        "energy_left": attacker.energy,
        "missions": completed_missions,
        "speedup": speedup_won,
    }


async def pvp_attack_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, attacker_id, target_id = query.data.split(":")
    if update.effective_user.id != int(attacker_id):
        await query.answer("این حمله مال تو نیست — خودت روی پیام حریف «اتک» بفرست.", show_alert=True)
        return
    try:
        result = await run_db(_pvp_attack_sync, update.effective_chat, update.effective_user, int(target_id))
    except (RaidError, GameError) as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer("🟢 بردی!" if result["attacker_won"] else "🔴 باختی.")
    head = (
        f"{get_emoji('celebrate')} <b>{result['winner_name']} برد!</b>"
        if result["attacker_won"]
        else f"💀 <b>باختی — {result['winner_name']} برنده شد.</b>"
    )
    reward = f"\n\n{get_emoji('coin')} برنده {result['loot']} طلا از بازنده غارت کرد · +{constants.DUEL_WIN_XP} XP"
    if result["winner_level_up"]:
        reward += f" {get_emoji('celebrate')} {result['winner_creature']} رسید به سطح {result['winner_new_level']}!"
    reward += f"\n⚡ ۱ انرژی کم شد (باقی‌مونده: {result['energy_left']})"
    reward += _mission_lines(result["missions"]) + _speedup_note(result["speedup"])
    context.user_data["pvp_last_detail"] = result.get("detail_log", "")
    keyboard = InlineKeyboardMarkup(
        [[btn("🔍 جزییات حمله", style=NAV, callback_data=f"gatk_detail:{update.effective_user.id}")]]
        + list(group_footer_keyboard(update.effective_user.id).inline_keyboard)
    )
    await safe_edit_message_text(
        query, result["log_text"] + "\n\n" + head + reward, parse_mode="HTML",
        reply_markup=keyboard,
    )


async def pvp_detail_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, owner_id = query.data.split(":")
    if update.effective_user.id != int(owner_id):
        await query.answer("این جزییات مال تو نیست.", show_alert=True)
        return
    detail = context.user_data.get("pvp_last_detail")
    if not detail:
        await query.answer("جزییاتی ذخیره نشده.", show_alert=True)
        return
    await query.answer()
    await query.message.reply_text(detail, parse_mode="HTML")


async def pvp_attack_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, attacker_id = query.data.split(":")
    if update.effective_user.id != int(attacker_id):
        await query.answer()
        return
    await query.answer("لغو شد.")
    await safe_edit_message_text(query, "🚫 حمله لغو شد.")


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

    lines = [
        f"{get_emoji('comet')} <b>یه شهاب‌سنگ جهش‌زا روی گروه فرود اومد!</b> "
        "همه‌ی موجودهای فعال این جهش رایگان رو گرفتن:\n"
    ]
    for name, stat, bonus in results:
        lines.append(f"• {name}: +{bonus} {constants.MUTATION_EVENT_STAT_LABELS[stat]}")
    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=group_footer_keyboard(update.effective_user.id),
    )


def _creature_power(c: Creature) -> int:
    return c.base_hp + c.base_atk + c.base_def + c.base_spd


def _leaderboard_sync(chat, tg_user):
    group = get_or_create_group(chat)
    user, _ = get_or_create_user(tg_user)
    touch_membership(group, user)
    return sorted(group_member_creatures(group), key=_creature_power, reverse=True)[:10]


async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    creatures = await run_db(_leaderboard_sync, update.effective_chat, update.effective_user)
    keyboard = group_footer_keyboard(update.effective_user.id, skip="leaderboard")
    if not creatures:
        await update.message.reply_text(
            "هنوز هیچ موجودی توی این گروه ثبت نشده.", reply_markup=keyboard
        )
        return
    medals = [get_emoji("medal_gold"), get_emoji("medal_silver"), get_emoji("medal_bronze")]
    lines = [f"{get_emoji('trophy')} <b>برترین بازیکن‌های این گروه</b>\n"]
    for i, c in enumerate(creatures, start=1):
        rank = medals[i - 1] if i <= 3 else f"{i}."
        lines.append(f"{rank} {mention(c.owner)} — 💪{_creature_power(c)}  <i>(Lv{c.level})</i>")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML", reply_markup=keyboard)


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
        f"{get_emoji('guardian')} <b>محافظ فعلی گروه</b>\n"
        f"{top.name} ({constants.RARITY_LABELS[top.rarity]}, Lv{top.level}) — متعلق به {owner_name}\n"
        f"قدرت کل: {_creature_power(top)}\n\n"
        f"{get_emoji('battle')} برای گرفتن عنوان: /guardian_challenge\n"
        f"{get_emoji('gift')} محافظ فعلی هر روز با /guardian_claim جایزه می‌گیره",
        parse_mode="HTML",
    )


def _guardian_challenge_sync(chat, tg_user):
    group = get_or_create_group(chat)
    user, _ = get_or_create_user(tg_user)
    touch_membership(group, user)

    creature = get_active_creature(user)
    if creature is None:
        raise GameError("اول باید توی پیوی بات /start بزنی تا موجودت رو بگیری.")

    spend_energy(user, constants.GUARDIAN_CHALLENGE_ENERGY_COST, "چالش نگهبان")
    user.save(update_fields=["energy", "energy_updated_at"])
    ensure_guardian(group, group_member_creatures(group))
    won, log_text = challenge_guardian(group, user, creature)

    record_action(user, "guardian_challenge")
    completed_missions = check_missions(user, "guardian_challenge")
    speedup_won = maybe_award_speedup_card(user) if won else None
    return won, log_text, completed_missions, speedup_won


async def guardian_challenge(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        won, log_text, completed_missions, speedup_won = await run_db(
            _guardian_challenge_sync, update.effective_chat, update.effective_user
        )
    except GameError as exc:
        await update.message.reply_text(str(exc))
        return
    result_text = f"{get_emoji('celebrate')} <b>بردی و محافظ جدید گروه شدی!</b>" if won else "😔 باختی، محافظ همون قبلیه."
    await update.message.reply_text(
        log_text + "\n\n" + result_text + _mission_lines(completed_missions) + _speedup_note(speedup_won),
        parse_mode="HTML",
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
        f"{get_emoji('guardian')} به‌عنوان محافظ گروه، امروز "
        f"<b>{constants.GUARDIAN_STIPEND_COINS} {get_emoji('coin')}</b> و "
        f"<b>{constants.GUARDIAN_STIPEND_DNA} {get_emoji('dna')}</b> گرفتی!",
        parse_mode="HTML",
    )


def register(application) -> None:
    group_filter = filters.ChatType.GROUPS
    application.add_handler(CommandHandler("duel", duel, group_filter))
    application.add_handler(CallbackQueryHandler(duel_wager_callback, pattern=r"^duelwager_"))
    application.add_handler(CommandHandler("raid_spawn", raid_spawn, group_filter))
    application.add_handler(CommandHandler("attack", attack, group_filter))
    application.add_handler(CallbackQueryHandler(pvp_attack_callback, pattern=r"^gatk:\d+:\d+$"))
    application.add_handler(CallbackQueryHandler(pvp_attack_cancel_callback, pattern=r"^gatk_cancel:\d+$"))
    application.add_handler(CallbackQueryHandler(pvp_detail_callback, pattern=r"^gatk_detail:\d+$"))
    application.add_handler(CommandHandler("leaderboard", leaderboard, group_filter))
    application.add_handler(CommandHandler("guardian", guardian, group_filter))
    application.add_handler(CommandHandler("guardian_challenge", guardian_challenge, group_filter))
    application.add_handler(CommandHandler("guardian_claim", guardian_claim, group_filter))
    application.add_handler(CommandHandler("give", give, group_filter))
    application.add_handler(CommandHandler("mutation_event", mutation_event, group_filter))

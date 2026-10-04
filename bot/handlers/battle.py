import datetime

from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

from bot.utils import alert_text
from bio_lab.models import InteractiveBattle, User
from bio_lab.repository import display_name, get_active_creature, get_or_create_group, get_or_create_user, touch_membership
from bot.buttons import BATTLE, CONFIRM, DANGER, PRIMARY, back_btn, btn
from bot.utils import mission_reward_text, run_db, safe_edit_message_text
from game import constants
from game.creature import GameError, add_xp, effective_stats
from game.daily import check_missions, record_action
from game.emoji import get_emoji
from game.interactive_battle import attach_player_names, advance_turn, is_finished, perform_action, pick_first_turn, render_battle_card


# A live battle nobody is playing must not block that pair forever: an unanswered offer
# or an abandoned fight stops counting after this long.
BATTLE_STALE_MINUTES = 15
# Live battles cost no energy, so the WIN REWARD is capped per day — otherwise two
# accounts could loop «/battle → قبول → تسلیم» for unlimited gold/XP/mission progress.
LIVE_BATTLE_REWARDED_WINS_PER_DAY = 3


def _battle_keyboard(battle: InteractiveBattle) -> InlineKeyboardMarkup:
    skill_uses = battle.skill_uses_a if battle.turn == "a" else battle.skill_uses_b
    buttons = [btn("حمله", emoji_key="btn_attack", style=BATTLE, callback_data=f"battle_action:{battle.id}:attack")]
    if skill_uses > 0:
        buttons.append(btn("مهارت", emoji_key="btn_skill", style=PRIMARY, callback_data=f"battle_action:{battle.id}:skill"))
    buttons.append(btn("تسلیم", emoji_key="btn_forfeit", style=DANGER, callback_data=f"battle_action:{battle.id}:forfeit"))
    return InlineKeyboardMarkup([buttons])


def _battle_cmd_sync(chat, challenger_tg, opponent_tg):
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

    stale_before = timezone.now() - datetime.timedelta(minutes=BATTLE_STALE_MINUTES)
    InteractiveBattle.objects.filter(
        group_id=group.id, status__in=["pending", "active"], created_at__lt=stale_before
    ).update(status="expired")
    existing = (
        InteractiveBattle.objects.filter(group_id=group.id, status__in=["pending", "active"])
        .filter(
            Q(player_a_id=challenger_user.id, player_b_id=opponent_user.id)
            | Q(player_a_id=opponent_user.id, player_b_id=challenger_user.id)
        )
        .first()
    )
    if existing is not None:
        raise GameError("شما دو نفر همین الان یه نبرد باز دارید!")

    battle = InteractiveBattle.objects.create(
        group_id=group.id,
        player_a_id=challenger_user.id,
        player_b_id=opponent_user.id,
        creature_a_id=challenger_creature.id,
        creature_b_id=opponent_creature.id,
        hp_a=effective_stats(challenger_creature)["hp"],
        hp_b=effective_stats(opponent_creature)["hp"],
        turn="a",
        status="pending",
    )
    return battle, challenger_user, challenger_creature, opponent_user


async def battle_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message.reply_to_message is None:
        await update.message.reply_text(
            f"{get_emoji('battle')} برای نبرد تعاملی، روی پیام حریف ریپلای کن و بنویس /battle",
            parse_mode="HTML",
        )
        return

    opponent_tg = update.message.reply_to_message.from_user
    challenger_tg = update.effective_user
    if opponent_tg.id == challenger_tg.id or opponent_tg.is_bot:
        await update.message.reply_text("🙅 نمی‌تونی با خودت یا با یه بات نبرد کنی!")
        return

    try:
        battle, challenger_user, challenger_creature, opponent_user = await run_db(
            _battle_cmd_sync, update.effective_chat, challenger_tg, opponent_tg
        )
    except GameError as exc:
        await update.message.reply_text(alert_text(exc, 3500))
        return

    keyboard = InlineKeyboardMarkup(
        [
            [
                btn("قبول نبرد", emoji_key="btn_confirm", style=CONFIRM, callback_data=f"battle_accept:{battle.id}"),
                btn("رد نبرد", emoji_key="btn_cancel", style=DANGER, callback_data=f"battle_decline:{battle.id}"),
            ]
        ]
    )
    await update.message.reply_text(
        f"{get_emoji('battle')} <b>{display_name(challenger_user)}</b> با <b>{challenger_creature.name}</b> به "
        f"<b>{display_name(opponent_user)}</b> پیشنهاد نبرد تعاملی زنده داد!\n"
        f"<i>آیا قبول می‌کنید؟</i> 👇",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


def _battle_accept_sync(battle_id, acceptor_id):
    try:
        battle = InteractiveBattle.objects.get(id=battle_id)
    except InteractiveBattle.DoesNotExist:
        raise GameError("این پیشنهاد دیگه معتبر نیست.")
    if battle.status != "pending":
        raise GameError("این پیشنهاد دیگه معتبر نیست.")
    if acceptor_id != battle.player_b_id:
        raise GameError("فقط طرف مقابل می‌تونه قبول کنه.")

    battle.turn = pick_first_turn(battle.creature_a, battle.creature_b)
    battle.status = "active"
    battle.save(update_fields=["turn", "status"])
    return attach_player_names(battle)


async def battle_accept_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    battle_id = int(query.data.split(":")[1])
    try:
        battle = await run_db(_battle_accept_sync, battle_id, update.effective_user.id)
    except GameError as exc:
        await query.answer(alert_text(exc), show_alert=True)
        return
    await query.answer()
    await safe_edit_message_text(query,
        render_battle_card(battle), parse_mode="HTML", reply_markup=_battle_keyboard(battle)
    )


def _battle_decline_sync(battle_id, decliner_id):
    try:
        battle = InteractiveBattle.objects.get(id=battle_id)
    except InteractiveBattle.DoesNotExist:
        raise GameError("این پیشنهاد دیگه معتبر نیست.")
    if battle.status != "pending":
        raise GameError("این پیشنهاد دیگه معتبر نیست.")
    if decliner_id != battle.player_b_id:
        raise GameError("فقط طرف مقابل می‌تونه رد کنه.")
    battle.status = "declined"
    battle.save(update_fields=["status"])


async def battle_decline_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    battle_id = int(query.data.split(":")[1])
    try:
        await run_db(_battle_decline_sync, battle_id, update.effective_user.id)
    except GameError as exc:
        await query.answer(alert_text(exc), show_alert=True)
        return
    await query.answer()
    await safe_edit_message_text(query, f"{get_emoji('cancel')} پیشنهاد نبرد زنده رد شد.", parse_mode="HTML")


@transaction.atomic
def _battle_action_sync(battle_id, actor_tg_id, action):
    try:
        # row lock: two taps on the same turn used to both apply (and pay the win twice)
        battle = InteractiveBattle.objects.select_for_update().get(id=battle_id)
    except InteractiveBattle.DoesNotExist:
        raise GameError("این نبرد دیگه فعال نیست.")
    if battle.status != "active":
        raise GameError("این نبرد دیگه فعال نیست.")

    expected_user_id = battle.player_a_id if battle.turn == "a" else battle.player_b_id
    if actor_tg_id != expected_user_id:
        raise GameError("نوبت تو نیست!")

    actor_side = battle.turn
    action_logs = perform_action(battle, actor_side, action)

    finished, winner_side = is_finished(battle)
    turn_logs = [] if finished else advance_turn(battle)
    battle.log = "\n".join(line for line in [battle.log, *action_logs, *turn_logs] if line)

    reward_lines: list[str] = []
    if finished:
        battle.status = "finished"
        winner_user_id = battle.player_a_id if winner_side == "a" else battle.player_b_id
        winner_creature = battle.creature_a if winner_side == "a" else battle.creature_b
        loser_creature = battle.creature_b if winner_side == "a" else battle.creature_a
        winner_user = User.objects.select_for_update().get(id=winner_user_id)

        rewarded_today = record_action(winner_user, "live_battle_win")
        if rewarded_today > LIVE_BATTLE_REWARDED_WINS_PER_DAY:
            battle.save()
            reward_lines.append(
                f"<blockquote>{get_emoji('trophy')} <b>{winner_creature.name} برنده شد!</b>\n"
                f"پاداش نبرد زنده روزی {LIVE_BATTLE_REWARDED_WINS_PER_DAY} بار داده می‌شه؛ "
                f"سهم امروزت رو گرفتی.</blockquote>"
            )
            return attach_player_names(battle), finished, reward_lines

        reward = constants.duel_win_reward(loser_creature.level)
        User.objects.filter(id=winner_user.id).update(
            coins=F("coins") + reward["coins"], dna_fragments=F("dna_fragments") + reward["dna"]
        )
        winner_levels = add_xp(winner_creature, reward["xp"])
        add_xp(loser_creature, constants.DUEL_LOSE_XP)
        winner_creature.save()
        loser_creature.save()

        record_action(winner_user, "duel_win")
        completed_missions = check_missions(winner_user, "duel_win")

        reward_lines.append(
            f"<blockquote>{get_emoji('trophy')} <b>پاداش پیروزی {winner_creature.name}:</b>\n"
            f"{get_emoji('coin')} طلا: <code>+{reward['coins']:,}</code>\n"
            f"{get_emoji('star')} تجربه: <code>+{reward['xp']:,}</code> XP"
            + (f"\n{get_emoji('dna')} DNA: <code>+{reward['dna']:,}</code>" if reward["dna"] else "")
            + (f"\n{get_emoji('celebrate')} <b>رسید به سطح <code>{winner_creature.level}</code>!</b>" if winner_levels else "")
            + "</blockquote>"
        )
        for m in completed_missions:
            reward_lines.append(
                f"<blockquote>{get_emoji('mission')} <b>ماموریت «{m['label']}» تکمیل شد!</b>\n{mission_reward_text(m)}</blockquote>"
            )

    battle.save()
    return attach_player_names(battle), finished, reward_lines


async def battle_action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, battle_id_str, action = query.data.split(":")
    battle_id = int(battle_id_str)

    try:
        battle, finished, reward_lines = await run_db(
            _battle_action_sync, battle_id, update.effective_user.id, action
        )
    except GameError as exc:
        await query.answer(alert_text(exc), show_alert=True)
        return

    await query.answer()
    card_text = render_battle_card(battle)
    if reward_lines:
        card_text += "\n\n" + "\n".join(reward_lines)
    keyboard = None if finished else _battle_keyboard(battle)
    await safe_edit_message_text(query, card_text, parse_mode="HTML", reply_markup=keyboard)


def register(application) -> None:
    application.add_handler(CommandHandler("battle", battle_cmd, filters.ChatType.GROUPS))
    application.add_handler(CallbackQueryHandler(battle_accept_callback, pattern=r"^battle_accept:"))
    application.add_handler(CallbackQueryHandler(battle_decline_callback, pattern=r"^battle_decline:"))
    application.add_handler(CallbackQueryHandler(battle_action_callback, pattern=r"^battle_action:"))

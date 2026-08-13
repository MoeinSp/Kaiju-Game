from sqlalchemy import and_, or_, select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

from db.models import InteractiveBattle, User
from db.repository import display_name, get_active_creature, get_or_create_group, get_or_create_user, touch_membership
from db.session import get_session
from game import constants
from game.creature import GameError, add_xp, effective_stats
from game.daily import check_missions, record_action
from game.interactive_battle import advance_turn, is_finished, perform_action, pick_first_turn, render_battle_card


def _battle_keyboard(battle: InteractiveBattle) -> InlineKeyboardMarkup:
    skill_uses = battle.skill_uses_a if battle.turn == "a" else battle.skill_uses_b
    buttons = [InlineKeyboardButton("🗡 حمله", callback_data=f"battle_action:{battle.id}:attack")]
    if skill_uses > 0:
        buttons.append(InlineKeyboardButton("✨ اسکیل", callback_data=f"battle_action:{battle.id}:skill"))
    buttons.append(InlineKeyboardButton("🏳 تسلیم", callback_data=f"battle_action:{battle.id}:forfeit"))
    return InlineKeyboardMarkup([buttons])


async def battle_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message.reply_to_message is None:
        await update.message.reply_text("برای نبرد تعاملی، روی پیام حریف ریپلای کن و بنویس /battle")
        return

    opponent_tg = update.message.reply_to_message.from_user
    challenger_tg = update.effective_user
    if opponent_tg.id == challenger_tg.id or opponent_tg.is_bot:
        await update.message.reply_text("نمی‌تونی با خودت یا با یه بات نبرد کنی!")
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

        existing = session.execute(
            select(InteractiveBattle).where(
                InteractiveBattle.group_id == group.id,
                InteractiveBattle.status.in_(["pending", "active"]),
                or_(
                    and_(
                        InteractiveBattle.player_a_id == challenger_user.id,
                        InteractiveBattle.player_b_id == opponent_user.id,
                    ),
                    and_(
                        InteractiveBattle.player_a_id == opponent_user.id,
                        InteractiveBattle.player_b_id == challenger_user.id,
                    ),
                ),
            )
        ).scalar_one_or_none()
        if existing is not None:
            await update.message.reply_text("شما دو نفر همین الان یه نبرد باز دارید!")
            return

        battle = InteractiveBattle(
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
        session.add(battle)
        session.commit()

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("✅ قبول می‌کنم", callback_data=f"battle_accept:{battle.id}"),
                    InlineKeyboardButton("❌ رد می‌کنم", callback_data=f"battle_decline:{battle.id}"),
                ]
            ]
        )
        await update.message.reply_text(
            f"⚔️ {display_name(challenger_user)} با {challenger_creature.name} به "
            f"{display_name(opponent_user)} پیشنهاد نبرد تعاملی داد! قبول می‌کنی؟",
            reply_markup=keyboard,
        )
    finally:
        session.close()


async def battle_accept_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    battle_id = int(query.data.split(":")[1])

    session = get_session()
    try:
        battle = session.get(InteractiveBattle, battle_id)
        if battle is None or battle.status != "pending":
            await query.answer("این پیشنهاد دیگه معتبر نیست.", show_alert=True)
            return
        if update.effective_user.id != battle.player_b_id:
            await query.answer("فقط طرف مقابل می‌تونه قبول کنه.", show_alert=True)
            return

        await query.answer()
        battle.turn = pick_first_turn(battle.creature_a, battle.creature_b)
        battle.status = "active"
        session.commit()

        await query.edit_message_text(
            render_battle_card(battle), parse_mode="HTML", reply_markup=_battle_keyboard(battle)
        )
    finally:
        session.close()


async def battle_decline_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    battle_id = int(query.data.split(":")[1])

    session = get_session()
    try:
        battle = session.get(InteractiveBattle, battle_id)
        if battle is None or battle.status != "pending":
            await query.answer("این پیشنهاد دیگه معتبر نیست.", show_alert=True)
            return
        if update.effective_user.id != battle.player_b_id:
            await query.answer("فقط طرف مقابل می‌تونه رد کنه.", show_alert=True)
            return

        battle.status = "declined"
        session.commit()
        await query.answer()
        await query.edit_message_text("❌ پیشنهاد نبرد رد شد.")
    finally:
        session.close()


async def battle_action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, battle_id_str, action = query.data.split(":")
    battle_id = int(battle_id_str)

    session = get_session()
    try:
        battle = session.get(InteractiveBattle, battle_id)
        if battle is None or battle.status != "active":
            await query.answer("این نبرد دیگه فعال نیست.", show_alert=True)
            return

        expected_user_id = battle.player_a_id if battle.turn == "a" else battle.player_b_id
        if update.effective_user.id != expected_user_id:
            await query.answer("نوبت تو نیست!", show_alert=True)
            return

        actor_side = battle.turn
        try:
            action_logs = perform_action(battle, actor_side, action)
        except GameError as exc:
            await query.answer(str(exc), show_alert=True)
            return
        await query.answer()

        finished, winner_side = is_finished(battle)
        turn_logs = [] if finished else advance_turn(battle)
        battle.log = "\n".join(line for line in [battle.log, *action_logs, *turn_logs] if line)

        reward_lines: list[str] = []
        if finished:
            battle.status = "finished"
            winner_user_id = battle.player_a_id if winner_side == "a" else battle.player_b_id
            winner_creature = battle.creature_a if winner_side == "a" else battle.creature_b
            loser_creature = battle.creature_b if winner_side == "a" else battle.creature_a
            winner_user = session.get(User, winner_user_id)

            winner_user.coins += constants.DUEL_WIN_COINS
            winner_levels = add_xp(winner_creature, constants.DUEL_WIN_XP)
            add_xp(loser_creature, constants.DUEL_LOSE_XP)
            record_action(session, winner_user, "duel_win")
            completed_missions = check_missions(session, winner_user, "duel_win")

            reward_lines.append(
                f"💰 {winner_creature.name} +{constants.DUEL_WIN_COINS} سکه, +{constants.DUEL_WIN_XP} XP"
                + (f" 🎉 سطح {winner_creature.level} شد!" if winner_levels else "")
            )
            for m in completed_missions:
                reward_lines.append(
                    f"🎯 ماموریت «{m['label']}» کامل شد! +{m['coins']} سکه"
                    + (f", +{m['dna']} DNA" if m["dna"] else "")
                )

        session.commit()

        card_text = render_battle_card(battle)
        if reward_lines:
            card_text += "\n\n" + "\n".join(reward_lines)
        keyboard = None if finished else _battle_keyboard(battle)

        await query.edit_message_text(card_text, parse_mode="HTML", reply_markup=keyboard)
    finally:
        session.close()


def register(application) -> None:
    application.add_handler(CommandHandler("battle", battle_cmd, filters.ChatType.GROUPS))
    application.add_handler(CallbackQueryHandler(battle_accept_callback, pattern=r"^battle_accept:"))
    application.add_handler(CallbackQueryHandler(battle_decline_callback, pattern=r"^battle_decline:"))
    application.add_handler(CallbackQueryHandler(battle_action_callback, pattern=r"^battle_action:"))

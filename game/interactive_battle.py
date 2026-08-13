import random

from bio_lab.models import Creature, InteractiveBattle
from game import constants
from game.creature import GameError, effective_stats
from game.emoji import get_emoji

OTHER_SIDE = {"a": "b", "b": "a"}


def _creature(battle: InteractiveBattle, side: str) -> Creature:
    return battle.creature_a if side == "a" else battle.creature_b


def pick_first_turn(creature_a: Creature, creature_b: Creature) -> str:
    stats_a = effective_stats(creature_a)
    stats_b = effective_stats(creature_b)
    a_roll = stats_a["spd"] + random.uniform(0, 3)
    b_roll = stats_b["spd"] + random.uniform(0, 3)
    return "a" if a_roll >= b_roll else "b"


def _apply_damage(battle: InteractiveBattle, target_side: str, dmg: int) -> int:
    shield_attr = f"shield_active_{target_side}"
    if getattr(battle, shield_attr):
        dmg = round(dmg * 0.5)
        setattr(battle, shield_attr, False)
    hp_attr = f"hp_{target_side}"
    new_hp = max(0, getattr(battle, hp_attr) - dmg)
    setattr(battle, hp_attr, new_hp)
    return dmg


def perform_action(battle: InteractiveBattle, actor_side: str, action: str) -> list[str]:
    """Mutates `battle` in place with the effect of `action`. Returns log lines."""
    defender_side = OTHER_SIDE[actor_side]
    actor = _creature(battle, actor_side)
    defender = _creature(battle, defender_side)
    actor_stats = effective_stats(actor)
    defender_stats = effective_stats(defender)

    if action == "attack":
        mult = constants.element_multiplier(actor.element, defender.element)
        base = max(1.0, actor_stats["atk"] - defender_stats["def"] * 0.5)
        is_crit = random.random() < constants.BATTLE_CRIT_CHANCE
        raw = round(base * mult * random.uniform(0.85, 1.15) * (constants.BATTLE_CRIT_MULTIPLIER if is_crit else 1.0))
        dealt = _apply_damage(battle, defender_side, raw)
        crit_txt = " 💥کریتیکال!" if is_crit else ""
        return [f"{get_emoji('attack_action')} {actor.name} حمله کرد و {dealt} دمیج به {defender.name} زد{crit_txt}"]

    if action == "skill":
        uses_attr = f"skill_uses_{actor_side}"
        if getattr(battle, uses_attr) <= 0:
            raise GameError("اسکیل این نبرد تموم شده!")
        setattr(battle, uses_attr, getattr(battle, uses_attr) - 1)
        skill = constants.ELEMENT_SKILLS[actor.element]

        if actor.element == "fire":
            mult = constants.element_multiplier(actor.element, defender.element) * skill["power_mult"]
            base = max(1.0, actor_stats["atk"] - defender_stats["def"] * 0.3)
            dealt = _apply_damage(battle, defender_side, round(base * mult))
            return [f"{skill['name']}! {actor.name} یه ضربه‌ی ویرانگر زد و {dealt} دمیج وارد کرد!"]

        if actor.element == "water":
            hp_attr = f"hp_{actor_side}"
            max_hp = actor_stats["hp"]
            old_hp = getattr(battle, hp_attr)
            healed = min(max_hp, old_hp + round(max_hp * skill["heal_pct"])) - old_hp
            setattr(battle, hp_attr, old_hp + healed)
            return [f"{skill['name']}! {actor.name} {healed} HP ترمیم کرد."]

        if actor.element == "earth":
            setattr(battle, f"shield_active_{actor_side}", True)
            return [f"{skill['name']}! {actor.name} یه سپر سنگی گرفت (نیمی از ضربه‌ی بعدی خنثی می‌شه)."]

        if actor.element == "electric":
            setattr(battle, f"stunned_{defender_side}", True)
            return [f"{skill['name']}! {defender.name} برق‌گرفته شد و نوبت بعدیش رو از دست می‌ده!"]

    if action == "forfeit":
        setattr(battle, f"hp_{actor_side}", 0)
        return [f"{get_emoji('forfeit_action')} {actor.name} تسلیم شد."]

    raise GameError("این حرکت شناخته‌شده نیست.")


def advance_turn(battle: InteractiveBattle) -> list[str]:
    """Switches whose turn it is, resolving a stun skip if needed. Call only when the battle isn't over."""
    other = OTHER_SIDE[battle.turn]
    stunned_attr = f"stunned_{other}"
    if getattr(battle, stunned_attr):
        setattr(battle, stunned_attr, False)
        creature = _creature(battle, other)
        return [f"{get_emoji('element_electric')} {creature.name} هنوز برق‌گرفته‌ست و این نوبت رو از دست داد!"]
    battle.turn = other
    return []


def is_finished(battle: InteractiveBattle) -> tuple[bool, str | None]:
    if battle.hp_a <= 0:
        return True, "b"
    if battle.hp_b <= 0:
        return True, "a"
    return False, None


def render_hp_bar(current: int, total: int, width: int = 10) -> str:
    return constants.render_bar(current, total, width) + f" {max(current, 0)}/{total}"


def render_battle_card(battle: InteractiveBattle) -> str:
    stats_a = effective_stats(battle.creature_a)
    stats_b = effective_stats(battle.creature_b)
    lines = [
        f"{get_emoji('battle')} <b>نبرد زنده</b>",
        f"{battle.creature_a.name}  {render_hp_bar(battle.hp_a, stats_a['hp'])}",
        f"{battle.creature_b.name}  {render_hp_bar(battle.hp_b, stats_b['hp'])}",
        "",
    ]

    if battle.log:
        tail = battle.log.strip().split("\n")[-6:]
        lines.extend(tail)
        lines.append("")

    if battle.status == "active":
        actor = _creature(battle, battle.turn)
        skill_uses = battle.skill_uses_a if battle.turn == "a" else battle.skill_uses_b
        lines.append(f"⏳ نوبت: <b>{actor.name}</b>  (اسکیل باقی‌مانده: {skill_uses})")
    elif battle.status == "finished":
        winner_side = "a" if battle.hp_b <= 0 else "b"
        winner = _creature(battle, winner_side)
        lines.append(f"{get_emoji('trophy')} <b>برنده: {winner.name}!</b>")

    return "\n".join(lines)

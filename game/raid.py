import datetime
import random

from django.utils import timezone

from bio_lab.models import Creature, RaidBoss, RaidDamageLog, User
from game import constants
from game.creature import effective_stats
from game.equipment import get_equipped_items

BOSS_NAMES = ["Kaiju Prime", "Terravore", "Voltiathan", "Abyssal Warden"]
BOSS_MAX_HP = 800
BOSS_DEF = 15
ATTACK_COOLDOWN_SECONDS = 30
DNA_REWARD_POOL = 60
COIN_REWARD_POOL = 300


class RaidError(Exception):
    pass


def get_active_boss(group_id: int) -> RaidBoss | None:
    return RaidBoss.objects.filter(group_id=group_id, is_active=True).first()


def spawn_boss(group_id: int) -> RaidBoss:
    if get_active_boss(group_id) is not None:
        raise RaidError("یک هیولای وحشی همین الان توی گروهه! اول باهاش تسویه‌حساب کنید.")
    return RaidBoss.objects.create(
        group_id=group_id,
        name=random.choice(BOSS_NAMES),
        element=constants.random_element(),
        max_hp=BOSS_MAX_HP,
        current_hp=BOSS_MAX_HP,
    )


def attack_boss(user: User, creature: Creature, boss: RaidBoss) -> tuple[int, bool]:
    last_hit = (
        RaidDamageLog.objects.filter(raid_id=boss.id, user_id=user.id).order_by("-created_at").first()
    )
    if last_hit is not None:
        elapsed = timezone.now() - last_hit.created_at
        if elapsed < datetime.timedelta(seconds=ATTACK_COOLDOWN_SECONDS):
            remaining = ATTACK_COOLDOWN_SECONDS - int(elapsed.total_seconds())
            raise RaidError(f"هیولات نفس‌نفس می‌زنه، {remaining} ثانیه دیگه دوباره حمله کن.")

    stats = effective_stats(creature, get_equipped_items(creature))
    mult = constants.element_multiplier(creature.element, boss.element)
    base = max(1.0, stats["atk"] - BOSS_DEF * 0.5)
    dmg = round(base * mult * random.uniform(0.85, 1.15)) + stats["poison"]

    boss.current_hp = max(0, boss.current_hp - dmg)
    RaidDamageLog.objects.create(raid_id=boss.id, user_id=user.id, creature_id=creature.id, damage=dmg)

    defeated = boss.current_hp <= 0
    if defeated:
        boss.is_active = False
    boss.save()
    return dmg, defeated


def distribute_rewards(boss: RaidBoss) -> dict[int, dict[str, int]]:
    logs = RaidDamageLog.objects.filter(raid_id=boss.id)
    totals: dict[int, int] = {}
    for entry in logs:
        totals[entry.user_id] = totals.get(entry.user_id, 0) + entry.damage
    total_damage = sum(totals.values()) or 1

    rewards: dict[int, dict[str, int]] = {}
    for user_id, dmg in totals.items():
        share = dmg / total_damage
        dna = round(DNA_REWARD_POOL * share)
        coins = round(COIN_REWARD_POOL * share)
        user = User.objects.filter(id=user_id).first()
        if user is not None:
            user.dna_fragments += dna
            user.coins += coins
            user.save(update_fields=["dna_fragments", "coins"])
        rewards[user_id] = {"dna": dna, "coins": coins, "damage": dmg}

    return rewards

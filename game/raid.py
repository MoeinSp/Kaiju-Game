import datetime
import random

from django.db.models import F
from django.utils import timezone

from bio_lab.models import Creature, Group, RaidBoss, RaidDamageLog, User
from game import constants
from game.creature import effective_stats
from game.equipment import get_equipped_items

BOSS_NAMES = ["Kaiju Prime", "Terravore", "Voltiathan", "Abyssal Warden", "Chronoclast", "Magmaw", "Frostfang"]
BOSS_BASE_HP = 700
BOSS_HP_PER_LEVEL = 0.22       # +22% base HP per raid level (linear, always beatable)
BOSS_HP_RANDOM = (0.8, 1.35)   # each spawn rolls a random size in this band
BOSS_DEF_BASE = 12
BOSS_DEF_PER_LEVEL = 1         # small def growth so it never becomes unkillable
DNA_REWARD_POOL_BASE = 60
COIN_REWARD_POOL_BASE = 300
REWARD_PER_LEVEL = 0.18        # +18% of the reward pool per level
ATTACK_COOLDOWN_SECONDS = 8    # brief anti-double-tap; the real limit is the daily cap
DAILY_ATTACK_CAP = constants.RAID_DAILY_ATTACKS


class RaidError(Exception):
    pass


def get_active_boss(group_id: int) -> RaidBoss | None:
    return RaidBoss.objects.filter(group_id=group_id, is_active=True).first()


def _boss_def(level: int) -> int:
    return BOSS_DEF_BASE + max(0, level - 1) * BOSS_DEF_PER_LEVEL


def spawn_boss(group: Group) -> RaidBoss:
    """Spawn a boss scaled to the group's raid level, with a random size. The boss
    never expires — it stays until the group fells it, and doing so raises the
    group's raid level so the next one is tougher and pays more."""
    if get_active_boss(group.id) is not None:
        raise RaidError("یک باس همین الان توی گروهه! اول باهاش تسویه‌حساب کنید.")
    level = max(1, group.raid_level)
    hp = round(BOSS_BASE_HP * (1 + (level - 1) * BOSS_HP_PER_LEVEL) * random.uniform(*BOSS_HP_RANDOM))
    return RaidBoss.objects.create(
        group_id=group.id,
        name=random.choice(BOSS_NAMES),
        element=constants.random_element(),
        level=level,
        max_hp=hp,
        current_hp=hp,
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
    base = max(1.0, stats["atk"] - _boss_def(boss.level) * 0.5)
    # a wider random swing than before makes each hit feel less deterministic
    dmg = round(base * mult * random.uniform(0.75, 1.3)) + stats["poison"]

    boss.current_hp = max(0, boss.current_hp - dmg)
    RaidDamageLog.objects.create(raid_id=boss.id, user_id=user.id, creature_id=creature.id, damage=dmg)

    defeated = boss.current_hp <= 0
    if defeated:
        boss.is_active = False
        # felling a boss levels the whole group's raid up
        Group.objects.filter(id=boss.group_id).update(raid_level=F("raid_level") + 1)
    boss.save()
    return dmg, defeated


def distribute_rewards(boss: RaidBoss) -> dict[int, dict[str, int]]:
    level_mult = 1 + max(0, boss.level - 1) * REWARD_PER_LEVEL
    dna_pool = round(DNA_REWARD_POOL_BASE * level_mult)
    coin_pool = round(COIN_REWARD_POOL_BASE * level_mult)

    logs = RaidDamageLog.objects.filter(raid_id=boss.id)
    totals: dict[int, int] = {}
    for entry in logs:
        totals[entry.user_id] = totals.get(entry.user_id, 0) + entry.damage
    total_damage = sum(totals.values()) or 1

    rewards: dict[int, dict[str, int]] = {}
    for user_id, dmg in totals.items():
        share = dmg / total_damage
        dna = round(dna_pool * share)
        coins = round(coin_pool * share)
        user = User.objects.filter(id=user_id).first()
        if user is not None:
            user.dna_fragments += dna
            user.coins += coins
            user.save(update_fields=["dna_fragments", "coins"])
        rewards[user_id] = {"dna": dna, "coins": coins, "damage": dmg}

    return rewards

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
COOLDOWN_STEP_SECONDS = 60     # each raid hit today makes the next one wait 1 min longer


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


def _fmt_wait(seconds: int) -> str:
    m, s = divmod(max(0, seconds), 60)
    if m and s:
        return f"{m} دقیقه و {s} ثانیه"
    if m:
        return f"{m} دقیقه"
    return f"{s} ثانیه"


def attack_boss(user: User, creature: Creature, boss: RaidBoss) -> tuple[int, bool]:
    # No daily cap: instead the cooldown escalates by 1 minute for every raid hit
    # already landed today, so early hits are fast and heavy grinding slows itself.
    from game.daily import get_daily_count

    hits_today = get_daily_count(user, "raid_attack")
    required = hits_today * COOLDOWN_STEP_SECONDS
    if required > 0:
        last_hit = (
            RaidDamageLog.objects.filter(user_id=user.id).order_by("-created_at").first()
        )
        if last_hit is not None:
            elapsed = int((timezone.now() - last_hit.created_at).total_seconds())
            if elapsed < required:
                nxt = (hits_today + 1) * COOLDOWN_STEP_SECONDS // 60
                raise RaidError(
                    f"هیولات خسته‌ست — {_fmt_wait(required - elapsed)} دیگه دوباره حمله کن. "
                    f"(هر اتک رید امروز، کول‌داون بعدی رو ۱ دقیقه بیشتر می‌کنه؛ اتک بعدی: {nxt} دقیقه)"
                )

    stats = effective_stats(creature, get_equipped_items(creature))
    mult = constants.element_multiplier(creature.element, boss.element)
    base = max(1.0, stats["atk"] - _boss_def(boss.level) * 0.5)
    # a wider random swing than before makes each hit feel less deterministic
    dmg = round(base * mult * random.uniform(0.75, 1.3)) + stats["poison"]

    boss.current_hp = max(0, boss.current_hp - dmg)
    # every hit drips a little DNA on top of the kill-split reward
    user.dna_fragments += constants.RAID_HIT_DNA
    user.save(update_fields=["dna_fragments"])
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

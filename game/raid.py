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
# The kill reward is the group's payoff for hours of coordinated raiding — bumped ×10
# so felling a boss feels like a real jackpot shared across everyone who fought it.
DNA_REWARD_POOL_BASE = 600
COIN_REWARD_POOL_BASE = 3000
REWARD_PER_LEVEL = 0.18        # +18% of the reward pool per level
RAID_COOLDOWN_SECONDS = 300    # flat 5-minute cooldown between raid hits (never escalates)
RAID_DAILY_ATTACKS = 10        # per-player daily raid-attack cap

# Weekly raid ranking rewards — top players by total raid damage in the week, paid at
# the weekly reset (game.season.close_due_season → settle_weekly_raid).
RAID_WEEKLY_REWARD_BY_RANK = {
    1: {"diamonds": 60, "coins": 5000, "dna": 300},
    2: {"diamonds": 45, "coins": 3500, "dna": 220},
    3: {"diamonds": 35, "coins": 2500, "dna": 160},
    4: {"diamonds": 22, "coins": 1500, "dna": 100},
    5: {"diamonds": 22, "coins": 1500, "dna": 100},
    6: {"diamonds": 15, "coins": 1000, "dna": 70},
    7: {"diamonds": 10, "coins": 700, "dna": 50},
    8: {"diamonds": 10, "coins": 700, "dna": 50},
    9: {"diamonds": 8, "coins": 500, "dna": 40},
    10: {"diamonds": 8, "coins": 500, "dna": 40},
}


class RaidError(Exception):
    pass


def get_active_boss(alliance_id: int) -> RaidBoss | None:
    """The alliance's single active raid boss (raids are alliance-based)."""
    if not alliance_id:
        return None
    return RaidBoss.objects.filter(alliance_id=alliance_id, is_active=True).first()


def _boss_def(level: int) -> int:
    return BOSS_DEF_BASE + max(0, level - 1) * BOSS_DEF_PER_LEVEL


def spawn_boss(alliance, group=None) -> RaidBoss:
    """Spawn a boss for an ALLIANCE, scaled to the alliance's raid level. Only that
    alliance's members can fight it; felling it raises the alliance's raid level so the
    next one is tougher and pays more. `group` is just the chat the احضار was posted in."""
    if get_active_boss(alliance.id) is not None:
        raise RaidError("یک باس همین الان برای اتحادتون فعاله! اول باهاش تسویه‌حساب کنید.")
    level = max(1, alliance.raid_level)
    hp = round(BOSS_BASE_HP * (1 + (level - 1) * BOSS_HP_PER_LEVEL) * random.uniform(*BOSS_HP_RANDOM))
    return RaidBoss.objects.create(
        alliance_id=alliance.id,
        group_id=group.id if group is not None else None,
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


def _joined_alliance_today(user: User) -> bool:
    """True if the player joined their current alliance today (before the next midnight
    boundary). Used to gate raid attacks and war rallies for brand-new members."""
    from game.daily import today_str

    if not user.alliance_id or user.alliance_joined_at is None:
        return False
    return timezone.localtime(user.alliance_joined_at).date().isoformat() == today_str()


def attack_boss(user: User, creature: Creature, boss: RaidBoss) -> tuple[int, bool, int, int]:
    """Land one hit on the raid boss. Flat 5-minute cooldown between hits and a daily
    cap of RAID_DAILY_ATTACKS. Returns (dmg, defeated, dna_gain, attacks_left_today)."""
    from game.daily import get_daily_count

    # a brand-new alliance member can't raid until the next midnight. The message
    # explains the surface reason (recently joined) + when it unlocks — not the
    # anti-fake motive behind it.
    if _joined_alliance_today(user):
        raise RaidError(
            "⏳ <b>چون به‌تازگی به این اتحاد پیوستی</b>، اتک رید هنوز برات فعال نیست.\n"
            "از نیمه‌شب امشب (ساعت ۰۰:۰۰) می‌تونی به باس رید اتحادت حمله کنی."
        )

    hits_today = get_daily_count(user, "raid_attack")
    if hits_today >= RAID_DAILY_ATTACKS:
        raise RaidError(
            "🚫 <b>سقف اتک روزانه‌ی رید پر شده!</b>\n\n"
            f"امروز هر <b>{RAID_DAILY_ATTACKS}</b> اتک رید‌تو زدی — فردا دوباره پر می‌شه."
        )
    # flat 5-minute cooldown since the last hit — never escalates
    last_hit = RaidDamageLog.objects.filter(user_id=user.id).order_by("-created_at").first()
    if last_hit is not None:
        elapsed = int((timezone.now() - last_hit.created_at).total_seconds())
        if elapsed < RAID_COOLDOWN_SECONDS:
            raise RaidError(
                "😮‍💨 <b>هیولات خسته‌ست!</b>\n\n"
                f"⏳ زمان تا اتک بعدی: <b>{_fmt_wait(RAID_COOLDOWN_SECONDS - elapsed)}</b>\n\n"
                f"🔁 اتک‌های امروز: <b>{hits_today}/{RAID_DAILY_ATTACKS}</b>"
            )

    stats = effective_stats(creature, get_equipped_items(creature))
    mult = constants.element_multiplier(creature.element, boss.element)
    base = max(1.0, stats["atk"] - _boss_def(boss.level) * 0.5)
    # a wider random swing than before makes each hit feel less deterministic
    dmg = round(base * mult * random.uniform(0.75, 1.3)) + stats["poison"]

    boss.current_hp = max(0, boss.current_hp - dmg)
    # each hit drips DNA scaled by how HARD the strike landed (1 … 50)
    dna_gain = constants.raid_hit_dna(dmg)
    user.dna_fragments += dna_gain
    user.save(update_fields=["dna_fragments"])
    RaidDamageLog.objects.create(raid_id=boss.id, user_id=user.id, creature_id=creature.id, damage=dmg)

    defeated = boss.current_hp <= 0
    if defeated:
        boss.is_active = False
        # felling a boss levels the alliance's raid up
        from bio_lab.models import Alliance

        if boss.alliance_id:
            Alliance.objects.filter(id=boss.alliance_id).update(raid_level=F("raid_level") + 1)
    boss.save()
    # attacks left AFTER this one is recorded (the caller records it right after)
    attacks_left = max(0, RAID_DAILY_ATTACKS - hits_today - 1)
    return dmg, defeated, dna_gain, attacks_left


def damage_leaderboard(alliance_id: int) -> dict | None:
    """Read-only standings for the ALLIANCE's active boss: each attacker's total damage
    and the reward they'd get if the boss fell right now (same split as
    distribute_rewards, but nothing is granted). None when there's no active boss."""
    from bio_lab.repository import display_name

    boss = get_active_boss(alliance_id)
    if boss is None:
        return None
    level_mult = 1 + max(0, boss.level - 1) * REWARD_PER_LEVEL
    dna_pool = round(DNA_REWARD_POOL_BASE * level_mult)
    coin_pool = round(COIN_REWARD_POOL_BASE * level_mult)

    totals: dict[int, int] = {}
    for entry in RaidDamageLog.objects.filter(raid_id=boss.id):
        totals[entry.user_id] = totals.get(entry.user_id, 0) + entry.damage
    total_damage = sum(totals.values()) or 1

    rows = []
    for uid, dmg in sorted(totals.items(), key=lambda kv: kv[1], reverse=True):
        share = dmg / total_damage
        user = User.objects.filter(id=uid).first()
        rows.append({
            "name": display_name(user) if user else str(uid),
            "damage": dmg,
            "share_pct": round(share * 100),
            "dna": round(dna_pool * share),
            "coins": round(coin_pool * share),
        })
    return {
        "boss_name": boss.name, "boss_level": boss.level,
        "hp": max(boss.current_hp, 0), "max_hp": boss.max_hp,
        "total_damage": sum(totals.values()), "rows": rows,
    }


RAID_WEEKLY_TOP_ALLIANCES = 3   # top-N alliances (by raid level) that get weekly rewards
RAID_WEEKLY_MEMBERS_REWARDED = 10  # per rewarded alliance, split equally among its top raiders


def alliance_raid_members(alliance_id: int, limit: int = 10) -> list[dict]:
    """Top raiders WITHIN one alliance by total raid damage this week (logs wiped at the
    weekly reset). Used by the «جدول رید» inside the alliance section."""
    from django.db.models import Sum

    from bio_lab.repository import display_name

    agg = (
        RaidDamageLog.objects.filter(user__alliance_id=alliance_id)
        .values("user_id")
        .annotate(total=Sum("damage"))
        .order_by("-total")[:limit]
    )
    rows = []
    for i, entry in enumerate(agg, start=1):
        u = User.objects.filter(id=entry["user_id"]).first()
        rows.append({
            "rank": i, "user_id": entry["user_id"],
            "name": display_name(u) if u else str(entry["user_id"]),
            "damage": entry["total"] or 0,
        })
    return rows


def alliance_raid_ranking(limit: int = 10) -> list[dict]:
    """Alliances ranked by their RAID LEVEL (the social «رتبه‌بندی رید» board). At week's
    end the top-3 alliances' best raiders share the weekly reward."""
    from bio_lab.models import Alliance

    ranked = list(Alliance.objects.order_by("-raid_level", "id")[:limit])
    return [
        {"rank": i, "alliance": a, "raid_level": a.raid_level, "member_count": a.members.count()}
        for i, a in enumerate(ranked, start=1)
    ]


def reset_all_raids() -> None:
    """Wipe the raid slate: despawn every active boss, reset every alliance's (and
    group's) raid level to 1, and clear all damage logs. Used by the weekly reset and
    the one-time manual reset."""
    from bio_lab.models import Alliance

    RaidBoss.objects.filter(is_active=True).update(is_active=False)
    Alliance.objects.update(raid_level=1)
    Group.objects.update(raid_level=1)
    RaidDamageLog.objects.all().delete()


def settle_weekly_raid() -> list[tuple[int, str]]:
    """Weekly raid payout: the top-3 alliances (by raid level) each have their 10 best
    raiders split that rank's reward EQUALLY. Then reset every raid. Returns DMs."""
    from game.battlepass import _grant

    out: list[tuple[int, str]] = []
    for entry in alliance_raid_ranking(limit=RAID_WEEKLY_TOP_ALLIANCES):
        reward = RAID_WEEKLY_REWARD_BY_RANK.get(entry["rank"])
        if not reward:
            continue
        members = alliance_raid_members(entry["alliance"].id, limit=RAID_WEEKLY_MEMBERS_REWARDED)
        if not members:
            continue
        n = len(members)
        share = {
            "coins": reward["coins"] // n,
            "diamonds": reward["diamonds"] // n,
            "dna": reward["dna"] // n,
        }
        if not any(share.values()):
            continue
        for m in members:
            u = User.objects.filter(id=m["user_id"]).first()
            if u is None:
                continue
            _grant(u, share)
            if u.notifications_on:
                out.append((
                    u.id,
                    f"🐲 <b>جایزه‌ی هفتگی رید اتحاد!</b>\nاتحاد <b>{entry['alliance'].name}</b> این هفته "
                    f"رتبه‌ی <b>{entry['rank']}</b> رید شد.\n"
                    f"🎁 سهم تو: {share['diamonds']}💎 + {share['coins']:,}🪙 + {share['dna']} DNA",
                ))
    reset_all_raids()
    return out


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
            if coins or dna:
                from game.ledger import record_gain

                record_gain(user, "raid", coins=coins, dna=dna)
        rewards[user_id] = {"dna": dna, "coins": coins, "damage": dmg}

    return rewards

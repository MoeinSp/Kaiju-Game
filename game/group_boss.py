"""«👹 باس گروه» — a boss that appears inside one group, for everyone in it.

Different from the world boss (one boss for the whole server) and the raid (one alliance
only): any player in the group hits it with the button under its message, so it works with
group privacy mode on and pulls the group together — the more of you play here, the more
gets dealt and paid.

* When: every BOSS_GAP_HOURS (random inside the range) in a group with at least
  MIN_ACTIVE players seen in the last ACTIVE_DAYS days, only between SPAWN_HOURS (Tehran).
  The bot job (bot/handlers/group_boss.py) calls due_spawns() and posts each boss.
* HP: Σ active-creature power of those players × HITS × HP_SHARE — half of what they could
  all deal, so a group that shows up kills it and a group that doesn't lets it escape.
* A hit is free (HITS per player per boss) and deals the creature's power (with gear),
  ×1.20 with the element advantage — the same numbers every card shows. Overkill doesn't count.
* Settled once, when it dies or its time is up (settle_due): everyone who hit gets gold + DNA
  by their share of the damage; on a kill also diamonds for the top three and the last hit.
  The group earns league points (game/group_war.py) either way.
"""

from __future__ import annotations

import datetime
import random

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from bio_lab.models import Creature, Group, GroupBoss, GroupBossHit, GroupMembership, User
from bio_lab.repository import get_active_creature, lock_row
from game import constants
from game.creature import GameError, creature_power

HITS = 3
LIFETIME_MIN = 40
BOSS_GAP_HOURS = (6, 10)
FIRST_GAP_HOURS = (0.3, 3)
SPAWN_HOURS = (10, 24)       # local hours a boss may appear
MIN_ACTIVE = 3
ACTIVE_DAYS = 2
HP_SHARE = 0.5
MIN_HP = 20_000
MAX_SPAWNS_PER_TICK = 15

GOLD_POOL_PER_HP = 2.0       # gold shared out per HP of the boss (×ESCAPE_SHARE when it escaped)
DNA_POOL_PER_HP = 0.10
MIN_GOLD = 3_000
ESCAPE_SHARE = 0.3
TOP_DIAMONDS = (30, 20, 10)
LAST_HIT_DIAMONDS = 10
KILL_POINTS = 100            # league points for the group on a kill (+ damage / 1000 either way)

NAMES = ["گورگان سنگی", "اژدهای مه", "کراکن تاریکی", "تیتان یخ", "ققنوس خاکستر", "مار طوفان", "گولم آهن"]


def _active_since() -> str:
    return (timezone.localdate() - datetime.timedelta(days=ACTIVE_DAYS)).isoformat()


def active_member_ids(group_id: int) -> list[int]:
    return list(GroupMembership.objects.filter(
        group_id=group_id, left_at__isnull=True, user__is_banned=False,
        user__last_active_day__gte=_active_since()).values_list("user_id", flat=True))


def round_hp(hp: float) -> int:
    step = 5_000 if hp >= 200_000 else 1_000
    return max(MIN_HP, int(round(hp / step)) * step)


def hp_for(group_id: int) -> int:
    from game.equipment import equipped_items_map

    ids = active_member_ids(group_id)
    creatures = list(Creature.objects.filter(owner_id__in=ids, is_active=True))
    gear = equipped_items_map(creatures)
    potential = sum(creature_power(c, gear.get(c.id, [])) for c in creatures) * HITS
    return round_hp(potential * HP_SHARE)


def current(group_id: int) -> GroupBoss | None:
    return (GroupBoss.objects.filter(group_id=group_id, status=GroupBoss.ACTIVE, ends_at__gt=timezone.now())
            .order_by("-id").first())


# ── spawning ──────────────────────────────────────────────────────────────────
def _next_gap(first: bool = False) -> datetime.timedelta:
    lo, hi = FIRST_GAP_HOURS if first else BOSS_GAP_HOURS
    return datetime.timedelta(hours=random.uniform(lo, hi))


def due_spawns(now: datetime.datetime | None = None) -> list[GroupBoss]:
    """Spawn a boss in every group whose turn has come. Returns the new bosses (the caller
    posts them and stores their message id)."""
    now = now or timezone.now()
    hour = timezone.localtime(now).hour
    if not (SPAWN_HOURS[0] <= hour < SPAWN_HOURS[1]):
        return []
    from django.db.models import Count, Q

    since = _active_since()
    candidates = (Group.objects.filter(Q(boss_next_at__isnull=True) | Q(boss_next_at__lte=now))
        .annotate(n=Count("groupmembership", filter=Q(
        groupmembership__left_at__isnull=True, groupmembership__user__last_active_day__gte=since)))
        .filter(n__gte=MIN_ACTIVE).order_by("-n"))
    spawned = []
    for g in candidates:
        if len(spawned) >= MAX_SPAWNS_PER_TICK:
            break
        if g.boss_next_at is None:
            Group.objects.filter(id=g.id, boss_next_at__isnull=True).update(boss_next_at=now + _next_gap(first=True))
            continue
        if g.boss_next_at > now:
            continue
        if GroupBoss.objects.filter(group=g, status=GroupBoss.ACTIVE).exists():
            continue
        with transaction.atomic():
            claimed = Group.objects.filter(id=g.id, boss_next_at=g.boss_next_at).update(boss_next_at=now + _next_gap())
            if not claimed:
                continue
            hp = hp_for(g.id)
            spawned.append(GroupBoss.objects.create(
                group=g, name=random.choice(NAMES), element=constants.random_element(),
                max_hp=hp, hp=hp, ends_at=now + datetime.timedelta(minutes=LIFETIME_MIN)))
    return spawned


def set_message(boss_id: int, message_id: int) -> None:
    GroupBoss.objects.filter(id=boss_id).update(message_id=message_id)


def drop(boss_id: int) -> None:
    """The post failed (the bot was removed, can't write…) — forget this boss."""
    GroupBoss.objects.filter(id=boss_id).update(status=GroupBoss.ESCAPED, settled=True)


# ── hitting ───────────────────────────────────────────────────────────────────
@transaction.atomic
def hit(user: User, boss_id: int) -> dict:
    from game import research
    from game.equipment import get_equipped_items

    lock_row(user)
    boss = GroupBoss.objects.select_for_update().filter(id=boss_id).first()
    if boss is None or boss.status != GroupBoss.ACTIVE or boss.ends_at <= timezone.now():
        raise GameError("این باس دیگه اینجا نیست.")
    creature = get_active_creature(user)
    if creature is None:
        raise GameError("اول توی پیوی ربات /start بزن و یه هیولا بگیر!")
    entry, _ = GroupBossHit.objects.select_for_update().get_or_create(boss=boss, user=user)
    if entry.hits >= HITS:
        raise GameError(f"هر {HITS} ضربه‌ات رو به این باس زدی. بقیه‌ی گروه رو صدا کن! 📣")
    GroupMembership.objects.get_or_create(group_id=boss.group_id, user=user)
    research.attach_research(user, creature)
    power = creature_power(creature, get_equipped_items(creature))
    advantage = constants.is_strong_against(creature.element, boss.element)
    raw = power * (constants.ELEMENT_ADVANTAGE_POWER_FACTOR if advantage else 1.0)
    damage = max(1, min(int(round(raw)), boss.hp))
    entry.hits += 1
    entry.damage += damage
    entry.save(update_fields=["hits", "damage"])
    boss.hp -= damage
    boss.last_hitter = user.id
    killed = boss.hp <= 0
    if killed:
        boss.hp = 0
        boss.status = GroupBoss.DEAD
    boss.save(update_fields=["hp", "status", "last_hitter"])
    return {"boss": boss, "damage": damage, "advantage": advantage, "killed": killed,
            "hits_left": HITS - entry.hits, "my_damage": entry.damage}


def leaderboard(boss_id: int, limit: int = 5) -> list[dict]:
    from bio_lab.repository import lab_display

    rows = (GroupBossHit.objects.filter(boss_id=boss_id, damage__gt=0).select_related("user")
            .order_by("-damage", "first_hit_at")[:limit])
    return [{"name": lab_display(r.user), "damage": r.damage, "user_id": r.user_id} for r in rows]


def fighters(boss_id: int) -> int:
    return GroupBossHit.objects.filter(boss_id=boss_id, hits__gt=0).count()


# ── settlement ────────────────────────────────────────────────────────────────
def settle(boss_id: int) -> dict | None:
    """Pay out a dead or timed-out boss, once. Returns {boss, killed, rewards, top} or None."""
    from game import group_war
    from game.referral import _give

    with transaction.atomic():
        boss = GroupBoss.objects.select_for_update().filter(id=boss_id).first()
        if boss is None or boss.settled:
            return None
        if boss.status == GroupBoss.ACTIVE:
            if boss.ends_at > timezone.now():
                return None
            boss.status = GroupBoss.ESCAPED
        boss.settled = True
        boss.save(update_fields=["status", "settled"])
        killed = boss.status == GroupBoss.DEAD
        hits = list(GroupBossHit.objects.filter(boss=boss, damage__gt=0).order_by("-damage", "first_hit_at"))
        total = sum(h.damage for h in hits) or 1
        share = 1.0 if killed else ESCAPE_SHARE
        gold_pool = boss.max_hp * GOLD_POOL_PER_HP * share
        dna_pool = boss.max_hp * DNA_POOL_PER_HP * share
        rewards: dict[int, dict] = {}
        for place, h in enumerate(hits):
            part = h.damage / total
            r = {"coins": max(MIN_GOLD, int(gold_pool * part)), "dna": int(dna_pool * part)}
            if killed and place < len(TOP_DIAMONDS):
                r["diamonds"] = TOP_DIAMONDS[place]
            if killed and h.user_id == boss.last_hitter:
                r["diamonds"] = r.get("diamonds", 0) + LAST_HIT_DIAMONDS
                r["last_hit"] = True
            u = User.objects.select_for_update().filter(id=h.user_id).first()
            if u is None or u.is_banned:
                continue
            _give(u, {k: v for k, v in r.items() if k in ("coins", "dna", "diamonds")})
            rewards[h.user_id] = r
        points = (KILL_POINTS if killed else 0) + total // 1000
        group_war.add_points(boss.group_id, points)
    return {"boss": boss, "killed": killed, "rewards": rewards, "points": points,
            "top": leaderboard(boss.id, 3), "fighters": len(hits), "dealt": min(total, boss.max_hp)}


def due_settlements() -> list[int]:
    now = timezone.now()
    return list(GroupBoss.objects.filter(settled=False).filter(
        models_q_dead_or_late(now)).values_list("id", flat=True)[:50])


def models_q_dead_or_late(now):
    from django.db.models import Q

    return Q(status=GroupBoss.DEAD) | Q(status=GroupBoss.ACTIVE, ends_at__lte=now)

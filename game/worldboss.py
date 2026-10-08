"""«👹 غول سرگردان» — a server-wide boss that shows up twice a day for 30 minutes.

Everyone fights the SAME boss (shared HP), each player has a few hits per boss, and if
the server brings it down together every participant is rewarded. It's the «come now»
moment of the day: short, collective, and usable by a solo player with no alliance.

How it runs (no dedicated scheduler — the 5-minute notification job calls tick()):

* WorldBossState.next_spawn_at holds the next appearance: a random minute inside the
  next window (SPAWN_WINDOWS, Asia/Tehran). When it's due, tick() spawns a boss and
  returns the announcement DMs for recently-active players.
* A hit costs 1 energy. Damage = the active creature's power (the same number every card
  shows), ×1.20 with the element advantage over the boss — the game's one element rule —
  with a small random swing. Each hit pays gold + DNA on the spot.
* When HP reaches 0 the boss is DEAD; when the window ends it ESCAPES. Either way tick()
  settles it ONCE and pays everyone who hit it by how much of its HP the SERVER dealt
  (MILESTONES: a chest at 25%, another at 50%, another at 75%; a kill adds diamonds for
  all). The top three damage dealers get diamonds either way — more on a kill.
* HP follows a simple ladder per time window (_next_hp): killed → the next boss of that
  window is HP_STEP stronger, escaped → HP_STEP weaker, always a round number. So it
  hovers around what the server can just about kill. The very first boss of a window (or
  one far below what the players online could deal) is seeded from server_potential.
"""

from __future__ import annotations

import datetime
import random

from django.db import transaction
from django.db.models import Count, F, Sum
from django.utils import timezone

from bio_lab.models import ActivityHour, Creature, User, WorldBoss, WorldBossHit, WorldBossState
from bio_lab.repository import get_active_creature, lab_display, lock_row
from game import constants
from game.creature import GameError, creature_power

DURATION_MINUTES = 30
HITS_PER_PLAYER = 3
ENERGY_COST = 1
# (start hour, end hour) in the game timezone — one boss per window, at a random minute
SPAWN_WINDOWS = ((12, 15), (19, 23))

DAMAGE_SWING = (0.90, 1.10)
# reward per hit, as a share of the damage dealt (damage ≈ the creature's power). One hit
# is worth about three hunts — the boss comes twice a day and people wait for it.
HIT_GOLD_PER_DAMAGE = 1.50
HIT_DNA_PER_DAMAGE = 0.045

# first boss ever / fallback when there is nothing to go on
DEFAULT_HP = 120_000
MIN_HP = 60_000
# HP ladder: each boss is this much stronger than the last one of its window if that one
# was killed, this much weaker if it escaped (owner's rule, 2026-10-08).
HP_STEP = 0.05
# Seed only: with no earlier boss in the window — or one killed while far below what the
# players online could deal — start from server_potential × turnout ÷ target share.
TARGET_DAMAGE_SHARE = 0.50
DEFAULT_TURNOUT = 0.65
SEED_CATCHUP = 0.40          # a killed boss under this share of the estimate jumps to the estimate
POTENTIAL_MIN_PLAYERS = 10   # fewer online players than this → not a usable sample

# end-of-fight rewards for EVERY participant, by the share of HP the server dealt — each
# milestone reached adds its chest (so 50% pays the silver AND the golden one)
MILESTONES = ((0.25, "silver"), (0.50, "golden"), (0.75, "magical"))
KILL_DIAMONDS_ALL = 10            # a kill: these on top, for everyone
TOP3_DIAMONDS = (50, 30, 20)      # top damage dealers when it is killed
TOP3_DIAMONDS_ESCAPED = (25, 15, 10)   # …and when it escapes
KILLER_DIAMONDS = 10              # whoever landed the last hit
FULL_HITS_SPEEDUP_MINUTES = 15    # a speed-up card for spending all HITS_PER_PLAYER hits

# only players seen this recently are told a boss appeared (no DM storm to dead accounts)
ANNOUNCE_ACTIVE_DAYS = 3

BOSS_NAMES = (
    "گورزیلای خاکستر", "اژدهای مه‌گرفته", "غول صخره‌شکن", "کراکن اعماق", "ققنوس سیاه",
    "دیو طوفان", "هیولای بلورین", "غول آذرخش", "مار کوه‌خوار", "شبح باتلاق",
)


def _state() -> WorldBossState:
    state, _ = WorldBossState.objects.get_or_create(id=1)
    return state


def _next_spawn_after(now: datetime.datetime, new_window: bool = False) -> datetime.datetime:
    """A random moment inside the next spawn window after `now` (local time). With
    `new_window` the window `now` falls in is skipped — used right after a spawn, so each
    window gets ONE boss (without it a boss that expired at 12:58 scheduled another one for
    13:13 and a third for 14:28 in the same 12–15 window)."""
    local = timezone.localtime(now)
    for day_offset in range(0, 3):
        day = (local + datetime.timedelta(days=day_offset)).date()
        for start_h, end_h in SPAWN_WINDOWS:
            start = timezone.make_aware(datetime.datetime.combine(day, datetime.time(start_h)), local.tzinfo)
            end = timezone.make_aware(datetime.datetime.combine(day, datetime.time(end_h)), local.tzinfo)
            if new_window and start <= local:
                continue  # this window already had its boss
            if end - datetime.timedelta(minutes=DURATION_MINUTES) <= local:
                continue  # window (almost) over
            lo = max(start, local + datetime.timedelta(minutes=1))
            hi = end - datetime.timedelta(minutes=DURATION_MINUTES)
            span = max(0, int((hi - lo).total_seconds()))
            return lo + datetime.timedelta(seconds=random.randint(0, span))
    return local + datetime.timedelta(hours=6)


def current_boss() -> WorldBoss | None:
    """The boss that can be hit right now, if any."""
    return (
        WorldBoss.objects.filter(status=WorldBoss.ACTIVE, expires_at__gt=timezone.now())
        .order_by("-id").first()
    )


def last_boss() -> WorldBoss | None:
    return WorldBoss.objects.order_by("-id").first()


def next_spawn_at() -> datetime.datetime | None:
    return _state().next_spawn_at


def _window_of(moment: datetime.datetime) -> int:
    """Index of the spawn window a moment belongs to (the one whose middle is nearest)."""
    hour = timezone.localtime(moment).hour
    return min(range(len(SPAWN_WINDOWS)), key=lambda i: abs(hour - sum(SPAWN_WINDOWS[i]) / 2))


def potential_damage(boss: WorldBoss) -> int:
    """What the players who showed up for `boss` could have dealt with ALL their hits:
    (damage per hit) × fighters × HITS_PER_PLAYER. A boss that died early cut the fight
    short, so the damage actually dealt (= its HP) says nothing about what the server can
    do — that was the old sizing bug: 80% of «damage dealt» shrank the boss every time it
    was killed (120k → 96k → 77k → 61k → 60k) instead of growing it."""
    agg = WorldBossHit.objects.filter(boss=boss, hits__gt=0).aggregate(d=Sum("damage"), h=Sum("hits"), n=Count("id"))
    if not agg["h"]:
        return 0
    return int(agg["d"] / agg["h"] * agg["n"] * HITS_PER_PLAYER)


def _online_ids(moment: datetime.datetime) -> set[int]:
    """Players who used the bot's private chat in the hour of `moment` or the one before."""
    local = timezone.localtime(moment)
    prev = local - datetime.timedelta(hours=1)
    ids: set[int] = set()
    for t in (local, prev):
        ids.update(ActivityHour.objects.filter(day=t.strftime("%Y-%m-%d"), hour=t.hour, private=True)
                   .values_list("user_id", flat=True))
    return ids


def server_potential(moment: datetime.datetime | None = None) -> int:
    """The damage the players online around `moment` would deal if EVERY one of them spent
    all their hits: Σ active-creature power × HITS_PER_PLAYER. Unlike the damage dealt to a
    past boss this isn't cut short by the boss dying — the old sizing only ever saw the
    10–17 strongest players who got their hits in first, while 55–95 were online.
    0 when the sample is too small to mean anything."""
    from game.equipment import equipped_items_map

    moment = moment or timezone.now()
    ids = _online_ids(moment)
    if len(ids) < POTENTIAL_MIN_PLAYERS:   # e.g. the metrics rows of this hour aren't flushed yet
        ids = _online_ids(moment - datetime.timedelta(days=1))
    if len(ids) < POTENTIAL_MIN_PLAYERS:
        return 0
    creatures = list(Creature.objects.filter(owner_id__in=ids, is_active=True))
    gear = equipped_items_map(creatures)
    return int(sum(creature_power(c, gear.get(c.id, [])) for c in creatures) * HITS_PER_PLAYER)


def round_hp(hp: float) -> int:
    """A boss's HP is always a round number (nearest 5,000; 1,000 for small ones)."""
    step = 5_000 if hp >= 200_000 else 1_000
    return max(MIN_HP, int(round(hp / step)) * step)


def _next_hp(now: datetime.datetime | None = None) -> int:
    """The ladder: the last boss of the SAME time window (the evening crowd is much bigger
    than the noon one) × (1 + HP_STEP) if it was killed, × (1 − HP_STEP) if it escaped,
    rounded. Seeded from server_potential when the window has no boss yet, or when the
    killed one was far too small for the players who are online now."""
    now = now or timezone.now()
    window = _window_of(now)
    last = next((b for b in WorldBoss.objects.exclude(status=WorldBoss.ACTIVE).order_by("-id")[:12]
                 if _window_of(b.spawned_at) == window), None)
    potential = server_potential(now)
    estimate = potential * DEFAULT_TURNOUT / TARGET_DAMAGE_SHARE if potential > 0 else 0
    if last is None:
        return round_hp(estimate or DEFAULT_HP)
    if last.status == WorldBoss.DEAD:
        hp = last.max_hp * (1 + HP_STEP)
        if last.max_hp < estimate * SEED_CATCHUP:
            hp = estimate
    else:
        hp = last.max_hp * (1 - HP_STEP)
    return round_hp(hp)


def _spawn(now: datetime.datetime) -> WorldBoss:
    hp = _next_hp(now)
    return WorldBoss.objects.create(
        name=random.choice(BOSS_NAMES),
        element=constants.random_element(),
        max_hp=hp,
        current_hp=hp,
        expires_at=now + datetime.timedelta(minutes=DURATION_MINUTES),
    )


def my_entry(boss: WorldBoss, user: User) -> WorldBossHit | None:
    return WorldBossHit.objects.filter(boss=boss, user=user).first()


def leaderboard(boss: WorldBoss, limit: int = 5) -> list[dict]:
    rows = WorldBossHit.objects.filter(boss=boss, damage__gt=0).select_related("user").order_by("-damage", "id")[:limit]
    return [{"rank": i, "name": lab_display(r.user), "damage": r.damage, "user_id": r.user_id}
            for i, r in enumerate(rows, start=1)]


def hit_reward(damage: int) -> tuple[int, int]:
    return max(1, round(damage * HIT_GOLD_PER_DAMAGE)), max(1, round(damage * HIT_DNA_PER_DAMAGE))


@transaction.atomic
def hit(user: User) -> dict:
    """Land one hit on the current boss. Returns what happened (damage, reward, state)."""
    from game import research
    from game.energy import spend_energy
    from game.equipment import get_equipped_items
    from game.ledger import record_gain

    lock_row(user)
    boss = (
        WorldBoss.objects.select_for_update()
        .filter(status=WorldBoss.ACTIVE, expires_at__gt=timezone.now()).order_by("-id").first()
    )
    if boss is None:
        raise GameError("الان غولی توی میدون نیست. منتظر غول بعدی باش.")
    creature = get_active_creature(user)
    if creature is None:
        raise GameError("اول یه هیولای فعال انتخاب کن.")
    entry, _ = WorldBossHit.objects.select_for_update().get_or_create(boss=boss, user=user)
    if entry.hits >= HITS_PER_PLAYER:
        raise GameError(f"هر {HITS_PER_PLAYER} ضربه‌ات رو به این غول زدی.")

    spend_energy(user, ENERGY_COST, "ضربه به غول")
    research.attach_research(user, creature)
    power = creature_power(creature, get_equipped_items(creature))
    advantage = constants.is_strong_against(creature.element, boss.element)
    raw = power * (constants.ELEMENT_ADVANTAGE_POWER_FACTOR if advantage else 1.0) * random.uniform(*DAMAGE_SWING)
    damage = max(1, min(int(round(raw)), boss.current_hp))  # overkill doesn't count

    coins, dna = hit_reward(damage)
    user.coins += coins
    user.dna_fragments += dna
    user.save(update_fields=["energy", "energy_updated_at", "coins", "dna_fragments"])
    record_gain(user, "worldboss", coins=coins, dna=dna)

    entry.hits += 1
    entry.damage += damage
    entry.save(update_fields=["hits", "damage"])
    boss.current_hp -= damage
    killed = boss.current_hp <= 0
    if killed:
        boss.current_hp = 0
        boss.status = WorldBoss.DEAD
        boss.killer = user
    boss.save(update_fields=["current_hp", "status", "killer"])
    return {
        "boss": boss, "damage": damage, "coins": coins, "dna": dna, "advantage": advantage,
        "killed": killed, "hits_left": HITS_PER_PLAYER - entry.hits, "my_damage": entry.damage,
    }


def _chest_line(c: dict) -> str:
    parts = [f"{c['coins']:,} طلا", f"{c['dna']:,} DNA"]
    if c["diamonds"]:
        parts.append(f"{c['diamonds']} الماس")
    rarity = constants.RARITY_LABELS.get(c["rarity"], c["rarity"])
    if c["creature"] is not None:
        parts.append(f"هیولای {rarity} «{c['creature'].name}»")
    elif c["item"] is not None:
        parts.append(f"تجهیزات {rarity} «{c['item'].name}»")
    return f"{c['emoji']} {c['name']}: " + " + ".join(parts)


@transaction.atomic
def _settle(boss_id: int) -> list[tuple]:
    """Pay the end-of-fight rewards of one finished boss, exactly once. Returns DMs."""
    from game.arena_chests import grant_chest_contents

    boss = WorldBoss.objects.select_for_update().get(id=boss_id)
    if boss.settled:
        return []
    if boss.status == WorldBoss.ACTIVE:
        boss.status = WorldBoss.ESCAPED  # the window ran out
    boss.settled = True
    boss.save(update_fields=["status", "settled"])

    from game.buildings import grant_speedup_card

    entries = list(
        WorldBossHit.objects.filter(boss=boss, damage__gt=0).select_related("user").order_by("-damage", "id")
    )
    out: list[tuple] = []
    dead = boss.status == WorldBoss.DEAD
    share = 1.0 if dead else (boss.max_hp - max(0, boss.current_hp)) / max(1, boss.max_hp)
    chests = [tier for need, tier in MILESTONES if share >= need]
    top3 = TOP3_DIAMONDS if dead else TOP3_DIAMONDS_ESCAPED
    pct = round(100 * share)
    for rank, e in enumerate(entries, start=1):
        user = User.objects.select_for_update().get(id=e.user_id)
        got = [grant_chest_contents(user, tier, user.cup, source="worldboss") for tier in chests]
        diamonds = top3[rank - 1] if rank <= len(top3) else 0
        if dead:
            diamonds += KILL_DIAMONDS_ALL
            if boss.killer_id == user.id:
                diamonds += KILLER_DIAMONDS
        if diamonds:
            User.objects.filter(pk=user.pk).update(diamonds=F("diamonds") + diamonds)
        full_hits = e.hits >= HITS_PER_PLAYER
        if full_hits:
            grant_speedup_card(user, FULL_HITS_SPEEDUP_MINUTES, count=1)
        if not user.notifications_on:
            continue
        lines = [
            f"🏆 <b>{boss.name} از پا دراومد!</b>" if dead
            else f"💨 <b>{boss.name} فرار کرد!</b> سرور <code>{pct}%</code> جونش رو زد.",
            f"رتبه‌ی تو: <b>{rank}</b> از <code>{len(entries)}</code>",
            f"آسیب تو: <code>{e.damage:,}</code>",
        ]
        lines += [f"🎁 {_chest_line(c)}" for c in got]
        if diamonds:
            lines.append(f"💎 الماس: <code>{diamonds}</code>"
                         + (" (ضربه‌ی آخر مال تو بود!)" if dead and boss.killer_id == user.id else ""))
        if full_hits:
            lines.append(f"⏱ کارت سرعت {FULL_HITS_SPEEDUP_MINUTES} دقیقه‌ای (هر {HITS_PER_PLAYER} ضربه رو زدی)")
        if not dead:
            nxt = next((need for need, _tier in MILESTONES if share < need), None)
            lines.append(f"<i>برای جعبه‌ی بعدی باید <code>{round(nxt * 100)}%</code> جونش زده بشه.</i>" if nxt
                         else "<i>فقط یه قدم تا از پا درآوردنش مونده بود!</i>")
        out.append((user.id, "\n".join(lines), "worldboss"))
    return out


def tick() -> list[tuple]:
    """Called from the notification job. Settles finished bosses, spawns a due one and
    returns every DM to send: (user_id, text, "worldboss")."""
    now = timezone.now()
    out: list[tuple] = []

    finished = WorldBoss.objects.filter(settled=False).exclude(
        status=WorldBoss.ACTIVE, expires_at__gt=now
    ).values_list("id", flat=True)
    for boss_id in list(finished):
        out.extend(_settle(boss_id))

    with transaction.atomic():
        state = WorldBossState.objects.select_for_update().get(id=_state().id)
        if state.next_spawn_at is None:
            state.next_spawn_at = _next_spawn_after(now)
            state.save(update_fields=["next_spawn_at"])
            return out
        if state.next_spawn_at > now or current_boss() is not None:
            return out
        boss = _spawn(now)
        state.next_spawn_at = _next_spawn_after(now, new_window=True)
        state.save(update_fields=["next_spawn_at"])

    since = now - datetime.timedelta(days=ANNOUNCE_ACTIVE_DAYS)
    text = (
        f"👹 <b>غول سرگردان پیدا شد!</b>\n"
        f"<b>{boss.name}</b> — عنصر {constants.element_label(boss.element)}\n"
        f"❤️ جون: <code>{boss.max_hp:,}</code> · ⏳ فقط <b>{DURATION_MINUTES} دقیقه</b> می‌مونه\n"
        f"هر نفر {HITS_PER_PLAYER} ضربه داره؛ اگه با هم از پا درش بیارید همه جعبه می‌گیرن."
    )
    recipients = User.objects.filter(
        notifications_on=True, is_banned=False, energy_updated_at__gte=since
    ).values_list("id", flat=True)
    out.extend((uid, text, "worldboss") for uid in recipients)
    return out


def panel_state(user: User) -> dict:
    """Everything the «غول سرگردان» screen shows."""
    from game.energy import get_max_energy, sync_energy

    boss = current_boss()
    creature = get_active_creature(user)
    data = {
        "boss": boss, "next_spawn_at": next_spawn_at(), "energy": sync_energy(user),
        "max_energy": get_max_energy(user), "has_creature": creature is not None,
    }
    if boss is not None:
        entry = my_entry(boss, user)
        data.update({
            "hits_left": HITS_PER_PLAYER - (entry.hits if entry else 0),
            "my_damage": entry.damage if entry else 0,
            "top": leaderboard(boss),
            "fighters": WorldBossHit.objects.filter(boss=boss, damage__gt=0).count(),
            "seconds_left": max(0, int((boss.expires_at - timezone.now()).total_seconds())),
            "advantage": bool(creature and constants.is_strong_against(creature.element, boss.element)),
        })
    else:
        prev = last_boss()
        data["last"] = None if prev is None else {
            "name": prev.name, "status": prev.status,
            "fighters": WorldBossHit.objects.filter(boss=prev, damage__gt=0).count(),
        }
    return data

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
  settles it ONCE: on a kill every participant gets a chest (better for the top damage
  dealers) and the top three get diamonds.
* HP is self-balancing: the next boss is sized to ~80% of the total damage the server did
  to the previous one, so a kill needs about the same turnout — close fights, by design.
"""

from __future__ import annotations

import datetime
import random

from django.db import transaction
from django.db.models import Count, F, Sum
from django.utils import timezone

from bio_lab.models import User, WorldBoss, WorldBossHit, WorldBossState
from bio_lab.repository import get_active_creature, lab_display, lock_row
from game import constants
from game.creature import GameError, creature_power

DURATION_MINUTES = 30
HITS_PER_PLAYER = 3
ENERGY_COST = 1
# (start hour, end hour) in the game timezone — one boss per window, at a random minute
SPAWN_WINDOWS = ((12, 15), (19, 23))

DAMAGE_SWING = (0.90, 1.10)
# reward per hit, as a share of the damage dealt (damage ≈ the creature's power, so a
# hit pays a bit more than the «هم‌سطح» hunt the same energy would buy: ≈0.39 × power)
HIT_GOLD_PER_DAMAGE = 0.50
HIT_DNA_PER_DAMAGE = 0.015

# first boss ever / fallback when the previous one had no fighters
DEFAULT_HP = 120_000
MIN_HP = 60_000
# the server should deal about this share of a boss's HP with everyone's hits — killing it
# takes a better turnout than usual (more fighters, the right element), not a normal day
TARGET_DAMAGE_SHARE = 0.50
HP_SAMPLE = 3          # bosses of the same time window averaged for the estimate
KILLED_GROWTH = 1.5    # a boss that died anyway → the next one of that window is at least this × bigger

# kill rewards
KILL_CHEST_ALL = "golden"         # every participant
KILL_CHEST_TOP = "magical"        # the top TOP_SHARE of damage dealers (at least the top 3)
TOP_SHARE = 0.10
TOP3_DIAMONDS = (50, 30, 20)
KILLER_DIAMONDS = 10              # whoever landed the last hit

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


def _next_hp(now: datetime.datetime | None = None) -> int:
    """Size the next boss so the server deals about TARGET_DAMAGE_SHARE of its HP: the
    average potential_damage of the last few bosses of the SAME time window (the evening
    crowd is ~3× the noon one), divided by the target share. A boss that was killed anyway
    also sets a floor of KILLED_GROWTH × its HP, so a growing server can't outrun it."""
    now = now or timezone.now()
    done = list(WorldBoss.objects.exclude(status=WorldBoss.ACTIVE).order_by("-id")[:12])
    if not done:
        return DEFAULT_HP
    window = _window_of(now)
    same = [b for b in done if _window_of(b.spawned_at) == window][:HP_SAMPLE] or done[:HP_SAMPLE]
    potentials = [p for p in (potential_damage(b) for b in same) if p > 0]
    if not potentials:
        return max(MIN_HP, int(same[0].max_hp * 0.6))  # nobody came → an easier one next time
    hp = int(sum(potentials) / len(potentials) / TARGET_DAMAGE_SHARE)
    last = same[0]
    if last.status == WorldBoss.DEAD:
        hp = max(hp, int(last.max_hp * KILLED_GROWTH))
    return max(MIN_HP, hp)


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

    entries = list(
        WorldBossHit.objects.filter(boss=boss, damage__gt=0).select_related("user").order_by("-damage", "id")
    )
    out: list[tuple] = []
    if boss.status != WorldBoss.DEAD:
        left = round(100 * boss.current_hp / max(1, boss.max_hp))
        for e in entries:
            if e.user.notifications_on:
                out.append((e.user_id,
                            f"💨 <b>{boss.name} فرار کرد!</b>\nهنوز <code>{left}٪</code> جون داشت. "
                            "دفعه‌ی بعد بیشتر بیاید تا از پا دربیاد.", "worldboss"))
        return out

    top_n = max(3, int(len(entries) * TOP_SHARE + 0.999))
    for rank, e in enumerate(entries, start=1):
        user = User.objects.select_for_update().get(id=e.user_id)
        tier = KILL_CHEST_TOP if rank <= top_n else KILL_CHEST_ALL
        chest = grant_chest_contents(user, tier, user.cup, source="worldboss")
        diamonds = TOP3_DIAMONDS[rank - 1] if rank <= len(TOP3_DIAMONDS) else 0
        if boss.killer_id == user.id:
            diamonds += KILLER_DIAMONDS
        if diamonds:
            User.objects.filter(pk=user.pk).update(diamonds=F("diamonds") + diamonds)
        if user.notifications_on:
            lines = [
                f"🏆 <b>{boss.name} از پا دراومد!</b>",
                f"رتبه‌ی تو: <b>{rank}</b> از <code>{len(entries)}</code> · آسیب: <code>{e.damage:,}</code>",
                f"🎁 {_chest_line(chest)}",
            ]
            if diamonds:
                lines.append(f"💎 جایزه‌ی ویژه: <code>{diamonds}</code> الماس"
                             + (" (ضربه‌ی آخر مال تو بود!)" if boss.killer_id == user.id else ""))
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

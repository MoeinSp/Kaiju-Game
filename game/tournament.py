"""«🏟 جام آخر هفته» — a weekly knockout tournament.

Timeline (Asia/Tehran), one tournament per ISO week:

* Thursday 00:00 → Friday 19:30  registration (free; needs an active creature)
* Friday 19:30                   the draw: players are sorted by cup and split into
                                 groups of up to 8, so everyone meets players of their
                                 own level — a weak player can still win THEIR group
* Friday 20:00 / 21:00 / 22:00   quarter-final, semi-final, final (one round an hour)

A match is one duel under the game's deterministic rule (constants.duel_attacker_wins):
higher effective power wins, ×1.20 for the element advantage. The twist is that every
player may change which creature they field until the round starts, and can see the
creature their opponent currently has selected — so it's a guessing game about elements,
not just a power check.

Everything is driven by tick() from the 5-minute notification job, so a round is played
within a few minutes of its hour. No timers of its own.
"""

from __future__ import annotations

import datetime
import random

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from bio_lab.models import Creature, Tournament, TournamentEntry, TournamentMatch, User
from bio_lab.repository import creature_name, get_active_creature, lab_display
from game import constants
from game.creature import GameError, creature_power

GROUP_SIZE = 8
ROUNDS = 3
ROUND_NAMES = {1: "یک‌چهارم نهایی", 2: "نیمه‌نهایی", 3: "فینال"}

# weekday: Monday=0 … Thursday=3, Friday=4
REG_OPEN = (3, 0, 0)            # Thursday 00:00
DRAW_AT = (4, 19, 30)           # Friday 19:30
ROUND_AT = {1: (4, 20, 0), 2: (4, 21, 0), 3: (4, 22, 0)}

# prizes by final place in a group: (chest tier, diamonds)
PRIZES = {
    1: ("magical", 40),   # champion
    2: ("golden", 20),    # runner-up
    3: ("golden", 10),    # lost the semi-final
    5: ("silver", 0),     # went out earlier
}
PLACE_NAMES = {1: "🏆 قهرمان", 2: "🥈 نایب‌قهرمان", 3: "🥉 نیمه‌نهایی", 5: "شرکت‌کننده"}


def _week_key(now=None) -> str:
    from game.season import week_key

    return week_key(now)


def _moment(spec: tuple[int, int, int], now=None) -> datetime.datetime:
    """This ISO week's occurrence of (weekday, hour, minute), local time."""
    local = timezone.localtime(now) if now is not None else timezone.localtime()
    weekday, hour, minute = spec
    monday = (local - datetime.timedelta(days=local.weekday())).date()
    day = monday + datetime.timedelta(days=weekday)
    return timezone.make_aware(datetime.datetime.combine(day, datetime.time(hour, minute)), local.tzinfo)


def reg_open_at(now=None) -> datetime.datetime:
    return _moment(REG_OPEN, now)


def draw_at(now=None) -> datetime.datetime:
    return _moment(DRAW_AT, now)


def round_at(round_no: int, now=None) -> datetime.datetime:
    return _moment(ROUND_AT[round_no], now)


def next_reg_open() -> datetime.datetime:
    """When the next registration opens (this week's if still ahead, else next week's)."""
    now = timezone.localtime()
    opening = reg_open_at(now)
    return opening if opening > now else opening + datetime.timedelta(days=7)


def current() -> Tournament | None:
    return Tournament.objects.filter(week_key=_week_key()).first()


def registration_open() -> bool:
    now = timezone.localtime()
    return reg_open_at(now) <= now < draw_at(now)


def _power(entry: TournamentEntry) -> tuple[Creature | None, int, str]:
    """The creature an entry fields right now and its card power. Falls back to the
    player's active creature if the selected one is gone."""
    from game import research
    from game.equipment import get_equipped_items

    creature = entry.creature
    if creature is None or creature.owner_id != entry.user_id:
        creature = get_active_creature(entry.user)
    if creature is None:
        return None, 0, ""
    research.attach_research(entry.user, creature)
    return creature, creature_power(creature, get_equipped_items(creature)), creature.element


# ── registration ──────────────────────────────────────────────────────────────
@transaction.atomic
def register(user: User) -> TournamentEntry:
    if not registration_open():
        raise GameError("ثبت‌نام جام الان باز نیست (پنجشنبه تا جمعه ساعت ۱۹:۳۰).")
    creature = get_active_creature(user)
    if creature is None:
        raise GameError("اول یه هیولای فعال انتخاب کن.")
    tournament, _ = Tournament.objects.get_or_create(week_key=_week_key())
    if tournament.status != Tournament.REGISTRATION:
        raise GameError("ثبت‌نام این هفته بسته شده.")
    entry, created = TournamentEntry.objects.get_or_create(
        tournament=tournament, user=user, defaults={"creature": creature}
    )
    if not created:
        raise GameError("قبلاً توی جام این هفته ثبت‌نام کردی.")
    return entry


@transaction.atomic
def unregister(user: User) -> None:
    tournament = current()
    if tournament is None or tournament.status != Tournament.REGISTRATION:
        raise GameError("بعد از قرعه‌کشی دیگه نمی‌شه انصراف داد.")
    deleted, _ = TournamentEntry.objects.filter(tournament=tournament, user=user).delete()
    if not deleted:
        raise GameError("توی جام این هفته ثبت‌نام نکردی.")


def my_entry(user: User, tournament: Tournament | None = None) -> TournamentEntry | None:
    tournament = tournament or current()
    if tournament is None:
        return None
    return (
        TournamentEntry.objects.filter(tournament=tournament, user=user)
        .select_related("creature", "user").first()
    )


@transaction.atomic
def set_creature(user: User, creature_id: int) -> TournamentEntry:
    """Choose who to field in the next round (allowed until that round is played)."""
    entry = my_entry(user)
    if entry is None:
        raise GameError("توی جام این هفته نیستی.")
    if not entry.alive or entry.tournament.status == Tournament.FINISHED:
        raise GameError("دیگه توی جام نیستی؛ نمی‌شه هیولا عوض کرد.")
    creature = Creature.objects.filter(id=creature_id, owner=user).first()
    if creature is None:
        raise GameError("این هیولا توی کلکسیون تو نیست.")
    entry.creature = creature
    entry.save(update_fields=["creature"])
    return entry


def creature_choices(user: User, limit: int = 30) -> list[tuple[Creature, int]]:
    """The player's creatures, strongest first, for the «who do I field» picker."""
    from game import research
    from game.equipment import equipped_items_map

    creatures = list(Creature.objects.filter(owner=user))
    research.attach_research(user, creatures)
    gear = equipped_items_map(creatures)
    scored = [(c, creature_power(c, gear[c.pk])) for c in creatures]
    scored.sort(key=lambda t: (-t[1], t[0].id))
    return scored[:limit]


# ── the draw ──────────────────────────────────────────────────────────────────
def _next_match_for(entry: TournamentEntry) -> TournamentMatch | None:
    from django.db.models import Q

    return (
        TournamentMatch.objects.filter(tournament=entry.tournament, winner__isnull=True)
        .filter(Q(a=entry) | Q(b=entry))
        .select_related("a__user", "b__user", "a__creature", "b__creature").first()
    )


def _draw(tournament: Tournament) -> list[tuple]:
    entries = list(
        TournamentEntry.objects.filter(tournament=tournament).select_related("user").order_by("-user__cup", "id")
    )
    out: list[tuple] = []
    if len(entries) < 2:
        tournament.status = Tournament.FINISHED
        tournament.save(update_fields=["status"])
        for e in entries:
            out.append((e.user_id, "🏟 جام این هفته برگزار نشد؛ شرکت‌کننده‌ی کافی نبود. هفته‌ی بعد دوباره بیا!", "tournament"))
        return out

    # groups of up to GROUP_SIZE, balanced in size, of neighbouring cups
    groups = max(1, -(-len(entries) // GROUP_SIZE))
    base, extra = divmod(len(entries), groups)
    start = 0
    for g in range(1, groups + 1):
        size = base + (1 if g <= extra else 0)
        members = entries[start:start + size]
        start += size
        random.shuffle(members)  # who meets whom inside a group is the luck of the draw
        for e in members:
            e.group_no = g
            e.save(update_fields=["group_no"])
        seats = members + [None] * (GROUP_SIZE - len(members))
        # spread the byes so nobody gets two in a row for free
        pairs = [(seats[i], seats[GROUP_SIZE - 1 - i]) for i in range(GROUP_SIZE // 2)]
        for slot, (a, b) in enumerate(pairs):
            if a is None and b is None:
                continue
            first, second = (a, b) if a is not None else (b, None)
            TournamentMatch.objects.create(
                tournament=tournament, group_no=g, round=1, slot=slot, a=first, b=second
            )
    tournament.status = Tournament.RUNNING
    tournament.round = 1
    tournament.save(update_fields=["status", "round"])
    for e in entries:
        if e.user.notifications_on:
            out.append((e.user_id,
                        f"🏟 <b>قرعه‌کشی جام انجام شد!</b>\nتوی گروه <b>{e.group_no}</b> هستی. "
                        f"{ROUND_NAMES[1]} ساعت ۲۰ شروع می‌شه؛ حریفت رو ببین و هیولات رو انتخاب کن.",
                        "tournament"))
    return out


# ── playing a round ───────────────────────────────────────────────────────────
def _finish(entry: TournamentEntry, place: int) -> None:
    entry.alive = place == 1
    entry.place = place
    entry.save(update_fields=["alive", "place"])


def _pay_prizes(tournament: Tournament) -> list[tuple]:
    from game.arena_chests import grant_chest_contents

    out: list[tuple] = []
    for e in TournamentEntry.objects.filter(tournament=tournament).select_related("user"):
        place = e.place or 5
        tier, diamonds = PRIZES.get(place, PRIZES[5])
        user = User.objects.select_for_update().get(id=e.user_id)
        chest = grant_chest_contents(user, tier, user.cup, source="tournament")
        if diamonds:
            User.objects.filter(pk=user.pk).update(diamonds=F("diamonds") + diamonds)
        if user.notifications_on:
            got = [f"{chest['coins']:,} طلا", f"{chest['dna']:,} DNA"]
            total_diamonds = diamonds + chest["diamonds"]
            if total_diamonds:
                got.append(f"{total_diamonds} الماس")
            rarity = constants.RARITY_LABELS.get(chest["rarity"], chest["rarity"])
            if chest["creature"] is not None:
                got.append(f"هیولای {rarity} «{chest['creature'].name}»")
            elif chest["item"] is not None:
                got.append(f"تجهیزات {rarity} «{chest['item'].name}»")
            out.append((user.id,
                        f"🏟 <b>جام آخر هفته تموم شد!</b>\nجایگاه تو در گروه {e.group_no}: <b>{PLACE_NAMES[place]}</b>\n"
                        f"🎁 {chest['emoji']} {chest['name']}: " + " + ".join(got), "tournament"))
    return out


def _play_round(tournament: Tournament) -> list[tuple]:
    r = tournament.round
    out: list[tuple] = []
    matches = list(
        TournamentMatch.objects.filter(tournament=tournament, round=r, winner__isnull=True)
        .select_related("a__user", "b__user", "a__creature", "b__creature").order_by("group_no", "slot")
    )
    for m in matches:
        ca, pa, ea = _power(m.a)
        if m.b is None:
            winner, loser = m.a, None
            pb, eb = 0, ""
        else:
            cb, pb, eb = _power(m.b)
            # tie → the earlier registration (a stable, visible rule)
            first_is_a = m.a.id <= m.b.id
            if first_is_a:
                a_wins = constants.duel_attacker_wins(pa, ea, pb, eb)
            else:
                a_wins = not constants.duel_attacker_wins(pb, eb, pa, ea)
            winner, loser = (m.a, m.b) if a_wins else (m.b, m.a)
        m.winner, m.power_a, m.power_b, m.element_a, m.element_b = winner, pa, pb, ea, eb
        m.save(update_fields=["winner", "power_a", "power_b", "element_a", "element_b"])
        if loser is not None:
            _finish(loser, 2 if r == ROUNDS else (3 if r == ROUNDS - 1 else 5))
            if loser.user.notifications_on:
                out.append((loser.user_id,
                            f"🏟 <b>{ROUND_NAMES[r]}:</b> به «{lab_display(winner.user)}» باختی و از جام کنار رفتی. "
                            "جایزه‌ات آخر شب می‌رسه.", "tournament"))
            if r < ROUNDS and winner.user.notifications_on:
                out.append((winner.user_id,
                            f"🏟 <b>{ROUND_NAMES[r]}:</b> «{lab_display(loser.user)}» رو بردی! "
                            f"{ROUND_NAMES[r + 1]} ساعت {ROUND_AT[r + 1][1]} شروع می‌شه.", "tournament"))

    if r >= ROUNDS:
        for m in TournamentMatch.objects.filter(tournament=tournament, round=ROUNDS).select_related("winner"):
            _finish(m.winner, 1)
        # a group too small to reach round 3 crowns the survivor of its last played round
        for e in TournamentEntry.objects.filter(tournament=tournament, alive=True, place=0):
            _finish(e, 1)
        tournament.status = Tournament.FINISHED
        tournament.save(update_fields=["status"])
        out.extend(_pay_prizes(tournament))
        return out

    # build the next round: winners of slots 2k and 2k+1 meet in slot k
    by_group: dict[int, dict[int, TournamentEntry]] = {}
    for m in TournamentMatch.objects.filter(tournament=tournament, round=r).select_related("winner"):
        by_group.setdefault(m.group_no, {})[m.slot] = m.winner
    for g, winners in by_group.items():
        for k in range(GROUP_SIZE // (2 ** (r + 1))):
            a, b = winners.get(2 * k), winners.get(2 * k + 1)
            if a is None and b is None:
                continue
            first, second = (a, b) if a is not None else (b, None)
            TournamentMatch.objects.create(tournament=tournament, group_no=g, round=r + 1, slot=k, a=first, b=second)
    tournament.round = r + 1
    tournament.save(update_fields=["round"])
    return out


@transaction.atomic
def tick() -> list[tuple]:
    """Called from the notification job: runs the draw and each round when its time has
    come. Idempotent — safe to call any number of times."""
    now = timezone.localtime()
    tournament = Tournament.objects.select_for_update().filter(week_key=_week_key(now)).first()
    if tournament is None or tournament.status == Tournament.FINISHED:
        return []
    out: list[tuple] = []
    if tournament.status == Tournament.REGISTRATION and now >= draw_at(now):
        out.extend(_draw(tournament))
    while (tournament.status == Tournament.RUNNING and tournament.round in ROUND_AT
           and now >= round_at(tournament.round, now)):
        out.extend(_play_round(tournament))
    return out


# ── state for the screen ──────────────────────────────────────────────────────
def panel_state(user: User) -> dict:
    now = timezone.localtime()
    tournament = current()
    data = {
        "status": tournament.status if tournament else None,
        "registration_open": registration_open(),
        "draw_in": max(0, int((draw_at(now) - now).total_seconds())),
        "next_reg_in": max(0, int((next_reg_open() - now).total_seconds())),
        "entered": False,
    }
    if tournament is None:
        return data
    data["players"] = TournamentEntry.objects.filter(tournament=tournament).count()
    entry = my_entry(user, tournament)
    if entry is None:
        return data
    creature, power, element = _power(entry)
    data.update({
        "entered": True, "alive": entry.alive, "place": entry.place, "group": entry.group_no,
        "round": tournament.round,
        "my": None if creature is None else {"name": creature_name(creature), "power": power, "element": element},
    })
    if tournament.status == Tournament.RUNNING and entry.alive:
        match = _next_match_for(entry)
        if match is not None:
            foe = match.b if match.a_id == entry.id else match.a
            data["round_in"] = max(0, int((round_at(match.round, now) - now).total_seconds()))
            data["match_round"] = match.round
            if foe is None:
                data["foe"] = None
            else:
                fc, fp, fe = _power(foe)
                data["foe"] = {"name": lab_display(foe.user),
                               "creature": creature_name(fc) if fc else "؟", "power": fp, "element": fe}
    if entry.group_no:
        champ = (
            TournamentEntry.objects.filter(tournament=tournament, group_no=entry.group_no, place=1)
            .select_related("user").first()
        )
        data["champion"] = lab_display(champ.user) if champ else None
    return data

"""🔬 آزمایشگاه — the research-lab system.

A research-lab BUILDING (game.constants "research_lab", unlocks at main-hall 5) gates a
set of research TRACKS. Each track climbs 1→5 (never above the building's level), grants
a permanent bonus, and is slow + expensive: gold + DNA, 24h + 12h per level of real time,
and — unlike a building upgrade — can be finished early ONLY with diamonds (speed-up
cards are refused here).

Effects come in two flavours:
  * combat buffs (per-element power, global ATK/HP, crit, lifesteal) — applied inside
    game.creature.effective_stats via a transient `creature._research` dict that the sync
    layer attaches with attach_research(); an un-attached creature simply gets no bonus.
  * economy buffs (mine output, hunt gold) — read directly where those are computed.
"""

from __future__ import annotations

import datetime

from django.db import transaction
from django.utils import timezone

from bio_lab.models import Research, ResearchUpgrade, User
from game import constants
from game.buildings import building_level
from game.creature import GameError, InsufficientGoldError

# key -> definition. `kind` decides how the level is applied; `per_level` is the bonus
# gained per research level. All bonuses are permanent and stack additively per level.
RESEARCH_DEFS: dict[str, dict] = {
    "elem_fire":     {"label": "قدرت آتش", "emoji": "🔥", "btn_key": "btn_rsch_fire", "kind": "element", "element": "fire", "per_level": 0.02},
    "elem_water":    {"label": "قدرت آب", "emoji": "💧", "btn_key": "btn_rsch_water", "kind": "element", "element": "water", "per_level": 0.02},
    "elem_earth":    {"label": "قدرت خاک", "emoji": "🪨", "btn_key": "btn_rsch_earth", "kind": "element", "element": "earth", "per_level": 0.02},
    "elem_electric": {"label": "قدرت برق", "emoji": "⚡", "btn_key": "btn_rsch_electric", "kind": "element", "element": "electric", "per_level": 0.02},
    "might":      {"label": "خشمِ باستانی", "emoji": "⚔️", "btn_key": "btn_rsch_might", "kind": "atk", "per_level": 0.03},
    "vigor":      {"label": "سرزندگی", "emoji": "❤️", "btn_key": "btn_rsch_vigor", "kind": "hp", "per_level": 0.04},
}

# a human sentence describing each track's payoff (shown in the panel)
RESEARCH_DESC: dict[str, str] = {
    "elem_fire": "قدرتِ همه‌ی هیولاهای عنصرِ آتش رو هر لِوِل ۲٪ بیشتر می‌کنه.",
    "elem_water": "قدرتِ همه‌ی هیولاهای عنصرِ آب رو هر لِوِل ۲٪ بیشتر می‌کنه.",
    "elem_earth": "قدرتِ همه‌ی هیولاهای عنصرِ خاک رو هر لِوِل ۲٪ بیشتر می‌کنه.",
    "elem_electric": "قدرتِ همه‌ی هیولاهای عنصرِ برق رو هر لِوِل ۲٪ بیشتر می‌کنه.",
    "might": "حمله‌ی همه‌ی هیولاهات رو هر لِوِل ۳٪ بیشتر می‌کنه.",
    "vigor": "جانِ (HP) همه‌ی هیولاهات رو هر لِوِل ۴٪ بیشتر می‌کنه.",
}

RESEARCH_KEYS = list(RESEARCH_DEFS)


# A tiny in-memory cache: owner_id -> {key: level}. Research is a main-hall-5 endgame
# building, so only a handful of players ever have rows here — the cache stays small.
# It lets effective_stats (which must never hit the DB) apply combat buffs anywhere the
# owner has been "warmed" this process-lifetime, so the bonus is consistent across every
# battle path without wiring each one. A cold miss simply yields no bonus (graceful).
_LEVELS_CACHE: dict[int, dict[str, int]] = {}


# ── levels / caps ────────────────────────────────────────────────────────────
def research_levels(user: User) -> dict[str, int]:
    """{key: level} for every track (0 for untouched ones). Refreshes the cache."""
    have = {r.key: r.level for r in Research.objects.filter(owner=user)}
    levels = {k: have.get(k, 0) for k in RESEARCH_KEYS}
    _LEVELS_CACHE[user.id] = levels
    return levels


def warm(user: User) -> None:
    """Load the owner's research into the cache (sync). Cheap to call before a battle so
    effective_stats can apply the buff; only touches the DB when the player has research."""
    research_levels(user)


def combat_bonuses_for(owner_id: int, element: str) -> dict | None:
    """Pure, cache-only (async-safe) combat buffs for a creature — or None on a cold miss
    or when the owner has no research. Used by game.creature.effective_stats."""
    levels = _LEVELS_CACHE.get(owner_id)
    if not levels or not _has_any(levels):
        return None
    return combat_bonuses(levels, element)


def research_cap(user: User) -> int:
    """The highest level any track can reach = the research-lab building's level."""
    return building_level(user, "research_lab")


def is_unlocked(user: User) -> bool:
    return building_level(user, "research_lab") > 0


# ── upgrade jobs (mirror game.buildings) ──────────────────────────────────────
def _finish_row(upgrade: ResearchUpgrade) -> None:
    owner_id = upgrade.owner_id
    row, _ = Research.objects.get_or_create(owner_id=owner_id, key=upgrade.key)
    if upgrade.target_level > row.level:
        row.level = upgrade.target_level
        row.save(update_fields=["level"])
    upgrade.delete()
    # refresh the cache so the new bonus is live immediately (not just after next warm)
    have = {r.key: r.level for r in Research.objects.filter(owner_id=owner_id)}
    _LEVELS_CACHE[owner_id] = {k: have.get(k, 0) for k in RESEARCH_KEYS}


def check_and_apply(user: User) -> None:
    """Lazily complete every research job whose timer has passed (read-time, no cron)."""
    now = timezone.now()
    for up in list(ResearchUpgrade.objects.filter(owner=user, finishes_at__lte=now)):
        _finish_row(up)


def active_upgrades(user: User) -> list[ResearchUpgrade]:
    check_and_apply(user)
    return list(ResearchUpgrade.objects.filter(owner=user).order_by("finishes_at"))


def upgrade_for(user: User, key: str) -> ResearchUpgrade | None:
    return ResearchUpgrade.objects.filter(owner=user, key=key).first()


def next_cost(target_level: int) -> tuple[int, int]:
    """(gold, dna) to reach `target_level`."""
    return (
        constants.RESEARCH_GOLD_COST[target_level],
        constants.RESEARCH_DNA_COST[target_level],
    )


@transaction.atomic
def start_research(user: User, key: str) -> ResearchUpgrade:
    if key not in RESEARCH_DEFS:
        raise GameError("این پژوهش وجود نداره.")
    user = User.objects.select_for_update().get(id=user.id)
    check_and_apply(user)
    cap = research_cap(user)
    if cap <= 0:
        raise GameError("اول باید ساختمونِ «🔬 آزمایشگاه» رو بسازی (از سطح ۵ تالار مِهر باز می‌شه).")
    if ResearchUpgrade.objects.filter(owner=user, key=key).exists():
        raise GameError("این پژوهش همین الان در حال انجامه.")
    # only ONE research may run at a time across all tracks
    other = ResearchUpgrade.objects.exclude(key=key).filter(owner=user).first()
    if other is not None:
        od = RESEARCH_DEFS.get(other.key, {})
        raise GameError(
            f"همزمان فقط یک پژوهش می‌شه انجام داد — الان «{od.get('emoji','')} {od.get('label', other.key)}» "
            "در حال انجامه. اول اون تموم بشه (یا با الماس تمومش کن)."
        )
    current = Research.objects.filter(owner=user, key=key).first()
    level = current.level if current else 0
    if level >= constants.RESEARCH_MAX_LEVEL:
        raise GameError("این پژوهش به سقف نهایی (سطح ۵) رسیده.")
    target = level + 1
    if target > cap:
        raise GameError(
            f"لِوِلِ این پژوهش نمی‌تونه از لِوِلِ ساختمونِ آزمایشگاه ({cap}) جلو بزنه — "
            "اول خودِ آزمایشگاه رو ارتقا بده."
        )
    gold, dna = next_cost(target)
    if user.coins < gold:
        raise InsufficientGoldError(
            f"طلا کافی نداری! این پژوهش <b>{gold:,}</b> طلا می‌خواد (الان {user.coins:,} داری).",
            need=gold, have=user.coins,
        )
    if user.dna_fragments < dna:
        raise GameError(f"DNA کافی نداری! این پژوهش <b>{dna:,}</b> DNA می‌خواد (الان {user.dna_fragments:,} داری).")
    user.coins -= gold
    user.dna_fragments -= dna
    user.save(update_fields=["coins", "dna_fragments"])
    finishes_at = timezone.now() + datetime.timedelta(seconds=constants.research_seconds(target))
    return ResearchUpgrade.objects.create(
        owner=user, key=key, target_level=target, finishes_at=finishes_at
    )


def diamond_finish_price(upgrade: ResearchUpgrade) -> int:
    remaining = (upgrade.finishes_at - timezone.now()).total_seconds()
    return constants.diamond_finish_cost(remaining)


@transaction.atomic
def finish_with_diamonds(user: User, key: str) -> int:
    """Instantly complete a research job for diamonds. Returns the diamonds spent."""
    user = User.objects.select_for_update().get(id=user.id)
    upgrade = ResearchUpgrade.objects.select_for_update().filter(owner=user, key=key).first()
    if upgrade is None:
        raise GameError("این پژوهش در حال انجام نیست.")
    cost = diamond_finish_price(upgrade)
    if user.diamonds < cost:
        raise GameError(f"الماس کافی نداری! تموم‌کردنِ فوری این پژوهش {cost} الماس می‌خواد.")
    user.diamonds -= cost
    user.save(update_fields=["diamonds"])
    _finish_row(upgrade)
    return cost


# ── effects ──────────────────────────────────────────────────────────────────
def combat_bonuses(levels: dict[str, int], element: str) -> dict:
    """Per-creature combat multipliers for a creature of `element`, given the owner's
    research `levels`. Returns stat multipliers + additive crit/lifesteal. element power
    lifts every stat equally (so it raises the power score by exactly that %)."""
    elem_key = f"elem_{element}"
    elem_pct = levels.get(elem_key, 0) * RESEARCH_DEFS[elem_key]["per_level"] if elem_key in RESEARCH_DEFS else 0.0
    atk_extra = levels.get("might", 0) * RESEARCH_DEFS["might"]["per_level"]
    hp_extra = levels.get("vigor", 0) * RESEARCH_DEFS["vigor"]["per_level"]
    return {
        "hp_mult": 1 + elem_pct + hp_extra,
        "atk_mult": 1 + elem_pct + atk_extra,
        "def_mult": 1 + elem_pct,
        "spd_mult": 1 + elem_pct,
        "crit_add": 0.0,
        "leech_add": 0.0,
    }


def _has_any(levels: dict[str, int]) -> bool:
    return any(v > 0 for v in levels.values())


def attach_research_multi(creatures) -> None:
    """Like attach_research but for creatures spanning MANY owners (leaderboards, member
    lists). Warms each distinct owner's research levels ONCE (sync/DB) and stamps every
    creature's transient `_research`, so a power display never silently drops a foreign
    owner's research buffs just because that owner wasn't in the process cache yet."""
    by_owner: dict[int, list] = {}
    for c in creatures:
        if c is not None:
            by_owner.setdefault(c.owner_id, []).append(c)
    for owner_id, cs in by_owner.items():
        have = {r.key: r.level for r in Research.objects.filter(owner_id=owner_id)}
        levels = {k: have.get(k, 0) for k in RESEARCH_KEYS}
        _LEVELS_CACHE[owner_id] = levels  # warm the cache too (belt-and-suspenders)
        if _has_any(levels):
            for c in cs:
                c._research = combat_bonuses(levels, c.element)


def attach_research(user: User, creatures) -> None:
    """Stamp each creature with the owner's combat research bonuses (a transient
    `_research` dict effective_stats prefers over the cache) AND warm the cache. Call in
    the SYNC layer before a battle or a power display. A no-op when the player has no
    research. `creatures` may be a single creature or an iterable."""
    if isinstance(creatures, (list, tuple, set)):
        items = [c for c in creatures if c is not None]
    else:
        items = [creatures] if creatures is not None else []
    levels = research_levels(user)  # also warms the cache for any un-stamped creature
    if not items or not _has_any(levels):
        return
    for c in items:
        c._research = combat_bonuses(levels, c.element)

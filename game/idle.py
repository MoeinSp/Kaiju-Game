"""Idle / AFK rewards + a rotating daily dungeon — the classic idle-game hooks.

* **Idle chest** — loot accrues in real time whether you're playing or away, up to
  a cap, and you collect the lump on return. The rate scales with how far you've
  pushed (campaign + lab level), so progressing makes even your downtime pay more.
  The cap (12h) is the "come back at least once a day" nudge.
* **Daily dungeon** — a single free run per day with a real boss fight. Reward type
  rotates daily (gold / DNA / XP) and scales with your progress.

Lazy like everything else: accrual is computed from `idle_since`, and the dungeon
is deduped by the last-run day. No cron.
"""

from __future__ import annotations

import random

from django.utils import timezone

from bio_lab.models import Creature, User
from game import lab
from game.combat import resolve_duel
from game.creature import GameError, effective_stats
from game.daily import today_str
from game.equipment import get_equipped_items

IDLE_CAP_HOURS = 12

# Dungeon boss definitions: element, name bank, and how much harder than the player
DUNGEON_DEFS = [
    {
        "key": "gold",
        "emoji": "💰",
        "title": "دخمه‌ی طلا",
        "resource": "coins",
        "boss_names": ["محافظ خزانه", "دزد طلا", "گارد فولادی"],
        "boss_element": "earth",
        "boss_power_mult": 1.35,
        "boss_flavor": "درون دخمه درخشش طلا چشم‌ها رو می‌زنه...",
    },
    {
        "key": "dna",
        "emoji": "🧬",
        "title": "دخمه‌ی DNA",
        "resource": "dna",
        "boss_names": ["بیوهیولا", "موتانت آزمایشگاه", "ژنتیک‌باز"],
        "boss_element": "electric",
        "boss_power_mult": 1.5,
        "boss_flavor": "فضای دخمه از انرژی زیستی لرزش داره...",
    },
    {
        "key": "xp",
        "emoji": "⭐",
        "title": "دخمه‌ی تجربه",
        "resource": "xp",
        "boss_names": ["قهرمان باستان", "آزمایشگر افسانه‌ای", "استاد دخمه"],
        "boss_element": "fire",
        "boss_power_mult": 1.65,
        "boss_flavor": "سایه‌ی یک موجود قدرتمند توی تاریکی حرکت می‌کنه...",
    },
]


def _idle_rates(user: User) -> tuple[float, float]:
    lvl = lab.lab_level(user)
    stage = user.campaign_stage
    coins_per_hour = 40 + stage * 6 + lvl * 4
    dna_per_hour = 1 + stage * 0.2 + lvl * 0.1
    return coins_per_hour, dna_per_hour


def idle_status(user: User) -> dict:
    elapsed_h = (timezone.now() - user.idle_since).total_seconds() / 3600
    elapsed_h = max(0.0, min(IDLE_CAP_HOURS, elapsed_h))
    cph, dph = _idle_rates(user)
    return {
        "hours": elapsed_h,
        "coins": round(cph * elapsed_h),
        "dna": round(dph * elapsed_h),
        "capped": elapsed_h >= IDLE_CAP_HOURS,
        "cap_hours": IDLE_CAP_HOURS,
    }


def collect_idle(user: User) -> dict:
    """Grant the accrued idle loot and reset the accrual clock. Returns what was
    granted (may be zero if collected again immediately)."""
    st = idle_status(user)
    fields = ["idle_since"]
    if st["coins"]:
        user.coins += st["coins"]; fields.append("coins")
    if st["dna"]:
        user.dna_fragments += st["dna"]; fields.append("dna_fragments")
    user.idle_since = timezone.now()
    user.save(update_fields=fields)
    return {"coins": st["coins"], "dna": st["dna"]}


# ── rotating daily dungeon ────────────────────────────────────────────────────

def today_dungeon() -> dict:
    day_of_year = timezone.localtime(timezone.now()).timetuple().tm_yday
    return DUNGEON_DEFS[day_of_year % len(DUNGEON_DEFS)]


def dungeon_reward(user: User) -> dict:
    lvl = lab.lab_level(user)
    stage = user.campaign_stage
    res = today_dungeon()["resource"]
    if res == "coins":
        return {"coins": 300 + stage * 30 + lvl * 20}
    if res == "dna":
        return {"dna": 20 + stage + lvl}
    return {"xp": 50 + stage * 3 + lvl * 2}


def dungeon_status(user: User) -> dict:
    dg = today_dungeon()
    return {
        "dungeon": dg,
        "reward": dungeon_reward(user),
        "can_run": user.last_dungeon_day != today_str(),
    }


def _boss_creature(dg: dict, player_power: int) -> Creature:
    """Build an unsaved boss creature scaled relative to the player's power."""
    power = max(5, round(player_power * dg["boss_power_mult"]))
    from game.creature import base_share_for_rating

    share = base_share_for_rating(power)
    return Creature(
        name=random.choice(dg["boss_names"]),
        element=dg["boss_element"],
        rarity="rare",
        level=1,
        base_hp=share,
        base_atk=share,
        base_def=share,
        base_spd=share,
    )


def _player_power(creature: Creature) -> int:
    from game.creature import creature_power

    return creature_power(creature, get_equipped_items(creature))


def run_dungeon(user: User) -> dict | None:
    """One free dungeon run per day. Requires the user's active creature for the fight.
    Returns result dict (won, log_text, reward_or_consolation), or None if already run."""
    today = today_str()
    if user.last_dungeon_day == today:
        return None

    creature = Creature.objects.filter(owner=user, is_active=True).first()
    if creature is None:
        raise GameError("اول یه موجود فعال انتخاب کن.")

    dg = today_dungeon()
    player_power = _player_power(creature)
    boss = _boss_creature(dg, player_power)

    winner, log_text = resolve_duel(creature, boss)
    won = (winner is creature)

    full_reward = dungeon_reward(user)
    fields = ["last_dungeon_day"]
    user.last_dungeon_day = today

    if won:
        reward = full_reward
    else:
        # consolation: 30% of the full reward so losing still feels worth the try
        reward = {}
        if full_reward.get("coins"):
            reward["coins"] = max(1, round(full_reward["coins"] * 0.3))
        if full_reward.get("dna"):
            reward["dna"] = max(1, round(full_reward["dna"] * 0.3))
        if full_reward.get("xp"):
            reward["xp"] = max(1, round(full_reward["xp"] * 0.3))

    if reward.get("xp"):
        lab.add_lab_xp(user, reward["xp"])
    if reward.get("coins"):
        user.coins += reward["coins"]; fields.append("coins")
    if reward.get("dna"):
        user.dna_fragments += reward["dna"]; fields.append("dna_fragments")

    user.save(update_fields=fields)

    return {
        "won": won,
        "log_text": log_text,
        "reward": reward,
        "full_reward": full_reward,
        "boss_name": boss.name,
        "boss_power": player_power,  # approximate
        "player_power": player_power,
        "dungeon": dg,
    }
